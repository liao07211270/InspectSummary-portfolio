"""
Analysis layer for the v2 single-page care summary.

Produces one nested dict consumed by report_pdf.py and llm_summary.py:

    {
      "header":  {...completeness counts...},
      "obs":     { bp, spo2_food_water, hr_weight_temp, sleep_activity_social },
      "signals": { block1, block2, block3, block4 },
    }

第一部分・全數據觀察 (obs)  → left column, neutral data
第二部分・疾病訊號   (signals) → right column, 4 disease modules
"""

import numpy as np
import pandas as pd
from scipy import stats

from .mock_data import (
    WEIGHT_NOW, WEIGHT_LAST_MONTH, WEIGHT_6MO_AGO, DRY_WEIGHT, TEMP_BASELINE,
)

# ── 資料不足標記 ─────────────────────────────────────────────────────────────
NO_DATA = "__NO_DATA__"

def _has_data(series) -> bool:
    """檢查 pandas Series 是否有至少一筆非 null 的值"""
    return bool(series.notna().any())

# ── Thresholds ──────────────────────────────────────────────────────────────
# 2026-08-03：逐項查證過外部文獻依據，整理在 docs/thresholds-references.md，
# 這裡只在有查到依據的常數旁邊補引用，沒查到依據的維持原樣不動（不確定的
# 東西不該假裝有依據）。
BP_DIA_TARGET = 90           # 2017 ACC/AHA高血壓指引：舒張壓>=90 mmHg為第二期高血壓
# 2026-08-07依醫師會議確認，收縮壓改為五級分段：
# 危急(<90) / 偏低([90,100)) / 正常([100,130)) / 偏高([130,160)) / 危急(>=160)
# 兩端「危急」在畫面上合併顯示（同色、同一個chip），「偏低」則是獨立顯示的
# 新增橘色區塊，不再併入危急——這是跟先前(2026-08-05)把低血壓/危象合併
# 顯示的版本不同的地方，先前只有兩級(低血壓/危象)，現在中間多了一個
# 「偏低」的過渡級距。
BP_SYS_CRISIS_LOW = 90       # <90 危急（低）——收縮壓<90 mmHg是最廣泛採用的臨床低血壓定義
                              # （2026-08查證，見docs/thresholds-references.md）
BP_SYS_LOW    = 100          # [90,100) 偏低——90/100/130/160四個門檻皆為醫師會議確認值
BP_SYS_TARGET = 130          # [100,130) 正常／[130,160) 偏高的分界
BP_CRISIS     = 160          # >=160 危急（高）
HR_LOW        = 50           # 低心跳
# 2026-08-07新增：v9「本月量測分布」脈搏五級分布圖專用門檻（依醫師會議
# 確認值），跟上面HR_LOW是v2既有candidate pool用的舊常數，語意不同、
# 刻意不共用，比照BP_SYS_LOW系列同樣的命名邏輯（半開區間左閉右開）。
PULSE_VERY_LOW  = 50          # <50 極低
PULSE_LOW       = 60          # [50,60) 偏低
PULSE_HIGH      = 101         # [60,101) 正常／[101,120) 偏高的分界
PULSE_VERY_HIGH = 120         # >=120 極高
# 2026-08-12新增：舒張壓五級門檻，供情境1(無個人基線時)「整體偏高/偏低」
# 絕對值判斷用（使用者提供）。跟既有的BP_DIA_TARGET(=90，基線相關候選池
# 用的單一「達標上限」門檻)是不同用途、不同語意，刻意不合併/不共用，
# 比照BP_SYS_*系列同樣的命名邏輯（半開區間左閉右開）。
DIA_CRISIS_LOW = 60           # <60 危急（低）
DIA_LOW        = 65           # [60,65) 偏低
DIA_TARGET     = 80           # [65,80) 正常／[80,100) 偏高的分界
DIA_CRISIS     = 100          # >=100 危急（高）
SPO2_LOW      = 94           # 血氧偏低門檻——常見血氧篩檢門檻(SpO2<=94%)
SPO2_CRITICAL = 90           # 血氧危急門檻（比偏低更嚴重的第二層，v9血氧分布三色用）——
                              # 文獻上SpO2<90%屬「臨床顯著低血氧」，需要臨床處置
URINE_DARK    = 5            # 尿色 >= 5 視為偏深
TEMP_LOW_DELTA  = -1.1       # 對基線偏低（依老年感染文獻；參數）
TEMP_HIGH_DELTA = 1.1        # 對基線偏高
TEMP_HIGH_ABS   = 37.2       # 或絕對 >=37.2
# 2026-08-12新增：體溫過低的絕對值門檻，供情境1(無個人基線)使用——原本
# TEMP_LOW_DELTA是「對個人基線偏低幾度」，沒有基線時無法算；這裡是使用者
# 訂的絕對門檻，跟TEMP_HIGH_ABS(發燒)並列成一組跟基線無關的獨立判斷。
TEMP_LOW_ABS    = 36.0       # 絕對 <36.0 視為體溫過低
WEIGHT_LOSS_SIG = 5.0        # % over 6 months → 臨床顯著——Fried衰弱表型(2001)的
                              # 非刻意體重減輕診斷標準之一(追蹤期間內>=5%體重減輕)
# 2026-08-12新增：BMI分級（使用者提供），供情境1(無基線時)體重判讀替代
# 方案用——沒有前三月體重可比較時，改用BMI單次量測分級，需要身高
# (BodyMetrics.heightCm，見data_parser.py)才能算，沒有身高時無法分級。
BMI_UNDERWEIGHT     = 18.5   # <18.5 過輕
BMI_NORMAL_HIGH     = 24     # [18.5,24) 正常／[24,27) 過重的分界
BMI_OVERWEIGHT_HIGH = 27     # >=27 肥胖
MIN_TREND_DAYS  = 10         # 晨間血壓折線門檻：低於此天數不畫（原則六）
MIN_READINGS    = 10         # 達標長條最低讀數：低於此標「樣本有限」
MIN_TREND_POINTS = 3         # 趨勢斜率最低讀數（避免 <3 點迴歸得出無意義的 NaN 方向）
CV_HIGH_DELTA   = 2.0        # CV 明顯高於基線的差距門檻（原則五；參數）

# ── D1（醫師確認中，待定案）：候選池排序用的各指標輕/中/重度門檻 ────────────
# v9 討論清單 PART1 第1題：不同指標的偏離幅度不能直接比較原始百分比（血壓偏離15%
# 跟體重減輕5%臨床意義不同），改為每個指標各自分級，分級只決定排序，呈現仍用原始
# 數值。門檻數字大部分仍是佔位值，等醫師確認後應改成設定檔/參數，不要留在程式碼
# 常數——2026-08-03查證結果詳見docs/thresholds-references.md，摘要如下：
#   有方法論文獻支持的中度門檻：bp_reach_gap_pct/diastolic_pct的25%——遠端照護
#   監測文獻常引用25%變化幅度當通用臨床警示門檻；輕度/重度是內插值，無直接文獻。
#   完全查無依據、純工程判斷：weight_pct以外的其餘天數佔比類門檻(food/water/
#   temp_pct)、hr_delta的絕對bpm分級——這些數字仍待醫師確認，不是有依據的定案值。
SEVERITY_THRESHOLDS = {
    "bp_crisis_pct":     {"mild": 1,   "moderate": 5,   "severe": 15},   # 危象讀數佔比 %（無外部文獻依據）
    "bp_reach_gap_pct":  {"mild": 10,  "moderate": 25,  "severe": 40},   # 達標率距100%落差（中度25%有方法論
                                                                          # 文獻支持，見上方說明；輕度/重度無）
    "weight_pct":        {"mild": 3,   "moderate": 5,   "severe": 8},    # 體重變化幅度 %（絕對值，無外部文獻依據）
    "temp_delta":        {"mild": 0.5, "moderate": 1.1, "severe": 2.0},  # 對基線偏離 °C（絕對值，無外部文獻依據）
    "spo2_low_pct":      {"mild": 10,  "moderate": 25,  "severe": 40},   # 血氧偏低天數佔比 %（無外部文獻依據）
    "hr_delta":          {"mild": 10,  "moderate": 15,  "severe": 20},   # 心率變化 bpm（絕對值，2026-08-07依醫師
                                                                          # 會議確認修正，原本輕/中度為5/10，改為
                                                                          # 現值；仍屬臨床設定值，非外部文獻依據）
    "food_short_pct":    {"mild": 15,  "moderate": 30,  "severe": 50},   # 進食不足天數佔比 %（無外部文獻依據——查到
                                                                          # 的是「單次進食量佔需求量%」的GLIM營養不良
                                                                          # 篩檢標準，跟這裡的「天數頻率」是不同指標）
    "water_short_pct":   {"mild": 15,  "moderate": 30,  "severe": 50},   # 飲水未達標天數佔比 %（無外部文獻依據，理由同上）
    "temp_pct":          {"mild": 15,  "moderate": 30,  "severe": 50},   # 體溫偏離基線天數佔比 %（v9候選池專用，
                                                                          # 跟temp_delta是不同東西——temp_delta是v2既有
                                                                          # candidate pool用的°C絕對值門檻，不要搞混；
                                                                          # 無外部文獻依據）
    "diastolic_pct":     {"mild": 10,  "moderate": 25,  "severe": 40},   # 舒張壓平均值變化幅度 %（v9候選池專用，
                                                                          # 比照收縮壓的bp_reach_gap_pct門檻數值，中度
                                                                          # 25%同樣有方法論文獻支持，見上方說明）
    # 2026-08-19新增（情境4：生理數值穩定但生活照護逐步變差）：候選池不能
    # 只看生理量測，排便異常/精神不佳/白天睡多這三項生活照護天數頻率指標
    # 也要能跟血壓/血氧等一起競爭top3——沿用food/water_short_pct同一組
    # 15/30/50佔位值（同樣無外部文獻依據，待醫師確認）。
    "defecation_pct":    {"mild": 15,  "moderate": 30,  "severe": 50},   # 排便異常天數佔比 %（無外部文獻依據）
    "mental_pct":        {"mild": 15,  "moderate": 30,  "severe": 50},   # 精神不佳天數佔比 %（無外部文獻依據）
    "sleep_pct":         {"mild": 15,  "moderate": 30,  "severe": 50},   # 白天睡多天數佔比 %（無外部文獻依據）
}


def _severity_level(kind, magnitude):
    """依 magnitude(絕對值) 對照 SEVERITY_THRESHOLDS[kind]，回傳 重度/中度/輕度/None。
    None 表示未達最低門檻——不算偏離，候選池排序時會被排除（見 _select_top_signals）。"""
    if magnitude is None or kind not in SEVERITY_THRESHOLDS:
        return None
    t = SEVERITY_THRESHOLDS[kind]
    magnitude = abs(magnitude)
    if magnitude >= t["severe"]:
        return "重度"
    if magnitude >= t["moderate"]:
        return "中度"
    if magnitude >= t["mild"]:
        return "輕度"
    return None


_SEVERITY_RANK = {"重度": 3, "中度": 2, "輕度": 1, None: 0}


# ── D2（醫師確認中，待定案）：偏離方向性 ────────────────────────────────────
# v9 討論清單 PART1 第2題：好轉但未達標的項目不該跟真正惡化的項目混在一起。
# 目前用「觀察期內首尾值變化」當方向的替代指標——系統目前沒有 v9 設計採用的
# 「本月 vs 前三月同期」歷史基準資料源，等該資料源就緒後，這裡的 delta 來源
# 應改成「本月比前三月」而非期間內首尾差。
def _direction_label(delta, worsening_if_positive=True):
    """delta：現值相對基準/期初的變化量。worsening_if_positive=False 表示數值
    變小才是惡化方向（例如體重下降）。delta 為 0 或缺值時不標方向。"""
    if delta is None or (isinstance(delta, float) and np.isnan(delta)) or delta == 0:
        return None
    is_worsening = (delta > 0) if worsening_if_positive else (delta < 0)
    return "惡化" if is_worsening else "改善中"


def _trend_note(direction, severity):
    """D2 文字規則：好轉但未達標時要明講「已改善，但仍未達參考值內」，
    不能只留數字讓讀者自己判斷（v9 討論清單建議）。"""
    if direction == "改善中":
        return "已改善，但仍未達參考值內" if severity else "已改善且回到參考值內"
    if direction == "惡化":
        return "持續惡化"
    return None


def _weight_context(df):
    """體重錨點：真實量測資料（由 data_parser 標記 is_real_data）用實際送來的
    最早／最新讀數當基準；模擬資料（mock_data pipeline）沿用固定的 demo 個案常數。
    """
    if df.attrs.get("is_real_data"):
        now = df.attrs.get("weight_now")
        base = df.attrs.get("weight_baseline")
        count = df.attrs.get("weight_reading_count", 0)
        return {
            "weight_now": now if now is not None else NO_DATA,
            "weight_baseline": base if base is not None else NO_DATA,
            "dry_weight": df.attrs.get("dry_weight"),        # 簡易版無此欄位，保持 None
            "temp_baseline": df.attrs.get("temp_baseline") or TEMP_BASELINE,
            "has_enough": count >= 2,
            "is_real": True,
        }
    return {
        "weight_now": WEIGHT_NOW, "weight_baseline": WEIGHT_6MO_AGO,
        "dry_weight": DRY_WEIGHT, "temp_baseline": TEMP_BASELINE,
        "has_enough": True, "is_real": False,
    }


def _fmt_md(iso: str) -> str:
    """2025-11-20 → 11/20"""
    p = iso.split("-")
    return f"{int(p[1])}/{int(p[2])}"


def _slope_dir(slope, thr=0.2):
    return "緩升" if slope > thr else "緩降" if slope < -thr else "平穩"


def _cv(values):
    v = [x for x in values if x is not None and not np.isnan(x)]
    if len(v) < 2:
        return 0.0
    mu = float(np.mean(v))
    sd = float(np.std(v, ddof=1))
    return round(sd / mu * 100, 1) if mu else 0.0


def _longest_streak(flags):
    """Longest run of True in a boolean iterable."""
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best


# ── 第一部分・全數據觀察 ────────────────────────────────────────────────────
def _obs_bp(df):
    am  = df["systolic_am"].tolist()
    pm  = df["systolic_pm"].tolist()
    eve = df["systolic_eve"].tolist()
    # TIR (達標分布) is reading-based over ALL systolic readings (晨+午+晚), per §5.1
    all_sys = [v for v in (am + pm + eve) if v is not None and not np.isnan(v)]
    n_readings = len(all_sys)

    # 整段觀察期完全沒有血壓量測 → 顯性退場（原則六），不可硬算
    if n_readings == 0:
        return {
            "total_count": 0, "measured_days": 0,
            "crisis_low_pct": NO_DATA, "low_pct": NO_DATA, "ok_pct": NO_DATA, "high_pct": NO_DATA,
            "ok_count": 0, "high_count": 0,
            "low_count": 0, "low_detail": [],
            "crisis_low_count": 0, "crisis_low_detail": [],
            "crisis_count": 0, "crisis_pct": NO_DATA, "crisis_detail": [],
            "am_mean": NO_DATA, "pm_mean": NO_DATA, "eve_mean": NO_DATA,
            "cv_pct": NO_DATA, "cv_baseline": 9,
            "first_week": NO_DATA, "last_week": NO_DATA, "trend_dir": NO_DATA,
            "weekly": [], "target": BP_SYS_TARGET, "baseline": NO_DATA,
            "trend_drawable": False, "sample_limited": True,
        }

    valid_am = [v for v in am if v is not None and not np.isnan(v)]
    valid_pm = [v for v in pm if v is not None and not np.isnan(v)]
    valid_eve = [v for v in eve if v is not None and not np.isnan(v)]

    # 2026-08-07依醫師會議改為五級：兩端「危急」各自獨立計算(crisis_low/
    # crisis)，畫面上再由呼叫端(analysis_v9.py)合併顯示成同一個「危急」chip；
    # 「偏低」(low)現在專指[90,100)這個新增的過渡級距，不再是"<90"的別名
    # ——這是跟2026-08-05版本（低血壓/危象直接合併成同一級）不同的地方。
    n_crisis_low = sum(1 for v in all_sys if v < BP_SYS_CRISIS_LOW)               # 危急(低) <90
    n_low        = sum(1 for v in all_sys if BP_SYS_CRISIS_LOW <= v < BP_SYS_LOW) # 偏低 [90,100)
    n_ok         = sum(1 for v in all_sys if BP_SYS_LOW <= v < BP_SYS_TARGET)     # 正常 [100,130)
    n_crisis     = sum(1 for v in all_sys if v >= BP_CRISIS)                      # 危急(高) >=160
    n_high       = n_readings - n_crisis_low - n_low - n_ok - n_crisis            # 偏高 [130,160)

    crisis = [r for _, r in df.iterrows()
              if r["systolic_am"] is not None and not np.isnan(r["systolic_am"])
              and r["systolic_am"] >= BP_CRISIS]

    crisis_low = [r for _, r in df.iterrows()
                  if r["systolic_am"] is not None and not np.isnan(r["systolic_am"])
                  and r["systolic_am"] < BP_SYS_CRISIS_LOW]

    low = [r for _, r in df.iterrows()
           if r["systolic_am"] is not None and not np.isnan(r["systolic_am"])
           and BP_SYS_CRISIS_LOW <= r["systolic_am"] < BP_SYS_LOW]

    # weekly morning-systolic means for the trend line — even chunks so the
    # trailing partial week doesn't produce a noisy 2-day point (per rehab fix)
    n_weeks = max(2, len(am) // 7)
    weekly_raw = [float(np.nanmean(ch))
                  for ch in np.array_split(np.array(am, dtype=float), n_weeks)]
    weekly = [round(w, 1) if not np.isnan(w) else None for w in weekly_raw]
    weekly_valid = [w for w in weekly if w is not None]

    # 少於 3 個有效讀數時，線性迴歸只會得出無意義的 NaN 方向 —— 直接標資料不足
    if len(valid_am) >= MIN_TREND_POINTS:
        slope, *_ = stats.linregress(np.arange(len(valid_am)), valid_am)
        trend_dir = _slope_dir(slope)
    else:
        trend_dir = NO_DATA

    measured_days = int(df["systolic_am"].notna().sum())

    return {
        "total_count":  n_readings,          # 總讀數（分母）
        "measured_days": measured_days,
        "crisis_low_pct": round(n_crisis_low / n_readings * 100),
        "low_pct":    round(n_low / n_readings * 100),
        "ok_pct":     round(n_ok / n_readings * 100),
        "high_pct":   round(n_high / n_readings * 100),
        "ok_count":   n_ok,
        "high_count": n_high,
        "low_count":  len(low),
        "low_detail": [{"date": _fmt_md(r["date"]), "sys": int(r["systolic_am"])} for r in low],
        "crisis_low_count": len(crisis_low),
        "crisis_low_detail": [{"date": _fmt_md(r["date"]), "sys": int(r["systolic_am"])} for r in crisis_low],
        "crisis_count": len(crisis),
        "crisis_pct": round(len(crisis) / n_readings * 100, 1),
        "crisis_detail": [{"date": _fmt_md(r["date"]), "sys": int(r["systolic_am"])} for r in crisis],
        "am_mean":  round(float(np.mean(valid_am)), 0) if valid_am else NO_DATA,
        "pm_mean":  round(float(np.mean(valid_pm)), 0) if valid_pm else NO_DATA,
        "eve_mean": round(float(np.mean(valid_eve)), 0) if valid_eve else NO_DATA,
        "cv_pct":   _cv(all_sys),            # CV over all systolic readings
        "cv_baseline": 9,                    # 個人前三月基線（暫定）
        "first_week": weekly_valid[0] if weekly_valid else NO_DATA,
        "last_week":  weekly_valid[-1] if weekly_valid else NO_DATA,
        "trend_dir":  trend_dir,
        "weekly":     weekly,
        "target":     BP_SYS_TARGET,
        "baseline":   round(float(np.mean(valid_am[:7])), 0) if valid_am else NO_DATA,
        "trend_drawable": measured_days >= MIN_TREND_DAYS,   # 折線門檻（原則六）
        "sample_limited": n_readings < MIN_READINGS,         # 樣本有限（原則二）
    }


def _obs_spo2_food_water(df):
    n = len(df)
    if _has_data(df["spo2"]):
        spo2_numeric = pd.to_numeric(df["spo2"], errors="coerce")
        spo2_flags = (spo2_numeric < SPO2_LOW).fillna(False).tolist()
        spo2_low = sum(spo2_flags)
        spo2_low_days, spo2_ok_days, spo2_streak = spo2_low, n - spo2_low, _longest_streak(spo2_flags)
        # 危急天數(<90)是spo2_low_days(<94)的子集，刻意分開回傳、不動
        # spo2_low_days原本的定義——v2既有report_pdf.py用它代表「<94全部」，
        # 改了會動到既有輸出；v9血氧分布要畫三色時，自己用
        # spo2_low_days − spo2_critical_days算出中段([90,94))。
        spo2_critical_days = int(sum((spo2_numeric < SPO2_CRITICAL).fillna(False)))
        # 2026-08-06新增：v9血氧分布「<90%」chip要像血壓危急chip一樣列出實際
        # 日期，供使用者比照血壓分布的呈現方式一併核對。
        spo2_critical_detail = [
            {"date": _fmt_md(r["date"])} for _, r in df.iterrows()
            if pd.notna(r["spo2"]) and r["spo2"] < SPO2_CRITICAL
        ]
    else:
        spo2_low_days = spo2_ok_days = spo2_streak = NO_DATA
        spo2_critical_days = NO_DATA
        spo2_critical_detail = []

    # 照護觀察欄位：無資料時回傳 NO_DATA
    if _has_data(df["food_intake"]):
        food_flags = df["food_intake"].isin(["不足", "拒食"]).tolist()
        food_short = sum(food_flags)
        food_ok = n - food_short
        food_streak = _longest_streak(food_flags)
    else:
        food_short = food_ok = food_streak = NO_DATA

    if _has_data(df["water_intake"]):
        water_flags = (df["water_intake"] == "未達標").tolist()
        water_short = sum(water_flags)
        water_ok = n - water_short
        water_streak = _longest_streak(water_flags)
    else:
        water_short = water_ok = water_streak = NO_DATA

    return {
        "total": n,
        "spo2_low_days": spo2_low_days, "spo2_ok_days": spo2_ok_days,
        "spo2_critical_days": spo2_critical_days,
        "spo2_critical_detail": spo2_critical_detail,
        "spo2_streak": spo2_streak,
        "food_short_days": food_short, "food_ok_days": food_ok,
        "food_streak": food_streak,
        "water_short_days": water_short, "water_ok_days": water_ok,
        "water_streak": water_streak,
    }


def _obs_hr_weight_temp(df):
    hr = df["heart_rate"].tolist()
    valid_hr = [v for v in hr if v is not None and not np.isnan(v)]
    if valid_hr:
        hr_start = round(float(np.mean(valid_hr[:5])), 0)
        hr_end   = round(float(np.mean(valid_hr[-5:])), 0)
        hr_dir = "上升" if hr_end - hr_start > 3 else "下降" if hr_end - hr_start < -3 else "持平"
    else:
        hr_start = hr_end = hr_dir = NO_DATA

    wc = _weight_context(df)
    if wc["has_enough"]:
        weight_pct = round((wc["weight_now"] - wc["weight_baseline"]) / wc["weight_baseline"] * 100, 1)
    else:
        weight_pct = NO_DATA

    # 體脂：簡易版新欄位，沒有 mock/歷史錨點可比，直接用觀察期內首尾讀數算變化
    fat_valid = df["body_fat"].dropna().tolist() if _has_data(df["body_fat"]) else []
    if len(fat_valid) >= 2 and fat_valid[0]:
        body_fat_now = fat_valid[-1]
        body_fat_pct = round((fat_valid[-1] - fat_valid[0]) / fat_valid[0] * 100, 1)
    elif len(fat_valid) == 1:
        body_fat_now, body_fat_pct = fat_valid[0], NO_DATA
    else:
        body_fat_now = body_fat_pct = NO_DATA

    temp_baseline = wc["temp_baseline"]
    temp = df["temperature"]
    if _has_data(temp):
        low = [{"date": _fmt_md(r["date"]), "t": r["temperature"],
                "delta": round(r["temperature"] - temp_baseline, 1)}
               for _, r in df.iterrows()
               if pd.notna(r["temperature"]) and r["temperature"] - temp_baseline <= TEMP_LOW_DELTA]
        high = [{"date": _fmt_md(r["date"]), "t": r["temperature"],
                 "delta": round(r["temperature"] - temp_baseline, 1)}
                for _, r in df.iterrows()
                if pd.notna(r["temperature"])
                and (r["temperature"] - temp_baseline >= TEMP_HIGH_DELTA
                     or r["temperature"] >= TEMP_HIGH_ABS)]
        temp_cv = _cv(temp.tolist())
    else:
        low = high = []
        temp_cv = NO_DATA

    spo2 = df["spo2"].tolist()
    valid_spo2 = [v for v in spo2 if v is not None and not np.isnan(v)]
    if valid_spo2:
        spo2_start = round(float(np.mean(valid_spo2[:5])), 0)
        spo2_end   = round(float(np.mean(valid_spo2[-5:])), 0)
    else:
        spo2_start = spo2_end = NO_DATA

    return {
        "hr_start": hr_start, "hr_end": hr_end, "hr_dir": hr_dir,
        "spo2_start": spo2_start, "spo2_end": spo2_end,
        "weight_6mo": wc["weight_baseline"], "weight_now": wc["weight_now"],
        "weight_pct": weight_pct,
        "body_fat_now": body_fat_now, "body_fat_pct": body_fat_pct,
        "temp_baseline": temp_baseline, "temp_days": int(temp.notna().sum()),
        "temp_low": low, "temp_high": high,
        "temp_cv": temp_cv,
        "temp_available": _has_data(temp),
    }


def _obs_sleep_activity_social(df):
    n = len(df)

    reversal_days = int(df["day_night_reversal"].sum()) if _has_data(df["day_night_reversal"]) else NO_DATA

    if _has_data(df["rehab_attended"]):
        df2 = df.copy()
        df2["_wk"] = (pd.to_datetime(df2["date"]) - pd.to_datetime(df2["date"]).min()).dt.days // 7
        grp = df2.groupby("_wk")["rehab_attended"]
        wk_sum = grp.sum().tolist()
        wk_days = grp.size().tolist()
        full = [v for v, d in zip(wk_sum, wk_days) if d >= 5]
        recent = (full[-2:] if len(full) >= 2 else wk_sum[-2:]) or [0]
        lo, hi = min(recent), max(recent)
        rehab_start = int(full[0]) if full else (int(wk_sum[0]) if wk_sum else 0)
        rehab_end_lo = int(lo)
        rehab_end_hi = int(hi)
    else:
        rehab_start = rehab_end_lo = rehab_end_hi = NO_DATA

    if _has_data(df["social"]):
        social_late = df["social"].iloc[n // 2:]
        social_trend = "減少" if (social_late == "減").sum() > (social_late == "增").sum() else "持平"
    else:
        social_trend = NO_DATA

    return {
        "reversal_days": reversal_days,
        "rehab_start": rehab_start,
        "rehab_end_lo": rehab_end_lo,
        "rehab_end_hi": rehab_end_hi,
        "social_trend": social_trend,
    }


# ── 第二部分・疾病訊號 ──────────────────────────────────────────────────────
def _block1_cardio(df, bp):
    total_days = len(df)

    # 整段觀察期沒有量測日、或完全沒有血壓讀數 → 顯性退場，不可硬算比例
    if total_days == 0 or bp["total_count"] == 0:
        return {
            "reach_days": NO_DATA, "total": total_days,
            "reach_pct": NO_DATA, "total_readings": 0,
            "am_mean": NO_DATA, "first_week": NO_DATA,
            "last_week": NO_DATA, "trend_dir": NO_DATA,
            "cv_pct": NO_DATA, "cv_baseline": bp.get("cv_baseline", 9),
            "crisis_count": 0, "crisis_pct": NO_DATA, "crisis_detail": [],
            "low_bp_days": NO_DATA, "low_hr_days": NO_DATA,
            "summary": "本期無血壓量測資料，無法評估。",
        }

    # 達標率（族群）is day-based per BLOCK1 spec: 達標天數 / 量測天數
    day_mean_sys = df[["systolic_am", "systolic_pm", "systolic_eve"]].mean(axis=1)
    reach_days = int((day_mean_sys < BP_SYS_TARGET).sum())
    low_bp = int((df["systolic_am"] < BP_SYS_LOW).sum())
    low_hr = int((df["heart_rate"] < HR_LOW).sum()) if _has_data(df["heart_rate"]) else NO_DATA

    # 解讀只在明確偏離時加（原則五）；危象已於指標列呈現，解讀行從簡（§7.6）
    notes = []
    if bp["am_mean"] != NO_DATA and bp["am_mean"] > BP_SYS_TARGET + 1:
        notes.append(f"晨間月均{int(bp['am_mean'])}高於目標{BP_SYS_TARGET}")
    if bp["cv_pct"] != NO_DATA and bp["cv_pct"] > bp["cv_baseline"] + CV_HIGH_DELTA:
        notes.append(f"CV {bp['cv_pct']}% 高於基線")
    summary = ("；".join(notes) + "。") if notes else "晨間血壓大致落在族群目標附近。"

    return {
        "reach_days": reach_days, "total": total_days,
        "reach_pct": round(reach_days / total_days * 100),
        "total_readings": bp["total_count"],
        "am_mean": bp["am_mean"], "first_week": bp["first_week"],
        "last_week": bp["last_week"], "trend_dir": bp["trend_dir"],
        "cv_pct": bp["cv_pct"], "cv_baseline": bp["cv_baseline"],
        "crisis_count": bp["crisis_count"], "crisis_pct": bp["crisis_pct"],
        "crisis_detail": bp["crisis_detail"],
        "low_bp_days": low_bp, "low_hr_days": low_hr,
        "summary": summary,
    }


def _block2_fluid(df, obs_hwt, obs_sfw):
    hr_ok = obs_hwt["hr_start"] != NO_DATA and obs_hwt["hr_end"] != NO_DATA
    spo2_ok = obs_hwt["spo2_start"] != NO_DATA and obs_hwt["spo2_end"] != NO_DATA
    hr_rise = (obs_hwt["hr_end"] - obs_hwt["hr_start"]) if hr_ok else NO_DATA

    wc = _weight_context(df)
    weight_now = obs_hwt["weight_now"]
    if wc["is_real"]:
        # 簡易版沒有「上月體重」「乾體重」這兩個外部錨點，資料不足就不能硬算
        weight_delta_month = NO_DATA
        dry_weight = wc["dry_weight"]
        if dry_weight is not None and weight_now != NO_DATA:
            dry_gap = round(weight_now - dry_weight, 1)
            retention_support = dry_gap > 1.0
        else:
            dry_weight = NO_DATA
            dry_gap = NO_DATA
            retention_support = NO_DATA
    else:
        weight_delta_month = round(WEIGHT_NOW - WEIGHT_LAST_MONTH, 1)
        dry_weight = DRY_WEIGHT
        dry_gap = round(WEIGHT_NOW - DRY_WEIGHT, 1)
        retention_support = dry_gap > 1.0

    if _has_data(df["night_void"]):
        night_void_late = round(float(df["night_void"].iloc[len(df)//2:].mean()), 1)
        night_void_early = round(float(df["night_void"].iloc[:len(df)//2].mean()), 1)
    else:
        night_void_late = night_void_early = NO_DATA

    urine_dark_days = int((df["urine_color"] >= URINE_DARK).sum()) if _has_data(df["urine_color"]) else NO_DATA
    # earliest day of the mid-month cluster
    start_iso = df["date"].iloc[int(len(df) * 0.45)] if len(df) else None

    # 依實際方向組合惡化敘述（原則五：只描述明確偏離；無偏離則不杜撰）
    parts = []
    if hr_ok and obs_hwt["hr_end"] - obs_hwt["hr_start"] > 3:
        parts.append("心率上行")
    if spo2_ok and obs_hwt["spo2_end"] - obs_hwt["spo2_start"] < -1:
        parts.append("血氧下降")
    if night_void_late != NO_DATA and night_void_early != NO_DATA and night_void_late - night_void_early >= 0.3:
        parts.append("夜間如廁增")

    if not start_iso:
        summary = "本期無資料，無法評估。"
    elif parts:
        summary = f"非經由體重的惡化方向（{_fmt_md(start_iso)} 起）：" + "、".join(parts) + "。"
    else:
        summary = "本期未見明顯非體重相關的惡化集群。"

    return {
        "weight_now": weight_now, "weight_delta_month": weight_delta_month,
        "dry_weight": dry_weight, "dry_gap": dry_gap,
        "retention_support": retention_support,        # 是否支持滯留（簡易版資料不足時為 NO_DATA）
        "hr_rise": hr_rise, "hr_start": obs_hwt["hr_start"], "hr_end": obs_hwt["hr_end"],
        "spo2_start": obs_hwt["spo2_start"], "spo2_end": obs_hwt["spo2_end"],
        "night_void_early": night_void_early, "night_void_late": night_void_late,
        "water_short_days": obs_sfw["water_short_days"], "total": obs_sfw["total"],
        "urine_dark_days": urine_dark_days,
        "cluster_start": _fmt_md(start_iso) if start_iso else NO_DATA,
        "summary": summary,
    }


def _block3_infection(df, obs_hwt):
    low = obs_hwt["temp_low"]
    high = obs_hwt["temp_high"]
    n_temp = len(df)
    temp_baseline = obs_hwt["temp_baseline"]
    # acute functional decline: temp-abnormal day with food/activity drop same day
    # 簡易版沒有照護觀察欄位，這兩項會自然算出空結果；隨行本版本/mock 資料有這些欄位時仍要照算
    acute_days = []
    has_food = _has_data(df["food_intake"])
    has_rehab = _has_data(df["rehab_attended"])
    if has_food or has_rehab:
        for _, r in df.iterrows():
            t = r["temperature"]
            if pd.isna(t):
                continue
            abn = (t - temp_baseline <= TEMP_LOW_DELTA or t >= TEMP_HIGH_ABS)
            food_drop = r["food_intake"] in ("不足", "拒食") if has_food else False
            rehab_drop = r["rehab_attended"] == 0 if has_rehab else False
            if abn and (food_drop or rehab_drop):
                acute_days.append(_fmt_md(r["date"]))

    behav = []
    if _has_data(df["abnormal_behavior"]):
        behav = [{"date": _fmt_md(r["date"]), "kind": r["abnormal_behavior"]}
                 for _, r in df.iterrows() if pd.notna(r["abnormal_behavior"])]

    has_temp = obs_hwt.get("temp_available", _has_data(df["temperature"]))
    has_hr = _has_data(df["heart_rate"])
    has_spo2 = _has_data(df["spo2"])

    # simplified NEWS-like score (temp + hr + spo2), flag if >= 2 any day
    if has_temp or has_hr or has_spo2:
        over = 0
        for _, r in df.iterrows():
            s = 0
            if has_temp and pd.notna(r["temperature"]) and (
                r["temperature"] >= TEMP_HIGH_ABS or r["temperature"] - temp_baseline <= TEMP_LOW_DELTA
            ):
                s += 1
            if has_hr and pd.notna(r["heart_rate"]) and r["heart_rate"] >= 90:
                s += 1
            if has_spo2 and pd.notna(r["spo2"]) and r["spo2"] < SPO2_LOW:
                s += 1
            if s >= 2:
                over += 1
    else:
        over = NO_DATA

    # summary 依實際偵測結果分層（acute_days 已驗證同日食慾／活動下降）
    if acute_days:
        summary = f"{acute_days[0]} 體溫偏離基線且食慾或活動同日下降。"
    elif low and high:
        summary = f"體溫偏低 {len(low)} 次、偏高 {len(high)} 次。"
    elif low:
        summary = f"體溫偏低 {len(low)} 次（首見 {low[0]['date']}）。"
    elif high:
        summary = f"體溫偏高 {len(high)} 次（首見 {high[0]['date']}）。"
    elif not has_temp:
        summary = "本期無體溫量測資料，無法評估。"
    else:
        summary = "本月體溫未見明顯偏離基線。"
    return {
        "temp_baseline": temp_baseline, "temp_total": n_temp,
        "low": low, "high": high, "temp_cv": obs_hwt["temp_cv"],
        "acute_days": acute_days,
        "behavior": behav,
        "news_over_days": over,
        "summary": summary,
    }


def _block4_frailty(df, obs_hwt, obs_sfw, obs_sas):
    weight_pct = obs_hwt["weight_pct"]
    weight_now = obs_hwt["weight_now"]
    weight_6mo = obs_hwt["weight_6mo"]
    food_short = obs_sfw["food_short_days"]
    n = obs_sfw["total"]
    # longest consecutive food-short streak
    if _has_data(df["food_intake"]):
        streak = best = 0
        for ok in df["food_intake"].isin(["不足", "拒食"]).tolist():
            streak = streak + 1 if ok else 0
            best = max(best, streak)
    else:
        best = NO_DATA

    fatigue = int(df["fatigue_report"].sum()) if _has_data(df["fatigue_report"]) else NO_DATA
    walk = int(df["walk_assist"].sum()) if _has_data(df["walk_assist"]) else NO_DATA
    falls = int(df["fall"].sum()) if _has_data(df["fall"]) else NO_DATA
    frailty_items = NO_DATA if any(x == NO_DATA for x in [fatigue, walk, falls]) else fatigue + walk + falls

    # summary 依實際數值組合（體重方向、活動是否下降、進食是否不足）
    weight_significant = weight_pct != NO_DATA and abs(weight_pct) >= WEIGHT_LOSS_SIG
    activity_down = (
        obs_sas["rehab_end_hi"] < obs_sas["rehab_start"]
        if obs_sas["rehab_end_hi"] != NO_DATA and obs_sas["rehab_start"] != NO_DATA
        else False
    )
    food_meaningful = (
        food_short >= max(2, round(n * 0.2))
        if food_short != NO_DATA
        else False
    )
    if weight_pct == NO_DATA:
        head = "體重資料不足，無法評估變化"
    elif weight_significant:
        head = f"體重達顯著{'減輕' if weight_pct < 0 else '增加'}（{weight_pct}%）"
    elif weight_pct <= -1:
        head = f"體重輕度下降（{weight_pct}%）"
    else:
        head = "體重大致穩定"
    co = [t for t, on in (("活動", activity_down), ("進食", food_meaningful)) if on]
    summary = head + (f"，伴{'與'.join(co)}同步降低。" if co else "。")
    return {
        "weight_6mo": weight_6mo, "weight_now": weight_now,
        "weight_pct": weight_pct, "weight_significant": weight_significant,
        "rehab_start": obs_sas["rehab_start"],
        "rehab_end_lo": obs_sas["rehab_end_lo"], "rehab_end_hi": obs_sas["rehab_end_hi"],
        "food_short_days": food_short, "food_total": n,
        "food_streak": best,
        "fatigue_count": fatigue, "walk_assist_count": walk, "fall_count": falls,
        "frailty_items": frailty_items,
        "summary": summary,
    }


# ── L1（候選池統一資料結構）────────────────────────────────────────────────
# v9 討論清單 PART2 第1題：每個訊號各自算出一張完整卡片內容，頂部三項只是從池子
# 裡依偏離幅度挑前三名——排序規則以後要調整，只需要動 _select_top_signals，
# 不用去動下面各卡片自己的計算邏輯（那些邏輯留在 _obs_*/_block* 裡不動）。
def _signal_card(key, name, value, unit, baseline, severity, direction, no_data=False,
                  deviation_pct=None):
    return {
        "key": key, "name": name, "value": value, "unit": unit, "baseline": baseline,
        "deviation_pct": deviation_pct, "severity": severity, "direction": direction,
        "trend_note": None if no_data else _trend_note(direction, severity),
        "no_data": no_data,
    }


def _build_signal_pool(obs, sig):
    pool = []

    bp, b1 = obs["bp"], sig["block1"]
    if bp["total_count"]:
        crisis_pct = bp["crisis_pct"] if bp["crisis_pct"] != NO_DATA else 0.0
        reach_gap = (100 - b1["reach_pct"]) if b1["reach_pct"] != NO_DATA else 0
        sev = _severity_level("bp_crisis_pct", crisis_pct) or _severity_level("bp_reach_gap_pct", reach_gap)
        direction = _direction_label(bp["am_mean"] - BP_SYS_TARGET) if bp["am_mean"] != NO_DATA else None
        pool.append(_signal_card("bp", "血壓（晨間收縮壓）", bp["am_mean"], "mmHg", BP_SYS_TARGET,
                                  sev, direction, deviation_pct=max(crisis_pct, reach_gap)))
    else:
        pool.append(_signal_card("bp", "血壓", NO_DATA, "mmHg", BP_SYS_TARGET, None, None, no_data=True))

    hwt = obs["hr_weight_temp"]
    if hwt["weight_pct"] != NO_DATA:
        sev = _severity_level("weight_pct", hwt["weight_pct"])
        direction = _direction_label(hwt["weight_pct"], worsening_if_positive=False)
        pool.append(_signal_card("weight", "體重", hwt["weight_now"], "kg", hwt["weight_6mo"],
                                  sev, direction, deviation_pct=abs(hwt["weight_pct"])))
    else:
        pool.append(_signal_card("weight", "體重", NO_DATA, "kg", NO_DATA, None, None, no_data=True))

    if hwt.get("temp_available"):
        temp_evs = hwt["temp_low"] + hwt["temp_high"]
        max_delta = max((abs(e["delta"]) for e in temp_evs), default=0.0)
        sev = _severity_level("temp_delta", max_delta) if temp_evs else None
        latest = sorted(temp_evs, key=lambda e: e["date"])[-1] if temp_evs else None
        direction = _direction_label(latest["delta"]) if latest else None
        pool.append(_signal_card("temp", "體溫", latest["t"] if latest else NO_DATA, "°C",
                                  hwt["temp_baseline"], sev, direction, deviation_pct=max_delta))
    else:
        pool.append(_signal_card("temp", "體溫", NO_DATA, "°C", NO_DATA, None, None, no_data=True))

    sfw = obs["spo2_food_water"]
    if sfw["spo2_low_days"] != NO_DATA and sfw["total"]:
        low_pct = round(sfw["spo2_low_days"] / sfw["total"] * 100, 1)
        sev = _severity_level("spo2_low_pct", low_pct)
        pool.append(_signal_card("spo2", "血氧", low_pct, "%偏低天數", 0, sev,
                                  _direction_label(low_pct), deviation_pct=low_pct))
    else:
        pool.append(_signal_card("spo2", "血氧", NO_DATA, "%偏低天數", 0, None, None, no_data=True))

    if hwt["hr_start"] != NO_DATA and hwt["hr_end"] != NO_DATA:
        hr_delta = hwt["hr_end"] - hwt["hr_start"]
        sev = _severity_level("hr_delta", hr_delta)
        pool.append(_signal_card("hr", "心率", hwt["hr_end"], "bpm", hwt["hr_start"], sev,
                                  _direction_label(hr_delta), deviation_pct=abs(hr_delta)))
    else:
        pool.append(_signal_card("hr", "心率", NO_DATA, "bpm", NO_DATA, None, None, no_data=True))

    if sfw["food_short_days"] != NO_DATA and sfw["total"]:
        food_pct = round(sfw["food_short_days"] / sfw["total"] * 100, 1)
        sev = _severity_level("food_short_pct", food_pct)
        pool.append(_signal_card("food", "進食", food_pct, "%不足天數", 0, sev,
                                  _direction_label(food_pct), deviation_pct=food_pct))
    else:
        pool.append(_signal_card("food", "進食", NO_DATA, "%不足天數", 0, None, None, no_data=True))

    if sfw["water_short_days"] != NO_DATA and sfw["total"]:
        water_pct = round(sfw["water_short_days"] / sfw["total"] * 100, 1)
        sev = _severity_level("water_short_pct", water_pct)
        pool.append(_signal_card("water", "飲水", water_pct, "%未達標天數", 0, sev,
                                  _direction_label(water_pct), deviation_pct=water_pct))
    else:
        pool.append(_signal_card("water", "飲水", NO_DATA, "%未達標天數", 0, None, None, no_data=True))

    return pool


def _select_top_signals(pool, n=3):
    """頂部概述『本月偏離與偏離幅度最大的三項』（v9 整體總覽）。排序：先比嚴重度
    分級（D1，待醫師確認），同級再依 deviation_pct；無資料或未達最低門檻
    （severity=None）的訊號不列入候選。"""
    candidates = [s for s in pool if not s["no_data"] and s["severity"]]
    return sorted(
        candidates,
        key=lambda s: (_SEVERITY_RANK[s["severity"]], s["deviation_pct"] or 0),
        reverse=True,
    )[:n]


# ── Public entry point ──────────────────────────────────────────────────────
def analyze_all(df: pd.DataFrame) -> dict:
    obs_bp  = _obs_bp(df)
    obs_sfw = _obs_spo2_food_water(df)
    obs_hwt = _obs_hr_weight_temp(df)
    obs_sas = _obs_sleep_activity_social(df)

    is_real = df.attrs.get("is_real_data", False)
    weight_note = "月量1次" if not is_real else f"{int(df['weight'].notna().sum())}次" if _has_data(df["weight"]) else NO_DATA

    header = {
        "bp_days": int(df["systolic_am"].notna().sum()),
        "bp_count": obs_bp["total_count"],     # 總收縮壓讀數（晨+午+晚）
        "temp_days": int(df["temperature"].notna().sum()),
        "hr_days": int(df["heart_rate"].notna().sum()),
        "weight_note": weight_note,
        "care_days": len(df),
    }

    obs = {
        "bp": obs_bp,
        "spo2_food_water": obs_sfw,
        "hr_weight_temp": obs_hwt,
        "sleep_activity_social": obs_sas,
    }
    signals = {
        "block1": _block1_cardio(df, obs_bp),
        "block2": _block2_fluid(df, obs_hwt, obs_sfw),
        "block3": _block3_infection(df, obs_hwt),
        "block4": _block4_frailty(df, obs_hwt, obs_sfw, obs_sas),
    }
    signal_pool = _build_signal_pool(obs, signals)

    return {
        "header": header,
        "obs": obs,
        "signals": signals,
        # v9 頂部概述（候選池 L1 + 排序 D1/D2）：signal_pool 是每個訊號的完整卡片，
        # top3 是從池子挑出來、要放在頂部概述的「本月偏離最大三項」。
        "signal_pool": signal_pool,
        "top3": _select_top_signals(signal_pool),
        # D6 事件優先級（見 data_parser.py），沒有照護觀察資料（簡易版/空資料）時
        # df.attrs 不會有這個 key，給預設空結構避免下游 KeyError。
        "events": df.attrs.get("event_priority") or {
            "high_priority": [], "medication_refused_days": [],
            "general_summary": {"count": 0, "by_code": {}},
        },
    }
