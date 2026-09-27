"""
v9 版面的「比較運算」層 —— 吃兩個真實schema形狀的事件陣列（前三月/本月），
各自跑一次 `analyze_all()`，把結果配對成 `report_pdf_v9.py` 要吃的
view-model（跟 `mock_data_v9.generate_v9_view_model()` 輸出同一種形狀，
所以 `report_pdf_v9.py` 完全不用改）。

刻意獨立成新檔案，不動 `mock_data_v9.py`／`report_pdf_v9.py`（已被取代的demo
路徑）。2026-07-31起`data_parser.py`多了兩個小改動供這裡使用——Urination
的status(normal/special)欄位解析、event_priority多一份逐日逐代碼明細
（`daily_by_code`）——`analysis_core.py`本身完全沒動，都是額外新增資料，不影響
既有v2輸出。

**哪些是真的算出來的**（見 2026-07-29 [[project_inspectsummary_v9_redesign_task]]
的資料盤點結論；2026-07-31起感染監測卡「精神不佳」也改用真實Behavior代碼，
目前整個view-model已經沒有寫死佔位的顯示欄位了）：
    頂部候選池/偏離幅度最大三項、血壓血氧分布、高血壓與心血管卡全部子項、
    體液代謝卡的飲水未達標天數/特殊排尿/排便異常/尿失禁備註、
    感染監測卡的體溫偏離基線/遊走/精神不佳、衰弱卡的體重/進食量/衛生協助
    項目、查核說明的紀錄完整度與其餘偏離項、每日狀態四列(精神/情緒/睡眠/
    排泄,見_code_day_flags/_defecation_special_flags)、偏離集中期偵測與橘紅線
    (見_detect_cluster_days)、偶發事件軸(高優先級行為代碼優先顯示,無則
    退而顯示一般行為最高頻的前4種)、各卡片趨勢註記(見_trend_phrase)、
    頂部摘要的跨指標同期關聯偵測(見_multi_signal_overlap/_overlap_clause——
    這裡只算出事實子句，LLM潤飾語序在src/llm_summary_v9.py，由呼叫端
    routes_v9.py的背景工作決定要不要潤飾，analysis_v9.py本身不呼叫LLM)

唯一完全沒實作的功能缺項：偶發事件的「通報」——Event自由文字型別真實
schema未解析，不會出現在事件軸上（不是顯示假資料，是這個事件類型目前
不會被偵測到，見_HIGH_PRIORITY_ORDER/_select_event_codes都沒有它）。
"""
from datetime import date as _date, timedelta as _timedelta

from .data_parser import build_dataframe_from_request, BEHAVIOR_LABELS, WATER_TARGET_ML
from .analysis_core import (
    analyze_all, NO_DATA, _severity_level, _direction_label,
    BP_SYS_TARGET, BP_DIA_TARGET, WEIGHT_LOSS_SIG,
    SPO2_LOW, SPO2_CRITICAL,
    PULSE_VERY_LOW, PULSE_LOW, PULSE_HIGH, PULSE_VERY_HIGH,
    BP_SYS_CRISIS_LOW, BP_SYS_LOW, BP_CRISIS, DIA_CRISIS_LOW, DIA_LOW, DIA_TARGET, DIA_CRISIS,
    TEMP_LOW_ABS, TEMP_HIGH_ABS, BMI_UNDERWEIGHT, BMI_NORMAL_HIGH, BMI_OVERWEIGHT_HIGH,
)

_TARGET_WEIGHT_SPAN_MONTHS = 6

# 每日狀態格子四色（2026-07-31新增，對應report_html_v9.py legend「綠＝正常，
# 灰＝異常，橘＝異常且落在偏離集中期」——之前只有綠/橘兩色，沒有實作「異常但
# 不在集中期」的灰色，也沒有真的算集中期，是使用者實測抓到的漏做項目）。
# 2026-08-12五次修正：使用者要求「單次異常」改成黃色，「無量測紀錄」改成
# 灰色實心格子——這是配色語意的一次性對調，不是漸進調整：異常從灰色搬去
# 黃色後，原本被異常佔用的灰色就空出來，剛好給「無資料」這個中性狀態用，
# 不用再另外找第三種顏色。無資料格子的實際渲染(border-only→filled gray)
# 在report_html_v9.py的_daily_status_section()，這裡只是色號本身的定義。
_COLOR_NORMAL = "#639922"
_COLOR_ABNORMAL = "#D9A521"  # 2026-08-12：異常(非集中期)改成黃色，跟集中期的
                             # 橘紅色(_COLOR_CLUSTER)、正常的綠色都能明顯區分，
                             # 不再是灰色(灰色現在改給「無量測紀錄」用)
_COLOR_CLUSTER = "#D85A30"
# 當天完全沒有任何照護紀錄(不是「有登打但這項正常」)——_row_colors()回傳
# None代表這一天，report_html_v9.py收到None時畫成灰色實心格子(顏色定義在
# report_html_v9.py自己的PALETTE["mute"]，這裡不用重複定義一份)。

# 偏離集中期偵測門檻：連續達到這個天數就算集中期。2026-08-13使用者定案
# （情境5：短期急性異常）：原本5天的連續門檻改成2天，同時拿掉原本「任一
# 連續10天視窗內異常天數超過5天」這條規則（門檻改成2天後，這條規則幾乎
# 必然被更寬鬆的連續規則涵蓋，保留反而增加不必要的複雜度）。這個門檻現在
# 同時服務兩種用途：(1) 每日狀態格子(精神/情緒/睡眠/排泄)的集中期橘色標記，
# (2) 情境5的「急性事件」判斷——生理數值(血壓/舒張壓/心率/體溫)連續達到
# 這個天數的「危急」等級，就視為短期急性事件（見_acute_cluster_label()）。
_CLUSTER_MIN_CONSECUTIVE = 2

# 衰弱卡「進食量」趨勢註記用：baseline/current換算成「每天發生比例」後，
# 差距超過這個相對幅度才講「變差/改善」，否則講「維持穩定」——避免正規化後
# 的微小雜訊被誤讀成趨勢。2026-08-03查證：遠端照護監測文獻常引用20-25%變化
# 幅度當臨床顯著變化的通用門檻（心臟科特定族群甚至用20%當心率警示值），
# 這裡的20%落在這個常見範圍內，但量測對象（進食不足天數頻率）跟原文獻
# （生命徵象數值）不完全相同，屬類比借用，仍待醫師確認，詳見
# docs/thresholds-references.md。
_TREND_RATE_TOLERANCE = 0.2

# 每日狀態「精神/情緒/睡眠」三列對應的真實行為代碼分組（2026-07-31 討論定案）。
# 「排泄」列改用 Defecation.status 特殊天數（見 _defecation_special_flags），
# 不是行為代碼。2026-08-03：原本用Urination時段(22:00-06:00)推算「夜間如廁」，
# 但日照個案晚上不在機構、理論上不會有落在夜間時段的CareRecords，這個推算
# 對日照個案沒有意義，已改用不受作息限制的Defecation.status（見模組說明）。
_MENTAL_CODES = {"irritable", "depressed", "complain"}
_MOOD_CODES = {"emotional_abnormal"}
_SLEEP_CODES = {"day_sleep"}

# 偶發事件軸的候選事件類型優先順序：先看本月有沒有出現這6種安全疑慮類代碼
# （見 data_parser.BEHAVIOR_PRIORITY_HIGH），有則優先顯示；完全沒有才退而
# 顯示一般行為裡本月次數最高的代碼。兩種情況都最多顯示4種類型（不是4次）。
_HIGH_PRIORITY_ORDER = [
    "physical_aggression", "elope", "sexual_harass",
    "hallucination", "expose_private", "false_accuse",
]
_MAX_EVENT_TYPES = 4
# 一般行為次數並列時的決定性排序依據（BEHAVIOR_LABELS字典定義順序）——
# 不能單純排present這個set()的走訪順序，Python的set本身走訪順序不保證
# 跨執行穩定，次數打平時選中哪個代碼會變來變去，同一份資料兩次產出報告
# 可能選到不同代碼，報告應該要是可重現的。
_BEHAVIOR_ORDER_INDEX = {c: i for i, c in enumerate(BEHAVIOR_LABELS)}

# 偶發事件圖標對照表（2026-08-04新增）：原本格子裡直接塞BEHAVIOR_LABELS的
# 中文全名（例如「情緒異常(突哭/笑)」），窄格子放不下、逐格換行擠壓，使用者
# 反映版面亂。改成每個代碼固定配一個單色符號＋顏色，格子裡只放符號，中文
# 說明收斂到events_row的legend_items，report_html_v9.py畫成「▲肢體攻擊
# (3次)」這種格式。26種代碼全部固定配對（不是只配當月出現的4種），確保
# 同一代碼不管哪個月/哪個個案的報告，看到的符號都一樣，才能建立對應記憶。
# 高風險六種(_HIGH_PRIORITY_ORDER)固定紅色，精神/情緒/睡眠橘色，其餘灰色，
# 跟每日狀態格子紅/橘/灰三色分級的語意一致(灰=異常但非高風險)。
_ICON_RED = "#A32D2D"
_ICON_ORANGE = "#D85A30"
_ICON_TEAL = "#0F5F54"
_ICON_GRAY = "#5C6B68"

EVENT_ICONS = {
    "physical_aggression": ("▲", _ICON_RED),
    "elope": ("➤", _ICON_RED),
    "sexual_harass": ("⚠", _ICON_RED),
    "hallucination": ("✺", _ICON_RED),
    "expose_private": ("⚑", _ICON_RED),
    "false_accuse": ("✦", _ICON_RED),
    "irritable": ("✱", _ICON_ORANGE),
    "depressed": ("▼", _ICON_ORANGE),
    "complain": ("❢", _ICON_ORANGE),
    "emotional_abnormal": ("◐", _ICON_ORANGE),
    "no_interest": ("○", _ICON_ORANGE),
    "wander": ("◆", _ICON_ORANGE),
    "day_sleep": ("☾", _ICON_TEAL),
    "refuse_hygiene": ("⊘", _ICON_GRAY),
    "refuse_eat": ("✕", _ICON_GRAY),
    "uncooperative": ("✋", _ICON_GRAY),
    "urine_incont": ("▽", _ICON_GRAY),
    "bowel_incont": ("▿", _ICON_GRAY),
    "repeat_question": ("↻", _ICON_GRAY),
    "repeat_action": ("↺", _ICON_GRAY),
    "lost_item": ("⬦", _ICON_GRAY),
    "curse": ("❕", _ICON_GRAY),
    "improper_dress": ("▣", _ICON_GRAY),
    "improper_undress": ("▢", _ICON_GRAY),
    "hoard": ("▤", _ICON_GRAY),
    "destroy": ("✂", _ICON_GRAY),
}
_DEFAULT_ICON = ("●", _ICON_GRAY)


def _pct_or_dash(v):
    return "—" if v == NO_DATA or v is None else f"{v:g}%"


def _num_or_dash(v):
    return "—" if v == NO_DATA or v is None else v


def _int_or_dash(v):
    """整數化顯示——am_mean/hr_end這類值算出來是round(x,0)的float(例如
    138.0)，直接塞進f-string會印出「138.0」這種不自然的寫法，情境1(無
    基線)的本月生理數值提醒需要單一數字呈現，這裡轉成乾淨的整數字串。"""
    return "—" if v == NO_DATA or v is None else str(int(round(v)))


def _pct_num(v):
    """給長條圖分段寬度用的數值安全版——NO_DATA/None一律當0%，不能直接
    `v or 0`，因為NO_DATA是非空字串,Python視為真值,`or 0`救不了它，
    NO_DATA字串會被原封不動塞進CSS的width百分比裡。"""
    return 0 if v == NO_DATA or v is None else v


_MAX_INLINE_DATES = 4


def _join_dates_capped(dates, max_n=_MAX_INLINE_DATES):
    """危急事件日期清單用逗號串接顯示在chip裡，固定寬度的23mm欄位塞不下
    太多日期——超過max_n個就只顯示前面幾個+刪節號，不然版面會被撐爆
    （2026-07-31使用者實測抓到：4次危急還撐得住，日期一多就會爆版）。"""
    if not dates:
        return None
    shown = "、".join(dates[:max_n])
    return shown + "…" if len(dates) > max_n else shown


def _urination_special_days(df):
    """該期間內「排尿狀況為特殊」的天數。這是事件式紀錄（照服員有異常才登打），
    只要這段期間本身有任何紀錄（len(df)>0），沒出現Urination.status=special
    就是真的0天，不是NO_DATA——只有整段期間完全沒送任何資料才是NO_DATA
    （無法評估）。2026-07-31修正：原本用「這個欄位本身notna().any()」判斷，
    導致某情境完全沒有Hygiene/Urination紀錄時整段顯示NO_DATA(—)，但期間
    其餘資料明明都有，正確答案應該是0，不是「無法評估」。"""
    if len(df) == 0 or "urination_special" not in df:
        return NO_DATA
    return int((df["urination_special"].fillna(0) > 0).sum())


def _defecation_special_days(df):
    """該期間內「排便狀況為特殊」的天數。跟_urination_special_days同一套
    NO_DATA語意，2026-08-03新增，取代原本的夜間如廁次數子項（見模組說明）。"""
    if len(df) == 0 or "defecation_special" not in df:
        return NO_DATA
    return int((df["defecation_special"].fillna(0) > 0).sum())


_DAYS_PER_MONTH = 30


def _monthly_avg(df, column, days_per_month=_DAYS_PER_MONTH):
    """某個逐日計數欄位（hygiene_items等）的「月平均發生次數」——
    正規化成「每30天」的頻率，不是原始總和。2026-07-31修正：baseline橫跨
    前三月(通常~90天)，如果直接顯示「期間總和」或「達門檻天數」，數字會
    比只有~30天的current大上好幾倍（例如「43」對比「19」），讓人誤以為
    一個月有43天，baseline/current也不是同一把尺。正確做法：算「總數 /
    總天數 * 每月天數」，baseline跟current才能直接比較。NO_DATA語意：只有
    整段期間完全沒資料才是NO_DATA，期間有資料但這個欄位剛好沒出現任何
    紀錄，就是真的0。"""
    if len(df) == 0 or column not in df:
        return NO_DATA
    total = float(df[column].fillna(0).sum())
    return round(total / len(df) * days_per_month)


def _full_calendar_dates(df):
    """從df實際資料的首末日期，展開成連續的日曆日期清單(iso字串)——中間缺測
    的日子也會出現在清單裡（該天沒有任何照護紀錄），不會被悄悄跳過。2026-08-04
    新增：使用者用「四個月資料中間有缺測」的情境實測，發現原本每日狀態格子
    只用「有資料的天數」當總數，缺測天連帶被跳過、後面的日子往前遞補，格子
    數量會少於實際日曆天數。改成這個函式展開完整日曆天，讓格子數==實際跨的
    日曆天數，缺測天格子留空白顯示，不是被跳過。"""
    if len(df) == 0:
        return []
    dates = sorted(df["date"].tolist())
    start = _date.fromisoformat(dates[0])
    end = _date.fromisoformat(dates[-1])
    n = (end - start).days + 1
    return [(start + _timedelta(days=i)).isoformat() for i in range(n)]


def _defecation_special_flags(df, full_dates=None):
    """每日狀態「排泄」列用：逐日回傳當天是否有Defecation.status=special的
    旗標。取代原本用Urination夜間時段推算的做法——排便不受機構作息限制，
    日照/團屋個案都適用。傳入full_dates(完整日曆天清單)時，清單裡不在df內
    的日期(該天完全沒有任何照護紀錄)回傳None，供_row_colors()畫成留白，
    跟「有資料但沒有排便異常」的False區分開來；不傳full_dates則維持原本
    行為(只回傳df自己有的天數，不含None)。"""
    if full_dates is not None:
        have = set(df["date"].tolist()) if len(df) else set()
        special_by_date = (
            dict(zip(df["date"], df["defecation_special"].fillna(0) > 0))
            if "defecation_special" in df and len(df) else {}
        )
        return [bool(special_by_date.get(d, False)) if d in have else None for d in full_dates]
    if "defecation_special" not in df or len(df) == 0:
        return [False] * max(len(df), 1)
    return (df["defecation_special"].fillna(0) > 0).tolist()


def _detect_cluster_days(combined_flags):
    """偏離集中期偵測（2026-07-31新增，原本cluster_start_i/cluster_end_i是
    寫死佔位，使用者實測發現橘紅線完全沒作用，要求依規則真的算）。規則：
    連續>=_CLUSTER_MIN_CONSECUTIVE天異常，回傳一個bool清單（哪些天算「落在
    集中期」），給_row_colors()判斷格子要不要標橘紅色，也給_cluster_
    segments()畫頂部橘紅線，情境5的急性事件偵測也直接重用這個函式（見
    _acute_cluster_label()）。2026-08-13使用者定案：拿掉原本「任一連續
    10天視窗內異常天數超過5天」那條規則——連續門檻從5天改成2天後，這條
    規則幾乎必然被更寬鬆的連續規則涵蓋，保留只會增加不必要的複雜度，沒有
    再多抓到任何連續規則抓不到的情況。"""
    combined_flags = [bool(f) for f in combined_flags]  # None(當天無資料)當作非異常處理
    n = len(combined_flags)
    marked = [False] * n

    i = 0
    while i < n:
        if combined_flags[i]:
            j = i
            while j < n and combined_flags[j]:
                j += 1
            if j - i >= _CLUSTER_MIN_CONSECUTIVE:
                for k in range(i, j):
                    marked[k] = True
            i = j
        else:
            i += 1

    return marked


def _cluster_segments(marked):
    """把逐日bool旗標壓縮成連續區段清單 [(start_i, end_i), ...]（皆含頭尾、
    0-indexed），report_html_v9.py用來畫橘紅線——「可不只一條」，一個月內
    可能有兩段互不相鄰的集中期，各自畫一條線段。"""
    segments = []
    n = len(marked)
    i = 0
    while i < n:
        if marked[i]:
            j = i
            while j < n and marked[j]:
                j += 1
            segments.append((i, j - 1))
            i = j
        else:
            i += 1
    return segments


def _fmt_md(iso_date):
    return f"{int(iso_date[5:7])}/{int(iso_date[8:10])}"


def _baseline_range_label(baseline_df):
    """查核與說明「個人基線」欄——顯示baseline實際涵蓋的日期區間，不是
    只顯示天數（見build_v9_view_model()呼叫處的說明）。沒有baseline資料
    時（例如剛收案、尚未累積歷史）如實顯示「尚無歷史資料」，不是0天這種
    容易誤讀的數字。"""
    if len(baseline_df) == 0:
        return "尚無歷史資料"
    dates = sorted(baseline_df["date"].tolist())
    return f"{_fmt_md(dates[0])}–{_fmt_md(dates[-1])}（{len(baseline_df)} 天）"


def _current_range_label(current_df):
    """2026-08-28新增：紀錄完整度標題旁補上本月實際日期區間，跟
    _baseline_range_label()同一套格式（起訖日期＋天數），方便使用者
    直接看出「30天」指的是哪一段日期，不用自己回頭對照。跟其餘欄位
    一樣，這裡的「天數」是「本月有任一種紀錄的天數」，不是嚴格的
    日曆月天數（例如2月或有整天缺測時，兩者可能不同）。"""
    if len(current_df) == 0:
        return "本月尚無紀錄"
    dates = sorted(current_df["date"].tolist())
    return f"{_fmt_md(dates[0])}–{_fmt_md(dates[-1])}，共{len(current_df)}天"


def _cluster_label(segments, dates):
    if not segments:
        return "本月無明顯集中異常期"
    parts = []
    for s, e in segments:
        parts.append(_fmt_md(dates[s]) if s == e else f"{_fmt_md(dates[s])}–{_fmt_md(dates[e])}")
    return "、".join(parts)


def _row_colors(flags, cluster_marked):
    colors = []
    for i, f in enumerate(flags):
        if f is None:
            colors.append(None)
        elif f and cluster_marked[i]:
            colors.append(_COLOR_CLUSTER)
        elif f:
            colors.append(_COLOR_ABNORMAL)
        else:
            colors.append(_COLOR_NORMAL)
    return colors


# 趨勢註記用的「連續發生」門檻——刻意比每日狀態橘紅線的門檻(_CLUSTER_MIN_
# CONSECUTIVE=5)寬鬆，因為這裡只是「這句話要不要用『集中』措辭」的判斷，
# 不是要不要在版面上畫一條顯眼的線，兩者標準本來就該分開。跟_CLUSTER_MIN_
# CONSECUTIVE同樣的文獻依據等級：落在群聚偵測文獻常見的2-8天範圍內，但無
# 直接數字對應，仍待醫師確認，詳見docs/thresholds-references.md。
_TREND_MIN_CONSECUTIVE = 3


def _longest_run(flags):
    """回傳(length, start_i, end_i)——flags裡最長的連續True區段。沒有任何
    True時回傳(0, -1, -1)。"""
    best = (0, -1, -1)
    n = len(flags)
    i = 0
    while i < n:
        if flags[i]:
            j = i
            while j < n and flags[j]:
                j += 1
            if j - i > best[0]:
                best = (j - i, i, j - 1)
            i = j
        else:
            i += 1
    return best


def _trend_phrase(df, flags, subject):
    """依逐日flag組一句趨勢描述：完全沒異常回傳None（呼叫端決定沒異常時
    整體要顯示什麼安全句）；有異常但零星分散寫「自X起共N天」；連續達
    _TREND_MIN_CONSECUTIVE天以上寫「多集中於X–Y」。只看單一期間(通常是
    current_df，敘述「這個月」的型態)，不做baseline/current比較——比較
    交給各卡片自己的chip處理。"""
    n = sum(flags)
    if n == 0:
        return None
    dates = df["date"].tolist()
    length, s, e = _longest_run(flags)
    if length >= _TREND_MIN_CONSECUTIVE:
        return f"{subject}多集中於{_fmt_md(dates[s])}–{_fmt_md(dates[e])}"
    first_i = next(i for i, f in enumerate(flags) if f)
    return f"{subject}自{_fmt_md(dates[first_i])}起共{n}天"


def _water_short_flags(df):
    """每日「飲水未達標」bool旗標，給_trend_phrase用。"""
    if "water_intake" not in df or len(df) == 0:
        return [False] * max(len(df), 1)
    return (df["water_intake"] == "未達標").tolist()


def _temp_abnormal_flags(df, block3):
    """每日「體溫偏離基線」bool旗標，從block3['low']/['high']（已經是
    MM/DD格式的日期清單）反查對應到df的每一天。"""
    if len(df) == 0:
        return []
    abnormal_mmdd = {e["date"] for e in block3.get("low", [])} | {e["date"] for e in block3.get("high", [])}
    return [_fmt_md(d) in abnormal_mmdd for d in df["date"].tolist()]


def _food_short_flags(df):
    """每日「進食不足」bool旗標，跟_water_short_flags同樣用途，補齊候選池
    「進食量」新增的附加說明用。"""
    if "food_intake" not in df or len(df) == 0:
        return [False] * max(len(df), 1)
    return df["food_intake"].isin(["不足", "拒食"]).tolist()


def _bp_high_flags(df):
    """每日「日均收縮壓偏高」bool旗標（跟_block1_cardio的達標率算法一致：
    晨午晚平均 > 目標值），給候選池「晨間收縮壓」卡片的附加說明用。"""
    if len(df) == 0 or "systolic_am" not in df:
        return []
    day_mean = df[["systolic_am", "systolic_pm", "systolic_eve"]].mean(axis=1)
    return (day_mean > BP_SYS_TARGET).fillna(False).tolist()


def _diastolic_mean(df):
    """該期間舒張壓平均值（晨+午合併）。真實schema晚間讀數本來就只收收縮壓
    （data_parser.py的bp_mean()對晚間讀數丟棄舒張壓），舒張壓平均值只能用
    晨/午兩個時段，跟收縮壓有三個時段(晨午晚)不同，是資料本身的限制。"""
    if len(df) == 0 or "diastolic_am" not in df:
        return NO_DATA
    vals = [v for v in df["diastolic_am"].tolist() + df["diastolic_pm"].tolist() if v is not None and v == v]
    if not vals:
        return NO_DATA
    return round(sum(vals) / len(vals), 1)


def _diastolic_high_flags(df):
    """每日「日均舒張壓偏高」bool旗標，跟_bp_high_flags同樣概念，用
    BP_DIA_TARGET=90這個既有常數（analysis_core.py本來就定義了，但一直
    沒有任何地方使用過）。"""
    if len(df) == 0 or "diastolic_am" not in df:
        return []
    day_mean = df[["diastolic_am", "diastolic_pm"]].mean(axis=1)
    return (day_mean > BP_DIA_TARGET).fillna(False).tolist()


def _hr_high_flags(df, baseline_hr):
    """每日「心率高於個人基線」bool旗標，給候選池「靜態心率」卡片的
    附加說明用。baseline_hr是前三月的hr_end，當作這個人的個人基準線。"""
    if len(df) == 0 or "heart_rate" not in df or baseline_hr in (NO_DATA, None):
        return []
    return (df["heart_rate"] > baseline_hr).fillna(False).tolist()


# ── 情境5（短期急性異常，2026-08-13使用者定案）──────────────────────────
# 「急性」定義：連續>=_CLUSTER_MIN_CONSECUTIVE天(門檻已改成2天，見上方
# 常數說明)落在「危急」等級——不是單純「偏高/偏低」，必須是真正的危急
# 門檻（跟血壓分布圖chip「≥160／<90 mmHg」、脈搏分布圖chip「≥120／<50
# 次/分」同一組門檻），才算得上急性事件。只有血壓/舒張壓/心率/體溫這4項
# 有明確的「危急」等級定義，套用這條規則；飲水未達標、進食不足沒有危急
# 概念，不套用（使用者2026-08-13確認：「沒有危急等級概念的就不套用該
# 規則」，跟情境3體重被排除同一種處理方式）。


def _bp_crisis_flags(df):
    """每日「收縮壓達到危急等級」bool旗標(>=BP_CRISIS或<BP_SYS_CRISIS_LOW)
    ——跟_bp_high_flags(>目標值BP_SYS_TARGET，範圍寬鬆很多)是不同門檻，
    這裡只有真正到危急等級才算，單純「偏高」不夠格當「急性」。"""
    if len(df) == 0 or "systolic_am" not in df:
        return []
    day_mean = df[["systolic_am", "systolic_pm", "systolic_eve"]].mean(axis=1)
    return ((day_mean >= BP_CRISIS) | (day_mean < BP_SYS_CRISIS_LOW)).fillna(False).tolist()


def _diastolic_crisis_flags(df):
    """跟_bp_crisis_flags同樣概念，用DIA_CRISIS/DIA_CRISIS_LOW（情境1
    新增的舒張壓絕對值門檻，這裡借用當危急等級判斷用）。"""
    if len(df) == 0 or "diastolic_am" not in df:
        return []
    day_mean = df[["diastolic_am", "diastolic_pm"]].mean(axis=1)
    return ((day_mean >= DIA_CRISIS) | (day_mean < DIA_CRISIS_LOW)).fillna(False).tolist()


def _hr_crisis_flags(df):
    """每日「脈搏達到危急等級」bool旗標，用PULSE_VERY_HIGH/PULSE_VERY_LOW
    ——跟本月量測分布圖脈搏五級分布的「極高/極低」同一組門檻，語意一致。"""
    if len(df) == 0 or "heart_rate" not in df:
        return []
    hr = df["heart_rate"]
    return ((hr >= PULSE_VERY_HIGH) | (hr < PULSE_VERY_LOW)).fillna(False).tolist()


def _acute_cluster_label(current_df, crisis_flags):
    """把crisis_flags餵進_detect_cluster_days()(連續門檻現在是2天)算出
    集中期，有的話回傳「M/D–M/D、M/D–M/D連續危急」（單一段時只有一組
    日期範圍；單日集中期只顯示單一日期），給候選池項目當「acute」欄位用；
    沒有集中期回傳None。2026-08-13使用者定案：本月如果出現不只一段急性
    集中期，全部都要列出來，不能只顯示最早那一段——report_html_v9.py的
    _top3_card()負責在文字太長時動態縮小字級，避免多段日期塞爆卡片。"""
    if not crisis_flags or len(current_df) == 0:
        return None
    marked = _detect_cluster_days(crisis_flags)
    segments = _cluster_segments(marked)
    if not segments:
        return None
    dates = current_df["date"].tolist()
    ranges = []
    for s, e in segments:
        start_md, end_md = _fmt_md(dates[s]), _fmt_md(dates[e])
        ranges.append(start_md if start_md == end_md else f"{start_md}–{end_md}")
    return f"{'、'.join(ranges)}連續危急"


def _onset_phrase(flags, dates):
    """候選池卡片第三行的簡短附加說明（比_trend_phrase精簡，卡片空間小，
    不需要「共N天」這種完整敘述，只需要「什麼時候開始/集中在哪」）。
    完全沒異常回傳None。"""
    if not flags or not dates or sum(flags) == 0:
        return None
    length, s, e = _longest_run(flags)
    if length >= _TREND_MIN_CONSECUTIVE:
        return f"{_fmt_md(dates[s])}–{_fmt_md(dates[e])}集中"
    first_i = next(i for i, f in enumerate(flags) if f)
    return f"{_fmt_md(dates[first_i])}起"


def _weight_qualifier(delta_pct):
    """體重候選池卡片的附加說明：是否已達臨床顯著界（沿用analysis_core.py
    既有的WEIGHT_LOSS_SIG=5.0%常數，不是新訂門檻）。體重讀數本來就稀疏，
    用「日期起始」這種措辭沒有意義，改用「是否達界」。"""
    if delta_pct is None:
        return None
    return "已達顯著界" if abs(delta_pct) >= WEIGHT_LOSS_SIG else "未達顯著界"


def _spo2_low_flags(df):
    """每日「血氧偏低」bool旗標(<SPO2_LOW=94%)，給跨指標同期關聯偵測用。"""
    if "spo2" not in df or len(df) == 0:
        return [False] * max(len(df), 1)
    return (df["spo2"].fillna(100) < 94).tolist()


# 跨指標同期關聯偵測門檻：同一天至少這麼多種訊號同時異常，這天才算「值得
# 注意」；連續達到這個天數才算一個關聯事件。跟_TREND_MIN_CONSECUTIVE(3)
# 共用同一個連續天數門檻，語意上是同一類「集中發生」判斷。2026-08-03查證：
# 這是純粹的報告設計邏輯，沒有查到對應的臨床文獻，仍待醫師確認，詳見
# docs/thresholds-references.md。
_OVERLAP_MIN_SIGNALS = 2
_OVERLAP_MIN_CONSECUTIVE = _TREND_MIN_CONSECUTIVE


def _find_runs(flags, min_length):
    """回傳flags裡所有長度>=min_length的連續True區段[(start,end),...]（皆含
    頭尾）。跟_longest_run只留最長一段不同，這裡要留下每一段（一個月可能
    不只一次多訊號重疊）。"""
    runs = []
    n = len(flags)
    i = 0
    while i < n:
        if flags[i]:
            j = i
            while j < n and flags[j]:
                j += 1
            if j - i >= min_length:
                runs.append((i, j - 1))
            i = j
        else:
            i += 1
    return runs


def _multi_signal_overlap(signal_flags, dates):
    """跨指標同期關聯偵測（v9原稿「偏離集中在X–Y，7天內心率↑血氧↓...多項
    同向」敘述背後的規則）。signal_flags是{中文訊號名稱: 逐日bool旗標}；
    找出「當天同時有>=_OVERLAP_MIN_SIGNALS種訊號異常」的連續>=
    _OVERLAP_MIN_CONSECUTIVE天區段，回傳結構化事實（不是文字）：
    [{"start","end","signals":[...]}]，日期範圍內出現過的訊號名稱取聯集。
    這是純規則運算，不呼叫LLM——LLM只負責把這個結構化結果潤飾成一句話
    （見src/llm_summary_v9.py），不負責判斷關聯本身，避免LLM自己捏造
    不存在的關聯或看錯數字。"""
    n = len(dates)
    notable = [
        sum(1 for flags in signal_flags.values() if i < len(flags) and flags[i]) >= _OVERLAP_MIN_SIGNALS
        for i in range(n)
    ]
    events = []
    for s, e in _find_runs(notable, _OVERLAP_MIN_CONSECUTIVE):
        names = sorted({
            name for i in range(s, e + 1)
            for name, flags in signal_flags.items() if i < len(flags) and flags[i]
        })
        events.append({"start": dates[s], "end": dates[e], "signals": names})
    return events


_OVERLAP_MAX_SIGNALS_SHOWN = 3


_OVERLAP_SIGNAL_TO_POOL_KEY = {
    "血壓偏高": "bp", "心率上升": "hr", "體溫偏離": "temp",
    "飲水未達標": "water", "進食不足": "food",
    # 2026-08-19新增：血氧補進候選池後，跨指標關聯偵測的「血氧下降」訊號
    # 也能查到對應的樣本有限判斷了。
    "血氧下降": "spo2",
    # 2026-08-19新增（情境4）：排便異常補進候選池、也有sample_limited判斷，
    # 一併查表。精神不佳/白天睡多雖然也補進候選池，但這兩項刻意不算
    # sample_limited(見_candidate_pool()說明——行為代碼沒登打不等於沒
    # 測量)，這裡查表會固定拿到False，不影響現況，只是保留對照完整性。
    "排便異常": "defecation", "精神不佳": "mental", "白天睡多": "sleep",
}


def _overlap_clause(event, max_shown=_OVERLAP_MAX_SIGNALS_SHOWN, sample_limited_signals=frozenset()):
    """把_multi_signal_overlap()算出的結構化事實組成一句事實子句——單位/
    數字/訊號名稱都已經烤進字串，LLM後續只能潤飾語序，不能新增或修改
    任何數字或訊號名稱（跟llm_summary.py的_build_clauses()同一種安全
    設計哲學）。2026-08-05修正：使用者實測反映訊號名稱全部列出來（可能
    多達10種）讀起來太雜，改成最多顯示_OVERLAP_MAX_SIGNALS_SHOWN個
    （目前3個），超過的用「等N項」收尾（N是總項數，不是超出的項數，
    符合中文「A、B、C等10項」的慣用讀法）。回傳dict而不是純字串——
    date_range/shown/more_count都要單獨拿出來，一是給required_terms驗證
    LLM潤飾後這些精確片段有沒有被改動，二是給report_html_v9.py用來把
    日期、訊號名稱、「N項」數字上色（2026-08-05二次修正：使用者確認
    這幾個「項目本身」——日期範圍、訊號名稱、計數數字——都要上色，只有
    「以」「變化最為明顯」「等」這類純連接字維持黑色），這兩件事都沒辦法
    在潤飾後的最終句子裡用字串比對可靠地找回來（LLM可能把「11/1–11/14」
    重新排版成「11月1日至14日」，數字沒變但格式變了）。
    2026-08-13新增（情境3）：sample_limited_signals是「本月或基線任一
    密度不足」的訊號名稱集合（見build_v9_view_model()呼叫處，只有候選池
    本身有算過sample_limited的5種訊號查得到）——使用者決定「仍參與跨
    指標關聯偵測，不整個排除，但句子裡要附帶標註」，因為即使樣本少，
    這幾天多訊號同時異常仍是真實發生過的事實，只是這個訊號的判斷基礎
    比較薄弱，讀者該知道。"""
    s, e = _fmt_md(event["start"]), _fmt_md(event["end"])
    date_range = s if event["start"] == event["end"] else f"{s}–{e}"
    signals = event["signals"]
    shown = signals[:max_shown]
    # 2026-08-19修正：跟頂部「N項紀錄偏離參考值」（月平均vs基線的指標計數）
    # 用同一個「項」字，讀者容易誤以為是同一套統計、數字對不上就以為算錯——
    # 這裡改用「種訊號」，跟上面的「項」做語意區隔（這裡數的是逐日旗標在
    # 短窗口內同時出現的訊號種類，跟月平均比較是兩回事）。
    more_count = f"{len(signals)}種訊號" if len(signals) > max_shown else None
    more_suffix = f"等{more_count}" if more_count else None
    labeled = [f"{name}(樣本有限)" if name in sample_limited_signals else name for name in shown]
    signals_text = "、".join(labeled) + (more_suffix or "")
    clause = f"{date_range}期間，{signals_text}同時出現異常。"
    required_terms = [date_range] + labeled + ([more_count] if more_count else [])
    return {
        "clause": clause, "date_range": date_range,
        "shown_signals": labeled, "more_count": more_count,
        "required_terms": required_terms,
    }


def _spo2_reading_count(events):
    """血氧分布標題「N次量測」——直接數原始事件裡有BloodOxygen欄位的筆數，
    不是天數。2026-07-31修正：原本用c_sfw['total']（其實是len(df)，也就是
    期間總天數）冒充「次量測」，跟原始設計稿的語意不符，也可能跟真實血氧
    量測次數對不上（同一天可能量測不只一次）。data_parser.py把同一天多筆
    血氧平均成一個每日值，沒有保留原始筆數，所以這裡直接數原始事件而不是
    讀已經聚合過的DataFrame。"""
    return sum(1 for e in events if isinstance(e, dict) and e.get("BloodOxygen") is not None)


def _spo2_reading_tiers(events):
    """血氧分布三層改用「次數」為主(不是天數)——2026-08-06使用者反映天數
    跟次數被畫成同一個數字，但同一天可能量測不只一次，天/次應該分開算才對。
    data_parser.py的df["spo2"]是同一天多筆讀數平均後的單一值，用它算分層
    只能得到「天」的分母；這裡改成直接掃原始事件，每一筆BloodOxygen讀數
    各自獨立判斷落在哪一層，天數則從落在該層的讀數所屬日期集合(去重)算出，
    當輔助資訊。"""
    ok_n = mid_n = critical_n = 0
    ok_dates, mid_dates, critical_dates = set(), set(), set()
    for e in events:
        if not isinstance(e, dict):
            continue
        spo2 = e.get("BloodOxygen")
        t = e.get("RecordTime")
        if spo2 is None or not t:
            continue
        date = t[:10]
        if spo2 < SPO2_CRITICAL:
            critical_n += 1
            critical_dates.add(date)
        elif spo2 < SPO2_LOW:
            mid_n += 1
            mid_dates.add(date)
        else:
            ok_n += 1
            ok_dates.add(date)
    total = ok_n + mid_n + critical_n
    if total == 0:
        return None
    return {
        "total": total,
        "ok_count": ok_n, "ok_days": len(ok_dates),
        "ok_pct": round(ok_n / total * 100, 1),
        "mid_count": mid_n, "mid_days": len(mid_dates),
        "mid_pct": round(mid_n / total * 100, 1),
        "critical_count": critical_n, "critical_days": len(critical_dates),
        "critical_pct": round(critical_n / total * 100, 1),
        "critical_detail": sorted(critical_dates),  # ISO格式(YYYY-MM-DD)字串排序即為正確時序
    }


def _pulse_reading_count(events):
    """脈搏分布標題「N次量測」——比照_spo2_reading_count()直接數原始事件裡
    有Pulse欄位的筆數，不是天數（同一天可能量測不只一次）。"""
    return sum(1 for e in events if isinstance(e, dict) and e.get("Pulse") is not None)


def _temp_reading_count(events):
    """2026-08-28新增：紀錄完整度「體溫」欄改用次數而非天數，比照
    _spo2_reading_count()／_pulse_reading_count()同一套算法，直接數原始
    事件裡有Temperature欄位的筆數。"""
    return sum(1 for e in events if isinstance(e, dict) and e.get("Temperature") is not None)


def _pulse_reading_tiers(events):
    """2026-08-07新增：「本月量測分布」原本的血氧分布區域改成脈搏分布
    （依醫師會議確認），五級門檻：極低<50／偏低[50,60)／正常[60,101)／
    偏高[101,120)／極高>=120。比照_spo2_reading_tiers()以「次數」為主
    (不是天數)直接掃原始事件，每一筆Pulse讀數各自獨立判斷落在哪一層——
    data_parser.py的df["heart_rate"]是同一天多筆讀數平均後的單一值，
    用它算分層只能得到「天」的分母，這裡不用它。極低/極高兩個極端層
    另外保留日期明細（比照血壓分布的危急層），供臨床端逐項查核。"""
    very_low_n = low_n = ok_n = high_n = very_high_n = 0
    very_low_dates, high_dates = set(), set()
    very_high_dates = set()
    for e in events:
        if not isinstance(e, dict):
            continue
        pulse = e.get("Pulse")
        t = e.get("RecordTime")
        if pulse is None or not t:
            continue
        date = t[:10]
        if pulse < PULSE_VERY_LOW:
            very_low_n += 1
            very_low_dates.add(date)
        elif pulse < PULSE_LOW:
            low_n += 1
        elif pulse < PULSE_HIGH:
            ok_n += 1
        elif pulse < PULSE_VERY_HIGH:
            high_n += 1
        else:
            very_high_n += 1
            very_high_dates.add(date)
    total = very_low_n + low_n + ok_n + high_n + very_high_n
    if total == 0:
        return None
    def _pct(n):
        return round(n / total * 100, 1)
    return {
        "total": total,
        "very_low_count": very_low_n, "very_low_pct": _pct(very_low_n),
        "very_low_detail": sorted(very_low_dates),
        "low_count": low_n, "low_pct": _pct(low_n),
        "ok_count": ok_n, "ok_pct": _pct(ok_n),
        "high_count": high_n, "high_pct": _pct(high_n),
        "very_high_count": very_high_n, "very_high_pct": _pct(very_high_n),
        "very_high_detail": sorted(very_high_dates),
    }


def _mental_abnormal_days(df):
    """感染監測卡「精神不佳」子項：套用跟每日狀態「精神」列同一組真實行為
    代碼(irritable/depressed/complain)，不再是寫死佔位。NO_DATA語意跟其餘
    事件式欄位一致：期間本身空的才是NO_DATA，有資料但沒觸發就是0。"""
    if len(df) == 0:
        return NO_DATA
    return sum(_code_day_flags(df, _MENTAL_CODES))


def _sleep_abnormal_days(df):
    """2026-08-19新增（情境4候選池用）：白天睡多天數，跟_mental_abnormal_
    days同一套NO_DATA語意，供候選池比照排便異常/精神不佳計算佔比%。"""
    if len(df) == 0:
        return NO_DATA
    return sum(_code_day_flags(df, _SLEEP_CODES))


def _code_day_flags(df, codes, full_dates=None):
    """每日狀態「精神/情緒/睡眠」三列用：逐日回傳當天有沒有出現該組行為
    代碼中的任一種。依賴 data_parser.py 的 event_priority.daily_by_code
    （逐日逐代碼計數），跟high_priority/general_summary（只留總數）是不同
    的資料，因為畫格子需要知道「哪一天」，彙總計數辦不到。傳入full_dates
    (完整日曆天清單)時，不在df內的日期(當天完全沒有任何照護紀錄)回傳None，
    跟_defecation_special_flags同一套語意，供_row_colors()畫成留白。"""
    by_code = df.attrs.get("event_priority", {}).get("daily_by_code", {})
    if full_dates is not None:
        have = set(df["date"].tolist()) if len(df) else set()
        return [
            (any(c in by_code.get(d, {}) for c in codes) if d in have else None)
            for d in full_dates
        ]
    dates = df["date"].tolist()
    return [any(c in by_code.get(d, {}) for c in codes) for d in dates]


def _select_event_codes(df, max_types=_MAX_EVENT_TYPES):
    """偶發事件軸最多顯示max_types種行為代碼：本月出現過的高優先級代碼
    （依_HIGH_PRIORITY_ORDER固定順序）優先佔位；還有剩餘名額才由一般行為
    依本月次數高到低遞補填滿。超過max_types而沒被選中的代碼，不逐一顯示，
    改彙總成「其他行為」，回傳其加總次數(others_count)，交由report_html_v9.py
    渲染成「其他行為（N次）」帶過——2026-08-06改法，原本高優先級代碼一出現
    就完全排除一般行為遞補的名額，即使高優先級代碼不到4種、名額還有空位。"""
    by_code = df.attrs.get("event_priority", {}).get("daily_by_code", {})
    totals = {}
    for day_codes in by_code.values():
        for c, n in day_codes.items():
            totals[c] = totals.get(c, 0) + n
    present = set(totals)

    high_present = [c for c in _HIGH_PRIORITY_ORDER if c in present]
    selected = high_present[:max_types]
    if len(selected) < max_types:
        remaining_slots = max_types - len(selected)
        general_present = [c for c in present if c not in _HIGH_PRIORITY_ORDER]
        general_ranked = sorted(
            general_present,
            key=lambda c: (-totals[c], _BEHAVIOR_ORDER_INDEX.get(c, 999)),
        )
        selected += general_ranked[:remaining_slots]

    others_count = sum(n for c, n in totals.items() if c not in selected)
    return selected, others_count


def _events_row(df, dates):
    """偶發事件軸：icons_by_day每天列出當天出現的已選代碼對應圖標(glyph,color)
    tuple——2026-08-04改法，原本格子裡塞中文全名擠壓換行，改成格子只放
    EVENT_ICONS的單一符號。legend_items是選中代碼(最多_MAX_EVENT_TYPES=4種)
    的(圖標,顏色,中文名稱,本月次數)清單，report_html_v9.py畫成「▲肢體攻擊
    (3次)」這種格式，次數是該代碼在本月的總出現次數(不是出現天數)。dates
    吃完整日曆天清單(full_dates)——缺測天by_code.get(d, {})自然拿不到東西，
    顯示空清單，跟「有資料但沒事件」視覺上一致(這條列本來就沒有no_data
    專屬顏色)。"""
    codes, others_count = _select_event_codes(df)
    by_code = df.attrs.get("event_priority", {}).get("daily_by_code", {})
    icons_by_day = [
        [EVENT_ICONS.get(c, _DEFAULT_ICON) for c in codes if c in by_code.get(d, {})]
        for d in dates
    ] or [[] for _ in dates]
    legend_items = [
        {
            "glyph": EVENT_ICONS.get(c, _DEFAULT_ICON)[0],
            "color": EVENT_ICONS.get(c, _DEFAULT_ICON)[1],
            "label": BEHAVIOR_LABELS.get(c, c),
            "count": sum(day_codes.get(c, 0) for day_codes in by_code.values()),
        }
        for c in codes
    ]
    # 名額外的代碼彙總成一則「其他行為（N次）」帶過，不逐一佔位——沒有專屬
    # glyph(不在每日格子裡個別標示)，醫師如對某天格子有疑慮可自行往資料源查核。
    if others_count:
        legend_items.append({
            "glyph": "", "color": _ICON_GRAY, "label": "其他行為", "count": others_count,
        })
    return {"icons_by_day": icons_by_day, "legend_items": legend_items}


_DATE_TICK_MIN_GAP = 4  # 相鄰刻度至少間隔這麼多格，避免文字擠在一起


def _date_ticks(dates):
    """每日狀態格子下方的日期刻度：每7格標一次，並固定加上最後一格，
    標籤直接讀該格實際對應的真實日期（不是照固定7天間隔推算），2026-08-04
    改法——原本只標第0格，缺測情境下沒有足夠刻度能驗證格子對不對得上實際
    日期，這裡改成用真實資料自我修正，缺測不會讓標籤錯位。2026-08-04修正：
    使用者實測抓到——當總格數不是7的整除時（例如30格），每7格一次的常規
    刻度最後一個可能落在倒數第2格，跟固定加上的最後一格只差1格，欄位太窄
    導致兩個日期文字疊在一起變成「11-2911-30」。改成常規刻度裡跟最後一格
    距離小於_DATE_TICK_MIN_GAP的都先剔除，再統一加回最後一格，確保刻度
    間至少留有足夠間距。"""
    n = len(dates)
    if n == 0:
        return {}
    last = n - 1
    ticks = {
        i: dates[i][5:] for i in range(0, n, 7)
        if last - i >= _DATE_TICK_MIN_GAP
    }
    ticks[last] = dates[last][5:]
    return ticks


def _period_label(df):
    if len(df) == 0:
        return "—"
    dates = sorted(df["date"].tolist())
    y, m, _ = dates[-1].split("-")
    return f"{y} 年 {int(m)} 月・照護摘要"


def _weight_span_days_months(baseline_df, current_df):
    """算「baseline最早一筆體重讀數」到「current最新一筆體重讀數」的實際
    天數/月數，供_weight_span_label()跟_compare_method_label()共用——兩者
    都需要知道體重資料真正涵蓋多久，不是用baseline_df/current_df裡任意
    欄位的日期，是特別過濾weight欄位有值的那些天，避免血壓等其他量測的
    日期範圍冒充成體重涵蓋範圍。完全沒有體重讀數可比對時回傳(None, None)。"""
    b_dates = baseline_df.loc[baseline_df["weight"].notna(), "date"] if "weight" in baseline_df else None
    c_dates = current_df.loc[current_df["weight"].notna(), "date"] if "weight" in current_df else None
    if b_dates is None or c_dates is None or b_dates.empty or c_dates.empty:
        return None, None
    span_days = (_date.fromisoformat(c_dates.max()) - _date.fromisoformat(b_dates.min())).days
    return span_days, round(span_days / 30.44)


def _weight_span_label(baseline_df, current_df):
    """衰弱卡「體重・OO」的標籤：設計目標是六個月長期趨勢（跟其餘三張卡的
    「前三月」是不同的比較窗口，這是衰弱卡本來就要看更長期的設計本意，不是
    可以隨資料長度隨便換的東西）。但系統目前只給baseline_df這一包資料，實際
    涵蓋不到六個月時，標籤要誠實反映實際天數/月數，不能繼續寫死「六個月」
    宣稱一個資料根本沒有的時間長度——達到六個月門檻就顯示「六個月」，不足則是
    例外情況、改顯示實際月數/天數。"""
    span_days, span_months = _weight_span_days_months(baseline_df, current_df)
    if span_days is None:
        return "體重・長期趨勢"  # 完全沒有體重讀數可比對，不宣稱任何時間長度
    if span_months >= _TARGET_WEIGHT_SPAN_MONTHS:
        return "體重・六個月"
    if span_months >= 1:
        return f"體重・近{span_months}個月"  # 例外情況：資料還不到六個月，如實顯示
    return f"體重・近{max(span_days, 1)}天"


def _compare_method_label(baseline_df, current_df):
    """查核與說明「跨月比較」欄——2026-08-05修正：原本是寫死的固定字
    「本月 vs 前3個月（體重採6個月）」，資料不足時（例如剛收案、baseline
    只累積了1個月）這句話會跟實際情況不符，改成依baseline_df實際涵蓋的
    天數動態產生，跟_weight_span_label()同一套「達到目標門檻就顯示目標值，
    不足就如實顯示實際值」的原則。2026-08-12再修正：使用者反映括號附加的
    degrade說明（「未滿3個月」／「體重採OO」）讓這一欄看起來太雜亂，且
    「個人基線」欄（見_baseline_range_label()）已經另外揭露了基線實際涵蓋
    的天數、體重的長期趨勢窗口也已經在衰弱卡自己的period_label顯示，這裡
    不需要重複交代——改成只保留最前面「本月 vs 前N個月」這句核心比較基準，
    拿掉括號跟裡面的文字，體重專屬的回溯窗口不再是這一欄的責任。"""
    if len(baseline_df) == 0:
        return "本月（尚無歷史可比較）"
    b_dates = sorted(baseline_df["date"].tolist())
    c_dates = sorted(current_df["date"].tolist()) if len(current_df) else None
    if not c_dates:
        return "本月（尚無歷史可比較）"
    span_days = (_date.fromisoformat(c_dates[-1]) - _date.fromisoformat(b_dates[0])).days
    span_months = round(span_days / 30.44)
    if span_months >= _BASELINE_MONTHS:
        return f"本月 vs 前{_BASELINE_MONTHS}個月"
    if span_months >= 1:
        return f"本月 vs 前{span_months}個月"
    return f"本月 vs 前{max(span_days, 1)}天"


# ── 情境1（首月無個人基線）：改用公定參考值判讀，不比較前三月基線 ────────
# 2026-08-12新增。判斷門檻用「這個個案有紀錄以來的資料實際涵蓋了多久」，
# 不是看baseline_events是否為空——即使baseline_events有部分資料（例如收案
# 兩個多月），只要還沒滿3個月一樣算資料不足（使用者確認：「先預設有填寫的
# 天數都有完整資料，這邊是設定只要未滿三個月(即使已經兩個多月)，都視為
# 資料不足，直接套用情境一」）。用「首筆到末筆事件相隔天數」而不是
# round()換算月數，是為了避免2.9個月被四捨五入成3個月而誤判為資料充足。
_MIN_BASELINE_DAYS = 90  # ≈3個月，跟_BASELINE_MONTHS(定義在下方)是同一個門檻的兩種單位


def _overall_data_span_days(events):
    """全部事件（不分baseline/current）實際涵蓋的天數（首筆到末筆），用來
    判斷這個個案的歷史資料是否已經累積滿3個月——跟split_events_by_current_
    month()切出來的baseline_events不同，那個函式固定只回溯3個月，即使個案
    實際收案超過3個月也看不出「原本到底累積了多久」。完全沒有可解析日期的
    事件時回傳0（視為資料不足）。"""
    dated = _dated_events(events)
    if not dated:
        return 0
    dates = [_date.fromisoformat(d) for _, d in dated]
    return (max(dates) - min(dates)).days


_ABS_CRISIS_LOW = "危急偏低"
_ABS_LOW = "偏低"
_ABS_NORMAL = "正常"
_ABS_HIGH = "偏高"
_ABS_CRISIS_HIGH = "危急偏高"


def _abs_four_tier_level(value, crisis_low, low, high, crisis_high):
    """情境1用的四門檻絕對值分級，收縮壓/舒張壓/脈搏共用同一套判斷邏輯
    （半開區間左閉右開，跟analysis_core.py既有的BP_SYS_*/DIA_*/PULSE_*系列
    命名邏輯一致）：<crisis_low危急偏低／<low偏低／<high正常／<crisis_high
    偏高／>=crisis_high危急偏高。value是NO_DATA/None時回傳None，呼叫端
    自行決定沒有資料要不要顯示這個項目。"""
    if value in (NO_DATA, None):
        return None
    if value < crisis_low:
        return _ABS_CRISIS_LOW
    if value < low:
        return _ABS_LOW
    if value < high:
        return _ABS_NORMAL
    if value < crisis_high:
        return _ABS_HIGH
    return _ABS_CRISIS_HIGH


def _abs_bmi_level(bmi):
    """BMI分級（使用者提供）：<18.5過輕／[18.5,24)正常／[24,27)過重／>=27肥胖。
    沒有「危急」層，跟血壓/舒張壓/脈搏的四門檻不同，門檻數量本身就不一樣，
    刻意不強行套用_abs_four_tier_level()。"""
    if bmi is None:
        return None
    if bmi < BMI_UNDERWEIGHT:
        return "過輕"
    if bmi < BMI_NORMAL_HIGH:
        return _ABS_NORMAL
    if bmi < BMI_OVERWEIGHT_HIGH:
        return "過重"
    return "肥胖"


def _temp_abnormal_flags_abs(df):
    """情境1用：逐日「體溫異常」bool旗標，改用絕對門檻(TEMP_LOW_ABS/
    TEMP_HIGH_ABS)判斷，不像_temp_abnormal_flags()依賴block3的基線相對
    算法（對個人基線偏離幾度）——首月個案還沒有個人基線可比。"""
    if len(df) == 0 or "temperature" not in df:
        return [False] * max(len(df), 1)
    t = df["temperature"]
    return ((t < TEMP_LOW_ABS) | (t >= TEMP_HIGH_ABS)).fillna(False).tolist()


def _no_baseline_period_label(span_days):
    """情境1四張卡片的period_label：如實反映目前實際累積的天數，不宣稱
    「前三月」這種還沒發生的比較窗口（跟_compare_method_label()同一套
    「達到門檻才顯示目標值，不足就如實顯示實際值」原則）。"""
    return f"近{max(span_days, 1)}天資料（未滿3個月）"


def _reference_standards_text():
    """情境1查核與說明「參考標準」六項的文字，數值直接讀既有常數組出來——
    門檻改了這裡自動跟著變，不用另外維護一份寫死的字串。"""
    return {
        "收縮壓": f"收縮壓 {BP_SYS_LOW}–{BP_SYS_TARGET} mmHg",
        "舒張壓": f"舒張壓 {DIA_LOW}–{DIA_TARGET} mmHg",
        "脈搏": f"脈搏 {PULSE_LOW}–{PULSE_HIGH - 1} 次/分",
        "體溫": f"體溫 {TEMP_LOW_ABS:.1f}–{TEMP_HIGH_ABS} °C",
        "飲水": f"飲水 ≥{WATER_TARGET_ML:,} ml/天",
        "BMI": f"BMI {BMI_UNDERWEIGHT}–{BMI_NORMAL_HIGH}",
    }


def _abs_standard_top3(sys_mean, dia_mean, pulse_mean):
    """情境1「本月生理數值提醒」：收縮壓/舒張壓/脈搏各自對公定標準分級，
    只有分級得出結果（不是NO_DATA）的項目才會出現，最多3項——跟原本
    _select_top()「偏離幅度最大3項」概念類似，但這裡沒有「偏離幅度」可排
    （沒有基線可比），固定順序收縮壓→舒張壓→脈搏。"""
    items = []
    sys_level = _abs_four_tier_level(sys_mean, BP_SYS_CRISIS_LOW, BP_SYS_LOW, BP_SYS_TARGET, BP_CRISIS)
    if sys_level:
        items.append({
            "name": "收縮壓", "value": _int_or_dash(sys_mean), "unit": "mmHg",
            "ref_line": f"正常範圍 {BP_SYS_LOW}–{BP_SYS_TARGET} mmHg",
            "delta_line": f"整體{sys_level}", "qualifier": None,
        })
    dia_level = _abs_four_tier_level(dia_mean, DIA_CRISIS_LOW, DIA_LOW, DIA_TARGET, DIA_CRISIS)
    if dia_level:
        items.append({
            "name": "舒張壓", "value": _num_or_dash(dia_mean), "unit": "mmHg",
            "ref_line": f"正常範圍 {DIA_LOW}–{DIA_TARGET} mmHg",
            "delta_line": f"整體{dia_level}", "qualifier": None,
        })
    pulse_level = _abs_four_tier_level(pulse_mean, PULSE_VERY_LOW, PULSE_LOW, PULSE_HIGH, PULSE_VERY_HIGH)
    if pulse_level:
        items.append({
            "name": "脈搏", "value": _int_or_dash(pulse_mean), "unit": "次/分",
            "ref_line": f"正常範圍 {PULSE_LOW}–{PULSE_HIGH - 1} 次/分",
            "delta_line": f"整體{pulse_level}", "qualifier": None,
        })
    return items


# ── 情境3（基線期間資料不完整/稀疏）：2026-08-12使用者定案────────────────
# 「資料量小於1/4時就算不夠充足」——用「有量測的天數／期間總天數」這個比例
# （不是絕對次數），因為候選池比的是「本月 vs 基線」兩段長度本來就不一樣
# （本月通常30天、基線通常90天），用比例才能同一套門檻套用在兩邊。體重
# 刻意不套用這個規則（見_candidate_pool()呼叫處），體重設計上就是每月只測
# 1-2次，稀疏是正常量測頻率、不是資料品質問題，套用同一個比例門檻會讓
# 體重幾乎永遠被判定樣本有限。
_SAMPLE_LIMITED_RATIO = 0.25


def _coverage_ratio(df, column):
    """該欄位在df這段期間裡「有量測的天數」佔「期間總天數」的比例。分母
    刻意用「首筆到末筆記錄實際跨的日曆天數」（呼應_full_calendar_dates()／
    header的「血壓 X/Y天」同一套denominator，Y就是這個算法），不是
    len(df)——df只保留「當天有任一種紀錄」的那些天，同一批稀疏資料如果
    剛好每個有紀錄的日子都量了血壓，用len(df)當分母會算出100%覆蓋率，
    完全掩蓋掉「這個月大半天數根本沒被量測」這件事，跟使用者要偵測的
    「資料量稀疏」語意正好相反。df本身是空的(len==0)時回傳None，交給
    呼叫端決定「完全沒資料」要不要當作樣本有限（見_is_sample_limited()，
    答案是視為有限——完全沒資料比稀疏更不可信）。"""
    if len(df) == 0 or column not in df:
        return None
    dates = df["date"].tolist()
    span_days = (_date.fromisoformat(max(dates)) - _date.fromisoformat(min(dates))).days + 1
    return df[column].notna().sum() / span_days


def _is_sample_limited(baseline_df, current_df, column):
    """情境3判斷：本月或基線「任一期間」的資料密度低於_SAMPLE_LIMITED_RATIO
    就視為樣本有限——使用者確認：候選池比較的是本月vs基線兩期，任一邊
    不可信，比較出來的百分比整個都不可信，不是只看其中一邊。"""
    b_cov = _coverage_ratio(baseline_df, column)
    c_cov = _coverage_ratio(current_df, column)
    if b_cov is None or c_cov is None:
        return True
    return bool(b_cov < _SAMPLE_LIMITED_RATIO or c_cov < _SAMPLE_LIMITED_RATIO)


def _candidate_pool(baseline, current, baseline_df, current_df, weight_baseline_df):
    """跟 analysis_core._build_signal_pool() 同樣的候選池概念，但這裡的 deviation
    是「本月 vs 前三月基線」的真實變化量，不是跟固定目標值比——這是 v9 設計
    (個人歷史基線) 跟現有 analyze_all() 候選池 (族群目標值) 的關鍵差異，
    見上次討論的 D1/D2。baseline_df/current_df是舒張壓平均值需要的原始
    dataframe——analyze_all()輸出的obs/signals目前沒有算舒張壓，只能自己
    從原始資料算（見_diastolic_mean）。weight_baseline_df是體重項專用的
    擴充6個月baseline，用來讓體重項的標籤（見下方"體重・OO"）比照衰弱卡
    的_weight_span_label()動態反映實際資料涵蓋月數，不是寫死六個月。"""
    pool = []

    b_bp, c_bp = baseline["obs"]["bp"], current["obs"]["bp"]
    b_b1, c_b1 = baseline["signals"]["block1"], current["signals"]["block1"]
    if c_bp["am_mean"] != NO_DATA and b_bp["am_mean"] != NO_DATA:
        delta_pct = round((c_bp["am_mean"] - b_bp["am_mean"]) / b_bp["am_mean"] * 100, 1)
        sev = _severity_level("bp_reach_gap_pct", abs(100 - c_b1["reach_pct"])) if c_b1["reach_pct"] != NO_DATA else None
        pool.append({
            "key": "bp", "name": "收縮壓", "before": b_bp["am_mean"], "after": c_bp["am_mean"],
            "unit": "mmHg", "ref_line": f"目標上限 {BP_SYS_TARGET}・基線 {int(b_bp['am_mean'])}",
            "deviation_pct": abs(delta_pct), "severity": sev,
            "direction": _direction_label(c_bp["am_mean"] - b_bp["am_mean"]),
            "sample_limited": _is_sample_limited(baseline_df, current_df, "systolic_am"),
            "acute": _acute_cluster_label(current_df, _bp_crisis_flags(current_df)),
        })

    # 2026-08-03新增：舒張壓比照收縮壓的候選池待遇——目前唯一算過的「偏離
    # 幅度最大」清單只有收縮壓，舒張壓完全沒被納入，即使analysis_core.py早就
    # 有BP_DIA_TARGET=90這個目標值常數。跟收縮壓不同的是，這裡的嚴重度分級
    # 直接用平均值變化幅度%(diastolic_pct)，不像收縮壓那樣繞去用達標率落差
    # ——舒張壓沒有對應的「達標率」計算，用平均值變化%更直接、也更一致。
    b_dia = _diastolic_mean(baseline_df)
    c_dia = _diastolic_mean(current_df)
    if c_dia != NO_DATA and b_dia != NO_DATA and b_dia:
        dia_delta_pct = round((c_dia - b_dia) / b_dia * 100, 1)
        sev = _severity_level("diastolic_pct", abs(dia_delta_pct))
        pool.append({
            "key": "diastolic", "name": "舒張壓", "before": b_dia, "after": c_dia,
            "unit": "mmHg", "ref_line": f"目標上限 {BP_DIA_TARGET}・基線 {b_dia}",
            "deviation_pct": abs(dia_delta_pct), "severity": sev,
            "direction": _direction_label(c_dia - b_dia),
            "sample_limited": _is_sample_limited(baseline_df, current_df, "diastolic_am"),
            "acute": _acute_cluster_label(current_df, _diastolic_crisis_flags(current_df)),
        })

    b_hwt, c_hwt = baseline["obs"]["hr_weight_temp"], current["obs"]["hr_weight_temp"]
    if c_hwt["hr_end"] != NO_DATA and b_hwt["hr_end"] != NO_DATA:
        delta = c_hwt["hr_end"] - b_hwt["hr_end"]
        delta_pct = round(delta / b_hwt["hr_end"] * 100, 1) if b_hwt["hr_end"] else 0
        sev = _severity_level("hr_delta", delta)
        pool.append({
            "key": "hr", "name": "脈搏", "before": b_hwt["hr_end"], "after": c_hwt["hr_end"],
            "unit": "次/分", "ref_line": f"個人基線 {int(b_hwt['hr_end'])}",
            "deviation_pct": abs(delta_pct), "severity": sev,
            "direction": _direction_label(delta),
            "sample_limited": _is_sample_limited(baseline_df, current_df, "heart_rate"),
            "acute": _acute_cluster_label(current_df, _hr_crisis_flags(current_df)),
        })

    # 體重比較基準用 baseline_df 裡「最早的」一筆讀數（weight_6mo，data_parser.py既有
    # 欄位），不是baseline的「最後一筆」（weight_now）——做法B：沒有真的6個月資料時，
    # 用baseline期間內能拿到的最早讀數退化計算，比較跨度盡量拉長，不是精確6個月，
    # 但至少比只拿baseline期末那筆更接近設計原意（見下面D2/衰弱卡討論）。
    # 2026-08-06修正：使用者截圖抓到候選池這裡的名稱寫死「體重・近六個月」，
    # 跟同一份資料在衰弱卡上用_weight_span_label()動態算出來的「體重・近4個月」
    # 對不起來——兩處講的是同一件事，資料實際只涵蓋4個月，候選池卻宣稱六個月。
    # 改成呼叫同一個_weight_span_label()，用weight_baseline_df(擴充6個月窗口)
    # 動態產生，兩處保證一致。
    if c_hwt["weight_now"] != NO_DATA and b_hwt["weight_6mo"] != NO_DATA and b_hwt["weight_6mo"]:
        delta_pct = round((c_hwt["weight_now"] - b_hwt["weight_6mo"]) / b_hwt["weight_6mo"] * 100, 1)
        sev = _severity_level("weight_pct", delta_pct)
        pool.append({
            "key": "weight", "name": _weight_span_label(weight_baseline_df, current_df),
            "before": b_hwt["weight_6mo"], "after": c_hwt["weight_now"],
            "unit": "kg", "ref_line": "參考值 減輕 ≥5%",
            "deviation_pct": abs(delta_pct), "severity": sev,
            "direction": _direction_label(delta_pct, worsening_if_positive=False),
        })

    # 2026-08-19修正（情境4：生理數值穩定但生活照護逐步變差）：使用者確認
    # 候選池不能只看生理量測——飲水/進食（上一輪一度移出候選池、只留在
    # 跨指標關聯偵測裡）現在跟排便異常/精神不佳/白天睡多一起，全部併入
    # 同一個候選池，用同一套deviation_pct/severity排序，直接跟血壓/血氧
    # 等生理量測競爭top3，不分類別（見情境4投影片但書：「偏離前三項的
    # 候選池須包含生活照護資料，不能只依生理量測排序」）。
    b_sfw, c_sfw = baseline["obs"]["spo2_food_water"], current["obs"]["spo2_food_water"]

    if c_sfw["water_short_days"] != NO_DATA and b_sfw["water_short_days"] != NO_DATA:
        b_pct = round(b_sfw["water_short_days"] / b_sfw["total"] * 100, 1) if b_sfw["total"] else 0
        c_pct = round(c_sfw["water_short_days"] / c_sfw["total"] * 100, 1) if c_sfw["total"] else 0
        sev = _severity_level("water_short_pct", c_pct)
        pool.append({
            "key": "water", "name": "飲水未達標天數", "before": f"{b_sfw['water_short_days']} 天",
            "after": f"{c_sfw['water_short_days']} 天", "unit": "",
            "ref_line": "水量未達標天數", "deviation_pct": abs(c_pct - b_pct), "severity": sev,
            "direction": _direction_label(c_pct - b_pct),
            "sample_limited": _is_sample_limited(baseline_df, current_df, "water_intake"),
        })

    if c_sfw["food_short_days"] != NO_DATA and b_sfw["food_short_days"] != NO_DATA:
        b_food_pct = round(b_sfw["food_short_days"] / b_sfw["total"] * 100, 1) if b_sfw["total"] else 0
        c_food_pct = round(c_sfw["food_short_days"] / c_sfw["total"] * 100, 1) if c_sfw["total"] else 0
        sev = _severity_level("food_short_pct", c_food_pct)
        pool.append({
            "key": "food", "name": "進食不足天數", "before": f"{b_sfw['food_short_days']} 天",
            "after": f"{c_sfw['food_short_days']} 天", "unit": "",
            "ref_line": "進食不足天數", "deviation_pct": abs(c_food_pct - b_food_pct), "severity": sev,
            "direction": _direction_label(c_food_pct - b_food_pct),
            "sample_limited": _is_sample_limited(baseline_df, current_df, "food_intake"),
        })

    # 2026-08-19新增：血氧補進候選池——原本只有分布圖(絕對值)跟跨指標關聯
    # 偵測看得到血氧，單獨血氧偏低、沒搭配其他異常訊號時完全不會被摘要
    # 提及。跟飲水/進食同一套算法(月偏低天數佔比%當分級基準，兩期佔比
    # 落差當顯示%)，門檻沿用analysis_core.py既有的spo2_low_pct(v2候選池
    # 已經在用同一組門檻，見_build_signal_pool())。
    if c_sfw["spo2_low_days"] != NO_DATA and b_sfw["spo2_low_days"] != NO_DATA:
        b_spo2_pct = round(b_sfw["spo2_low_days"] / b_sfw["total"] * 100, 1) if b_sfw["total"] else 0
        c_spo2_pct = round(c_sfw["spo2_low_days"] / c_sfw["total"] * 100, 1) if c_sfw["total"] else 0
        sev = _severity_level("spo2_low_pct", c_spo2_pct)
        pool.append({
            "key": "spo2", "name": "血氧偏低天數", "before": f"{b_sfw['spo2_low_days']} 天",
            "after": f"{c_sfw['spo2_low_days']} 天", "unit": "",
            "ref_line": "血氧偏低天數", "deviation_pct": abs(c_spo2_pct - b_spo2_pct), "severity": sev,
            "direction": _direction_label(c_spo2_pct - b_spo2_pct),
            "sample_limited": _is_sample_limited(baseline_df, current_df, "spo2"),
        })

    # 2026-08-19新增（情境4）：排便異常天數——跟飲水/進食同一個total分母
    # (b_sfw/c_sfw['total'])，也套用sample_limited判斷，因為Defecation
    # 紀錄有明確的status欄位(有登打才有值)，跟水/食/血氧同樣語意：沒登打
    # 就是沒測量，不是「沒異常所以不用打」。
    b_defe = _defecation_special_days(baseline_df)
    c_defe = _defecation_special_days(current_df)
    if c_defe != NO_DATA and b_defe != NO_DATA:
        b_defe_pct = round(b_defe / b_sfw["total"] * 100, 1) if b_sfw["total"] else 0
        c_defe_pct = round(c_defe / c_sfw["total"] * 100, 1) if c_sfw["total"] else 0
        sev = _severity_level("defecation_pct", c_defe_pct)
        pool.append({
            "key": "defecation", "name": "排便異常天數", "before": f"{b_defe} 天",
            "after": f"{c_defe} 天", "unit": "",
            "ref_line": "排便異常天數", "deviation_pct": abs(c_defe_pct - b_defe_pct), "severity": sev,
            "direction": _direction_label(c_defe_pct - b_defe_pct),
            "sample_limited": _is_sample_limited(baseline_df, current_df, "defecation_special"),
        })

    # 2026-08-19新增（情境4）：精神不佳／白天睡多天數——這兩項是「觀察到
    # 異常才登打」的行為代碼，沒有對應「今天有沒有觀察」的獨立欄位，跟
    # 水/食/血氧/排便「沒登打=沒測量」的語意不同（沒登打也可能代表「這
    # 天沒有異常行為」，不是「沒觀察」）。刻意不套用sample_limited判斷，
    # 避免把「本月都正常」誤判成「樣本不足」。"""
    b_mental, c_mental = _mental_abnormal_days(baseline_df), _mental_abnormal_days(current_df)
    if c_mental != NO_DATA and b_mental != NO_DATA:
        b_mental_pct = round(b_mental / b_sfw["total"] * 100, 1) if b_sfw["total"] else 0
        c_mental_pct = round(c_mental / c_sfw["total"] * 100, 1) if c_sfw["total"] else 0
        sev = _severity_level("mental_pct", c_mental_pct)
        pool.append({
            "key": "mental", "name": "精神不佳天數", "before": f"{b_mental} 天",
            "after": f"{c_mental} 天", "unit": "",
            "ref_line": "精神不佳天數", "deviation_pct": abs(c_mental_pct - b_mental_pct), "severity": sev,
            "direction": _direction_label(c_mental_pct - b_mental_pct),
        })

    b_sleep, c_sleep = _sleep_abnormal_days(baseline_df), _sleep_abnormal_days(current_df)
    if c_sleep != NO_DATA and b_sleep != NO_DATA:
        b_sleep_pct = round(b_sleep / b_sfw["total"] * 100, 1) if b_sfw["total"] else 0
        c_sleep_pct = round(c_sleep / c_sfw["total"] * 100, 1) if c_sfw["total"] else 0
        sev = _severity_level("sleep_pct", c_sleep_pct)
        pool.append({
            "key": "sleep", "name": "白天睡多天數", "before": f"{b_sleep} 天",
            "after": f"{c_sleep} 天", "unit": "",
            "ref_line": "白天睡多天數", "deviation_pct": abs(c_sleep_pct - b_sleep_pct), "severity": sev,
            "direction": _direction_label(c_sleep_pct - b_sleep_pct),
        })

    # 2026-08-03修正：體溫偏離基線改用跟飲水/進食同一套算法——原本嚴重度是
    # 拿「本月偏離天數」直接套用temp_delta這組°C門檻表(單位對不起來)，顯示的
    # 偏離%又是另一個「天數差×10」的湊法。現在跟water/food一致：兩期都換算
    # 成「偏離天數佔比%」，本月佔比當分級基準，兩期佔比落差當顯示的偏離%，
    # 門檻改用新增的temp_pct(15/30/50，比照water_short_pct/food_short_pct)。
    b3, c3 = baseline["signals"]["block3"], current["signals"]["block3"]
    if b3.get("temp_total") and c3.get("temp_total"):
        b_cnt = len(b3["low"]) + len(b3["high"])
        c_cnt = len(c3["low"]) + len(c3["high"])
        b_temp_pct = round(b_cnt / b3["temp_total"] * 100, 1)
        c_temp_pct = round(c_cnt / c3["temp_total"] * 100, 1)
        sev = _severity_level("temp_pct", c_temp_pct)
        pool.append({
            "key": "temp", "name": "體溫偏離基線", "before": f"{b_cnt} 天", "after": f"{c_cnt} 天",
            "unit": "", "ref_line": f"基線 {c3['temp_baseline']}°C",
            "deviation_pct": abs(c_temp_pct - b_temp_pct), "severity": sev,
            "direction": _direction_label(c_cnt - b_cnt),
            "sample_limited": _is_sample_limited(baseline_df, current_df, "temperature"),
            "acute": _acute_cluster_label(current_df, _temp_abnormal_flags_abs(current_df)),
        })

    return pool


_SEVERITY_RANK = {"重度": 3, "中度": 2, "輕度": 1, None: 0}


def _select_top(pool, n=3):
    # 2026-08-12使用者定案（情境3）：資料量低於1/4門檻的項目一律不列入
    # 「本月生理數值提醒」上方三項——不管偏離百分比看起來多大，樣本不足
    # 時這個百分比本身就不可信，會被降級到「其餘偏離」並加註記（見
    # build_v9_view_model()裡remaining_deviations的組成）。
    eligible = [p for p in pool if not p.get("sample_limited")]
    # 2026-08-13使用者定案（情境5）：急性事件(連續>=_CLUSTER_MIN_CONSECUTIVE
    # 天危急等級，見_acute_cluster_label())一定要進top3，不管月平均偏離%
    # 多小——月平均本來就會把短期尖峰稀釋掉，這正是情境5要解決的問題，
    # 所以急性項目繞過「必須有severity分級」這個一般門檻(急性期可能只佔
    # 全月一小部分，月平均落差不一定能觸發severity分級，但這不代表這件
    # 事不重要)。如果同時有超過n個急性項目互相競爭名額，一樣照既有的
    # 嚴重度分級排序決定順序（使用者原話：「若都有短期急性則就照之前的
    # 危急程度門檻比較」），沒進前n名的急性項目會在remaining_deviations
    # 那邊被撿回來、一樣標示急性（見build_v9_view_model()）。
    acute = sorted(
        (p for p in eligible if p.get("acute")),
        key=lambda p: (_SEVERITY_RANK[p["severity"]], p["deviation_pct"]), reverse=True,
    )
    non_acute = sorted(
        (p for p in eligible if not p.get("acute") and p["severity"]),
        key=lambda p: (_SEVERITY_RANK[p["severity"]], p["deviation_pct"]), reverse=True,
    )
    return (acute + non_acute)[:n]


def _event_count(events, kind):
    return events["general_summary"]["by_code"].get(kind, 0) + sum(
        1 for e in events["high_priority"] if e["label"] == kind
    )


def _incontinence_items(baseline_events, current_events):
    """尿失禁/大便失禁是Behavior行為代碼（不是Urination.status），跟體液
    代謝卡主題相關但目前只會被彙總進「其他行為N次」不會單獨顯示——使用者
    決定不用獨立子項呈現，只在趨勢文字裡補一句「若有發生」，不硬做卡片。
    2026-08-11改法：trend_note從單一字串改成條列式清單(PM要求)，這裡
    跟著回傳清單而不是用「、」黏成一句——原本「；」+「、」兩種分隔符號
    混用在同一句裡讀起來很亂，改成每個子項各自一個獨立bullet後，這個
    問題自然消失，不需要再煩惱分隔符號。"""
    b_urine = _event_count(baseline_events, "尿失禁")
    b_bowel = _event_count(baseline_events, "大便失禁")
    c_urine = _event_count(current_events, "尿失禁")
    c_bowel = _event_count(current_events, "大便失禁")
    items = []
    if c_urine or b_urine:
        items.append(f"尿失禁：前三月{b_urine}次／本月{c_urine}次")
    if c_bowel or b_bowel:
        items.append(f"大便失禁：前三月{b_bowel}次／本月{c_bowel}次")
    return items


def _event_date_str(e):
    raw = e.get("OccurredAt") or e.get("RecordTime")
    return raw[:10] if raw else None


def _month_start(d: _date) -> _date:
    return d.replace(day=1)


def _shift_months(d: _date, months: int) -> _date:
    """d必須是每月1號；回傳往前/往後移動months個月的1號。"""
    total = d.year * 12 + (d.month - 1) + months
    y, m = divmod(total, 12)
    return _date(y, m + 1, 1)


_BASELINE_MONTHS = 3          # 一般欄位（血壓/血氧/飲水/進食/體溫/精神/排泄/衛生協助等）
                               # 的「前三月」比較窗口
_WEIGHT_BASELINE_MONTHS = 6   # 衰弱卡「體重・OO」長期趨勢專用窗口，比一般欄位長


def _dated_events(events: list) -> list:
    dated = [(e, _event_date_str(e)) for e in events]
    return [(e, d) for e, d in dated if d]


def split_events_by_current_month(events: list, baseline_months: int = _BASELINE_MONTHS) -> tuple:
    """2026-08-04新增、2026-08-05修正：真實上游資料是一整串事件（不會像
    目前測試資料分好「前三月」「本月」兩包），這裡改成後端自己切分——
    本月＝資料中最新一筆紀錄所在的日曆月份，baseline＝本月之前、往回
    baseline_months個月內的事件。

    2026-08-05修正：原本baseline不設上限（本月以外全部都算），是為了不讓
    衰弱卡「體重・六個月」被砍斷；但這樣一來，如果上游一次給超過3個月的
    歷史，血壓/血氧/飲水/進食/體溫等「前三月近期趨勢」欄位的baseline平均
    會被更久以前的資料稀釋，看不出真正的近期變化——這正是這條規則要避免
    的問題。改法：baseline改回真的只取最近baseline_months個月（預設3），
    體重需要的更長歷史改用extended_baseline_events()另外抓，兩者在
    build_v9_view_model()裡分開處理，不共用同一份baseline_df。

    回傳(baseline_events, current_events)，日期缺失或無法解析的事件不計入
    任何一邊，比baseline_months個月更早的事件也不計入任何一邊（只有
    extended_baseline_events()會用到）。"""
    dated = _dated_events(events)
    if not dated:
        return [], []
    latest = max(_date.fromisoformat(d) for _, d in dated)
    current_month_start = _month_start(latest)
    next_month_start = _shift_months(current_month_start, 1)
    baseline_start = _shift_months(current_month_start, -baseline_months)

    baseline_events, current_events = [], []
    for e, d in dated:
        dd = _date.fromisoformat(d)
        if current_month_start <= dd < next_month_start:
            current_events.append(e)
        elif baseline_start <= dd < current_month_start:
            baseline_events.append(e)
    return baseline_events, current_events


def extended_baseline_events(events: list, months: int = _WEIGHT_BASELINE_MONTHS) -> list:
    """衰弱卡「體重・OO」長期趨勢專用的擴充baseline——往回看最多months個月
    （預設6，比split_events_by_current_month()一般欄位用的3個月更長），
    讓Fried衰弱表型的體重減輕判斷能看到足夠長的歷史，同時不影響其他欄位
    「前三月近期趨勢」的baseline範圍（見split_events_by_current_month()的
    2026-08-05修正說明）。"""
    dated = _dated_events(events)
    if not dated:
        return []
    latest = max(_date.fromisoformat(d) for _, d in dated)
    current_month_start = _month_start(latest)
    start = _shift_months(current_month_start, -months)
    return [e for e, d in dated if start <= _date.fromisoformat(d) < current_month_start]


def build_v9_view_model(events: list, profile: dict | None = None) -> dict:
    """events：一整串真實schema事件陣列（不分前三月/本月），由
    split_events_by_current_month()在這裡切分成baseline(3個月)/current。
    呼叫端（routes_v9.py、前端）不再需要自己決定切分點。

    2026-08-05新增：體重長期趨勢（衰弱卡「體重・OO」）另外用
    extended_baseline_events()抓最多6個月的擴充baseline，跟其餘欄位共用
    的3個月baseline分開——避免血壓/血氧/飲水/進食/體溫等「近期趨勢」欄位
    被更久以前的資料稀釋，同時保留體重判斷Fried衰弱表型需要的較長歷史。"""
    profile = profile or {}
    baseline_events, current_events = split_events_by_current_month(events)
    weight_baseline_events = extended_baseline_events(events)
    baseline_df = build_dataframe_from_request({"_data": baseline_events})
    weight_baseline_df = build_dataframe_from_request({"_data": weight_baseline_events})
    # 2026-08-06修正：current_df原本沒有指定temp_baseline，data_parser.py會
    # 用「本月自己的體溫中位數」自我計算基線——這樣「體溫偏離基線」永遠是
    # 跟自己比，只要整個月同步偏移（例如慢性低燒整月都偏高）就會被自己的
    # 中位數吸收掉，偵測不到，不管歷史基線資料多完整都一樣（已用單元測試
    # 驗證：baseline穩定36.0°C、本月整月37.1°C，理論上該全部偏高，修正前
    # 卻顯示0天）。改成直接沿用baseline_df算出來的temp_baseline（真正的
    # 歷史個人基線）；baseline本身沒有體溫資料時，data_parser.py內建會
    # 退回36.5°C的族群預設值，不會是None，所以這裡永遠有值可傳。
    current_df = build_dataframe_from_request(
        {"_data": current_events}, temp_baseline=baseline_df.attrs.get("temp_baseline")
    )
    baseline = analyze_all(baseline_df)
    weight_baseline = analyze_all(weight_baseline_df)
    current = analyze_all(current_df)
    # 把一般3個月baseline算出來的weight_6mo，覆蓋成擴充窗口(最多6個月)算出來
    # 的值——後面所有讀baseline["obs"]["hr_weight_temp"]["weight_6mo"]的地方
    # （候選池「體重・近六個月」、衰弱卡、qualifiers）都會自動吃到這個較長
    # 窗口的結果，不用一一修改呼叫點。
    baseline["obs"]["hr_weight_temp"]["weight_6mo"] = weight_baseline["obs"]["hr_weight_temp"]["weight_6mo"]

    header = {
        "name": profile.get("name", "測試個案"),
        "gender": profile.get("gender", "-"),
        "age": profile.get("age", "-"),
        "cfs": profile.get("cfs", "-"),
        "group": profile.get("group", "-"),
        "period_label": _period_label(current_df),
    }

    # 情境1（首月無個人基線）判斷：見_overall_data_span_days()/_MIN_BASELINE_
    # DAYS說明。下面candidate-pool驅動的top_summary/top3/four_aspects/
    # check_notes照舊算一次（pool資料不足時各項本來就會因NO_DATA而自然清空，
    # 不會壞），insufficient_baseline為真時，這幾塊在下面各自的位置改用
    # 公定參考值版本覆蓋掉，兩條路徑並存、互不影響彼此的計算。
    data_span_days = _overall_data_span_days(events)
    insufficient_baseline = data_span_days < _MIN_BASELINE_DAYS

    pool = _candidate_pool(baseline, current, baseline_df, current_df, weight_baseline_df)
    top = _select_top(pool, 3)
    # 2026-08-13新增（情境5）：remaining（沒進top3但仍要在「其餘偏離」
    # 提到的項目）比照_select_top()一樣，急性事件繞過「必須有severity」
    # 門檻（急性期短、月平均severity可能算不出來，不代表不重要），排序
    # 也讓急性項目優先（急性>嚴重度>偏離%，全部由大到小），這樣如果急性
    # 項目超過3個擠不進top3，優先被撿進remaining，不會被普通偏離項目
    # 排擠掉。
    # 2026-08-19修正（情境4）：候選池從7項擴大到11項(飲水/進食/排便異常/
    # 精神不佳/白天睡多併入後)，符合條件的項目可能超過原本[:3]上限，多出
    # 來的會完全不顯示、造成資訊漏失——上限調高到5，讓「其餘偏離」有機會
    # 撿到更多項目。
    remaining = sorted(
        (p for p in pool if p not in top and (p["severity"] or p.get("acute"))),
        key=lambda p: (bool(p.get("acute")), _SEVERITY_RANK[p["severity"]], p["deviation_pct"]),
        reverse=True,
    )[:5]
    # 2026-08-13新增（情境3）：候選池的sample_limited結果供這個函式後面
    # 多處重用（跨指標關聯偵測的訊號標註、四張卡片的badge）——搬到pool
    # 剛算完就建好，不要在各個用到的地方各自重複建一份。
    _sample_limited_by_key = {p["key"]: bool(p.get("sample_limited")) for p in pool}
    # 2026-08-13新增（情境5）：疾病監測面向卡片的急性badge比照sample_
    # limited同一套查表方式——只有bp(卡1)/temp(卡3)有對應的四張卡片可以
    # 掛badge，diastolic/hr沒有專屬卡片（跟sample_limited badge同樣的
    # 既有限制，這裡沿用不重新設計）。
    _acute_by_key = {p["key"]: p.get("acute") for p in pool}

    # 候選池卡片第三行的附加說明（2026-07-31新增，原稿三張卡片各自有一句
    # 「11月中旬起」／「已達界線」／「整月上行」這種依情況而定的補充句，
    # 之前完全沒做——只有前3名top3會顯示這個，remaining（其餘偏離項目）
    # 維持原樣不加，跟原稿一致）。只看current_df（本月）的逐日型態，
    # 邏輯上跟每日狀態/趨勢註記用的_onset_phrase是同一套機制。
    dates_c = current_df["date"].tolist()
    b_hwt_q = baseline["obs"]["hr_weight_temp"]
    c_hwt_q = current["obs"]["hr_weight_temp"]
    weight_delta_pct_q = (
        round((c_hwt_q["weight_now"] - b_hwt_q["weight_6mo"]) / b_hwt_q["weight_6mo"] * 100, 1)
        if c_hwt_q["weight_now"] != NO_DATA and b_hwt_q["weight_6mo"] not in (NO_DATA, None, 0)
        else None
    )
    qualifiers = {
        "bp": _onset_phrase(_bp_high_flags(current_df), dates_c),
        "diastolic": _onset_phrase(_diastolic_high_flags(current_df), dates_c),
        "hr": _onset_phrase(_hr_high_flags(current_df, b_hwt_q["hr_end"]), dates_c),
        "weight": _weight_qualifier(weight_delta_pct_q),
        "water": _onset_phrase(_water_short_flags(current_df), dates_c),
        "food": _onset_phrase(_food_short_flags(current_df), dates_c),
        "temp": _onset_phrase(_temp_abnormal_flags(current_df, current["signals"]["block3"]), dates_c),
        "spo2": _onset_phrase(_spo2_low_flags(current_df), dates_c),
        "defecation": _onset_phrase(_defecation_special_flags(current_df), dates_c),
        "mental": _onset_phrase(_code_day_flags(current_df, _MENTAL_CODES), dates_c),
        "sleep": _onset_phrase(_code_day_flags(current_df, _SLEEP_CODES), dates_c),
    }

    # 跨指標同期關聯偵測（頂部摘要detail句用）：純規則運算，找出多種訊號
    # 同一段期間一起異常的區段，組成事實子句——LLM(src/llm_summary_v9.py)
    # 只負責潤飾語序，不負責判斷關聯本身或碰數字，避免捏造不存在的關聯。
    _pad_c = lambda flags: flags if flags else [False] * len(dates_c)
    signal_flags_overlap = {
        "血壓偏高": _pad_c(_bp_high_flags(current_df)),
        "心率上升": _pad_c(_hr_high_flags(current_df, b_hwt_q["hr_end"])),
        "血氧下降": _spo2_low_flags(current_df),
        "體溫偏離": _pad_c(_temp_abnormal_flags(current_df, current["signals"]["block3"])),
        "精神不佳": _code_day_flags(current_df, _MENTAL_CODES),
        "情緒異常": _code_day_flags(current_df, _MOOD_CODES),
        "白天睡多": _code_day_flags(current_df, _SLEEP_CODES),
        "排便異常": _defecation_special_flags(current_df),
        "飲水未達標": _water_short_flags(current_df),
        "進食不足": _food_short_flags(current_df),
        "遊走": _code_day_flags(current_df, {"wander"}),
    }
    overlap_events = _multi_signal_overlap(signal_flags_overlap, dates_c)
    top_overlap_event = max(overlap_events, key=lambda e: len(e["signals"])) if overlap_events else None
    # 2026-08-13新增（情境3）：使用者決定「仍參與跨指標關聯偵測，但句子裡
    # 附帶標註」——不是整個排除樣本有限的訊號，只有候選池本身有算過
    # sample_limited的5種訊號(血壓/心率/體溫/飲水/進食)才查表，其餘訊號
    # (血氧/精神/情緒/白天睡多/排便異常/遊走)目前沒有對應的候選池項目，
    # 沒有樣本有限的判斷依據，維持原樣不標註。
    _overlap_sample_limited_signals = {
        name for name, key in _OVERLAP_SIGNAL_TO_POOL_KEY.items() if _sample_limited_by_key.get(key)
    }
    overlap_result = (
        _overlap_clause(top_overlap_event, sample_limited_signals=_overlap_sample_limited_signals)
        if top_overlap_event else None
    )
    detail_clause = overlap_result["clause"] if overlap_result else None

    def _fmt_item(p):
        delta_word = {"惡化": "偏離", "改善中": "改善"}.get(p["direction"], "變化")
        return {
            "name": p["name"], "value": f"{p['before']} → {p['after']}", "unit": p["unit"],
            "ref_line": p["ref_line"],
            "delta_line": f"{delta_word} {p['deviation_pct']:.0f}%",
            "qualifier": qualifiers.get(p["key"]),
            # 2026-08-13新增（情境5）：急性事件時，report_html_v9.py的
            # _top3_card()會用這個欄位整段取代原本delta_line/qualifier那行
            # 的呈現（改成紫紅色＋「急性事件」badge），不是同一個位置的
            # 附加說明——急性訊息比一般的偏離%/集中期描述重要，值得整段
            # 換掉而不是並排顯示造成擁擠。
            "acute": p.get("acute"),
        }

    # 2026-08-13修正（情境5）：急性事件項目可能severity是None(平均值落差
    # 不足以觸發severity分級，但仍被acute規則強制列入top3)，這裡如果只算
    # severity truthy的項目，會導致左上角大數字卡跟headline「本月共N項...」
    # 文字對不起來(數字比實際項目數少)——一併把acute項目算進來。
    # 2026-08-18修正：headline原本另外用len(top)（top3永遠<=3）算N，
    # deviation_count>3時（急性項目不受top3上限限制，可能超過3項）兩處
    # 數字會對不起來（大數字卡寫4項，headline卻寫「共3項」）。改成兩處
    # 共用同一個deviation_count，確保一致。
    deviation_count = len([p for p in pool if p["severity"] or p.get("acute")])
    top_summary = {
        "deviation_count": deviation_count,
        "headline": (
            f"本月共 {deviation_count} 項主要生理量測較前三月基線出現明顯變化"
            + (f"，以{top[0]['name']}變化最為明顯。" if top else "。")
        ),
        # 2026-08-05新增：headline結構化成分，供report_html_v9.py把「N項」
        # 跟「以OO變化最為明顯」上色——headline本身不會被LLM潤飾(只有detail
        # 會)，所以這裡的數字/名稱不會被重新排版，字串切分是安全的。
        "headline_count": deviation_count,
        "headline_top_name": top[0]["name"] if top else None,
        # detail句：有偵測到跨指標同期重疊就用事實子句（見_multi_signal_
        # overlap/_overlap_clause），這裡先給規則式版本，未經LLM潤飾；
        # routes_v9.py的背景工作會在detail_clause不是None時另外呼叫
        # llm_summary_v9.polish_clause()潤飾語序後覆蓋掉這個detail欄位，
        # LLM呼叫失敗時就直接沿用這裡算出來的規則版句子當fallback。
        "detail": (
            detail_clause if detail_clause
            else "系統僅依前三月與本月數值變化幅度排序，不作病因推論；本月未偵測到多項訊號同期重疊異常。"
        ),
        "detail_clause": detail_clause,
        # 2026-08-04新增、2026-08-05擴充：detail_clause裡烤進去的「必須逐字
        # 倖存」片段清單，原封不動列出來供llm_summary_v9.polish_clause()做
        # 「潤飾後這些詞是不是還逐字存在」的強制檢查——使用者實測抓到LLM把
        # 「情緒異常」潤成「的情異常」（白名單驗證抓不到，因為每個字都是合法
        # 繁體中文，只是拼錯/漏字）、「精神不佳」潤成「精神壓力下」（語意被
        # 偷換，不是單純語序調整）。這兩種都不是「新增不該出現的字元」，是
        # 既有的白名單驗證機制設計上就抓不到的類型，需要另一層「原始片段是否
        # 逐字倖存」的檢查才擋得住。2026-08-05起這份清單也包含日期範圍跟
        # 「等N項」收尾——同樣理由：LLM曾把「11/1–11/14」重新排版成
        # 「11月1日至14日」，數字沒變但格式變了；把日期字串也納入
        # required_terms後，格式跑掉會被判定驗證失敗、直接回退到原始子句，
        # 確保detail欄位裡的日期永遠是原始格式。
        "detail_signals": overlap_result["required_terms"] if overlap_result else [],
        # 2026-08-05二次修正：使用者確認要上色的是「日期範圍、訊號名稱、
        # 『N項』計數數字」這幾個項目本身，只有「等」「期間」「同時出現異常」
        # 這類純連接字維持黑色。detail_highlight_terms跟上面的detail_signals
        # 用途不同：detail_signals是「LLM潤飾後必須逐字倖存」的完整清單
        # (含more_count，用途是防止竄改)，detail_highlight_terms是
        # 「report_html_v9.py實際要上色」的清單——這裡兩者剛好內容一樣
        # (日期+訊號名稱+N項數字)，但保留分開的欄位是因為未來若要求「必須
        # 保留但不上色」的片段，兩者就會分岔，不應該共用同一份清單耦合。
        "detail_highlight_terms": overlap_result["required_terms"] if overlap_result else [],
    }

    top3 = [_fmt_item(p) for p in top]
    # 2026-08-13改法：remaining_deviations改成3-tuple，多帶一個sample_limited
    # bool——原本「樣本有限」是直接烤進delta_line文字尾巴(・樣本有限)，
    # report_html_v9.py沒辦法把它拆出來獨立做成一個有底色的badge方塊，
    # 只能整段文字用同一個顏色。分開成獨立欄位後，render端可以自由決定
    # delta_line本身的顏色(依偏離/改善方向)跟樣本有限badge的樣式，兩者互不
    # 綁死。
    # 2026-08-13新增（情境5）：第4個tuple元素是急性事件文字（沒有就是
    # None）——remaining裡的項目如果是被top3擠出來的急性事件（例如同時
    # 有4項急性、只有3個名額），這裡要跟top3卡片一樣標示出來，不能因為
    # 沒進top3就把「這其實是急性事件」這件事弄丟。
    remaining_deviations = [
        (p["name"], _fmt_item(p)["delta_line"], bool(p.get("sample_limited")), p.get("acute"))
        for p in remaining
    ]

    c_dia = _diastolic_mean(current_df)
    if insufficient_baseline:
        # 情境1覆蓋：候選池的「偏離幅度最大3項」概念整個不適用（沒有基線
        # 可算偏離量），改成收縮壓/舒張壓/脈搏各自對公定標準分級。
        # remaining_deviations同理清空——那是「候選池裡沒進top3、但仍有
        # 明顯偏離的其餘項目」，一樣建立在跟基線比較的前提上。
        top3 = _abs_standard_top3(current["obs"]["bp"]["am_mean"], c_dia, c_hwt_q["hr_end"])
        remaining_deviations = []
        top3_names = {item["name"] for item in top3}
        reference_standards = [
            text for name, text in _reference_standards_text().items() if name not in top3_names
        ]
        top_summary = {
            "deviation_count": sum(1 for it in top3 if not it["delta_line"].endswith(_ABS_NORMAL)),
            "headline": (
                f"本月尚無個人基線可比較（需累積滿{_BASELINE_MONTHS}個月資料），"
                "以下依公定參考值呈現本月量測概況。"
            ),
            # headline_count/headline_top_name刻意不放進這個dict——
            # report_html_v9.py的_render_headline()只要看到"headline_count"
            # 這個key存在（即使值是None）就會用baseline-relative的「本月共
            # N項...」模板重組字串，不會退回顯示這裡寫好的headline原文。
            # 省略這兩個key才會觸發它的fallback：直接顯示ts["headline"]。
            "detail": (
                detail_clause if detail_clause
                else f"個人化「前{_BASELINE_MONTHS}月→本月」比較，將於累積滿{_BASELINE_MONTHS}"
                     f"個月資料後開始呈現；目前已累積{data_span_days}天資料。"
            ),
            "detail_clause": detail_clause,
            "detail_signals": overlap_result["required_terms"] if overlap_result else [],
            "detail_highlight_terms": overlap_result["required_terms"] if overlap_result else [],
            "section_title": "本月生理數值提醒",
        }

    c_bp = current["obs"]["bp"]
    # 2026-08-07依醫師會議改為五級：危急(<90) / 偏低([90,100)) / 正常([100,130))
    # / 偏高([130,160)) / 危急(>=160)。兩端「危急」在畫面上合併顯示成同一個
    # chip（沿用2026-08-05的合併呈現邏輯，日期用(月,日)數值排序，不能直接
    # 字串排序），但「偏低」現在是獨立顯示的橘色區塊，不再併入危急——這是
    # 跟上一版最大的不同，上一版是「低血壓+危象=同一級」，現在中間多了一個
    # 「偏低」的過渡級距，只有真正的兩端極值才合併算危急。
    _crit_dates_raw = c_bp["crisis_detail"] + c_bp["crisis_low_detail"]
    _crit_dates_sorted = sorted(
        {d["date"] for d in _crit_dates_raw},
        key=lambda md: tuple(int(x) for x in md.split("/")),
    )
    # ticks動態算出交界位置（沿用2026-08-06的作法：只在[0,100]夾範圍避免
    # 浮點誤差，不夾到窄範圍犧牲位置準確度；文字避免被裁到的邏輯在
    # report_html_v9.py的_tick_html()處理，不動線本身的位置）。
    _bp_crisis_low_pct = _pct_num(c_bp.get("crisis_low_pct"))
    _bp_low_pct = _pct_num(c_bp.get("low_pct"))
    _bp_ok_pct = _pct_num(c_bp.get("ok_pct"))
    _bp_high_pct = _pct_num(c_bp.get("high_pct"))
    _tick_90 = max(0, min(100, _bp_crisis_low_pct))
    _tick_100 = max(0, min(100, _bp_crisis_low_pct + _bp_low_pct))
    _tick_130 = max(0, min(100, _bp_crisis_low_pct + _bp_low_pct + _bp_ok_pct))
    _tick_160 = max(0, min(100, _bp_crisis_low_pct + _bp_low_pct + _bp_ok_pct + _bp_high_pct))
    bp_distribution = {
        "count_label": f"{c_bp['total_count']} 次量測／{c_bp['measured_days']} 天・月均 {_num_or_dash(c_bp['am_mean'])} mmHg",
        "segments": [(_bp_crisis_low_pct, "#D85A30"), (_bp_low_pct, "#EF9F27"), (_bp_ok_pct, "#639922"),
                     (_bp_high_pct, "#EF9F27"), (_pct_num(c_bp.get("crisis_pct")), "#D85A30")],
        "ticks": [(_tick_90, "90"), (_tick_100, "100"), (_tick_130, "130"), (_tick_160, "160")],
        "chips": [("目標內", _pct_or_dash(c_bp["ok_pct"]), f"{_num_or_dash(c_bp['ok_count'])} 次"),
                  ("偏高", _pct_or_dash(c_bp["high_pct"]), f"{_num_or_dash(c_bp['high_count'])} 次"),
                  ("偏低", _pct_or_dash(c_bp["low_pct"]), f"{_num_or_dash(c_bp['low_count'])} 次"),
                  ("≥160／<90 mmHg", f"{c_bp['crisis_count'] + c_bp['crisis_low_count']} 次",
                   _join_dates_capped(_crit_dates_sorted, max_n=3))],
        # 2026-08-13新增（情境3）：分布圖只描述「本月」自己的讀數，跟候選池
        # (本月vs基線比較)不同，這裡只看本月密度，不牽涉基線——密度計算
        # 沿用_coverage_ratio()同一套標準(門檻/分母定義都一致)。
        "sample_limited": bool((_coverage_ratio(current_df, "systolic_am") or 0) < _SAMPLE_LIMITED_RATIO),
    }
    # 2026-08-06改法：使用者反映天數/次數被畫成同一個數字，但同一天可能
    # 量測不只一次，天跟次應該分開算——改成以「次數」為主(_spo2_reading_
    # tiers()直接掃原始事件，每一筆讀數各自獨立判斷分層)，不再拿c_sfw的
    # 日層級統計(spo2_ok_days/spo2_low_days/spo2_critical_days，這些是先把
    # 同一天多筆讀數平均成一個值才分層)冒充次數分母。2026-08-06二次修正：
    # chip完全比照血壓分布格式(只留次數，不摻雜天數)——標題count_label
    # 仍保留「X次量測／Y天」雙單位，但底下三個chip統一只用「次」這個單位，
    # 不要「次・天」混雜寫法。
    spo2_tiers = _spo2_reading_tiers(current_events)
    has_spo2_tiers = spo2_tiers is not None
    spo2_ticks = []
    if has_spo2_tiers:
        spo2_ticks = [
            (max(0, min(100, spo2_tiers["ok_pct"])), "94"),
            (max(0, min(100, spo2_tiers["ok_pct"] + spo2_tiers["mid_pct"])), "90"),
        ]
    spo2_ok_pct = spo2_tiers["ok_pct"] if has_spo2_tiers else NO_DATA
    spo2_mid_pct = spo2_tiers["mid_pct"] if has_spo2_tiers else NO_DATA
    spo2_critical_pct = spo2_tiers["critical_pct"] if has_spo2_tiers else NO_DATA
    spo2_critical_dates = [_fmt_md(d) for d in spo2_tiers["critical_detail"]] if has_spo2_tiers else []
    c_sfw = current["obs"]["spo2_food_water"]
    spo2_distribution = {
        "count_label": f"{_spo2_reading_count(current_events)} 次量測／{c_sfw['total']} 天",
        "segments": [(_pct_num(spo2_ok_pct), "#639922"), (_pct_num(spo2_mid_pct), "#EF9F27"),
                     (_pct_num(spo2_critical_pct), "#D85A30"), (0, "#A32D2D")],
        "ticks": spo2_ticks,
        "chips": [("≥95%", _pct_or_dash(spo2_ok_pct),
                   f"{spo2_tiers['ok_count']} 次" if has_spo2_tiers else None),
                  ("<94%", _pct_or_dash(spo2_mid_pct),
                   f"{spo2_tiers['mid_count']} 次" if has_spo2_tiers else None),
                  ("<90%", f"{_num_or_dash(spo2_tiers['critical_count'] if has_spo2_tiers else NO_DATA)} 次",
                   _join_dates_capped(spo2_critical_dates, max_n=3))],
    }

    # 2026-08-07新增、2026-08-12改版：「本月量測分布」把血氧分布區域換成
    # 脈搏分布（依醫師會議確認），五級門檻極低<50／偏低[50,60)／正常
    # [60,101)／偏高[101,120)／極高>=120。色塊仍是五段，但2026-08-12
    # 使用者要求呈現格式改成統一比照收縮壓分布的四欄——極低跟極高兩個
    # 極端層合併成一個「危急」風格的chip（沿用血壓危急chip「≥上限／<下限」
    # 的標籤格式，這裡是「≥120／<50 次/分」），次數相加、日期合併排序；
    # 偏低/正常/偏高維持三個各自獨立的「%為主、次數為輔」chip，四個chip
    # 排列順序也比照血壓分布(目標內/偏高/偏低/危急)：正常/偏高/偏低/危急。
    pulse_tiers = _pulse_reading_tiers(current_events)
    has_pulse_tiers = pulse_tiers is not None
    pulse_ticks = []
    if has_pulse_tiers:
        _p_very_low = pulse_tiers["very_low_pct"]
        _p_low = pulse_tiers["low_pct"]
        _p_ok = pulse_tiers["ok_pct"]
        _p_high = pulse_tiers["high_pct"]
        pulse_ticks = [
            (max(0, min(100, _p_very_low)), "50"),
            (max(0, min(100, _p_very_low + _p_low)), "60"),
            (max(0, min(100, _p_very_low + _p_low + _p_ok)), "101"),
            (max(0, min(100, _p_very_low + _p_low + _p_ok + _p_high)), "120"),
        ]
    pulse_very_low_pct = pulse_tiers["very_low_pct"] if has_pulse_tiers else NO_DATA
    pulse_low_pct = pulse_tiers["low_pct"] if has_pulse_tiers else NO_DATA
    pulse_ok_pct = pulse_tiers["ok_pct"] if has_pulse_tiers else NO_DATA
    pulse_high_pct = pulse_tiers["high_pct"] if has_pulse_tiers else NO_DATA
    pulse_very_high_pct = pulse_tiers["very_high_pct"] if has_pulse_tiers else NO_DATA
    _pulse_crit_dates_sorted = (
        sorted(set(pulse_tiers["very_low_detail"]) | set(pulse_tiers["very_high_detail"]))
        if has_pulse_tiers else []
    )  # ISO格式(YYYY-MM-DD)字串排序即為正確時序，不用像血壓那樣額外轉數值排序
    _pulse_crit_dates_fmt = [_fmt_md(d) for d in _pulse_crit_dates_sorted]
    _pulse_crit_count = (
        pulse_tiers["very_low_count"] + pulse_tiers["very_high_count"] if has_pulse_tiers else NO_DATA
    )
    pulse_distribution = {
        "count_label": f"{_pulse_reading_count(current_events)} 次量測／{current['header']['hr_days']} 天",
        "segments": [(_pct_num(pulse_very_low_pct), "#D85A30"), (_pct_num(pulse_low_pct), "#EF9F27"),
                     (_pct_num(pulse_ok_pct), "#639922"), (_pct_num(pulse_high_pct), "#EF9F27"),
                     (_pct_num(pulse_very_high_pct), "#D85A30")],
        "ticks": pulse_ticks,
        "chips": [("正常", _pct_or_dash(pulse_ok_pct),
                   f"{pulse_tiers['ok_count']} 次" if has_pulse_tiers else None),
                  ("偏高", _pct_or_dash(pulse_high_pct),
                   f"{pulse_tiers['high_count']} 次" if has_pulse_tiers else None),
                  ("偏低", _pct_or_dash(pulse_low_pct),
                   f"{pulse_tiers['low_count']} 次" if has_pulse_tiers else None),
                  ("≥120／<50 次/分", f"{_num_or_dash(_pulse_crit_count)} 次",
                   _join_dates_capped(_pulse_crit_dates_fmt, max_n=3))],
        "sample_limited": bool((_coverage_ratio(current_df, "heart_rate") or 0) < _SAMPLE_LIMITED_RATIO),
    }

    b1, c1 = baseline["signals"]["block1"], current["signals"]["block1"]
    b_bp = baseline["obs"]["bp"]

    # ── 卡片趨勢註記：2026-07-31改用真實逐日型態描述，不再是寫死的固定句子。
    # 只描述current_df（本月）的型態——「集中在哪段時間」對baseline這種橫跨
    # 前三月的長區間沒有意義，趨勢敘述天生就是「這個月」尺度的東西，
    # baseline/current的量值比較交給各卡片自己的chip處理。────────────────
    # 2026-08-11改法：trend_note從單一字串改成條列式清單(PM要求，見
    # report_html_v9.py的_four_aspect_card()——清單只有1項時渲染成單行
    # 文字不加bullet，2項以上才逐項條列)。這裡不再用「；」把子句黏成一句，
    # 直接組成清單，report_html_v9.py自己決定怎麼呈現。
    water_flags_c = _water_short_flags(current_df)
    defecation_flags_c = _defecation_special_flags(current_df)
    water_phrase = _trend_phrase(current_df, water_flags_c, "飲水未達標")
    defecation_phrase = _trend_phrase(current_df, defecation_flags_c, "排便異常")
    card2_parts = [p for p in (water_phrase, defecation_phrase) if p]
    card2_trend = (
        card2_parts if card2_parts else ["本月飲水與排便狀況皆在合理範圍內"]
    ) + _incontinence_items(baseline["events"], current["events"])

    temp_flags_c = _temp_abnormal_flags(current_df, current["signals"]["block3"])
    wander_flags_c = _code_day_flags(current_df, {"wander"})
    mental_flags_c = _code_day_flags(current_df, _MENTAL_CODES)
    card3_parts = [p for p in (
        _trend_phrase(current_df, temp_flags_c, "體溫偏離基線"),
        _trend_phrase(current_df, wander_flags_c, "遊走"),
        _trend_phrase(current_df, mental_flags_c, "精神不佳"),
    ) if p]
    card3_trend = card3_parts if card3_parts else ["本月體溫、遊走、精神狀態皆無明顯異常"]

    # 衰弱卡：體重用既有減輕百分比（跟_candidate_pool同一組門檻，>=5%算顯著）；
    # 進食量/衛生協助項目都用「本月平均比前三月平均」的相對變化，不是直接比較
    # 原始天數/次數（baseline通常橫跨90天，原始數字比較一樣會被期間長度誤導，
    # 跟夜間如廁次數原本的bug是同一個道理）。
    b_hwt, c_hwt = baseline["obs"]["hr_weight_temp"], current["obs"]["hr_weight_temp"]
    if c_hwt["weight_now"] != NO_DATA and b_hwt["weight_6mo"] not in (NO_DATA, None, 0):
        weight_delta_pct = round((c_hwt["weight_now"] - b_hwt["weight_6mo"]) / b_hwt["weight_6mo"] * 100, 1)
        weight_phrase = (
            f"體重顯著減輕（{weight_delta_pct}%）" if weight_delta_pct <= -5
            else f"體重變化在正常範圍內（{weight_delta_pct}%）"
        )
    else:
        weight_phrase = "體重資料不足以判讀趨勢"

    b_food, c_food = baseline["obs"]["spo2_food_water"]["food_short_days"], current["obs"]["spo2_food_water"]["food_short_days"]
    if c_food != NO_DATA and b_food != NO_DATA and len(baseline_df) and len(current_df):
        b_food_rate, c_food_rate = b_food / len(baseline_df), c_food / len(current_df)
        if c_food_rate > b_food_rate * (1 + _TREND_RATE_TOLERANCE):
            food_phrase = "進食量較前三月變差"
        elif c_food_rate < b_food_rate * (1 - _TREND_RATE_TOLERANCE):
            food_phrase = "進食量較前三月改善"
        else:
            food_phrase = "進食量維持穩定"
    else:
        food_phrase = None

    b_hygiene, c_hygiene = _monthly_avg(baseline_df, "hygiene_items"), _monthly_avg(current_df, "hygiene_items")
    if c_hygiene != NO_DATA and b_hygiene != NO_DATA:
        if c_hygiene > b_hygiene:
            hygiene_phrase = f"衛生協助項目月平均由{b_hygiene}次增加至{c_hygiene}次"
        elif c_hygiene < b_hygiene:
            hygiene_phrase = f"衛生協助項目月平均由{b_hygiene}次減少至{c_hygiene}次"
        else:
            hygiene_phrase = f"衛生協助項目月平均維持{c_hygiene}次"
    else:
        hygiene_phrase = None

    card4_trend = [p for p in (weight_phrase, food_phrase, hygiene_phrase) if p]

    # 四張卡片的主要數值(primary_before→after)底層跟候選池讀同一份資料——
    # 如果候選池已經判定某指標樣本有限(見_is_sample_limited())，這裡沿用
    # 同一個判斷(_sample_limited_by_key，在pool算完後就建好了，見上方)，
    # 卡片上也要有一致的提示，不能「其餘偏離」標了樣本有限、卡片上同一個
    # 數字卻完全沒提示。體重(衰弱卡)刻意不查表，跟候選池的除外規則一致。
    four_aspects = [
        {"title": "高血壓與心血管", "period_label": "前三月 → 本月", "accent": "#147D6F",
         "primary_label": "收縮壓達標率", "primary_before": _pct_or_dash(b1["reach_pct"]),
         "primary_after": _pct_or_dash(c1["reach_pct"]),
         "sample_limited": _sample_limited_by_key.get("bp", False),
         "acute": _acute_by_key.get("bp"),
         "chips": [("≥160 危急", _num_or_dash(b1["crisis_count"]), f"{c1['crisis_count']} 次"),
                   ("偏高比例", _pct_or_dash(b_bp["high_pct"]), _pct_or_dash(c_bp["high_pct"]))],
         "trend_note": [
             f"月均 {_num_or_dash(c_bp['am_mean'])} mmHg，較前三月"
             + ("上升" if c_bp["am_mean"] != NO_DATA and b_bp["am_mean"] != NO_DATA and c_bp["am_mean"] > b_bp["am_mean"] else "變化")
             + f"（{_num_or_dash(b_bp['am_mean'])} → {_num_or_dash(c_bp['am_mean'])}）"
         ]},
        {"title": "體液代謝與容積", "period_label": "前三月 → 本月", "accent": "#147D6F",
         "primary_label": "飲水未達標天數",
         "primary_before": f"{_num_or_dash(baseline['obs']['spo2_food_water']['water_short_days'])} 天",
         "primary_after": f"{_num_or_dash(current['obs']['spo2_food_water']['water_short_days'])} 天",
         "sample_limited": _sample_limited_by_key.get("water", False),
         "chips": [
             ("特殊排尿", f"{_num_or_dash(_urination_special_days(baseline_df))} 天",
              f"{_num_or_dash(_urination_special_days(current_df))} 天"),
             ("排便異常", f"{_num_or_dash(_defecation_special_days(baseline_df))} 天",
              f"{_num_or_dash(_defecation_special_days(current_df))} 天"),
         ],
         "trend_note": card2_trend},
        {"title": "感染監測", "period_label": "前三月 → 本月", "accent": "#147D6F",
         # 2026-08-04改法：使用者要求以體溫偏離基線為主軸（原本是精神不佳），
         # 精神不佳改列進chips——只是版面上主副欄位對調，底下的計算邏輯
         # （_mental_abnormal_days／block3的low+high天數）完全沒動。
         "primary_label": "體溫偏離基線",
         "primary_before": f"{len(baseline['signals']['block3']['low']) + len(baseline['signals']['block3']['high'])} 天",
         "primary_after": f"{len(current['signals']['block3']['low']) + len(current['signals']['block3']['high'])} 天",
         "sample_limited": _sample_limited_by_key.get("temp", False),
         "acute": _acute_by_key.get("temp"),
         "chips": [("精神不佳", f"{_num_or_dash(_mental_abnormal_days(baseline_df))} 天",
                    f"{_num_or_dash(_mental_abnormal_days(current_df))} 天"),
                   ("遊走", _num_or_dash(_event_count(baseline["events"], "遊走")),
                    f"{_event_count(current['events'], '遊走')} 次")],
         "trend_note": card3_trend},
        {"title": "衰弱與功能", "period_label": "長期趨勢・對標 Fried", "accent": "#D85A30",
         # 「六個月」跟其餘三張卡的「前三月」是不同的比較窗口——這是衰弱卡的設計本意
         # (長期趨勢)，目標還是六個月，不是隨資料長度隨便換。做法B：不新增資料合約
         # 欄位，直接用weight_baseline_df（擴充窗口，最多6個月，見build_v9_view_model()
         # 開頭）裡「最早」一筆體重讀數(weight_6mo)當左值；標籤則用
         # _weight_span_label() 動態判斷——資料真的涵蓋六個月就顯示「六個月」，
         # 不足六個月是例外情況，改如實顯示實際月數/天數，不再謊報做不到的時間長度。
         # 2026-08-05修正：這裡改吃weight_baseline_df不是一般的baseline_df——
         # baseline_df現在只有3個月，如果標籤還讀它，會跟weight_6mo實際取值的
         # 6個月窗口對不起來（例如數字明明來自6個月前，標籤卻可能因為baseline_df
         # 只有3個月資料而錯誤顯示「近3個月」）。
         "primary_label": _weight_span_label(weight_baseline_df, current_df),
         "primary_before": _num_or_dash(baseline["obs"]["hr_weight_temp"]["weight_6mo"]),
         "primary_after": f"{_num_or_dash(current['obs']['hr_weight_temp']['weight_now'])} kg",
         "chips": [("進食量",
                    "偏少" if baseline["obs"]["spo2_food_water"]["food_short_days"] not in (0, NO_DATA) else "正常",
                    "偏少" if current["obs"]["spo2_food_water"]["food_short_days"] not in (0, NO_DATA) else "正常"),
                   ("衛生協助項目", f"{_num_or_dash(_monthly_avg(baseline_df, 'hygiene_items'))} 次",
                    f"{_num_or_dash(_monthly_avg(current_df, 'hygiene_items'))} 次")],
         "trend_note": card4_trend},
    ]

    if insufficient_baseline:
        # 情境1覆蓋：四張卡片改成「只有本月數值」的單值呈現(primary_before/
        # chips前值一律None，report_html_v9.py的_four_aspect_card()已支援
        # 省略箭頭)，period_label改成如實反映實際累積天數，trend_note拿掉
        # 跟基線比較的敘述、改成單純的本月狀態描述。card2_trend(飲水/排便/
        # 尿失禁/大便失禁)、mental_flags_c/wander_flags_c、_monthly_avg()
        # 這些子計算本來就只讀current_df，跟有沒有基線無關，直接沿用。
        no_baseline_period = _no_baseline_period_label(data_span_days)

        sys_level = _abs_four_tier_level(c_bp["am_mean"], BP_SYS_CRISIS_LOW, BP_SYS_LOW, BP_SYS_TARGET, BP_CRISIS)
        card1_trend = (
            [f"月均 {_int_or_dash(c_bp['am_mean'])} mmHg，整體{sys_level}"] if sys_level
            else ["本月收縮壓在正常範圍內"]
        )

        temp_flags_abs_c = _temp_abnormal_flags_abs(current_df)
        card3_parts_abs = [p for p in (
            _trend_phrase(current_df, temp_flags_abs_c, "體溫異常"),
            _trend_phrase(current_df, wander_flags_c, "遊走"),
            _trend_phrase(current_df, mental_flags_c, "精神不佳"),
        ) if p]
        card3_trend_abs = card3_parts_abs if card3_parts_abs else ["本月體溫、遊走、精神狀態皆無明顯異常"]

        # 體重・BMI：有身高(BodyMetrics.heightCm，見data_parser.py)才能算BMI；
        # 沒有身高時退化成直接顯示本月平均體重（使用者2026-08-12決定：
        # 「如果沒有身高的話，資料不足時我覺得可以直接顯示平均體重即可」）。
        # 身高是成人幾乎不變的量，current_df當月剛好沒有BodyMetrics紀錄時
        # 退而找baseline_df/weight_baseline_df裡任一筆。
        height_cm = (
            current_df.attrs.get("height_cm")
            or baseline_df.attrs.get("height_cm")
            or weight_baseline_df.attrs.get("height_cm")
        )
        weight_now = c_hwt_q["weight_now"]
        bmi = (
            round(weight_now / ((height_cm / 100) ** 2), 1)
            if height_cm and weight_now not in (NO_DATA, None) else None
        )
        bmi_level = _abs_bmi_level(bmi)
        hygiene_now = _monthly_avg(current_df, "hygiene_items")
        card4_trend_abs = []
        if bmi is not None:
            card4_chips_abs = [
                ("BMI", None, f"{bmi}（{bmi_level}）"),
                ("衛生協助項目", None, f"{_num_or_dash(hygiene_now)} 次"),
            ]
            card4_primary_label = "體重"
            card4_primary_after = f"{_num_or_dash(weight_now)} kg"
            card4_trend_abs.append(f"本月體重{_num_or_dash(weight_now)}kg，BMI {bmi}屬{bmi_level}")
        else:
            avg_weight = (
                round(current_df["weight"].dropna().mean(), 1)
                if "weight" in current_df and current_df["weight"].notna().any() else None
            )
            card4_chips_abs = [("衛生協助項目", None, f"{_num_or_dash(hygiene_now)} 次")]
            card4_primary_label = "體重・月平均"
            card4_primary_after = f"{_num_or_dash(avg_weight)} kg" if avg_weight is not None else "—"
            if avg_weight is not None:
                card4_trend_abs.append(f"本月體重月平均{avg_weight}kg")
        if hygiene_now not in (NO_DATA, None):
            card4_trend_abs.append(f"衛生協助項目本月{hygiene_now}次")
        if not card4_trend_abs:
            card4_trend_abs = ["本月體重與衛生協助狀況資料不足"]

        four_aspects = [
            {"title": "高血壓與心血管", "period_label": no_baseline_period, "accent": "#147D6F",
             "primary_label": "收縮壓達標率", "primary_before": None,
             "primary_after": _pct_or_dash(c1["reach_pct"]),
             "chips": [("≥160 危急", None, f"{c1['crisis_count']} 次"),
                       ("偏高比例", None, _pct_or_dash(c_bp["high_pct"]))],
             "trend_note": card1_trend},
            {"title": "體液代謝與容積", "period_label": no_baseline_period, "accent": "#147D6F",
             "primary_label": "飲水未達標天數", "primary_before": None,
             "primary_after": f"{_num_or_dash(current['obs']['spo2_food_water']['water_short_days'])} 天",
             "chips": [
                 ("特殊排尿", None, f"{_num_or_dash(_urination_special_days(current_df))} 天"),
                 ("排便異常", None, f"{_num_or_dash(_defecation_special_days(current_df))} 天"),
             ],
             "trend_note": card2_trend},
            {"title": "感染監測", "period_label": no_baseline_period, "accent": "#147D6F",
             "primary_label": "體溫異常天數", "primary_before": None,
             "primary_after": f"{sum(temp_flags_abs_c)} 天",
             "chips": [("精神不佳", None, f"{_num_or_dash(_mental_abnormal_days(current_df))} 天"),
                       ("遊走", None, f"{_event_count(current['events'], '遊走')} 次")],
             "trend_note": card3_trend_abs},
            {"title": "衰弱與功能", "period_label": no_baseline_period, "accent": "#D85A30",
             "primary_label": card4_primary_label, "primary_before": None,
             "primary_after": card4_primary_after,
             "chips": card4_chips_abs,
             "trend_note": card4_trend_abs},
        ]

    # ── 每日狀態與偶發事件：2026-07-31改用真實資料（見模組上方說明）───────
    # 「精神/情緒/睡眠」三列讀Behavior行為代碼、「排泄」列讀Defecation.status
    # 異常天數，都只看current_df（本月），跟daily_status本來就是「本月」
    # 格子的設計一致，不做前三月對照（前三月對照已經在四個面向卡的chips
    # 做了）。2026-08-04改法：格子總數改用「本月首末實際日期展開的完整日曆
    # 天清單」(_full_calendar_dates)，不是單純「有資料的天數」——使用者用
    # 四個月缺資料的情境實測，抓到原本缺測天會被悄悄跳過、格子數縮水、
    # 日期跟著錯位；改成缺測天照樣佔一格、格子留空白代表「當天未測」，
    # 醫師才看得出跟「測了且正常」的差別。
    dates = _full_calendar_dates(current_df) or [""]
    days = len(dates)

    mental_flags = _code_day_flags(current_df, _MENTAL_CODES, dates)
    mood_flags = _code_day_flags(current_df, _MOOD_CODES, dates)
    sleep_flags = _code_day_flags(current_df, _SLEEP_CODES, dates)
    excretion_flags = _defecation_special_flags(current_df, dates)

    # 偏離集中期：2026-08-03修正——原本用「四列合併訊號」判斷集中期，會導致
    # 某一列只是零星1-2天異常（自己根本沒有連續型態），卻因為「同一天剛好
    # 落在別列造成的集中期範圍內」被連帶標成橘色。使用者實測抓到：情緒/
    # 睡眠列各只有孤立1天異常，卻顯示橘色。改成每一列各自獨立判斷集中期
    # （只看這一列自己的逐日flag，None當天無資料視為非異常），格子橘色只
    # 反映「這一列自己」有沒有連續達標——不會再被別列影響。頂部橘紅線則是
    # 四列各自集中期區段的聯集（可能不只一段，且可能只涵蓋部分列），仍然
    # 可以不只一條。
    mental_cluster = _detect_cluster_days(mental_flags)
    mood_cluster = _detect_cluster_days(mood_flags)
    sleep_cluster = _detect_cluster_days(sleep_flags)
    excretion_cluster = _detect_cluster_days(excretion_flags)

    def _status_row(label, flags, cluster_marked, note_word):
        n = sum(1 for f in flags if f)
        return {"label": label, "colors": _row_colors(flags, cluster_marked), "note": f"{note_word} {n}次"}

    all_cluster_marked = [
        any(day) for day in zip(mental_cluster, mood_cluster, sleep_cluster, excretion_cluster)
    ]
    cluster_segments = _cluster_segments(all_cluster_marked)

    daily_status = {
        "days": days,
        "cluster_segments": cluster_segments,
        "cluster_label": _cluster_label(cluster_segments, dates),
        "rows": [
            _status_row("精神", mental_flags, mental_cluster, "異常"),
            _status_row("情緒", mood_flags, mood_cluster, "異常"),
            _status_row("睡眠", sleep_flags, sleep_cluster, "白天睡多"),
            _status_row("排泄", excretion_flags, excretion_cluster, "異常"),
        ],
        "events_row": _events_row(current_df, dates),
        "date_ticks": _date_ticks(dates),
    }

    ch = current["header"]

    # 2026-08-12新增（情境3）：紀錄完整度每一項的資料密度(本月或基線任一
    # 低於1/4門檻)提示——體重刻意不套用(見_is_sample_limited()說明)。
    # 情境1(insufficient_baseline)時baseline_df本身通常是空的/極稀疏，每項
    # 幾乎都會判定樣本有限，但那個狀態已經在上方標題/查核說明顯性交代過
    # 「尚無個人基線」，這裡不需要再逐項重複貼標籤造成版面雜訊，改成不
    # 附加badge，直接顯示原始次數即可。
    def _completeness_row(label, val, column):
        if insufficient_baseline or not _is_sample_limited(baseline_df, current_df, column):
            return (label, val, False)
        return (label, val, False, "樣本有限")

    # 2026-08-28修正：使用者反映「血壓30/30天・90次」的「90」容易被誤認
    # 跟90天基線有關(其實純屬本月每天測3次的巧合)，且天/次單位混用不一致
    # （血壓/服藥用「天」、部分項目又混著「次」）。統一成：除了服藥・照護
    # 仍以「天」為單位（服藥是每天固定的行為，天數本身才有意義），其餘
    # 生理量測類全部改成「次」（實際量測次數），跟本月量測分布圖的
    # count_label同一套算法(_temp_reading_count/_pulse_reading_count/
    # _spo2_reading_count直接數原始事件筆數，不是聚合過的天數)，避免同一份
    # 報告裡「天」「次」意義各自表述。標題旁另外補上本月實際日期區間
    # （completeness_period），讓「30天」對應到哪一段日期一目了然。
    check_notes = {
        "remaining_deviations": remaining_deviations,
        "completeness_period": _current_range_label(current_df),
        "completeness": [
            _completeness_row("血壓", f"{ch['bp_count']} 次", "systolic_am"),
            _completeness_row("體溫", f"{_temp_reading_count(current_events)} 次", "temperature"),
            _completeness_row("心率", f"{_pulse_reading_count(current_events)} 次", "heart_rate"),
            _completeness_row("血氧", f"{_spo2_reading_count(current_events)} 次", "spo2"),
            ("體重", ch["weight_note"] if ch["weight_note"] != NO_DATA else "—", False),
            ("服藥・照護", f"{ch['care_days']}/{days} 天", False),
        ],
        # 2026-08-05改法：原本「個人基線」只顯示天數、「跨月比較」是完全沒
        # 意義的固定字「同期對照」——主管原始設計稿裡這兩欄要呈現的其實是
        # 「個人基線區間設定」（基線實際涵蓋哪段日期）跟「跨月對照方式」
        # （比較方法本身），「基線回溯月數」當時特別標注「待設定」。
        # 2026-08-05再修正：「跨月比較」原本改成寫死「本月 vs 前3個月
        # （體重採6個月）」，但資料不足時（剛收案、baseline不到3個月）
        # 這句話會跟實際情況不符，改成_compare_method_label()依實際涵蓋
        # 天數動態產生，資料足夠才顯示目標值、不足如實顯示實際值。
        "baseline_todo": [
            ("個人基線", _baseline_range_label(baseline_df)),
            ("跨月比較", _compare_method_label(baseline_df, current_df)),
        ],
        "reference_pending": "參考值由　　　醫師／　　　設定",
    }

    if insufficient_baseline:
        # 情境1覆蓋：「個人基線」如實說明尚未建立/尚未累積滿3個月（區分
        # 完全首月收案 vs 已有部分基線但不足3個月兩種情況）；「跨月比較」
        # 換成「比較方式」說明改採公定標準；「參考標準」條列出本月生理數值
        # 提醒(top3)沒用到的其餘標準，避免同一項標準上下重複出現(見使用者
        # 2026-08-12要求：「如果上面已經出現過的基準，下面就不要再寫一次」)。
        if len(baseline_df) == 0:
            baseline_status = "尚未建立（本月為首次收案）"
        else:
            baseline_status = f"尚未累積滿{_BASELINE_MONTHS}個月（已有{data_span_days}天資料）"
        check_notes["baseline_todo"] = [
            ("個人基線", baseline_status),
            ("比較方式", "採公定參考值判讀"),
            ("參考標準", reference_standards),
        ]

    footer = {
        "generated_date": current_df["date"].iloc[-1].replace("-", "/") if len(current_df) else "-",
        # 2026-08-05改法：原本disclaimer是一整段沒有分段的長句子、source_note
        # 又把「資料來源」跟兩則不相干的技術性附註用「｜」硬接在一起，使用者
        # 反映排版很亂。改成拆成獨立欄位，report_html_v9.py分行呈現、並把
        # 「未進行病因推論...」這句臨床責任邊界的關鍵句加粗——這是免責聲明裡
        # 最重要的一句，值得跟其餘說明文字有視覺區隔。
        "disclaimer_intro": "本頁為照護紀錄之彙整與統計，依設定參考值挑出偏離紀錄並標示同時發生的項目；",
        "disclaimer_key": "未進行病因推論，不判定疾病、嚴重程度或處置方式，所有臨床判斷由醫師為之。",
        "disclaimer_note": "所有數值取自本頁，可逐項核對。",
        "data_source": "照護隨行本（生理數值為機構量測，生活照護為照服員每日觀察）",
    }

    return {
        "header": header, "top_summary": top_summary, "top3": top3,
        "bp_distribution": bp_distribution, "spo2_distribution": spo2_distribution,
        "pulse_distribution": pulse_distribution,
        "four_aspects": four_aspects, "daily_status": daily_status,
        "check_notes": check_notes, "footer": footer,
    }
