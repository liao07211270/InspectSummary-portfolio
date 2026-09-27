"""
Mock data generator (v2) — one month of LTC resident vitals + care observations.

Scenario: an elderly hypertensive resident on a slow deterioration trajectory.
Injected signals (so the v2 report has real anomalies to surface):
  - Morning systolic drifts up   132 → 145 mmHg (crosses target 140)
  - Resting heart rate drifts up   70 →  88 bpm
  - SpO2 drifts down               97 →  93 %
  - 2 hypertensive-crisis days (systolic >= 180)
  - 1 low-temp day (35.6) and 1 high-temp day (37.3) vs personal baseline 36.8
  - A mid-month cluster: water/food below target, day-night sleep reversal,
    activity drop, social withdrawal, wandering behaviour, 1 walk-assist + fatigue

Weight is a monthly measurement; 6-month and last-month baselines are module
constants so the frailty / fluid blocks can compute % change and dry-weight gap.
"""
import random
from datetime import date, timedelta
import pandas as pd
import numpy as np


PATIENT = {
    "id": "P001",
    "name": "王大明",
    "age": 82,
    "gender": "男",
    "diagnoses": ["高齡高血壓", "衰弱 CFS 5"],
}

# ── Weight anchors (kg) — single monthly measurement + historical baselines ──
WEIGHT_NOW        = 57.0    # this month
WEIGHT_LAST_MONTH = 57.5    # last month  → 月變化 -0.5
WEIGHT_6MO_AGO    = 60.0    # 6 months ago → -5.0 %
DRY_WEIGHT        = 56.5    # clinical dry-weight baseline → +0.5 (no retention)

TEMP_BASELINE     = 36.8    # personal temperature baseline


def _rn(mu, sigma, lo=None, hi=None, rng=None):
    rng = rng or random
    while True:
        v = rng.gauss(mu, sigma)
        if lo is not None and v < lo:
            continue
        if hi is not None and v > hi:
            continue
        return round(v, 1)


def generate_vitals(days: int = 30, start: str = "2025-11-01", seed: int = 42) -> pd.DataFrame:
    rng = random.Random(seed)
    start_date = date.fromisoformat(start)

    # Mid-month deterioration window (roughly 11/14 onward for a 30-day month)
    mid = int(days * 0.45)

    rows = []
    for i in range(days):
        d = start_date + timedelta(days=i)
        frac = i / max(days - 1, 1)          # 0 → 1 across the month

        # ── Blood pressure: morning systolic 136 → 152 (clearly above 140),
        #    with elevated day-to-day variability so CV reads high vs baseline ──
        base_sys = 136 + frac * 16
        base_dia = 83 + frac * 4
        if i in (8, 22):                      # 2 crisis days
            sys_am = _rn(186, 4, 180, 198, rng)
            dia_am = _rn(104, 3, 100, 114, rng)
        else:
            sys_am = _rn(base_sys, 14, 106, 178, rng)
            dia_am = _rn(base_dia, 8, 58, 105, rng)
        sys_pm = _rn(base_sys - 14, 11, 100, 165, rng)   # 午
        dia_pm = _rn(base_dia - 6, 7, 54, 96, rng)
        sys_eve = _rn(base_sys - 8, 13, 100, 171, rng)   # 晚

        # ── Heart rate: 70 → 88 (rises with the mid-month decline) ──────
        hr = _rn(70 + frac * 18, 4, 50, 110, rng)

        # ── SpO2: 97 → 93, with low-O2 days clustered mid/late month ────
        spo2 = _rn(97 - frac * 4, 1.0, 88, 100, rng)

        # ── Temperature vs baseline 36.8; inject one low + one high ─────
        temp = _rn(TEMP_BASELINE, 0.25, 36.0, 37.1, rng)
        if i == mid + 5:                      # low-temp day (atypical infection)
            temp = 35.6
        elif i == mid + 12:                   # high-temp day
            temp = 37.3

        # ── Weight: single monthly measurement on day 0 ────────────────
        daily_weight = WEIGHT_NOW if i == 0 else None

        # ── Care observations ──────────────────────────────────────────
        # Water: more "未達標" in the mid-month window (target ~9 days short)
        water_short_p = 0.55 if i >= mid else 0.12
        water_intake = "未達標" if rng.random() < water_short_p else "達標"

        # Urine colour 1(light) – 6(dark); deeper when water is short
        if water_intake == "未達標":
            urine_color = rng.choice([4, 5, 5, 6])
        else:
            urine_color = rng.choice([1, 2, 2, 3, 3, 4])

        # Night-time voiding rises mid-month (fluid-shift signal)
        night_void = rng.choice([0, 1, 1]) if i < mid else rng.choice([1, 2, 2, 3])

        # Food intake: more "不足/拒食" mid-month (target ~12 short days)
        food_short_p = 0.55 if i >= mid else 0.20
        food_intake = ("不足" if rng.random() < food_short_p
                       else rng.choices(["正常", "拒食"], weights=[0.9, 0.1])[0])

        # Sleep: day-night reversal flagged on a mid-month cluster
        sleep_hrs = round(_rn(6.3, 1.2, 3, 9), 1)
        day_night_reversal = i >= mid and rng.random() < 0.4

        # Activity / rehab: ~4×/wk early → 1–2×/wk late
        rehab_p = 0.6 if i < mid else 0.18
        rehab_attended = 1 if rng.random() < rehab_p else 0

        # Social interaction: trends toward withdrawal
        if i < mid:
            social = rng.choices(["增", "持平", "減"], weights=[0.2, 0.6, 0.2])[0]
        else:
            social = rng.choices(["增", "持平", "減"], weights=[0.05, 0.35, 0.6])[0]

        # Mood / behaviour
        mood = rng.choices(["平穩", "焦慮", "易怒"],
                           weights=[0.7, 0.2, 0.1] if i < mid else [0.4, 0.35, 0.25])[0]
        abnormal_behavior = None
        if i in (mid + 4, mid + 6):           # wandering cluster (~11/19–21)
            abnormal_behavior = "遊走"
        elif i == mid + 9:
            abnormal_behavior = "拒絕照顧"

        walk_assist = (i == mid + 7)          # 1 new walk-assist event
        fatigue_report = (i == mid + 3)       # 1 fatigue report
        fall = False

        grip_strength = None                  # 握力 — not measured at day-care

        rows.append({
            "date": d.isoformat(),
            "systolic_am": sys_am, "diastolic_am": dia_am,
            "systolic_pm": sys_pm, "diastolic_pm": dia_pm,
            "systolic_eve": sys_eve,
            "heart_rate": hr,
            "spo2": spo2,
            "temperature": temp,
            "weight": daily_weight,
            "body_fat": None,          # 簡易版新欄位；模擬資料不生成（day-care 未量測）
            "water_intake": water_intake,
            "urine_color": urine_color,
            "night_void": night_void,
            "food_intake": food_intake,
            "sleep_hrs": sleep_hrs,
            "day_night_reversal": day_night_reversal,
            "rehab_attended": rehab_attended,
            "social": social,
            "mood": mood,
            "abnormal_behavior": abnormal_behavior,
            "walk_assist": walk_assist,
            "fatigue_report": fatigue_report,
            "fall": fall,
            "grip_strength": grip_strength,
        })

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_vitals()
    pd.set_option("display.max_columns", None, "display.width", 200)
    print(df.to_string())
