"""
LLM text summary for the v2 care report — two objective paragraphs:

    A 核心走勢   : which signals deviate most from the resident's own baseline.
    B 關聯因子   : how those deviations cluster in time with care observations.

Uses an OpenAI-compatible remote chat endpoint (holo-fast); falls back to a
rule-based composition if the model is unavailable, or if its output fails
post-generation validation. Returns a dict {"A": str, "B": str}.

架構重點（2026-07 從 InspectSummary/簡易版移植過來的穩定性技巧，依隨行本版本現有
欄位改寫，血糖/BMI/病史排序等簡易版專屬功能因為這裡沒有對應資料，未移植）：
1. 驗證邏輯是白名單制：只允許中文字、數字、常見標點，和一組明列的英文單位
   縮寫；任何其他文字（英文單字、簡體字、韓文/日文等其他語言）一律視為異常，
   不用每次冒出新語言才手動加規則。
2. 格式驗證通過後，再加一道「LLM 當評審」關卡，檢查語意是否通順、有無疊字/
   缺字/亂碼詞組——這類問題規則式驗證天生抓不到。
3. 重試採獨立呼叫（不帶前次失敗輸出進 context）：實測把失敗輸出留在對話裡
   重試，模型常常在同一個詞上重複犯錯，獨立重試給模型重新取樣的機會比要求
   自我修正更有效。第一次用 temperature=0 求最穩定，失敗才需要一點隨機性。
"""
import json
import os
import re

from openai import OpenAI
from opencc import OpenCC

from .analysis_core import NO_DATA

_cc = OpenCC("s2t")  # 簡體 → 繁體，模型偶爾會吐出簡體字（如「体重」），這裡收尾修正


# ── NO_DATA-safe formatters（給 _fallback() 用）───────────────────────────────
# 真實資料常見某些欄位缺測（例如 day_night_reversal 目前完全沒有真實 DB 來源，
# 一定是 NO_DATA），_fallback() 是純規則式字串拼接，沒有 LLM 幫忙轉述，資料不足
# 時不能直接把內部標記字串印給使用者看。
def _fi(v, no_data="—"):
    return no_data if v == NO_DATA else str(int(v))


def _fs(v, no_data="—"):
    return no_data if v == NO_DATA else v

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:9000/v1")
# 金鑰不給預設值——之前這裡寫死過一把真實金鑰在原始碼裡，已經進了 git
# history。啟動前用 `$env:LLM_API_KEY = "..."`（PowerShell）設定，缺少時
# generate_summary() 呼叫 OpenAI 用戶端會失敗，會被既有的 except 區塊接住
# 轉成規則式 fallback，不會讓服務整個掛掉，只是不會有真的 LLM 生成。
LLM_API_KEY  = os.environ.get("LLM_API_KEY")
MODEL        = os.environ.get("LLM_MODEL", "holo-fast")
TIMEOUT      = 60

if not LLM_API_KEY:
    print("[llm_summary] 警告：未設定 LLM_API_KEY 環境變數，LLM 摘要生成會直接失敗並轉為規則式 fallback。")

SYSTEM_PROMPT = (
    "你是一位老年醫學科醫師助理，根據統計數據撰寫客觀的單頁照護摘要。"
    "僅描述數據事實、趨勢與時間關聯，不得建議調藥或下診斷。"
    "務必全程使用繁體中文（正體字），禁止使用任何簡體字，"
    "也絕對禁止把中文詞換成英文單字，以下是常見錯誤示範，請勿模仿："
    "心率不可寫 Heart率/heart rate；顯著不可寫 significant；"
    "每週不可寫 weekly；每月不可寫 monthly；差值不可寫 delta；"
    "偏低不可寫 low；顛倒不可寫 flip；心血管不可寫 cardiovascular；"
    "攝氏不可寫 celsius；夜間不可寫 night；減少不可寫 reduce；"
    "需要不可寫 need；頻率不可寫 frequency；繼續不可寫 Continue。"
    "純文字，禁止任何數學符號或 LaTeX（用 → 表示變化，用 >= 表示大於等於）。"
    "只輸出兩段，分別以「A」「B」開頭，每段約 2 至 3 句、不超過 120 字。"
)

# 範例輸入輸出（few-shot）——實測加入正面範例後，原始輸出乾淨率從 0/5 提升到 3/5，
# 比單靠負面範例規則有效得多，跟正式 _build_prompt() 用同一種 facts 格式。
_FEWSHOT_FACTS = {
    "晨間收縮壓": {"月初": 132, "月底": 145, "趨勢": "緩升", "目標": 140},
    "靜態心率": {"起": 70, "訖": 88},
    "血氧趨勢": "97→93%",
    "體重6個月": {"前": 60, "現": 57, "百分比": "-5.0%"},
    "活動": {"起始每週": 4, "近期每週": "0-3"},
    "飲水未達標天數": 9,
    "睡眠日夜顛倒天數": 6,
    "惡化起始時段": "11/14",
    "體溫偏低事件": [{"date": "11/20", "t": 35.6, "delta": -1.2}],
    "體溫基線": 36.8,
}
_FEWSHOT_USER = (
    "依以下統計資料，撰寫兩段客觀照護摘要：\n\n"
    + json.dumps(_FEWSHOT_FACTS, ensure_ascii=False, indent=2)
    + "\n\n格式：\nA 核心走勢：（偏離個人基準最明顯的指標與同期惡化方向）\n"
    "B 關聯因子：（各指標惡化在時間上的重疊，及體溫偏低與食慾活動同日下降）"
)
_FEWSHOT_ASSISTANT = (
    "A 核心走勢：偏離個人基準最明顯者為晨間收縮壓，月初約 132 緩升至月底約 145 mmHg"
    "（目標 <140）；同期靜態心率（70→88）、血氧（97→93%）、體重（6 個月 60→57，-5.0%）亦呈惡化方向。\n"
    "B 關聯因子：血壓與心率上移、活動下降的時段（11/14 起），與飲水偏少（9 天未達標）、"
    "睡眠日夜顛倒（6 天）的紀錄重疊；11/20 另見體溫 35.6°C（較基線 -1.2°C）與食慾、活動同日驟降。"
)

# 實測 holo-fast 固定會夾雜的英文字 → 中文替換表，當 _repair() 的優先修正字典用
# （比對照表更全面的「查不到就砍掉」機制見 _strip_unresolved()）。
# 正規表達式只抓純英文字母片段（「率」是中文，不會被抓進「Heart率」這個片段
# 裡），所以鍵要用抓到的片段本身（"heart"），不是完整詞（"heart率"）。
_EN_WORD_FIX = {
    "weekly": "每週", "monthly": "每月", "month": "月", "delta": "差值",
    "low": "偏低", "flip": "顛倒", "cardiovascular": "心血管",
    "cardiorespiratory": "心肺", "celsius": "攝氏", "night": "夜間",
    "reduce": "減少", "need": "需要", "frequency": "頻率",
    "heart": "心", "continue": "繼續",
}

# ── 驗證白名單（取代舊版黑名單）─────────────────────────────────────────────
# 只允許：中文字、數字、常見標點符號、跟一組明列的英文單位縮寫。任何其他文字
# （不管是英文單字、韓文、日文，還是下次隨便什麼新語言）一律視為異常——不用
# 每次新語言冒出來才手動加規則，一次性堵住整類問題。
_ALLOWED_LATIN_TOKENS = {"mmhg", "bpm", "cv", "spo2"}
# 2026-08-13新增：「–」是U+2013 en dash，跟一般鍵盤打得出來的ASCII連字號
# 「-」(U+002D)、全形「－」(U+FF0D)是三個不同字元——v9的_fmt_md()系列函式
# （日期區間如「11/1–11/30」）全部固定用這個字元組字串，但原本的允許清單
# 只收了ASCII跟全形兩種，沒收en dash，導致_strip_unresolved()把它當成
# 「翻不出來的外語片段」整個砍掉（「11/1–11/30」變成「11/111/30」），
# 連帶讓v9的_terms_survived()逐字比對必定失敗、每次都轉fallback——這不是
# LLM輸出有問題，是我們自己的_repair()把LLM給對的答案改錯了。
_ALLOWED_SYMBOLS = set("，。；：、（）「」『』%+-.,()/~°=><→↑↓－＝_'\"!?！？＄$*～–")

# 生理量測不可能出現的幻覺單位詞（實測看過模型自己捏造的怪單位）。
_BAD_UNIT_WORDS = ("公釐", "公厘", "毫升水銀柱", "毫升")

_FOREIGN_RUN = re.compile(r"[^一-鿿0-9\s]+")


def _strip_unresolved(text: str) -> str:
    """把 _EN_WORD_FIX 字典裡查不到翻譯的外語/怪異片段直接清除，而不是要求整
    句重寫。實測洩漏詞的花樣多到字典法窮舉不完（英文、簡體字、韓文、阿拉伯
    文……每次測都冒出新的），與其每次都補字典，不如把「翻不出來的片段」直接
    拿掉——這些片段泰半是模型夾雜的贅字，砍掉後前後文通常還是通順的。"""
    def _clean_run(run: str) -> str:
        out = []
        i, n = 0, len(run)
        while i < n:
            ch = run[i]
            if ch in _ALLOWED_SYMBOLS:
                out.append(ch)
                i += 1
                continue
            if ch.isascii() and ch.isalpha():
                j = i
                while j < n and run[j].isascii() and run[j].isalpha():
                    j += 1
                token = run[i:j]
                if token.lower() in _ALLOWED_LATIN_TOKENS:
                    out.append(token)
                i = j
            else:
                i += 1  # 非 ASCII 的外語字元（韓文/日文/阿拉伯文…）一律丟棄
        return "".join(out)

    text = _FOREIGN_RUN.sub(lambda m: _clean_run(m.group(0)), text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"[-~+*/]+(?=[，。；：！？」』）]|$)", "", text)
    text = re.sub(r"[，、；：]+(?=[。！？])", "", text)
    text = re.sub(r"([，。；：])\1+", r"\1", text)
    return text


def _repair(text: str) -> str:
    """英文洩漏詞換回中文（大小寫不拘）+ 簡體轉繁體 + 清除字典查不到翻譯的
    殘留外語片段。"""
    if not text:
        return text

    def _sub(m):
        return _EN_WORD_FIX.get(m.group(0).lower(), m.group(0))

    text = re.sub(r"[A-Za-z]{3,}", _sub, text)
    text = _cc.convert(text)
    return _strip_unresolved(text)


def _validate(text: str) -> list:
    """白名單式驗證：回傳問題清單；空清單代表通過，可以直接使用。"""
    if not text:
        return ["內容為空"]
    problems = []

    foreign_runs = re.findall(r"[^一-鿿0-9\s]+", text)
    leftover = []
    for run in foreign_runs:
        remainder = "".join(ch for ch in run if ch not in _ALLOWED_SYMBOLS)
        if remainder and remainder.lower() not in _ALLOWED_LATIN_TOKENS:
            leftover.append(run)
    if leftover:
        problems.append(f"非允許字元殘留：{leftover}")

    for w in _BAD_UNIT_WORDS:
        idx = text.find(w)
        if idx != -1:
            start, end = max(0, idx - 8), min(len(text), idx + len(w) + 8)
            problems.append(f"疑似幻覺單位詞「{w}」（…{text[start:end]}…）")
    if not text.strip().endswith("。"):
        problems.append("未以句號結尾（疑似截斷）")
    return problems


# 只抓「具體壞掉的模式」，不要求整句語意分析：孤立的「率」通常代表「心率」
# 漏成「率」（正常中文裡「率」前面一定緊貼著修飾字，不會斷裂）。
_ORPHAN_RATE = re.compile(r"(?:^|[，、；：\s])率")


def _pattern_defect_check(text: str) -> list:
    problems = []
    if _ORPHAN_RATE.search(text):
        problems.append("疑似缺字（孤立的「率」，可能漏了「心」）")
    return problems


_SELF_CONTRADICTION = re.compile(
    r"「([^「」]{1,10})」[^「」]{0,20}"
    r"(?:應為|应为|應用|应用|应改為|應改為|误植[為为]|誤植[為为]|"
    r"应该使用(?:繁體字)?|應該使用(?:繁體字)?)「([^「」]{1,10})」"
)

# 「留意」「注意」是安全警語模板固定用詞，繁簡寫法完全相同、且是正確繁體
# 中文。實測評審對這兩個字反覆出現多種矛盾指控，直接視為已知假陽性來源。
_KNOWN_FALSE_FLAG_WORDS = ("留意", "注意")


def _discount_judge_hallucinations(verdict: str) -> str:
    """過濾評審判決裡已知的假陽性模式，回傳過濾後的判決文字（空字串代表沒
    有真正的問題）。兩類過濾：
    1. 自我矛盾：「X」...應為「X」，建議修正的字跟原字完全相同，這種宣稱
       本身就是錯的。
    2. 已知誤報詞：抱怨內容只跟「留意」「注意」有關。"""
    def _drop_self_contradiction(m):
        return "" if m.group(1) == m.group(2) else m.group(0)

    verdict = _SELF_CONTRADICTION.sub(_drop_self_contradiction, verdict)
    segments = re.split(r"[\n。]", verdict)
    filler = re.compile(r"^(其餘(部分)?)?(無明顯|無問題|無)[^「」]*$")
    kept = [
        s for s in segments
        if s.strip()
        and not any(w in s for w in _KNOWN_FALSE_FLAG_WORDS)
        and not filler.match(s.strip())
    ]
    return "。".join(kept)


def _llm_judge(client, text: str) -> list:
    """LLM 當評審：規則式驗證只檢查「有沒有出現不該出現的字元」，抓不到語意
    本身壞掉的問題（亂碼詞組、疊字、缺字）。這裡另外打一次（同一顆模型即可）
    專門檢查這類語言品質問題。評審呼叫本身失敗不應該卡住整個流程，視為通過
    但印出記錄，靠格式驗證＋重試機制兜底。"""
    review_user = (
        "請檢查以下繁體中文健康摘要文字，是否有下列任一問題：\n"
        "1) 語意不通或無意義詞組（例如「起與芒」「低血糖黃海」這種疑似亂碼"
        "拼接或成語誤用，指的是讀起來不像正常詞語的字元組合，不是你個人覺得"
        "措辭可以更好）\n"
        "2) 重複用字（例如「健康健康」）\n"
        "3) 缺字導致語句不完整或不通順（例如「建儘快」少了「議」字）\n\n"
        "只要文字語法通順、一般讀者看得懂，就不算問題——不要對用詞選擇、"
        "語氣、醫學術語（例如「血壓危象」都是正常醫學用語）提出意見，只挑真正"
        "讀不通、缺字、或明顯不是詞語的部分。\n\n"
        f"文字：{text}\n\n"
        "如果完全沒有問題，只回答「PASS」三個字，不要有其他文字。"
        "如果有問題，用一行簡短列出具體問題是什麼，不要重寫或修改原文。"
    )
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是嚴謹的繁體中文醫療文件校對員，只負責挑錯，不負責改寫內容。"},
                {"role": "user", "content": review_user},
            ],
            temperature=0,
            max_tokens=200,
            stream=False,
        )
        verdict = resp.choices[0].message.content.strip()
        # 模型常常不遵守「只回答 PASS」的指令，會先列出檢查清單才在結尾附上
        # PASS，只要文字裡任何位置出現 PASS 就視為通過，比要求精確格式更可靠。
        if "PASS" in verdict.upper():
            return []
        verdict = _discount_judge_hallucinations(verdict)
        if not verdict.strip():
            return []
        return [f"LLM 評審：{verdict}"]
    except Exception as e:
        print(f"[llm-judge] 評審呼叫失敗，略過此關卡：{e}")
        return []


def _strip_no_data(obj):
    """遞迴清掉 facts 裡的 NO_DATA 佔位值，不要把內部標記字串送進 prompt——
    模型看到不存在的資料還是會硬著頭皮描述它（例如把 __NO_DATA__ 織進句子
    裡），比乾脆不提這項指標更容易生成怪異、洩漏內部符號的文字。巢狀 dict
    裡單一 key 是 NO_DATA 就只拿掉那個 key，其餘資料齊全的欄位照樣送出。"""
    if isinstance(obj, dict):
        cleaned = {k: _strip_no_data(v) for k, v in obj.items()}
        return {k: v for k, v in cleaned.items() if v is not None and v != {}}
    if isinstance(obj, list):
        return [_strip_no_data(v) for v in obj]
    return None if obj == NO_DATA else obj


def _build_prompt(a: dict) -> str:
    bp  = a["obs"]["bp"]
    hwt = a["obs"]["hr_weight_temp"]
    sfw = a["obs"]["spo2_food_water"]
    sas = a["obs"]["sleep_activity_social"]
    b2  = a["signals"]["block2"]
    b3  = a["signals"]["block3"]

    # 血氧趨勢／近期活動這兩項是把兩個原始值組成一串複合字串；只要其中一個
    # 缺測，整組視為缺資料（NO_DATA），交給 _strip_no_data() 整組拿掉，不要
    # 生成「94→__NO_DATA__%」這種半數字半佔位的怪組合。
    spo2_ok = NO_DATA not in (hwt["spo2_start"], hwt["spo2_end"])
    rehab_ok = NO_DATA not in (sas["rehab_end_lo"], sas["rehab_end_hi"])

    facts = {
        "晨間收縮壓": {"月初": bp["first_week"], "月底": bp["last_week"],
                       "趨勢": bp["trend_dir"], "目標": bp["target"]},
        "靜態心率": {"起": hwt["hr_start"], "訖": hwt["hr_end"]},
        "血氧趨勢": f"{hwt['spo2_start']}→{hwt['spo2_end']}%" if spo2_ok else NO_DATA,
        "體重6個月": {"前": hwt["weight_6mo"], "現": hwt["weight_now"], "百分比": f"{hwt['weight_pct']}%" if hwt["weight_pct"] != NO_DATA else NO_DATA},
        "活動": {"起始每週": sas["rehab_start"],
                 "近期每週": f"{sas['rehab_end_lo']}-{sas['rehab_end_hi']}" if rehab_ok else NO_DATA},
        "飲水未達標天數": sfw["water_short_days"],
        "睡眠日夜顛倒天數": sas["reversal_days"],
        "惡化起始時段": b2["cluster_start"],
        "體溫偏低事件": b3["low"],
        "體溫基線": b3["temp_baseline"],
    }
    facts = _strip_no_data(facts)
    return (
        "依以下統計資料，撰寫兩段客觀照護摘要：\n\n"
        + json.dumps(facts, ensure_ascii=False, indent=2)
        + "\n\n格式：\nA 核心走勢：（偏離個人基準最明顯的指標與同期惡化方向）\n"
        "B 關聯因子：（各指標惡化在時間上的重疊，及體溫偏低與食慾活動同日下降）"
    )


_A_HEADER = re.compile(r"^A\s*核心走勢?\s*[：:]?\s*")
_B_HEADER = re.compile(r"^B\s*關聯因子?\s*[：:]?\s*")


def _parse_two(text: str) -> dict:
    """逐行解析 A/B 兩段。用顯性的 section 狀態機（而非用累積字串是否為空
    字串來推斷「這段開始了沒」）——舊版拿 a_txt/b_txt 的真假值當狀態，一旦
    標題行被清空成空字串就會誤判成「A段還沒開始」，把後面真正的內容整行
    丟掉。同時標題前綴改用 regex 精準去除，不用 str.lstrip()（lstrip 的參
    數是字元集合、不是前綴字串，模型把標題單獨換行時，整行剛好都是集合
    內字元，會被整行清空）。"""
    a_txt, b_txt = "", ""
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("A") and section != "B":
            section = "A"
            rest = _A_HEADER.sub("", line)
            if rest:
                a_txt = rest
            continue
        if line.startswith("B"):
            section = "B"
            rest = _B_HEADER.sub("", line)
            if rest:
                b_txt = rest
            continue
        if section == "A":
            a_txt = a_txt + line if a_txt else line
        elif section == "B":
            b_txt = b_txt + line if b_txt else line
    return {"A": a_txt, "B": b_txt}


def _fallback(a: dict) -> dict:
    bp  = a["obs"]["bp"]
    hwt = a["obs"]["hr_weight_temp"]
    sas = a["obs"]["sleep_activity_social"]
    sfw = a["obs"]["spo2_food_water"]
    b2  = a["signals"]["block2"]
    b3  = a["signals"]["block3"]

    A = (
        f"偏離個人基準最明顯者為晨間收縮壓，月初約 {_fi(bp['first_week'])} "
        f"{_fs(bp['trend_dir'])}至月底約 {_fi(bp['last_week'])} mmHg（目標 <{bp['target']}）；"
        f"同期靜態心率（{_fi(hwt['hr_start'])}→{_fi(hwt['hr_end'])}）、"
        f"血氧（{_fi(hwt['spo2_start'])}→{_fi(hwt['spo2_end'])}%）、"
        f"體重（6 個月 {_fi(hwt['weight_6mo'])}→{_fi(hwt['weight_now'])}，"
        f"{_fs(hwt['weight_pct'], '—')}{'' if hwt['weight_pct'] == NO_DATA else '%'}）"
        f"亦呈惡化方向。"
    )
    low_note = ""
    if b3["low"]:
        ev = b3["low"][0]
        delta = round(ev["t"] - b3["temp_baseline"], 1)
        low_note = (f"；{ev['date']} 另見體溫 {ev['t']}°C（較基線 {delta}°C）"
                    f"與食慾、活動同日驟降")
    B = (
        f"血壓與心率上移、活動下降的時段（{_fs(b2['cluster_start'])} 起），"
        f"與飲水偏少（{_fi(sfw['water_short_days'])} 天未達標）、"
        f"睡眠日夜顛倒（{_fi(sas['reversal_days'])} 天）的紀錄重疊{low_note}。"
    )
    return {"A": A, "B": B, "generation_method": "fallback"}


MAX_ATTEMPTS = 3  # 第一次 + 最多 2 次重試，都失敗才轉 fallback
_JUDGE_ENABLED = True


def generate_summary(analysis: dict) -> dict:
    """Return {'A': ..., 'B': ..., 'generation_method': 'llm'|'fallback'}.
    Falls back to rule-based text on any error, or if the (repaired) LLM
    output still fails validation after retries."""
    try:
        client = OpenAI(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            timeout=TIMEOUT,
        )
        base_messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _FEWSHOT_USER},
            {"role": "assistant", "content": _FEWSHOT_ASSISTANT},
            {"role": "user", "content": _build_prompt(analysis)},
        ]

        # 每次嘗試都是獨立呼叫（不帶前次失敗輸出進 context）——實測把失敗輸出
        # 留在對話裡重試，模型常常在同一個詞上重複犯錯，獨立重試給模型重新
        # 取樣的機會，比要求它自我修正更有效。第一次用 temperature=0 求最穩
        # 定；失敗後才拉高溫度，否則相同輸入配 temperature=0 只會重算出一模
        # 一樣的錯誤輸出，重試等於白做。frequency_penalty 用來抑制模型偶爾
        # 陷入的重複輸出迴圈（吐出同一詞重複到 max_tokens 硬切斷）。
        for attempt in range(1, MAX_ATTEMPTS + 1):
            resp = client.chat.completions.create(
                model=MODEL,
                messages=base_messages,
                temperature=0 if attempt == 1 else 0.3,
                frequency_penalty=0.4,
                max_tokens=400,
                stream=False,
            )
            content = resp.choices[0].message.content.strip()
            parsed = _parse_two(content)

            if not (parsed["A"] and parsed["B"]):
                problems = ["A/B 格式不完整"]
            else:
                repaired = {k: _repair(v) for k, v in parsed.items()}
                problems = (
                    _validate(repaired["A"]) + _validate(repaired["B"])
                    + _pattern_defect_check(repaired["A"])
                    + _pattern_defect_check(repaired["B"])
                )
                if not problems and _JUDGE_ENABLED:
                    problems = _llm_judge(client, repaired["A"]) + _llm_judge(client, repaired["B"])
                if not problems:
                    repaired["generation_method"] = "llm"
                    return repaired

            print(f"[llm] 第 {attempt} 次嘗試驗證失敗（{problems}）。")

        print("[llm] 已達最大重試次數仍驗證失敗，使用規則式摘要。")
        return _fallback(analysis)
    except Exception as e:
        print(f"[llm] 錯誤：{e}，使用規則式摘要。")
        return _fallback(analysis)
