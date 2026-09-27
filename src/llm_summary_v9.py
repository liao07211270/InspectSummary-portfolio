"""
v9 頂部摘要「跨指標同期關聯偵測」detail句的LLM潤飾層。

沿用 `llm_summary.py` 既有的LLM連線設定（LLM_BASE_URL/LLM_API_KEY/MODEL/
TIMEOUT）跟白名單驗證(`_validate`)/簡體轉繁體+外語清除(`_repair`)——這兩塊是
通用邏輯，不綁v2的obs/signals資料結構，直接重用；但這裡的prompt跟重試邏輯
是v9專用的單句潤飾，跟v2的兩段式(A核心走勢/B關聯因子)生成完全不同，故意
獨立成新檔案，不動`llm_summary.py`本身一行。

安全設計哲學跟v2一致：`analysis_v9._multi_signal_overlap()`已經把「事實」
（哪幾天、哪些訊號重疊）算好，組成單位/數字/訊號名稱都烤進字串的子句
（`_overlap_clause()`），LLM只能潤飾語序/連接詞/語氣，不能新增或修改任何
數字、日期、訊號名稱。

2026-08-03第一版刻意先不接LLM評審(judge)這一關——只做白名單驗證+獨立重試，
失敗就直接回傳原始子句。子句本身已經是通順的繁體中文事實描述，即使
LLM完全連不上，讀者看到的也是一句正常的話，不是佔位字或錯誤訊息。

2026-08-04修正：使用者實測抓到白名單驗證漏接的兩種真實案例——「情緒異常」
被潤成「的情異常」（每個字都合法繁體中文，白名單抓不到，但這詞已經不存在、
讀起來像漏字亂碼）、「精神不佳」被潤成「精神壓力下」（語意被整個換掉，不是
單純調整語序連接詞，違反system prompt明講的「不可修改訊號名稱」，但白名單
一樣抓不到，因為輸出仍是合法中文）。這兩種都不是「出現不該出現的字元」，
是既有白名單驗證機制設計上就顧不到的類型。修法：新增`required_terms`參數，
呼叫端(routes_v9.py)把_overlap_clause()烤進原始子句裡的訊號名稱清單傳進來，
`_terms_survived()`檢查潤飾後的文字是不是每個訊號名稱都還「逐字」存在——
這是比LLM評審更直接、免費、決定性(deterministic)的作法，直接針對「訊號
名稱不可被竄改」這個安全承諾做程式化驗證，不是靠prompt自律或另一顆LLM
碰運氣抓出來。
"""
from openai import OpenAI

from .llm_summary import LLM_BASE_URL, LLM_API_KEY, MODEL, TIMEOUT, _validate, _repair

_MAX_ATTEMPTS = 3


def _terms_survived(text: str, required_terms: list) -> list:
    """回傳required_terms裡「潤飾後的文字中找不到」的訊號名稱清單——空清單
    代表全部逐字倖存，沒有被LLM改寫、拆散或誤植。"""
    return [t for t in (required_terms or []) if t not in text]

_SYSTEM_PROMPT = (
    "你是照護摘要編輯，任務是把一句已經包含正確數字與事實的中文句子潤飾得"
    "更通順自然，只能調整語序、連接詞、語氣，絕對不可新增、修改、刪除任何"
    "數字、日期或訊號名稱——日期範圍必須維持原始的「M/D–M/D」格式，不可"
    "改寫成「M月D日至M月D日」；「心率上升」「情緒異常」「排便異常」這類"
    "固定訊號詞彙必須整組逐字照抄保留，不可在詞中間插入「的」「之」等字、"
    "不可拆解重組（例如「情緒異常」不可變成「的情異常」或「情緒的異常」），"
    "尤其絕對不可翻譯成英文（例如絕對不可把「心率上升」寫成RATE UP、"
    "把「情緒異常」寫成EMOTIONAL ABNORMALITY、把「排便異常」寫成STOOL "
    "ABNORMALITY——訊號名稱有幾項就要逐字保留幾項，不可省略或替換其中任何"
    "一項）。務必全程使用繁體中文（正體字），禁止使用任何簡體字，也絕對"
    "禁止把中文詞換成英文單字。只輸出潤飾後的單一句子，不要加任何說明、"
    "標題或引號。"
)

# 2026-08-13新增：實測抓到holo-fast在v9這句單句潤飾任務上，即使system
# prompt已經明講規則，仍會把日期範圍改寫格式、在訊號詞中間插字（「心率
# 上升」→「心率的上升」）、拆解重組（「情緒異常」→「的情異常」，
# _terms_survived()這個安全機制原本要防的就是這個案例，見上面2026-08-04
# 的說明）、甚至整組訊號詞彙翻譯成英文（尤其在訊號數量到3項以上時更容易
# 發生，例如「心率上升、情緒異常、排便異常」被翻成「RATE UP、EMOTIONAL
# ABNORMALITY、STOOL ABNORMALITY」，完全違反system prompt明講的規則）。
# v2(llm_summary.py)的_FEWSHOT_USER/_FEWSHOT_ASSISTANT實測證實加入正面
# 範例比單靠prompt規則有效得多（乾淨率0/5→3/5），這裡比照同一套做法，
# 用v9單句潤飾任務本身的格式做兩組範例：第一組示範2個訊號的基本案例，
# 第二組專門示範3個訊號同時列出時仍要逐字保留全部中文、不翻譯不省略
# ——3訊號的英文翻譯問題單靠第一組範例(只有2個訊號)類推效果有限，需要
# 訊號數量對得上的範例才有明顯改善(2026-08-13實測：只有第一組範例時，
# 3訊號案例3次嘗試全部翻成英文；加上第二組範例後，英文翻譯的發生率
# 明顯下降，但這顆模型在3訊號以上的任務仍不是100%穩定，_terms_survived()
# 這道逐字驗證關卡依然是必要的最後防線，不能只靠prompt/few-shot)。
_FEWSHOT_USER = "11/1–11/10期間，心率上升、血壓偏高同時出現異常。"
_FEWSHOT_ASSISTANT = "自11/1–11/10期間起，心率上升與血壓偏高同時出現異常趨勢。"
_FEWSHOT2_USER = "11/1–11/30期間，心率上升、情緒異常、排便異常同時出現異常。"
_FEWSHOT2_ASSISTANT = "自11/1–11/30期間起，心率上升、情緒異常與排便異常三項同時出現異常。"


def polish_clause(clause: str, required_terms: list = None) -> dict:
    """回傳 {"text": ..., "generation_method": "llm"|"fallback"}。

    required_terms：_overlap_clause()烤進clause裡的訊號名稱清單（見
    analysis_v9.py的top_summary["detail_signals"]）——潤飾後的文字必須
    逐字包含每一個，否則視為驗證失敗、觸發重試，這是_validate()白名單
    機制之外、專門針對「訊號名稱被竄改」的第二層檢查。

    任何失敗（連線失敗、白名單驗證不過、訊號名稱被改掉、重試用盡）都直接
    回傳原始clause當fallback——子句本身已經是通順的繁體中文事實描述，
    不需要LLM也能讀，這是刻意設計的行為，不是退而求其次的妥協。"""
    if not clause:
        return {"text": clause, "generation_method": "fallback"}
    try:
        client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=TIMEOUT)
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _FEWSHOT_USER},
                    {"role": "assistant", "content": _FEWSHOT_ASSISTANT},
                    {"role": "user", "content": _FEWSHOT2_USER},
                    {"role": "assistant", "content": _FEWSHOT2_ASSISTANT},
                    {"role": "user", "content": clause},
                ],
                temperature=0 if attempt == 1 else 0.3,
                frequency_penalty=0.4,
                max_tokens=150,
                stream=False,
            )
            text = resp.choices[0].message.content.strip()
            text = _repair(text)
            problems = _validate(text)
            missing = _terms_survived(text, required_terms)
            if missing:
                problems = problems + [f"訊號名稱被改動：{missing}"]
            if not problems:
                return {"text": text, "generation_method": "llm"}
            print(f"[llm_summary_v9] 第 {attempt} 次嘗試驗證失敗（{problems}）。")

        print("[llm_summary_v9] 已達最大重試次數仍驗證失敗，使用原始子句。")
        return {"text": clause, "generation_method": "fallback"}
    except Exception as e:
        print(f"[llm_summary_v9] 錯誤：{e}，使用原始子句。")
        return {"text": clause, "generation_method": "fallback"}
