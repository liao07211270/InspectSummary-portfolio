"""
2026-08-11起已停用：這支腳本產生的是baseline/current分兩個檔案的舊格式，
2026-08-04起真實API合約已經改成單一整段事件陣列(見routes_v9.py的
POST /v9/generate只吃一包`data`)，這支腳本沒有跟著更新，輸出格式已經
跟現在的合約對不起來。tests/fixtures/v9_scenarios/現在放的是用
gen_v9_manual_test_data.py產生（部分情境另有手動微調）的單一陣列格式，
**不要再執行這支腳本**，預設輸出路徑還是指向同一個資料夾，執行會用
舊格式覆蓋掉現在的內容。保留這個檔案只是為了保留舊版產生邏輯的參考，
不是可以安全執行的工具。

以下為原始說明（僅供參考，不代表目前建議用法）：
產生一組v9手動測試用的情境資料(baseline+current pairs)，真實schema事件形狀，
每組都刻意做出不同特徵，方便涵蓋不同運算路徑。輸出預設進repo裡的
tests/fixtures/v9_scenarios/，也可以用--out指到別的路徑重新產生。

用法：
    python scripts/gen_v9_test_scenarios.py
    python scripts/gen_v9_test_scenarios.py --out "自訂輸出路徑"
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
    urination_special_days=(), hygiene_events=None,
    sparse_rate=0.0,
):
    """behaviors: list of (day_index, code) tuples, e.g. [(3, "wander")]。
    urination_special_days: 該天至少一筆Urination記錄status=special（2026-07-31
    新增，測試體液代謝卡「排尿情況・特殊」）。
    hygiene_events: list of (day_index, item_name) tuples（2026-07-31新增，
    測試衰弱卡「衛生協助項目數」），跟behaviors同樣的tuple格式。
    hr_base：心率中心值，預設74（原本寫死，無法測試候選池心率偏離）。
    water_short_days：明確指定哪幾天飲水不足（不是機率），用來測試趨勢註記
    的「連續集中」措辭需要精準控制哪幾天——water_short_rate的隨機機率做不到
    這件事。有指定時優先於water_short_rate。"""
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
        if sparse_rate and rng.random() < sparse_rate:
            continue  # 該天完全沒有登打紀錄（資料稀疏情境用）
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
            n = len(weight_days & set(range(0, i + 1))) - 1
            total = max(len(weight_days) - 1, 1)
            w = round(weight_start + (weight_start * 0) , 1)  # placeholder overwritten below
            events.append({
                "RecordType": "BodyMetrics", "OccurredAt": _iso(d, 7, 30),
                "Content": {"heightCm": 158, "weightKg": w},
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

    # 體重讀數：依 weight_readings 數量從 weight_start 線性趨勢帶到 weight_end（若有指定）
    return events


def _apply_weight_trend(events, weight_start, weight_end):
    body = [e for e in events if e.get("RecordType") == "BodyMetrics"]
    n = len(body)
    for idx, e in enumerate(body):
        frac = idx / max(n - 1, 1)
        e["Content"]["weightKg"] = round(weight_start + (weight_end - weight_start) * frac, 1)
    return events


SCENARIOS = {}


def _reg(name, note):
    def deco(fn):
        SCENARIOS[name] = (fn, note)
        return fn
    return deco


@_reg("01_bp_crisis", "高血壓危象情境：前三月血壓平穩，本月出現多次≥180mmHg危象讀數，"
                       "測試偏離幅度最大三項/高血壓與心血管卡的危急次數計算。")
def scenario_bp_crisis():
    baseline = generate_period(60, "2025-08-01", seed=101, sys_base=130, sys_drift=0, weight_start=58.0, weight_readings=2)
    baseline = _apply_weight_trend(baseline, 58.0, 57.8)
    current = generate_period(30, "2025-11-01", seed=102, sys_base=140, sys_drift=25,
                               crisis_days={8, 15, 22, 27}, weight_start=57.8, weight_readings=1)
    current = _apply_weight_trend(current, 57.8, 57.5)
    return baseline, current


@_reg("02_weight_loss_frailty", "體重下降＋衰弱情境：本月體重相較前三月明顯下降（>=5%顯著界），"
                                 "同時進食不足比例升高、衛生協助項目數明顯增加（前三月2次、本月14次），"
                                 "測試衰弱與功能卡的體重/進食量/衛生協助項目數判讀。")
def scenario_weight_loss():
    baseline_hygiene = [(10, "洗澡"), (40, "洗澡")]
    baseline = generate_period(90, "2025-08-01", seed=201, sys_base=128, weight_start=62.0, weight_readings=3,
                                food_short_rate=0.1, hygiene_events=baseline_hygiene)
    baseline = _apply_weight_trend(baseline, 62.0, 61.5)
    current_hygiene = [(i, item) for i in (2, 6, 10, 14, 18, 22, 26) for item in ("洗澡", "更衣")]
    current = generate_period(30, "2025-11-01", seed=202, sys_base=128, weight_start=61.5, weight_readings=2,
                               food_short_rate=0.55, water_short_rate=0.4, hygiene_events=current_hygiene)
    current = _apply_weight_trend(current, 61.5, 57.8)  # 約6.0%下降
    return baseline, current


@_reg("03_infection_fever", "感染監測情境：本月出現多次發燒(>=38°C)跟血氧偏低事件，同時發燒期間"
                             "2天排尿狀況異常(status=special，模擬UTI症狀)，前三月體溫/血氧/排尿皆"
                             "平穩，測試感染監測卡的體溫偏離基線/血氧分布計算，以及體液代謝卡"
                             "「排尿情況・特殊」（夜間如廁次數異常天數本身是隨機的，每晚1-3次，"
                             "不是這組情境的設計重點）。")
def scenario_infection():
    baseline = generate_period(60, "2025-08-01", seed=301, sys_base=125, temp_base=36.6, spo2_base=97,
                                weight_start=55.0, weight_readings=2)
    baseline = _apply_weight_trend(baseline, 55.0, 55.0)
    current = generate_period(30, "2025-11-01", seed=302, sys_base=125, temp_base=36.6,
                               fever_days={5, 6, 14, 15, 21}, spo2_low_days={5, 6, 14, 15, 20, 21, 22},
                               urination_special_days={5, 6}, weight_start=55.0, weight_readings=1)
    current = _apply_weight_trend(current, 55.0, 54.8)
    return baseline, current


@_reg("04_behavior_events", "行為事件多發情境：本月出現多次遊走、1次肢體攻擊(高風險)、1次拒服，"
                             "測試事件優先級分類(高風險逐筆顯示 vs 一般彙總)跟遊走次數的候選池計算；"
                             "另外新增精神(irritable/depressed/complain共3天)、情緒(emotional_abnormal "
                             "2天)、睡眠(day_sleep 1天)分別觸發，跟尿失禁/大便失禁各1次，測試每日狀態"
                             "四列跟體液代謝卡失禁趨勢備註。")
def scenario_behavior():
    baseline = generate_period(60, "2025-08-01", seed=401, sys_base=130, weight_start=60.0, weight_readings=2,
                                behaviors=[(10, "wander")])
    baseline = _apply_weight_trend(baseline, 60.0, 59.8)
    current = generate_period(30, "2025-11-01", seed=402, sys_base=130, weight_start=59.8, weight_readings=1,
                               behaviors=[(4, "wander"), (9, "wander"), (13, "wander"), (13, "physical_aggression"),
                                          (20, "complain"), (22, "day_sleep"),
                                          (2, "irritable"), (16, "depressed"),
                                          (7, "emotional_abnormal"), (24, "emotional_abnormal"),
                                          (11, "urine_incont"), (17, "bowel_incont")],
                               medication_refused_days={6, 12, 19})
    current = _apply_weight_trend(current, 59.8, 59.5)
    return baseline, current


@_reg("05_sparse_data", "資料極稀疏情境：前三月跟本月都只有約3成天數有登打紀錄，"
                         "測試NO_DATA/樣本有限標記跟候選池在資料不足時的排除邏輯。")
def scenario_sparse():
    baseline = generate_period(60, "2025-08-01", seed=501, sys_base=133, weight_start=56.0, weight_readings=1,
                                sparse_rate=0.75)
    current = generate_period(30, "2025-11-01", seed=502, sys_base=133, weight_start=56.0, weight_readings=1,
                               sparse_rate=0.7)
    return baseline, current


@_reg("06_stable_no_deviation", "穩定無明顯偏離情境：本月跟前三月數值幾乎一致，"
                                 "測試候選池/頂部三項在「沒有明顯偏離」時是否正確回傳空清單，不硬湊假偏離。")
def scenario_stable():
    baseline = generate_period(60, "2025-08-01", seed=601, sys_base=126, weight_start=58.0, weight_readings=2,
                                food_short_rate=0.1, water_short_rate=0.1)
    baseline = _apply_weight_trend(baseline, 58.0, 58.0)
    current = generate_period(30, "2025-11-01", seed=602, sys_base=126, weight_start=58.0, weight_readings=2,
                               food_short_rate=0.1, water_short_rate=0.1)
    current = _apply_weight_trend(current, 58.0, 57.9)
    return baseline, current


@_reg("07_full_coverage", "全欄位覆蓋情境：刻意讓NOTE.md/計算方式整理裡列出的每一個數據項目"
                           "都有非零/非佔位的真實值可以看，用來一次驗證整份報告的呈現，不是要"
                           "模擬特定臨床情境。baseline故意拉長到180天(2025-05-01起)讓衰弱卡"
                           "體重能觸發「六個月」標籤；current(11月)集中安排5次血壓危象(測試"
                           "日期截斷)、4天連續飲水不足(測試趨勢註記「多集中於」)、5天連續"
                           "irritable(測試每日狀態橘紅線集中期)、2種高優先級行為(肢體攻擊+"
                           "偷跑走失，測試事件軸多類型選擇)、情緒/睡眠/尿失禁/大便失禁/拒絕"
                           "照顧各安排1-2次，衛生協助項目/進食量本月都明顯高於前三月。")
def scenario_full_coverage():
    baseline = generate_period(
        180, "2025-05-01", seed=701, sys_base=125, hr_base=70,
        weight_start=62.0, weight_readings=4,
        temp_base=36.6, spo2_base=97,
        water_short_rate=0.08, food_short_rate=0.08,
        urination_special_days={20, 90},
        hygiene_events=[(30, "洗澡"), (100, "洗澡"), (150, "更衣")],
    )
    baseline = _apply_weight_trend(baseline, 62.0, 60.5)

    current_hygiene = [(i, item) for i in (2, 5, 9, 12, 16, 19, 23, 26) for item in ("洗澡", "更衣")]
    current = generate_period(
        27, "2025-11-01", seed=702, sys_base=138, sys_drift=15,
        crisis_days={1, 8, 15, 22, 25}, hr_base=90,
        weight_start=60.5, weight_readings=3,
        temp_base=36.7, fever_days={9, 17, 21}, hypothermia_days={3},
        spo2_base=95, spo2_low_days={9, 10, 17, 21},
        water_short_days={5, 6, 7, 8}, food_short_rate=0.5,
        urination_special_days={9, 17, 21},
        hygiene_events=current_hygiene,
        behaviors=[
            (3, "irritable"), (4, "irritable"), (5, "irritable"), (6, "irritable"), (7, "irritable"),
            (12, "emotional_abnormal"), (20, "emotional_abnormal"),
            (18, "day_sleep"),
            (9, "physical_aggression"), (16, "elope"),
            (1, "wander"), (11, "wander"), (23, "wander"),
            (14, "urine_incont"), (15, "bowel_incont"),
            (13, "refuse_hygiene"), (24, "refuse_eat"),
        ],
        medication_refused_days={5, 12, 19},
    )
    current = _apply_weight_trend(current, 60.5, 55.6)  # 約8.1%下降，觸發重度門檻
    return baseline, current


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_OUT = os.path.join(_REPO_ROOT, "tests", "fixtures", "v9_scenarios")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=_DEFAULT_OUT)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    readme_lines = [
        "# v9 測試資料（v9 test fixtures）\n",
        "**這個資料夾是 v9（真實資料比較版）的測試資料**，跟 v2（模擬資料展示版）無關。\n",
        "## 用途\n",
        "每組都是 `baseline`（前三月）/`current`（當月）一對，**照真實照護資料庫schema"
        "的事件形狀**產生（`VitalSigns`的`RecordTime`+欄位、`CareRecords`的"
        "`RecordType`+`OccurredAt`+`Content`兩種形狀混合，詳見`src/data_parser.py`的"
        "docstring），不是這個系統自訂的格式。\n",
        "這批資料主要目的是給負責上游介接（照護隨行本後端）的工程師拿來**跟他們實際"
        "查出來的資料格式做比對**——如果他們送出的真實資料形狀跟這裡的測試資料對不"
        "起來（欄位名稱、巢狀結構、日期格式等），代表雙方對schema的理解有落差，可以"
        "及早發現、討論要統一成哪一種。\n",
        "## 怎麼用\n",
        "- 手動測試：把 `_baseline.json`／`_current.json` 內容合併成一個陣列"
        "（例如 `json.load(baseline_f) + json.load(current_f)`），貼進"
        "`static/index.html` 頁面（2026-07-31起是根路徑「/」預設頁面，原檔名"
        "`v9_test.html`）的「照護紀錄資料」框，按執行——2026-08-04起頁面改成"
        "只有一個輸入框，不分前三月/本月，後端會自己切分\n",
        "- 程式化測試：直接餵進 `src/analysis_v9.build_v9_view_model(baseline_events + current_events, profile)`\n",
        "- 產生器原始碼：`scripts/gen_v9_test_scenarios.py`（可調整參數重新產生，或當作"
        "範例照著寫新的測試情境）\n",
        "## 各組情境\n",
    ]

    for name, (fn, note) in SCENARIOS.items():
        baseline, current = fn()
        b_path = os.path.join(args.out, f"{name}_baseline.json")
        c_path = os.path.join(args.out, f"{name}_current.json")
        with open(b_path, "w", encoding="utf-8") as f:
            json.dump(baseline, f, ensure_ascii=False)
        with open(c_path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False)
        readme_lines.append(f"## {name}\n{note}\n- baseline: {len(baseline)} 筆事件\n- current: {len(current)} 筆事件\n")
        print(f"{name}: baseline {len(baseline)} 筆, current {len(current)} 筆")

    with open(os.path.join(args.out, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(readme_lines))
    print(f"\n完成，輸出到：{args.out}")
