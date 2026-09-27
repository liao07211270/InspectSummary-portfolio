# 鎖住今天實測抓到的 NO_DATA 相關行為：資料不足時要回顯性的 NO_DATA 標記
# 或安全的預設值，不能崩潰、也不能把 NO_DATA 誤判成「有資料」（例如
# `if x` 對非空字串 "__NO_DATA__" 一律是 True 的經典陷阱）。
from src.data_parser import build_dataframe_from_request
from src.analysis_core import analyze_all, NO_DATA


def test_analyze_all_on_empty_data_does_not_crash():
    df = build_dataframe_from_request({"_data": []})
    result = analyze_all(df)
    assert result["header"]["care_days"] == 0
    assert result["obs"]["bp"]["ok_pct"] == NO_DATA
    assert result["signals"]["block1"]["reach_pct"] == NO_DATA


def test_single_weight_reading_is_not_enough_for_pct():
    """真實資料常見月量體重只有 1 筆——weight_now/weight_6mo 應該有值
    （首尾讀數相同），但 weight_pct 資料不足要是 NO_DATA，不能硬算出 0%。"""
    data = [{
        "RecordType": "BodyMetrics", "OccurredAt": "2026-05-01T07:30:00",
        "Content": {"weightKg": 57.0},
    }]
    df = build_dataframe_from_request({"_data": data})
    result = analyze_all(df)
    hwt = result["obs"]["hr_weight_temp"]
    assert hwt["weight_now"] == 57.0
    assert hwt["weight_6mo"] == 57.0
    assert hwt["weight_pct"] == NO_DATA


def test_two_weight_readings_compute_pct():
    data = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2026-05-01T07:30:00", "Content": {"weightKg": 60.0}},
        {"RecordType": "BodyMetrics", "OccurredAt": "2026-05-20T07:30:00", "Content": {"weightKg": 57.0}},
    ]
    df = build_dataframe_from_request({"_data": data})
    result = analyze_all(df)
    hwt = result["obs"]["hr_weight_temp"]
    assert hwt["weight_pct"] != NO_DATA
    assert hwt["weight_pct"] < 0   # 體重下降


def test_no_blood_pressure_readings_returns_no_data_not_crash():
    """整段觀察期沒有血壓量測，_obs_bp() 跟 _block1_cardio() 都要顯性退場，
    不能拿 None 去做除法或比較運算。"""
    data = [{
        "RecordType": "Meal", "OccurredAt": "2026-05-01T12:00:00",
        "Content": {"intakeAmount": "half"},
    }]
    df = build_dataframe_from_request({"_data": data})
    result = analyze_all(df)
    bp = result["obs"]["bp"]
    assert bp["total_count"] == 0
    assert bp["ok_pct"] == NO_DATA
    assert bp["trend_drawable"] is False


def test_real_data_flag_set():
    df = build_dataframe_from_request({"_data": []})
    assert df.attrs.get("is_real_data") is True


def test_abnormal_behavior_translated_to_chinese():
    data = [{
        "RecordType": "Behavior", "OccurredAt": "2026-05-01T20:00:00",
        "Content": {"behaviors": ["wander", "uncooperative"]},
    }]
    df = build_dataframe_from_request({"_data": data})
    row = df[df["date"] == "2026-05-01"].iloc[0]
    assert row["abnormal_behavior"] == "遊走、不合作/堅持"
    assert "wander" not in row["abnormal_behavior"]


# ── L1 candidate pool / D1 severity / D2 direction / top3 ────────────────────
def test_empty_data_top3_and_signal_pool_are_all_no_data():
    df = build_dataframe_from_request({"_data": []})
    result = analyze_all(df)
    assert result["top3"] == []
    assert all(s["no_data"] for s in result["signal_pool"])


def test_bp_crisis_ranks_as_severe_and_tops_the_list():
    data = [
        {"RecordTime": "2026-05-01T08:00:00", "SystolicPressure": 185, "DiastolicPressure": 100},
        {"RecordTime": "2026-05-02T08:00:00", "SystolicPressure": 150, "DiastolicPressure": 90},
    ]
    df = build_dataframe_from_request({"_data": data})
    result = analyze_all(df)
    bp_card = next(s for s in result["signal_pool"] if s["key"] == "bp")
    assert bp_card["severity"] == "重度"
    assert result["top3"][0]["key"] == "bp"


def test_weight_loss_direction_is_worsening_not_improving():
    """體重下降在衰弱情境算惡化方向，不是「改善中」（D2 worsening_if_positive=False）。"""
    data = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2026-05-01T07:00:00", "Content": {"weightKg": 60.0}},
        {"RecordType": "BodyMetrics", "OccurredAt": "2026-05-25T07:00:00", "Content": {"weightKg": 56.0}},
    ]
    df = build_dataframe_from_request({"_data": data})
    result = analyze_all(df)
    weight_card = next(s for s in result["signal_pool"] if s["key"] == "weight")
    assert weight_card["direction"] == "惡化"
    assert weight_card["trend_note"] == "持續惡化"


def test_signals_below_all_thresholds_are_excluded_from_top3():
    """輕微、未達最低門檻的偏離（severity=None）不該被選進頂部三項。"""
    data = [
        {"RecordTime": "2026-05-01T08:00:00", "SystolicPressure": 120, "DiastolicPressure": 78},
        {"RecordTime": "2026-05-02T08:00:00", "SystolicPressure": 118, "DiastolicPressure": 76},
    ]
    df = build_dataframe_from_request({"_data": data})
    result = analyze_all(df)
    assert result["top3"] == []
