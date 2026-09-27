from datetime import datetime
from dateutil import parser as dtparser
import pandas as pd
import numpy as np
from typing import Optional

AM_END = 12
PM_END = 18

# 照護觀察欄位門檻 — 依真實資料庫 schema（packages/database/prisma/schema.prisma）
# 換算，皆為可調參數，正式上線前待醫師/機構確認。
WATER_TARGET_ML = 1500   # 每日飲水達標門檻（ml）— full-guide.md §8.1 列為「待定」，
                          # 2026-08-03查證：落在老年人每日建議飲水量1500–2000ml區間
                          # 下限（ESPEN／EFSA相關文獻建議65歲以上族群），詳見
                          # docs/thresholds-references.md
NIGHT_START_HOUR = 22    # 夜間如廁時段起（含）
NIGHT_END_HOUR = 6       # 夜間如廁時段迄（不含）
FOOD_SHORT_AMOUNTS = {"lt_half", "half"}   # CareRecords.Meal.intakeAmount 4 級中，併入「不足」的兩級

# CareRecords.Behavior.behaviors 26 種英文 code → 中文顯示對照（packages/shared/src/
# schemas/careRecord.ts 的 Zod enum，2026-07-14 版，逐項對照 dbschema 頁面核實）。
# 真實資料存的是英文 code，PDF/摘要要顯示中文，沒對到的 code 就地顯示原始英文
# （不要無聲吃掉，才會在報告上顯眼到有人發現、進而回頭補字典，而不是悄悄消失）。
BEHAVIOR_LABELS = {
    "repeat_question":    "重複問同樣問題",
    "lost_item":          "遺失東西",
    "false_accuse":       "誣賴他人",
    "repeat_action":      "重複同樣動作",
    "curse":              "罵人或詛咒人",
    "improper_dress":     "穿著不合宜",
    "wander":             "遊走",
    "emotional_abnormal": "情緒異常(突哭/笑)",
    "improper_undress":   "不當脫衣褲",
    "hoard":              "無故聚藏物品",
    "sexual_harass":      "性騷擾",
    "expose_private":     "暴露隱私部位",
    "hallucination":      "幻覺/妄想",
    "destroy":            "破壞衣物",
    "irritable":          "易怒/激躁",
    "depressed":          "憂鬱",
    "complain":           "抱怨",
    "no_interest":        "對日常沒興趣",
    "day_sleep":          "白天睡多",
    "refuse_hygiene":     "拒絕個人衛生協助",
    "refuse_eat":         "拒絕吃東西",
    "urine_incont":       "尿失禁",
    "bowel_incont":       "大便失禁",
    "elope":              "偷跑走失",
    "uncooperative":      "不合作/堅持",
    "physical_aggression": "肢體攻擊",
}

# D6（事件優先級，待醫師確認分級是否合理——v9 討論清單 PART1 第3題）：
# 候選事件（26種行為代碼＋拒服＋自由文字事件）版面塞不下時，安全疑慮類無條件
# 個別顯示；其餘（一般）類版面不足時彙總成「其他行為 N次」帶過，不逐一佔位。
BEHAVIOR_PRIORITY_HIGH = {
    "physical_aggression", "elope", "sexual_harass", "hallucination",
    "expose_private", "false_accuse",
}

# Full output schema — kept in sync with mock_data.generate_vitals() columns so
# analyze_all() always sees every column, even when 0 records are submitted.
COLUMNS = [
    "date", "systolic_am", "diastolic_am", "systolic_pm", "diastolic_pm",
    "systolic_eve", "heart_rate", "spo2", "temperature", "weight", "body_fat",
    "water_intake", "urine_color", "urination_special", "night_void", "defecation_special", "food_intake", "sleep_hrs",
    "day_night_reversal", "rehab_attended", "social", "mood",
    "abnormal_behavior", "walk_assist", "fatigue_report", "fall",
    "grip_strength",
    # 2026-07 新增：Medication／Hygiene 解析結果。只做「解析層」，這兩欄目前
    # analysis_core.py／report_pdf.py 都還沒有讀取或顯示——呈現方式待下一版
    # 定案再接，這裡先把真實資料備好，不用等版面決定才回頭補資料。
    "medication_total", "medication_refused", "hygiene_items",
]


def _parse_time(time_str):
    return dtparser.parse(time_str)

def _hour(time_str):
    return _parse_time(time_str).hour

def _date_str(time_str):
    return _parse_time(time_str).date().isoformat()

def parse_measurement_data(data, profile=None, weight_6mo_ago=None,
                            weight_last_month=None, dry_weight=None,
                            temp_baseline=None):
    """輸入 `data` 為兩種真實 DB 資料形狀的混合 list（哪一種由欄位判斷）：

    1. VitalSigns 表列（生理量測）：
       {"RecordTime": "...", "SystolicPressure": 130, "DiastolicPressure": 80,
        "Pulse": 78, "BloodOxygen": 97, "Temperature": 36.7}
       各欄皆可空，一次量測只填量到的項目。
       （RespiratoryRate、BloodSugarPeriod/Before/After 真實表裡也有，但分析層
       目前沒有呼吸或血糖模組——血糖依 full-guide.md 決策移除——這裡刻意不收，
       避免囤積分析層用不到的資料。）

    2. CareRecords 表列（照護登打）：
       {"RecordType": "Meal", "OccurredAt": "...", "Content": {...}}
       RecordType 對應真實 schema 的 13 種型別，這裡解析 Meal／Water／
       Urination／Defecation／BodyMetrics／Behavior／Medication／Hygiene 八種；
       其餘型別（Event/CareDiary/NightRound/DementiaPatch/Accompaniment）分析層
       無對應欄位，不解析。Medication／Hygiene 目前只解析到 DataFrame 欄位
       （medication_total/medication_refused/hygiene_items），還沒有
       analysis_core.py／report_pdf.py 的計算或顯示邏輯——呈現方式待下一版定案。
       2026-08-03新增Defecation（排便）：跟Urination是同一種共用結構
       （status: normal/special，special時note必填），不需要另外設計解析邏輯。
    """
    daily_bp = {}
    daily_hr = {}
    daily_spo2 = {}
    daily_temp = {}
    daily_wt = {}
    daily_height = {}
    daily_meal = {}
    daily_water_ml = {}
    daily_void_night = {}
    daily_urination_special = {}
    daily_defecation_total = {}
    daily_defecation_special = {}
    daily_behavior = {}
    daily_med_total = {}
    daily_med_refused = {}
    daily_hygiene_items = {}
    all_dates = set()

    # 單筆記錄壞掉（型別不對、時間字串解析不了）不該讓整批真實資料全部作廢——
    # 90 筆事件裡有 1 筆日期格式怪異，應該是跳過那 1 筆，不是整個請求 500。
    skipped = 0
    for record in data:
        try:
            if not isinstance(record, dict):
                raise TypeError(f"record is not a dict: {type(record).__name__}")

            # ── VitalSigns 形狀 ──────────────────────────────────────────
            if "RecordTime" in record:
                t = record.get("RecordTime", "")
                if not t:
                    continue
                date = _date_str(t)
                hour = _hour(t)
                all_dates.add(date)

                sys_val = record.get("SystolicPressure")
                dia_val = record.get("DiastolicPressure")
                if sys_val is not None:
                    daily_bp.setdefault(date, {"am": [], "pm": [], "eve": []})
                    if hour < AM_END:
                        daily_bp[date]["am"].append((sys_val, dia_val))
                    elif hour < PM_END:
                        daily_bp[date]["pm"].append((sys_val, dia_val))
                    else:
                        daily_bp[date]["eve"].append((sys_val, dia_val))

                if record.get("Pulse") is not None:
                    daily_hr.setdefault(date, []).append(record["Pulse"])

                if record.get("BloodOxygen") is not None:
                    daily_spo2.setdefault(date, []).append(record["BloodOxygen"])

                if record.get("Temperature") is not None:
                    daily_temp.setdefault(date, []).append(record["Temperature"])

                continue

            # ── CareRecords 形狀 ─────────────────────────────────────────
            record_type = record.get("RecordType")
            t = record.get("OccurredAt", "")
            if not record_type or not t:
                continue
            date = _date_str(t)
            hour = _hour(t)
            all_dates.add(date)
            content = record.get("Content") or {}

            if record_type == "Meal":
                level = content.get("intakeAmount")
                if level is not None:
                    daily_meal.setdefault(date, []).append(level)

            elif record_type == "Water":
                ml = content.get("amountMl")
                if ml is not None:
                    daily_water_ml[date] = daily_water_ml.get(date, 0) + ml

            elif record_type == "Urination":
                daily_void_night.setdefault(date, 0)
                if hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR:
                    daily_void_night[date] += 1
                # 2026-07 新增：status 是 normal/special 二元 enum（見官方
                # schema截圖），跟night_void（純看時段的如廁次數）是兩件事——
                # 這欄記錄的是「排尿狀況是否異常」，不分日夜都算。
                if content.get("status") == "special":
                    daily_urination_special[date] = daily_urination_special.get(date, 0) + 1

            elif record_type == "Defecation":
                # 跟Urination共用結構（status: normal/special），日照/團屋都適用
                # （排便不像夜間如廁那樣受限於機構是否有夜間人力），2026-08-03
                # 取代原本用Urination時段推算「夜間如廁」的做法——日照個案晚上
                # 不在機構，理論上不會有落在夜間時段的CareRecords，用時段推算
                # 對日照個案沒有意義；排便異常則全天都可能發生，不受此限制。
                daily_defecation_total.setdefault(date, 0)
                daily_defecation_total[date] += 1
                if content.get("status") == "special":
                    daily_defecation_special[date] = daily_defecation_special.get(date, 0) + 1

            elif record_type == "BodyMetrics":
                val = content.get("weightKg")
                if val is not None and date not in daily_wt:
                    daily_wt[date] = val
                # 2026-08-12新增：真實schema的BodyMetrics本來就有heightCm
                # （選填），先前只抓weightKg、heightCm完全沒被讀取——不是
                # 資料裡沒有身高，是這裡沒去解析。身高是成人幾乎不變的量，
                # 不需要像體重一樣分「最早/最新」兩個錨點，取任一筆有值的
                # 讀數即可（這裡用最新一筆，跟weight_now的錨點邏輯一致）。
                h = content.get("heightCm")
                if h is not None:
                    daily_height[date] = h

            elif record_type == "Behavior":
                items = content.get("behaviors")
                if items:
                    daily_behavior.setdefault(date, []).extend(items)

            elif record_type == "Medication":
                daily_med_total[date] = daily_med_total.get(date, 0) + 1
                if content.get("refused"):
                    daily_med_refused[date] = daily_med_refused.get(date, 0) + 1

            elif record_type == "Hygiene":
                n = len(content.get("items") or [])
                if n:
                    daily_hygiene_items[date] = daily_hygiene_items.get(date, 0) + n

        except Exception:
            skipped += 1
            continue

    # D6 事件優先級分組——安全疑慮類保留逐筆明細（日期＋code），一般類只留彙總計數。
    # 拒服（Medication.refused）另立一組，v9 範例把它跟遊走/拒絕照顧列為同級候選。
    high_events, general_counts = [], {}
    daily_behavior_by_code = {}  # date -> {code: count}，2026-07新增，給v9每日狀態
    # 四列(精神/情緒/睡眠)跟偶發事件軸的逐日對照用——高/一般彙總只留得住「總數」，
    # 不留日期，畫每日格子/事件軸需要知道「哪一天」發生了哪個代碼。
    for d, codes in daily_behavior.items():
        day_counts = daily_behavior_by_code.setdefault(d, {})
        for code in codes:
            day_counts[code] = day_counts.get(code, 0) + 1
            if code in BEHAVIOR_PRIORITY_HIGH:
                high_events.append({"date": d, "code": code, "label": BEHAVIOR_LABELS.get(code, code)})
            else:
                general_counts[code] = general_counts.get(code, 0) + 1
    high_events.sort(key=lambda e: e["date"])
    medication_refused_days = sorted(
        [{"date": d, "count": n} for d, n in daily_med_refused.items()],
        key=lambda e: e["date"],
    )
    event_priority = {
        "high_priority": high_events,
        "medication_refused_days": medication_refused_days,
        "general_summary": {
            "count": sum(general_counts.values()),
            "by_code": {BEHAVIOR_LABELS.get(c, c): n for c, n in general_counts.items()},
        },
        "daily_by_code": daily_behavior_by_code,
    }

    if temp_baseline is None:
        all_temps = [v for vals in daily_temp.values() for v in vals]
        temp_baseline = round(float(np.median(all_temps)), 1) if all_temps else 36.5

    sorted_dates = sorted(all_dates)
    rows = []

    for date in sorted_dates:
        def bp_mean(sessions, fallback_sys=None, fallback_dia=None):
            valid = [(s, d) for s, d in sessions if s is not None]
            if not valid:
                return fallback_sys, fallback_dia
            sys_vals = [s for s, d in valid]
            dia_vals = [d for s, d in valid if d is not None]
            sys_m = round(float(np.mean(sys_vals)), 1)
            dia_m = round(float(np.mean(dia_vals)), 1) if dia_vals else fallback_dia
            return sys_m, dia_m

        bp_day = daily_bp.get(date, {"am": [], "pm": [], "eve": []})
        sys_am, dia_am = bp_mean(bp_day["am"])
        sys_pm, dia_pm = bp_mean(bp_day["pm"], fallback_sys=sys_am, fallback_dia=dia_am)
        sys_eve, _ = bp_mean(bp_day["eve"], fallback_sys=sys_am)

        hr_vals = daily_hr.get(date, [])
        hr = round(float(np.mean(hr_vals)), 1) if hr_vals else None

        spo2_vals = daily_spo2.get(date, [])
        spo2 = round(float(np.mean(spo2_vals)), 1) if spo2_vals else None

        temp_vals = daily_temp.get(date, [])
        temp = round(float(np.mean(temp_vals)), 1) if temp_vals else None

        weight = daily_wt.get(date, None)

        meal_levels = daily_meal.get(date)
        food_intake = (
            "不足" if any(l in FOOD_SHORT_AMOUNTS for l in meal_levels) else "正常"
        ) if meal_levels else None

        water_ml = daily_water_ml.get(date)
        water_intake = (
            "達標" if water_ml >= WATER_TARGET_ML else "未達標"
        ) if water_ml is not None else None

        night_void = daily_void_night.get(date, None)

        behaviors = daily_behavior.get(date)
        abnormal_behavior = (
            "、".join(BEHAVIOR_LABELS.get(b, b) for b in behaviors) if behaviors else None
        )

        rows.append({
            "date": date,
            "systolic_am": sys_am,
            "diastolic_am": dia_am,
            "systolic_pm": sys_pm,
            "diastolic_pm": dia_pm,
            "systolic_eve": sys_eve,
            "heart_rate": hr,
            "spo2": spo2,
            "temperature": temp,
            "weight": weight,
            "body_fat": None,      # 真實 schema 無對應欄位（BodyMetrics 只有 heightCm/weightKg），維持 NO_DATA
            "water_intake": water_intake,
            "urine_color": None,   # 真實 schema 的 Urination 只有 status，無尿色欄位，維持 NO_DATA
            # 2026-07新增：status(normal/special)二元欄位。當天完全沒有Urination
            # 記錄時維持None(NO_DATA)；有記錄但都是normal時是0（跟night_void同樣的
            # 「有觀察、值是0」語意，不是「沒觀察」）。
            "urination_special": daily_urination_special.get(date, 0) if date in daily_void_night else None,
            "night_void": night_void,
            # 2026-08-03新增：跟urination_special同樣的NO_DATA語意——當天完全
            # 沒有Defecation記錄時是None，有記錄但都是normal時是0。
            "defecation_special": daily_defecation_special.get(date, 0) if date in daily_defecation_total else None,
            "food_intake": food_intake,
            "sleep_hrs": None,
            "day_night_reversal": None,
            "rehab_attended": None,
            "social": None,
            "mood": None,
            "abnormal_behavior": abnormal_behavior,
            "walk_assist": None,
            "fatigue_report": None,
            "fall": None,
            "grip_strength": None,
            "medication_total": daily_med_total.get(date),
            "medication_refused": daily_med_refused.get(date),
            "hygiene_items": daily_hygiene_items.get(date),
        })

    # Always emit the full column set, even with 0 rows, so downstream
    # analysis code never hits a missing-column KeyError on empty input.
    df = pd.DataFrame(rows, columns=COLUMNS)

    # weight anchors, derived from whatever real readings were actually
    # submitted (earliest = long-window baseline, latest = "now") — not a
    # fixed 6-month figure, since the simple-version API has no separate
    # historical-anchor input.
    wt_dates = sorted(daily_wt.keys())
    df.attrs["is_real_data"] = True
    df.attrs["weight_now"] = daily_wt.get(wt_dates[-1]) if wt_dates else None
    df.attrs["weight_baseline"] = daily_wt.get(wt_dates[0]) if wt_dates else None
    df.attrs["weight_reading_count"] = len(wt_dates)
    # 2026-08-12新增：身高供BMI計算用（情境1無基線時的體重判讀替代方案）。
    # 身高是成人幾乎不變的量，不像體重要分baseline/now兩個錨點，取最新一筆
    # 有值的讀數即可；完全沒有身高紀錄時為None，呼叫端要自行判斷None的
    # fallback行為（例如只顯示體重不做BMI分級）。
    height_dates = sorted(daily_height.keys())
    df.attrs["height_cm"] = daily_height.get(height_dates[-1]) if height_dates else None
    df.attrs["weight_last_month"] = weight_last_month
    df.attrs["weight_6mo_ago"] = weight_6mo_ago
    df.attrs["dry_weight"] = dry_weight
    df.attrs["temp_baseline"] = temp_baseline
    df.attrs["skipped_records"] = skipped
    df.attrs["event_priority"] = event_priority
    return df


def build_dataframe_from_request(req_body, temp_baseline=None):
    if not isinstance(req_body, dict):
        raise ValueError(f"request body must be an object, got {type(req_body).__name__}")
    profile = req_body.get("profile", {})
    data = req_body.get("_data", [])
    if not isinstance(data, list):
        raise ValueError(f"'_data' must be a list, got {type(data).__name__}")
    return parse_measurement_data(data=data, profile=profile, temp_baseline=temp_baseline)
