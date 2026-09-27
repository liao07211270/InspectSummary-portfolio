"""
產生v9手動測試用的情境資料（給頁面顯示測試用，非pytest單元測試資料——
pytest用的固定資料在 tests/fixtures/v9_scenarios/，不受這支腳本影響）。

跟 gen_v9_test_scenarios.py 分開成獨立檔案，避免修改到既有pytest fixture
產生邏輯；這裡額外支援 Defecation（排便異常）事件產生，以及 exclude_days
（整天缺測，用於模擬資料缺失情境）。

2026-08-04改法：每組情境輸出改成單一整段事件陣列（依日期排序，不再分
baseline/current兩個檔案）——真實上游資料就是一整串，後端自己切分
（見 analysis_v9.split_events_by_current_month()），這裡的測試資料跟著
改成同一種形狀。

用法：
    python scripts/gen_v9_manual_test_data.py
    python scripts/gen_v9_manual_test_data.py --out "自訂輸出路徑"
"""
import argparse
import json
import os
import random
from datetime import date, timedelta


def _rn(rng, mu, sigma, lo=None, hi=None):
    while True:
        v = rng.gauss(mu, sigma)
        if lo is not None and v < lo:
            continue
        if hi is not None and v > hi:
            continue
        return round(v, 1)


def _iso(d, hour, minute=0):
    return f"{d.isoformat()}T{hour:02d}:{minute:02d}:00"


def generate_period(
    days, start, seed, *,
    sys_base=132, sys_drift=0, crisis_days=(),
    hr_base=74,
    weight_start=57.0, weight_readings=1,
    temp_base=36.7, fever_days=(), hypothermia_days=(),
    spo2_base=96, spo2_low_days=(),
    water_short_rate=0.15, water_short_days=None, food_short_rate=0.15,
    behaviors=None, medication_refused_days=(),
    urination_special_days=(), defecation_special_days=(), hygiene_events=None,
    sparse_rate=0.0, exclude_days=(),
):
    """跟gen_v9_test_scenarios.generate_period同一套邏輯，新增兩項：
    defecation_special_days（排便異常天，跟urination_special_days同語意）、
    exclude_days（該天完全不產生任何事件，用來精準模擬資料缺失的區段/散落天，
    跟sparse_rate的機率版不同，這裡是明確指定哪幾天）。"""
    rng = random.Random(seed)
    start_date = date.fromisoformat(start)
    behaviors = behaviors or []
    behav_by_day = {}
    for i, code in behaviors:
        behav_by_day.setdefault(i, []).append(code)
    hygiene_by_day = {}
    for i, item in (hygiene_events or []):
        hygiene_by_day.setdefault(i, []).append(item)

    events = []
    weight_days = set()
    if weight_readings > 0:
        step = max(1, days // weight_readings)
        weight_days = set(range(0, days, step))

    for i in range(days):
        if i in exclude_days:
            continue
        if sparse_rate and rng.random() < sparse_rate:
            continue
        d = start_date + timedelta(days=i)
        frac = i / max(days - 1, 1)
        base = sys_base + sys_drift * frac

        if i in crisis_days:
            sys_am = _rn(rng, 188, 4, 180, 200)
            dia_am = _rn(rng, 105, 4, 100, 115)
        else:
            sys_am = _rn(rng, base, 12, 100, 178)
            dia_am = _rn(rng, base * 0.62, 6, 55, 100)
        sys_pm = _rn(rng, base - 10, 10, 95, 165)
        sys_eve = _rn(rng, base - 5, 11, 95, 170)
        hr = _rn(rng, hr_base, 5, 50, 110)

        temp = _rn(rng, temp_base, 0.2, 36.0, 37.2)
        if i in fever_days:
            temp = _rn(rng, 38.0, 0.3, 37.5, 39.0)
        elif i in hypothermia_days:
            temp = _rn(rng, 35.5, 0.2, 34.8, 35.8)

        spo2 = _rn(rng, spo2_base, 1.0, 88, 100)
        if i in spo2_low_days:
            spo2 = _rn(rng, 91, 1.5, 85, 93)

        events.append({
            "RecordTime": _iso(d, 8, 0),
            "SystolicPressure": round(sys_am), "DiastolicPressure": round(dia_am),
            "Pulse": round(hr), "Temperature": temp, "BloodOxygen": round(spo2),
        })
        events.append({"RecordTime": _iso(d, 13, 0), "SystolicPressure": round(sys_pm), "DiastolicPressure": round(dia_am - 6)})
        events.append({"RecordTime": _iso(d, 19, 0), "SystolicPressure": round(sys_eve)})

        if i in weight_days:
            events.append({
                "RecordType": "BodyMetrics", "OccurredAt": _iso(d, 7, 30),
                "Content": {"heightCm": 158, "weightKg": weight_start},
            })

        water_short = (i in water_short_days) if water_short_days is not None else (rng.random() < water_short_rate)
        total_ml = rng.randint(300, 900) if water_short else rng.randint(1500, 2200)
        for hour in (9, 14, 19):
            events.append({
                "RecordType": "Water", "OccurredAt": _iso(d, hour, rng.randint(0, 59)),
                "Content": {"amountMl": total_ml // 3},
            })

        food_short = rng.random() < food_short_rate
        for meal_kind, hour in (("breakfast", 8), ("lunch", 12), ("dinner", 18)):
            level = rng.choice(["lt_half", "half"]) if food_short else rng.choices(["most", "all"], weights=[0.4, 0.6])[0]
            events.append({
                "RecordType": "Meal", "OccurredAt": _iso(d, hour, rng.randint(0, 30)),
                "Content": {"mealKind": meal_kind, "intakeAmount": level},
            })

        n_urination = rng.choice([1, 2, 3])
        for k in range(n_urination):
            special = k == 0 and i in urination_special_days
            content = {"status": "special" if special else "normal"}
            if special:
                content["note"] = "陪同人員回報排尿疼痛/顏色異常"
            events.append({
                "RecordType": "Urination", "OccurredAt": _iso(d, rng.choice([1, 3, 23]), rng.randint(0, 59)),
                "Content": content,
            })

        defecation_special = i in defecation_special_days
        defecation_content = {"status": "special" if defecation_special else "normal"}
        if defecation_special:
            defecation_content["note"] = "陪同人員回報排便性狀異常"
        events.append({
            "RecordType": "Defecation", "OccurredAt": _iso(d, rng.choice([7, 10, 16]), rng.randint(0, 59)),
            "Content": defecation_content,
        })

        for code in behav_by_day.get(i, []):
            events.append({
                "RecordType": "Behavior", "OccurredAt": _iso(d, 20, 0),
                "Content": {"behaviors": [code], "shift": "D"},
            })

        if i in hygiene_by_day:
            events.append({
                "RecordType": "Hygiene", "OccurredAt": _iso(d, 9, 0),
                "Content": {"items": hygiene_by_day[i]},
            })

        if i in medication_refused_days:
            events.append({
                "RecordType": "Medication", "OccurredAt": _iso(d, 9, 0),
                "Content": {"drugName": "降血壓藥", "refused": True},
            })

    return events


def _apply_weight_trend(events, weight_start, weight_end):
    body = [e for e in events if e.get("RecordType") == "BodyMetrics"]
    n = len(body)
    for idx, e in enumerate(body):
        frac = idx / max(n - 1, 1)
        e["Content"]["weightKg"] = round(weight_start + (weight_end - weight_start) * frac, 1)
    return events


def _dates_for(exclude_days, start, days):
    start_date = date.fromisoformat(start)
    return [(start_date + timedelta(days=i)).isoformat() for i in sorted(exclude_days) if i < days]


SCENARIOS = {}


def _reg(name, note):
    def deco(fn):
        SCENARIOS[name] = (fn, note)
        return fn
    return deco


@_reg("01_healthy_full", "完整資料量・健康人：前三月(90天)、本月(30天)皆全量登打，"
                          "血壓/心率/體溫/血氧/體重/飲水/進食全部落在正常範圍，"
                          "無危象、無發燒、無血氧偏低、無體重顯著變化、無異常行為事件，"
                          "測試系統在「完全沒有偏離」情境下四個面向卡/候選池/每日狀態"
                          "是否正確顯示為穩定、不硬湊假異常。")
def scenario_healthy():
    baseline = generate_period(90, "2025-08-01", seed=1001, sys_base=115, hr_base=68,
                                weight_start=55.0, weight_readings=3,
                                temp_base=36.5, spo2_base=98,
                                water_short_rate=0.05, food_short_rate=0.05)
    baseline = _apply_weight_trend(baseline, 55.0, 54.9)
    current = generate_period(30, "2025-11-01", seed=1002, sys_base=115, hr_base=68,
                               weight_start=54.9, weight_readings=2,
                               temp_base=36.5, spo2_base=98,
                               water_short_rate=0.05, food_short_rate=0.05)
    current = _apply_weight_trend(current, 54.9, 54.8)
    return baseline, current


@_reg("02_unhealthy_all_aspects", "完整資料量・不健康人(涵蓋所有面向)：本月同時出現"
                                   "血壓危象(4次>=180mmHg)、心率上升、體重顯著下降"
                                   "(59.5→55.2kg，約7.2%)、進食/飲水不足比例升高、"
                                   "發燒3次、血氧偏低5次、排尿/排便異常各3天、"
                                   "衛生協助項目增加、多種異常行為(遊走/肢體攻擊/"
                                   "走失/躁動連續5天/情緒異常/日間嗜睡/尿失禁/大便"
                                   "失禁/拒服藥)，測試四個面向卡與候選池在「全面偏離」"
                                   "情境下是否都能正確觸發並排序。")
def scenario_unhealthy_all():
    baseline = generate_period(90, "2025-08-01", seed=2001, sys_base=125, hr_base=72,
                                weight_start=60.0, weight_readings=3,
                                temp_base=36.6, spo2_base=96,
                                water_short_rate=0.1, food_short_rate=0.1,
                                hygiene_events=[(15, "洗澡"), (55, "洗澡")])
    baseline = _apply_weight_trend(baseline, 60.0, 59.5)
    current_hygiene = [(i, item) for i in (2, 6, 10, 14, 18, 22, 26) for item in ("洗澡", "更衣")]
    current = generate_period(
        30, "2025-11-01", seed=2002, sys_base=135, sys_drift=20,
        crisis_days={3, 10, 17, 24}, hr_base=92,
        weight_start=59.5, weight_readings=3,
        temp_base=36.8, fever_days={6, 13, 20}, hypothermia_days={2},
        spo2_base=94, spo2_low_days={6, 7, 13, 20, 21},
        water_short_days={8, 9, 10, 11}, food_short_rate=0.5,
        urination_special_days={6, 13, 20}, defecation_special_days={6, 13, 20},
        hygiene_events=current_hygiene,
        behaviors=[
            (1, "wander"), (11, "wander"), (23, "wander"),
            (9, "physical_aggression"), (16, "elope"),
            (3, "irritable"), (4, "irritable"), (5, "irritable"), (6, "irritable"), (7, "irritable"),
            (12, "emotional_abnormal"), (20, "emotional_abnormal"),
            (18, "day_sleep"),
            (14, "urine_incont"), (15, "bowel_incont"),
        ],
        medication_refused_days={5, 12, 19},
    )
    current = _apply_weight_trend(current, 59.5, 55.2)
    return baseline, current


@_reg("03_partial_cardio_frailty", "完整資料量・部分疾病(心血管＋衰弱)：本月出現血壓"
                                    "危象(3次)、心率上升，同時體重明顯下降(57.5→54.0kg，"
                                    "約6.1%)、進食不足比例升高至45%、飲水不足比例升高至"
                                    "30%；感染監測(體溫/血氧)、精神行為、排尿排便皆維持"
                                    "正常，測試「只有部分面向異常」時系統是否只標出真正"
                                    "異常的卡片，不會連帶誤標其他正常面向。")
def scenario_cardio_frailty():
    baseline = generate_period(90, "2025-08-01", seed=3001, sys_base=128, hr_base=76,
                                weight_start=58.0, weight_readings=3,
                                temp_base=36.6, spo2_base=97,
                                water_short_rate=0.1, food_short_rate=0.1)
    baseline = _apply_weight_trend(baseline, 58.0, 57.5)
    current = generate_period(
        30, "2025-11-01", seed=3002, sys_base=140, sys_drift=15,
        crisis_days={5, 15, 25}, hr_base=95,
        weight_start=57.5, weight_readings=3,
        temp_base=36.6, spo2_base=97,
        food_short_rate=0.45, water_short_rate=0.3,
    )
    current = _apply_weight_trend(current, 57.5, 54.0)
    return baseline, current


@_reg("04_partial_infection_behavior", "完整資料量・部分疾病(感染＋精神行為)：本月出現"
                                        "發燒3次、血氧偏低5次、排尿異常2天、排便異常2天，"
                                        "同時精神行為出現躁動連續4天(11/3–11/6)、情緒異常"
                                        "3次、日間嗜睡2次、遊走2次；血壓/心率/體重維持"
                                        "穩定，測試「感染＋行為」組合情境下感染監測卡跟"
                                        "每日狀態列是否正確反映，不誤觸高血壓/衰弱卡。")
def scenario_infection_behavior():
    baseline = generate_period(90, "2025-08-01", seed=4001, sys_base=122, hr_base=70,
                                weight_start=56.0, weight_readings=3,
                                temp_base=36.6, spo2_base=97,
                                water_short_rate=0.1, food_short_rate=0.1)
    baseline = _apply_weight_trend(baseline, 56.0, 55.9)
    current = generate_period(
        30, "2025-11-01", seed=4002, sys_base=122, hr_base=72,
        weight_start=55.9, weight_readings=2,
        temp_base=36.6, fever_days={4, 11, 18}, spo2_base=97, spo2_low_days={4, 5, 11, 18, 19},
        urination_special_days={4, 11}, defecation_special_days={11, 18},
        water_short_rate=0.1, food_short_rate=0.1,
        behaviors=[
            (3, "irritable"), (4, "irritable"), (5, "irritable"), (6, "irritable"),
            (9, "emotional_abnormal"), (16, "emotional_abnormal"), (22, "emotional_abnormal"),
            (12, "day_sleep"), (24, "day_sleep"),
            (7, "wander"), (19, "wander"),
        ],
        medication_refused_days={7, 14},
    )
    current = _apply_weight_trend(current, 55.9, 55.7)
    return baseline, current


@_reg("05_new_user_day1", "初始使用者・無歷史紀錄，僅第一天資料：baseline為空陣列"
                           "(完全沒有前三月資料)，current只有11/1當天的量測與照護紀錄"
                           "(3次血壓/1次體重/3次飲水/3次餐食/1次排尿/1次排便)，測試"
                           "系統在「完全沒有基線可比較」時，候選池/四個面向卡的"
                           "before欄位是否正確顯示NO_DATA(—)而不是誤算或崩潰。")
def scenario_new_user_day1():
    baseline = []
    current = generate_period(1, "2025-11-01", seed=5001, sys_base=125, hr_base=75,
                               weight_start=60.0, weight_readings=1,
                               temp_base=36.6, spo2_base=97)
    return baseline, current


@_reg("06_one_month_only", "僅有一個月紀錄(已使用一個月)：baseline為空陣列(尚未累積"
                            "滿三個月前期資料)，current是完整的11月(30天)量測與照護"
                            "紀錄，含少量自然波動(飲水/進食不足比例15%，屬正常範圍"
                            "內的偶發)，測試系統在「有完整當月資料但無前期基線」時，"
                            "當月卡片本身的統計(達標率/分布圖/每日狀態)是否正常顯示，"
                            "只有需要跟基線比較的欄位(如候選池的delta%)顯示NO_DATA。")
def scenario_one_month_only():
    baseline = []
    current = generate_period(30, "2025-11-01", seed=6001, sys_base=128, hr_base=74,
                               weight_start=58.0, weight_readings=2,
                               temp_base=36.6, spo2_base=97,
                               water_short_rate=0.15, food_short_rate=0.15)
    return baseline, current


_S7_BASELINE_DAYS = 90
_S7_BASELINE_START = "2025-08-01"
_S7_CURRENT_DAYS = 30
_S7_CURRENT_START = "2025-11-01"
_S7_BASELINE_GAP_BLOCK = set(range(30, 45))          # 連續14天整段缺測
_S7_BASELINE_SCATTERED = {5, 60, 75}                  # 另外3天散落缺測
_S7_BASELINE_EXCLUDE = _S7_BASELINE_GAP_BLOCK | _S7_BASELINE_SCATTERED
_S7_CURRENT_SCATTERED = {10, 11, 20}                  # 本月3天散落缺測


@_reg("07_four_months_with_gaps", "四個月資料(前三月歷史＋當月)、中間有缺失："
                                   "baseline前三月(8/1–10/29)中，8/31–9/14整段15天"
                                   "完全沒有登打(模擬機構人力短缺/個案請假)，另外"
                                   "8/6、9/30、10/15三天散落缺測；current當月"
                                   "(11/1–11/30)則有11/11、11/12、11/21三天散落"
                                   "缺測。其餘天數皆正常登打，測試「長區段缺失+"
                                   "散落缺失混合」情境下完整度%計算(以首末日曆天"
                                   "跨距為分母)跟樣本有限標記是否正確。")
def scenario_gaps():
    baseline = generate_period(_S7_BASELINE_DAYS, _S7_BASELINE_START, seed=7001,
                                sys_base=126, hr_base=73, weight_start=59.0, weight_readings=3,
                                temp_base=36.6, spo2_base=97,
                                water_short_rate=0.1, food_short_rate=0.1,
                                exclude_days=_S7_BASELINE_EXCLUDE)
    baseline = _apply_weight_trend(baseline, 59.0, 58.6)
    current = generate_period(_S7_CURRENT_DAYS, _S7_CURRENT_START, seed=7002,
                               sys_base=126, hr_base=73, weight_start=58.6, weight_readings=2,
                               temp_base=36.6, spo2_base=97,
                               water_short_rate=0.1, food_short_rate=0.1,
                               exclude_days=_S7_CURRENT_SCATTERED)
    current = _apply_weight_trend(current, 58.6, 58.3)
    return baseline, current


_S12_CURRENT_DAYS = 30
_S12_CURRENT_START = "2025-11-01"
# 只保留這5天的資料，其餘25天整天缺測（exclude_days排除整天所有紀錄類型，
# 不是只排除血壓）——刻意讓本月密度掉到5/29≈17%，遠低於1/4門檻，血壓/
# 舒張壓/心率/體溫/飲水/進食會同時觸發樣本有限（因為都是同一批被排除的
# 天數），基線(3個月)維持正常密度當對照組，用來驗證「本月密度不足」這
# 條路徑（而不是基線不足）。
_S12_CURRENT_KEEP_DAYS = {0, 7, 14, 21, 28}
_S12_CURRENT_EXCLUDE = set(range(_S12_CURRENT_DAYS)) - _S12_CURRENT_KEEP_DAYS


@_reg("12_sparse_current_month_sample_limited", "情境3(2026-08-12新增)・本月資料極度稀疏"
      "但基線完整：baseline前三月(8/1–10/29)正常密度登打；current當月(11/1–11/30)"
      "只有11/1、11/8、11/15、11/22、11/29這5天有任何紀錄(密度5/29≈17%，"
      "遠低於1/4門檻)，其餘25天完全空白。同時讓收縮壓大幅上升(基線126→"
      "本月185附近，含2次危象)，測試「偏離幅度很大但樣本有限」時，"
      "系統是否正確把收縮壓/舒張壓/心率/體溫/飲水/進食都排除在top3外、"
      "改列進其餘偏離並標「樣本有限」，紀錄完整度對應項目也要出現橘色"
      "badge；體重不套用此規則，即使一樣稀疏也應該正常出現在top3。")
def scenario_sparse_current_month():
    baseline = generate_period(90, "2025-08-01", seed=12001, sys_base=126, hr_base=73,
                                weight_start=59.0, weight_readings=3,
                                temp_base=36.6, spo2_base=97,
                                water_short_rate=0.1, food_short_rate=0.1)
    baseline = _apply_weight_trend(baseline, 59.0, 58.6)
    current = generate_period(
        _S12_CURRENT_DAYS, _S12_CURRENT_START, seed=12002,
        sys_base=180, hr_base=73, crisis_days={0, 28},
        # weight_readings刻意設成>=days：generate_period內部用
        # step=days//weight_readings排程要不要在某天生成體重事件，
        # exclude_days又會把落在被排除天數的體重事件一併砍掉——如果只排
        # 2次體重(step=15)，兩個排程天有1個剛好落在被排除的天數，只會
        # 存活1筆讀數，_apply_weight_trend()看到只有1筆時fraction永遠是0，
        # 體重不會真的呈現下降趨勢。改成每天都排程(weight_readings=30)，
        # 讓5個保留天(0/7/14/21/28)各自都排到體重事件，確保剛好5筆讀數
        # 存活、趨勢線性內插才有意義。
        weight_start=58.6, weight_readings=_S12_CURRENT_DAYS,
        temp_base=36.6, spo2_base=97,
        water_short_rate=0.1, food_short_rate=0.1,
        exclude_days=_S12_CURRENT_EXCLUDE,
    )
    current = _apply_weight_trend(current, 58.6, 52.0)
    return baseline, current


@_reg("13_concentrated_overlap_demo", "簡報示範用・跨指標同期關聯偵測(集中窗口版)："
      "本月除了維持03情境的血壓危象(3次)/心率上升/體重下降(57.5→54.0kg)/"
      "進食不足45%等分散偏離(讓候選池/四面向卡有豐富內容)之外，額外讓"
      "11/10–11/13這4天同時出現發燒+血氧偏低，製造一段真正「短天期集中」"
      "的跨指標關聯期間，測試/展示detail句在日期範圍窄且訊號同步出現時"
      "的呈現效果，同時保留頁面整體視覺豐富度。")
def scenario_concentrated_overlap_demo():
    baseline = generate_period(90, "2025-08-01", seed=13001, sys_base=128, hr_base=76,
                                weight_start=58.0, weight_readings=3,
                                temp_base=36.6, spo2_base=97,
                                water_short_rate=0.1, food_short_rate=0.1)
    baseline = _apply_weight_trend(baseline, 58.0, 57.5)
    current = generate_period(
        30, "2025-11-01", seed=13002, sys_base=126, sys_drift=8,
        crisis_days={5, 20, 25}, hr_base=79,
        weight_start=57.5, weight_readings=3,
        temp_base=36.6, spo2_base=97,
        fever_days={9, 10, 11, 12}, spo2_low_days={9, 10, 11, 12},
        food_short_rate=0.3, water_short_rate=0.1,
    )
    current = _apply_weight_trend(current, 57.5, 54.0)
    return baseline, current


@_reg("14_stable_vitals_declining_care", "情境4(2026-08-19新增)・生理數值穩定，但生活照護逐步變差："
      "本月血壓/心率/體溫/血氧全部維持在基線附近(不觸發候選池)，但飲水未達標"
      "比例升高至40%(約12天)、進食不足比例升高至35%(約10-11天)、白天睡多"
      "集中出現在11/17–11/28共12天。測試候選池「不能只看生理量測」——"
      "情境4定案(2026-08-19)後，飲水/進食/白天睡多這幾項生活照護指標應該"
      "能直接排進top3、贏過完全正常的生理量測，不會因為都是生理量測才有"
      "資格排序。")
def scenario_stable_vitals_declining_care():
    baseline = generate_period(90, "2025-08-01", seed=14001, sys_base=122, hr_base=72,
                                weight_start=58.0, weight_readings=3,
                                temp_base=36.6, spo2_base=97,
                                water_short_rate=0.1, food_short_rate=0.1)
    baseline = _apply_weight_trend(baseline, 58.0, 57.9)
    current = generate_period(
        30, "2025-11-01", seed=14002, sys_base=122, hr_base=72,
        weight_start=57.9, weight_readings=2,
        temp_base=36.6, spo2_base=97,
        water_short_days=set(range(30)) - set(range(0, 30, 3)),  # 20/30天未達標
        food_short_rate=0.6,
        behaviors=[(i, "day_sleep") for i in range(16, 28)],
    )
    current = _apply_weight_trend(current, 57.9, 57.7)
    return baseline, current


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Downloads", "v9_test_scenarios")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=_DEFAULT_OUT)
    args = ap.parse_args()

    if os.path.isdir(args.out):
        for fn in os.listdir(args.out):
            if fn.endswith(".json") or fn == "README.md":
                os.remove(os.path.join(args.out, fn))
    os.makedirs(args.out, exist_ok=True)

    readme_lines = [
        "# v9 手動測試情境資料（2026-08-04 改成單一整段事件陣列）\n",
        "2026-08-04改法：真實上游資料是一整串事件，不會事先分成「前三月」"
        "「本月」兩包，後端改成自己依資料裡最新一筆紀錄的日曆月份切分"
        "（見`analysis_v9.split_events_by_current_month()`）。這裡的產生器"
        "維持原本每組情境各自的baseline/current生成邏輯（方便閱讀/調整"
        "個案設計），只在輸出前合併成一個依日期排序的陣列、存成單一檔案，"
        "貼進 `/`（原 v9_test.html）測試頁唯一的「照護紀錄資料」框即可。\n",
        "產生器原始碼：`scripts/gen_v9_manual_test_data.py`\n",
    ]

    def _event_sort_key(e):
        return e.get("OccurredAt") or e.get("RecordTime") or ""

    for name, (fn, note) in SCENARIOS.items():
        baseline, current = fn()
        combined = sorted(baseline + current, key=_event_sort_key)
        path = os.path.join(args.out, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(combined, f, ensure_ascii=False)
        readme_lines.append(
            f"## {name}\n{note}\n"
            f"- 合併後共 {len(combined)} 筆事件（baseline {len(baseline)} 筆 + current {len(current)} 筆）\n"
        )
        print(f"{name}: 合併 {len(combined)} 筆（baseline {len(baseline)} + current {len(current)}）")

    gap_dates_block = _dates_for(_S7_BASELINE_GAP_BLOCK, _S7_BASELINE_START, _S7_BASELINE_DAYS)
    gap_dates_scattered = _dates_for(_S7_BASELINE_SCATTERED, _S7_BASELINE_START, _S7_BASELINE_DAYS)
    gap_dates_current = _dates_for(_S7_CURRENT_SCATTERED, _S7_CURRENT_START, _S7_CURRENT_DAYS)
    readme_lines.append(
        "## 07_four_months_with_gaps 缺失明細\n"
        f"- baseline整段缺測：{gap_dates_block[0]} ~ {gap_dates_block[-1]}（共{len(gap_dates_block)}天）\n"
        f"- baseline散落缺測：{', '.join(gap_dates_scattered)}\n"
        f"- current散落缺測：{', '.join(gap_dates_current)}\n"
    )

    with open(os.path.join(args.out, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(readme_lines))
    print(f"\n完成，輸出到：{args.out}")
