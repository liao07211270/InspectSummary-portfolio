"""
產生「隨行本真實格式」的模擬資料 —— 跟 src/mock_data.py 用同一套劇本/異常事件邏輯
（月中開始惡化、危象血壓、體溫偏低/偏高事件、遊走行為……seed 可重現），
但輸出的不是 analysis_core.py 吃的扁平表格，而是真實 DB 會送出的事件形狀：

  VitalSigns 表列： {"RecordTime": "...", "SystolicPressure": ..., ...}
  CareRecords 表列： {"RecordType": "Meal"/"Water"/"Urination"/"BodyMetrics"/"Behavior",
                      "OccurredAt": "...", "Content": {...}}

用來驗證 data_parser.py 對「真實形狀」輸入的解析邏輯，不是驗證 analysis_core.py 本身
（那件事 mock_data.py 已經覆蓋了）。

用法：
    python scripts/gen_real_schema_mock.py --days 30 --start 2026-05-01 --seed 42 \
        --out output/real_schema_payload.json
"""
import argparse
import json
import random
from datetime import date, timedelta

# ── 中文行為描述 → 真實系統的 26 種英文 code（挑最接近的對應） ────────────────
_BEHAVIOR_CODE = {
    "遊走": "wander",
    "拒絕照顧": "uncooperative",
}


def _rn(mu, sigma, lo=None, hi=None, rng=None):
    while True:
        v = rng.gauss(mu, sigma)
        if lo is not None and v < lo:
            continue
        if hi is not None and v > hi:
            continue
        return round(v, 1)


def _iso(d, hour, minute=0):
    return f"{d.isoformat()}T{hour:02d}:{minute:02d}:00"


def generate_real_schema_events(days: int = 30, start: str = "2026-05-01", seed: int = 42) -> list:
    rng = random.Random(seed)
    start_date = date.fromisoformat(start)
    mid = int(days * 0.45)

    events = []

    for i in range(days):
        d = start_date + timedelta(days=i)
        frac = i / max(days - 1, 1)

        # ── 生理量測（跟 mock_data.py 完全相同的產生邏輯）──────────────────
        base_sys = 136 + frac * 16
        base_dia = 83 + frac * 4
        if i in (8, 22):
            sys_am = _rn(186, 4, 180, 198, rng)
            dia_am = _rn(104, 3, 100, 114, rng)
        else:
            sys_am = _rn(base_sys, 14, 106, 178, rng)
            dia_am = _rn(base_dia, 8, 58, 105, rng)
        sys_pm = _rn(base_sys - 14, 11, 100, 165, rng)
        dia_pm = _rn(base_dia - 6, 7, 54, 96, rng)
        sys_eve = _rn(base_sys - 8, 13, 100, 171, rng)
        hr = _rn(70 + frac * 18, 4, 50, 110, rng)
        spo2 = _rn(97 - frac * 4, 1.0, 88, 100, rng)
        temp = _rn(36.8, 0.25, 36.0, 37.1, rng)
        if i == mid + 5:
            temp = 35.6
        elif i == mid + 12:
            temp = 37.3

        # VitalSigns：晨間一筆（血壓+心率+體溫+血氧一起量）、午間一筆、晚間一筆
        events.append({
            "RecordTime": _iso(d, 8, 0),
            "SystolicPressure": round(sys_am), "DiastolicPressure": round(dia_am),
            "Pulse": round(hr), "Temperature": temp, "BloodOxygen": round(spo2),
        })
        events.append({
            "RecordTime": _iso(d, 13, 0),
            "SystolicPressure": round(sys_pm), "DiastolicPressure": round(dia_pm),
        })
        events.append({
            "RecordTime": _iso(d, 19, 0),
            "SystolicPressure": round(sys_eve),
        })

        # ── 體重：只有第 0 天量（月量一次）──────────────────────────────
        if i == 0:
            events.append({
                "RecordType": "BodyMetrics", "OccurredAt": _iso(d, 7, 30),
                "Content": {"heightCm": 158, "weightKg": 57.0},
            })

        # ── 飲水：達標/未達標機率跟 mock_data.py 一致，換算成實際 ml 分次登打 ──
        water_short_p = 0.55 if i >= mid else 0.12
        water_short = rng.random() < water_short_p
        if water_short:
            total_ml = rng.randint(400, 1400)
            splits = rng.choice([2, 3])
        else:
            total_ml = rng.randint(1500, 2200)
            splits = rng.choice([3, 4])
        per = total_ml // splits
        times = sorted(rng.sample(range(7, 21), splits))
        remaining = total_ml
        for j, hour in enumerate(times):
            amt = per if j < splits - 1 else remaining
            remaining -= amt
            events.append({
                "RecordType": "Water", "OccurredAt": _iso(d, hour, rng.randint(0, 59)),
                "Content": {"amountMl": max(amt, 50)},
            })

        # ── 進食：不足/拒食機率跟 mock_data.py 一致，分早午晚三餐登打 ──────
        food_short_p = 0.55 if i >= mid else 0.20
        for meal_kind, hour in (("breakfast", 8), ("lunch", 12), ("dinner", 18)):
            if rng.random() < food_short_p:
                level = rng.choice(["lt_half", "half"])
            else:
                level = rng.choices(["most", "all"], weights=[0.4, 0.6])[0]
            events.append({
                "RecordType": "Meal", "OccurredAt": _iso(d, hour, rng.randint(0, 30)),
                "Content": {"mealKind": meal_kind, "intakeAmount": level},
            })

        # ── 排尿：夜間次數跟 mock_data.py 的 night_void 邏輯一致，另加日間幾筆 ──
        night_void_n = rng.choice([0, 1, 1]) if i < mid else rng.choice([1, 2, 2, 3])
        for _ in range(night_void_n):
            hour = rng.choice([23, 0, 1, 2, 3, 4, 5])
            events.append({
                "RecordType": "Urination", "OccurredAt": _iso(d, hour, rng.randint(0, 59)),
                "Content": {"status": "normal"},
            })
        for _ in range(rng.randint(2, 4)):  # 日間排尿，不影響 night_void 計算
            hour = rng.choice([7, 9, 11, 14, 16, 20])
            events.append({
                "RecordType": "Urination", "OccurredAt": _iso(d, hour, rng.randint(0, 59)),
                "Content": {"status": "normal"},
            })

        # ── 行為情緒：跟 mock_data.py 同樣的固定天數注入 ─────────────────
        cn_label = None
        if i in (mid + 4, mid + 6):
            cn_label = "遊走"
        elif i == mid + 9:
            cn_label = "拒絕照顧"
        if cn_label:
            events.append({
                "RecordType": "Behavior", "OccurredAt": _iso(d, 20, 0),
                "Content": {"behaviors": [_BEHAVIOR_CODE[cn_label]], "shift": "D"},
            })

    return events


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--start", default="2026-05-01")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--patient", default="測試個案")
    ap.add_argument("--out", default="output/real_schema_payload.json")
    args = ap.parse_args()

    events = generate_real_schema_events(days=args.days, start=args.start, seed=args.seed)
    payload = {
        "profile": {"patient_id": "T-REAL-001", "patient_name": args.patient},
        "_data": events,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"共 {len(events)} 筆事件，已寫入 {args.out}")
