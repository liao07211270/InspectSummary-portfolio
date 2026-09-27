# 鎖住今天實測抓到的 data_parser.py 行為：兩種真實 DB 資料形狀（VitalSigns／
# CareRecords）能正確分流解析，單筆記錄壞掉不會讓整批資料作廢，Medication／
# Hygiene 的解析層有正確寫入。
import pytest
from src.data_parser import (
    parse_measurement_data, build_dataframe_from_request, BEHAVIOR_LABELS,
    BEHAVIOR_PRIORITY_HIGH,
)


def test_vitalsigns_parsed_into_am_pm_eve_buckets():
    data = [
        {"RecordTime": "2026-05-01T08:00:00", "SystolicPressure": 130, "DiastolicPressure": 80},
        {"RecordTime": "2026-05-01T13:00:00", "SystolicPressure": 128, "DiastolicPressure": 78},
        {"RecordTime": "2026-05-01T19:00:00", "SystolicPressure": 135},
    ]
    df = parse_measurement_data(data)
    row = df.iloc[0]
    assert row["systolic_am"] == 130
    assert row["systolic_pm"] == 128
    assert row["systolic_eve"] == 135


def test_carerecords_meal_and_water_parsed():
    data = [
        {"RecordType": "Meal", "OccurredAt": "2026-05-01T08:00:00", "Content": {"intakeAmount": "lt_half"}},
        {"RecordType": "Water", "OccurredAt": "2026-05-01T10:00:00", "Content": {"amountMl": 800}},
        {"RecordType": "Water", "OccurredAt": "2026-05-01T15:00:00", "Content": {"amountMl": 500}},
    ]
    df = parse_measurement_data(data)
    row = df.iloc[0]
    assert row["food_intake"] == "不足"       # lt_half 屬於「不足」門檻
    assert row["water_intake"] == "未達標"    # 800+500=1300 < 1500 門檻


def test_urination_counts_only_night_hours():
    data = [
        {"RecordType": "Urination", "OccurredAt": "2026-05-01T23:30:00", "Content": {"status": "normal"}},
        {"RecordType": "Urination", "OccurredAt": "2026-05-01T14:00:00", "Content": {"status": "normal"}},
        {"RecordType": "Urination", "OccurredAt": "2026-05-01T02:00:00", "Content": {"status": "normal"}},
    ]
    df = parse_measurement_data(data)
    assert df.iloc[0]["night_void"] == 2   # 23:30 跟 02:00 算夜間，14:00 不算


def test_malformed_records_are_skipped_not_fatal():
    data = [
        {"RecordTime": "2026-05-01T08:00:00", "SystolicPressure": 130},
        "not a dict",
        123,
        None,
        {"RecordTime": "not-a-real-date", "SystolicPressure": 999},
    ]
    df = parse_measurement_data(data)
    assert len(df) == 1
    assert df.attrs["skipped_records"] == 4


def test_medication_refused_count():
    data = [
        {"RecordType": "Medication", "OccurredAt": "2026-05-01T08:00:00", "Content": {"route": "oral"}},
        {"RecordType": "Medication", "OccurredAt": "2026-05-01T12:00:00", "Content": {"route": "oral", "refused": True}},
    ]
    df = parse_measurement_data(data)
    row = df.iloc[0]
    assert row["medication_total"] == 2
    assert row["medication_refused"] == 1


def test_hygiene_items_summed():
    data = [{
        "RecordType": "Hygiene", "OccurredAt": "2026-05-01T09:00:00",
        "Content": {"items": ["bath", "hairWash"]},
    }]
    df = parse_measurement_data(data)
    assert df.iloc[0]["hygiene_items"] == 2


def test_behavior_labels_cover_all_26_real_codes():
    """跟真實 schema 頁面核對過的 26 種行為 code，字典要全部收錄，不能漏。"""
    real_codes = {
        "repeat_question", "lost_item", "false_accuse", "repeat_action", "curse",
        "improper_dress", "wander", "emotional_abnormal", "improper_undress",
        "hoard", "sexual_harass", "expose_private", "hallucination", "destroy",
        "irritable", "depressed", "complain", "no_interest", "day_sleep",
        "refuse_hygiene", "refuse_eat", "urine_incont", "bowel_incont", "elope",
        "uncooperative", "physical_aggression",
    }
    assert real_codes.issubset(BEHAVIOR_LABELS.keys())


def test_build_dataframe_from_request_rejects_non_dict_body():
    with pytest.raises(ValueError):
        build_dataframe_from_request(["oops"])


def test_build_dataframe_from_request_rejects_non_list_data():
    with pytest.raises(ValueError):
        build_dataframe_from_request({"_data": "oops"})


def test_empty_data_produces_full_column_schema_no_keyerror():
    df = build_dataframe_from_request({"_data": []})
    assert len(df) == 0
    assert "systolic_am" in df.columns
    assert "medication_total" in df.columns


# ── D6 事件優先級（v9 討論清單 PART1 第3題） ─────────────────────────────────
def test_high_priority_behavior_kept_individually():
    data = [{
        "RecordType": "Behavior", "OccurredAt": "2026-05-04T20:00:00",
        "Content": {"behaviors": ["physical_aggression", "complain"]},
    }]
    df = build_dataframe_from_request({"_data": data})
    ev = df.attrs["event_priority"]
    assert ev["high_priority"] == [
        {"date": "2026-05-04", "code": "physical_aggression", "label": "肢體攻擊"}
    ]
    assert ev["general_summary"]["count"] == 1
    assert ev["general_summary"]["by_code"] == {"抱怨": 1}


def test_medication_refused_tracked_as_separate_event_type():
    data = [{
        "RecordType": "Medication", "OccurredAt": "2026-05-06T09:00:00",
        "Content": {"refused": True},
    }]
    df = build_dataframe_from_request({"_data": data})
    assert df.attrs["event_priority"]["medication_refused_days"] == [
        {"date": "2026-05-06", "count": 1}
    ]


def test_empty_data_has_empty_event_priority_structure():
    df = build_dataframe_from_request({"_data": []})
    ev = df.attrs["event_priority"]
    assert ev["high_priority"] == []
    assert ev["medication_refused_days"] == []
    assert ev["general_summary"] == {"count": 0, "by_code": {}}


def test_high_priority_codes_are_a_subset_of_known_behavior_codes():
    """BEHAVIOR_PRIORITY_HIGH 只能引用真實存在的 26 種 code，不能筆誤打錯字
    導致某個安全疑慮行為悄悄漏分類、掉進「一般」彙總被吃掉。"""
    assert BEHAVIOR_PRIORITY_HIGH.issubset(BEHAVIOR_LABELS.keys())
