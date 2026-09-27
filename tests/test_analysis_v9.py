# 鎖住 v9「比較運算」層(analysis_v9.py)的基本行為：單一整段真實schema事件
# 陣列進來（2026-08-04起不再要求呼叫端自己分好前三月/本月兩包），能吐出
# report_pdf_v9.py 要的 view-model 形狀，空資料不崩潰，已知資料缺口欄位
# 維持固定佔位內容。
from src.analysis_core import NO_DATA
from src.analysis_v9 import (
    build_v9_view_model, split_events_by_current_month, extended_baseline_events,
    _COLOR_NORMAL, _COLOR_ABNORMAL, _COLOR_CLUSTER,
)


def test_split_puts_latest_calendar_month_events_into_current():
    """本月＝資料裡最新一筆紀錄所在的日曆月份，不管最新紀錄是幾號——這裡
    最新紀錄是11/15，所以11月的兩筆都算current，8月的算baseline。"""
    events = [
        {"RecordTime": "2025-08-01T08:00:00", "SystolicPressure": 120},
        {"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 130},
        {"RecordTime": "2025-11-15T08:00:00", "SystolicPressure": 132},
    ]
    baseline, current = split_events_by_current_month(events)
    assert len(baseline) == 1 and baseline[0]["RecordTime"].startswith("2025-08")
    assert len(current) == 2
    assert all(e["RecordTime"].startswith("2025-11") for e in current)


def test_split_caps_baseline_at_three_months():
    """2026-08-05修正：一般欄位（血壓/血氧/飲水/進食/體溫等）的baseline
    改回真的只取最近3個月——如果不設上限，上游一次給超過3個月的歷史時，
    「前三月近期趨勢」會被更久以前的資料稀釋，看不出真正的近期變化。
    這裡5月那筆(距11月本月已經6個月)不該被split_events_by_current_month()
    納入一般baseline，只有extended_baseline_events()才會抓到它（見下一則
    測試）。"""
    events = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-05-01T07:30:00", "Content": {"weightKg": 62.0}},
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-09-01T07:30:00", "Content": {"weightKg": 60.0}},
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 58.0}},
    ]
    baseline, current = split_events_by_current_month(events)
    assert len(baseline) == 1
    assert baseline[0]["OccurredAt"].startswith("2025-09")
    assert len(current) == 1


def test_extended_baseline_events_looks_back_up_to_six_months_for_weight():
    """體重長期趨勢專用的擴充窗口：5月(距本月6個月)那筆該被納入，這是
    split_events_by_current_month()故意排除、只有這個函式才會抓的範圍。"""
    events = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-05-01T07:30:00", "Content": {"weightKg": 62.0}},
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-09-01T07:30:00", "Content": {"weightKg": 60.0}},
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 58.0}},
    ]
    weight_baseline = extended_baseline_events(events)
    assert len(weight_baseline) == 2
    assert {e["OccurredAt"][:7] for e in weight_baseline} == {"2025-05", "2025-09"}


def test_recent_indicators_not_diluted_by_weight_only_long_history():
    """端對端驗證：即使上游給了6個月歷史，血壓這種「前三月近期趨勢」欄位
    的baseline平均只該用最近3個月算，不能被5月那筆(6個月前、血壓明顯偏高)
    拉高，稀釋掉近期(9-10月)其實已經正常的事實；但體重仍要能看到5月那筆
    才能正確算出六個月的長期趨勢。"""
    events = [
        {"RecordTime": "2025-05-01T08:00:00", "SystolicPressure": 170},  # 6個月前，故意异常，不該進baseline平均
        {"RecordTime": "2025-09-01T08:00:00", "SystolicPressure": 120},
        {"RecordTime": "2025-09-15T08:00:00", "SystolicPressure": 122},
        {"RecordTime": "2025-10-01T08:00:00", "SystolicPressure": 121},
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-05-01T07:30:00", "Content": {"weightKg": 62.0}},
        {"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 123},
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 58.0}},
    ]
    vm = build_v9_view_model(events, {})
    bp_card = next(c for c in vm["four_aspects"] if c["title"] == "高血壓與心血管")
    # 前三月月均血壓不該被170拉高——120/122/121三筆平均約121，不會是
    # 摻進170之後那種明顯更高的數字。
    assert "170" not in "".join(bp_card["trend_note"])
    weight_card = next(c for c in vm["four_aspects"] if c["title"] == "衰弱與功能")
    assert weight_card["primary_label"] == "體重・六個月"
    assert weight_card["primary_before"] == 62.0


def test_split_handles_empty_and_undated_events():
    baseline, current = split_events_by_current_month([])
    assert baseline == [] and current == []
    events = [{"RecordType": "Behavior", "Content": {}}]  # 沒有OccurredAt/RecordTime
    baseline, current = split_events_by_current_month(events)
    assert baseline == [] and current == []


def test_baseline_todo_shows_real_date_range_and_comparison_method_when_data_sufficient():
    """2026-08-05修正：「個人基線」原本只顯示天數(90 天歷史資料)，「跨月
    比較」是完全沒意義的固定字「同期對照」——照主管原始設計稿的意圖改成
    如實呈現基線實際涵蓋的日期區間，跟已經定案的回溯月數方法(一般3個月/
    體重6個月)。這裡baseline的血壓在8/1(離本月3個月整)、體重在5/1(離本月
    6個月整)，兩者都剛好達標，該顯示目標值而不是實際計算出來的數字。"""
    baseline = [
        {"RecordTime": "2025-08-01T08:00:00", "SystolicPressure": 120},
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-05-01T07:30:00", "Content": {"weightKg": 62.0}},
    ]
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 130},
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 58.0}},
    ]
    vm = build_v9_view_model(baseline + current, {})
    todo = dict(vm["check_notes"]["baseline_todo"])
    # 5/1那筆體重超出一般3個月的回溯窗口，不算進「個人基線」（那是一般
    # 欄位用的3個月窗口），只有8/1那筆血壓落在窗口內。
    assert todo["個人基線"] == "8/1–8/1（1 天）"
    assert todo["跨月比較"] == "本月 vs 前3個月"


def test_baseline_todo_degrades_when_data_shorter_than_target_window():
    """2026-08-12改法：資料實際涵蓋不到3個月（這裡baseline只有10/15一筆，
    離本月約半個月）現在改套用情境1（首月無個人基線）分支，不再是舊版
    「跨月比較」degraded文字——「個人基線」改成如實說明「尚未累積滿3個月」
    （不是舊版繼續講目標值的degraded措辭），「比較方式」固定顯示改採公定
    標準判讀。"""
    baseline = [{"RecordTime": "2025-10-15T08:00:00", "SystolicPressure": 120}]
    current = [{"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 130}]
    vm = build_v9_view_model(baseline + current, {})
    todo = dict(vm["check_notes"]["baseline_todo"])
    assert todo["個人基線"] == "尚未累積滿3個月（已有17天資料）"
    assert todo["比較方式"] == "採公定參考值判讀"


def test_baseline_todo_shows_no_history_placeholder_when_baseline_empty():
    """2026-08-12改法：剛收案、完全沒有前期歷史時一樣落入情境1分支——
    「個人基線」要誠實顯示「尚未建立（本月為首次收案）」，不能顯示「0 天」
    這種容易被誤讀成「有資料但剛好0天」的數字；「比較方式」固定顯示改採
    公定標準判讀，不再宣稱前3個月/體重6個月這種還沒發生的比較窗口。"""
    current = [{"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 130}]
    vm = build_v9_view_model(current, {})
    todo = dict(vm["check_notes"]["baseline_todo"])
    assert todo["個人基線"] == "尚未建立（本月為首次收案）"
    assert todo["比較方式"] == "採公定參考值判讀"


def test_empty_periods_do_not_crash_and_use_placeholders():
    vm = build_v9_view_model([], {"name": "測試"})
    assert vm["header"]["name"] == "測試"
    assert vm["top3"] == []
    # 完全沒有事件時（span=0天）會落入情境1(無個人基線)分支——四張卡片的
    # chips前值一律是None（不比較基線），不能崩潰也不能把NO_DATA字面塞進
    # 畫面。
    assert vm["four_aspects"][1]["chips"][0] == ("特殊排尿", None, "— 天")


def test_real_comparison_produces_before_after_pairs():
    baseline = [
        {"RecordTime": "2025-08-01T08:00:00", "SystolicPressure": 120, "DiastolicPressure": 80},
        {"RecordTime": "2025-08-02T08:00:00", "SystolicPressure": 122, "DiastolicPressure": 80},
    ]
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 180, "DiastolicPressure": 100},
        {"RecordTime": "2025-11-02T08:00:00", "SystolicPressure": 182, "DiastolicPressure": 100},
    ]
    vm = build_v9_view_model(baseline + current, {"name": "王大明"})
    assert vm["top3"], "血壓大幅惡化應該被選進頂部三項"
    bp_item = vm["top3"][0]
    assert "120" in bp_item["value"] or "121" in bp_item["value"]
    assert "180" in bp_item["value"] or "181" in bp_item["value"]


def test_infection_card_mental_wellbeing_uses_real_behavior_codes_not_placeholder():
    """2026-07-31：感染監測卡「精神不佳」原本是寫死的假數值(1天/5天，永遠
    不變)，改用跟每日狀態「精神」列同一組真實行為代碼(irritable/depressed/
    complain)計算，並確認trend_note不再出現「佔位」字樣。2026-08-04：主副
    欄位對調（體溫偏離基線改當主軸），精神不佳改列進chips，這裡的斷言
    跟著改，但驗證的計算邏輯本身不變。"""
    # 空事件(span=0天)落入情境1(無個人基線)分支，主欄位改叫「體溫異常天數」
    # （用絕對門檻判斷，不是「偏離基線」——首月個案還沒有基線可比）。
    vm_empty = build_v9_view_model([], {})
    infection_card_empty = vm_empty["four_aspects"][2]
    assert infection_card_empty["primary_label"] == "體溫異常天數"
    assert "佔位" not in "".join(infection_card_empty["trend_note"])

    current = [
        {"RecordType": "Behavior", "OccurredAt": "2025-11-05T20:00:00", "Content": {"behaviors": ["irritable"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-12T20:00:00", "Content": {"behaviors": ["depressed"]}},
    ]
    # 這裡資料只涵蓋11/5–11/12(7天)，一樣落入情境1分支，chips前值為None。
    vm = build_v9_view_model(current, {})
    infection_card = vm["four_aspects"][2]
    mental_chip = next(c for c in infection_card["chips"] if c[0] == "精神不佳")
    assert mental_chip[1] is None
    assert mental_chip[2] == "2 天"


def test_weight_card_uses_earliest_baseline_reading_not_latest():
    """做法B：衰弱卡體重比較要用baseline裡最早一筆讀數，不是最後一筆——
    baseline體重從62降到61.5，current體重57.8，正確答案要抓62(最早)不是61.5(最後)。"""
    baseline = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-08-01T07:30:00", "Content": {"weightKg": 62.0}},
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-10-20T07:30:00", "Content": {"weightKg": 61.5}},
    ]
    current = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 57.8}}]
    vm = build_v9_view_model(baseline + current, {})
    weight_card = next(c for c in vm["four_aspects"] if c["title"] == "衰弱與功能")
    assert weight_card["primary_before"] == 62.0


def test_weight_span_label_reflects_actual_data_not_hardcoded_six_months():
    """衰弱卡「體重・OO個月」標籤：資料真的涵蓋六個月才顯示「六個月」，
    不足六個月是例外情況要如實顯示實際月數，不能謊報做不到的時間長度。"""
    short_baseline = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-08-01T07:30:00", "Content": {"weightKg": 60.0}}]
    short_current = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 58.0}}]
    vm_short = build_v9_view_model(short_baseline + short_current, {})
    weight_card_short = next(c for c in vm_short["four_aspects"] if c["title"] == "衰弱與功能")
    assert weight_card_short["primary_label"] != "體重・六個月"
    assert "個月" in weight_card_short["primary_label"]

    long_baseline = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-05-01T07:30:00", "Content": {"weightKg": 62.0}}]
    long_current = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 58.0}}]
    vm_long = build_v9_view_model(long_baseline + long_current, {})
    weight_card_long = next(c for c in vm_long["four_aspects"] if c["title"] == "衰弱與功能")
    assert weight_card_long["primary_label"] == "體重・六個月"


def test_weight_candidate_pool_label_matches_frailty_card_not_hardcoded():
    """2026-08-06修正：使用者截圖抓到「其餘偏離3項」裡的體重項寫死顯示
    「體重・近六個月」，但衰弱卡同一份資料動態算出來的是「體重・近4個月」
    ——同一件事兩處講法不一致。這裡構造baseline(8/1)到current(11/30)實際
    只跨約4個月的資料，驗證候選池(top3/remaining_deviations)跟衰弱卡
    (four_aspects)算出來的體重標籤是同一個字串，不會各說各話。"""
    baseline = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-08-01T07:30:00", "Content": {"weightKg": 60.0}}]
    current = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-11-30T07:30:00", "Content": {"weightKg": 55.2}}]
    vm = build_v9_view_model(baseline + current, {})
    frailty_card = next(c for c in vm["four_aspects"] if c["title"] == "衰弱與功能")
    weight_label = frailty_card["primary_label"]
    assert weight_label not in ("體重・近六個月", "體重・六個月"), "資料只涵蓋約4個月，不該顯示六個月"

    all_pool_names = [t["name"] for t in vm["top3"]] + [name for name, _, _, _ in vm["check_notes"]["remaining_deviations"]]
    assert weight_label in all_pool_names, "候選池的體重項名稱應該跟衰弱卡完全一致，不能各自寫死不同數字"


def test_no_data_sentinel_never_leaks_into_rendered_text_or_chart_widths():
    """實測抓到的bug：沒有Water事件時water_short_days是NO_DATA字串常數，
    直接f-string塞進primary_before/after會把"__NO_DATA__"這個內部標記字面
    印到PDF上；bp分布長條的segments也曾用`v or 0`擋NO_DATA，但NO_DATA是
    非空字串、Python視為真值，`or 0`救不了它，一樣會把NO_DATA塞進CSS寬度。"""
    baseline = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-08-01T07:30:00", "Content": {"weightKg": 60.0}}]
    current = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 58.0}}]
    vm = build_v9_view_model(baseline + current, {})

    water_card = next(c for c in vm["four_aspects"] if c["title"] == "體液代謝與容積")
    assert NO_DATA not in water_card["primary_before"]
    assert NO_DATA not in water_card["primary_after"]

    for pct, _color in vm["bp_distribution"]["segments"]:
        assert pct != NO_DATA
        assert isinstance(pct, (int, float))


def test_urination_and_defecation_chips_use_real_records():
    """2026-08-03：夜間如廁次數子項因為日照個案晚上不在機構、Urination
    時間戳推算法邏輯不成立，改用Defecation（排便異常，跟Urination一樣
    有status正常/特殊，不受時段限制）取代。這裡驗證兩個子項各自獨立：
    current period有1天特殊排尿、1天排便特殊。"""
    current = [
        {"RecordType": "Urination", "OccurredAt": "2025-11-01T09:00:00", "Content": {"status": "special", "note": "血尿"}},
        {"RecordType": "Urination", "OccurredAt": "2025-11-02T09:00:00", "Content": {"status": "normal"}},
        {"RecordType": "Defecation", "OccurredAt": "2025-11-01T10:00:00", "Content": {"status": "special", "note": "血便"}},
        {"RecordType": "Defecation", "OccurredAt": "2025-11-02T10:00:00", "Content": {"status": "normal"}},
    ]
    # 資料只涵蓋11/1–11/2，落入情境1(無個人基線)分支，chips前值為None。
    vm = build_v9_view_model(current, {})
    water_card = next(c for c in vm["four_aspects"] if c["title"] == "體液代謝與容積")
    urination_chip, defecation_chip = water_card["chips"]
    assert urination_chip == ("特殊排尿", None, "1 天")
    assert defecation_chip == ("排便異常", None, "1 天")


def test_hygiene_and_urination_show_zero_not_no_data_when_period_has_other_records():
    """2026-07-31修正：使用者實測抓到的bug——某情境本月有BP/體重等紀錄，
    但剛好沒有Hygiene紀錄，畫面顯示「— 次」（NO_DATA）；但這個月明明有在
    收案觀察，只是衛生協助真的是0次，不是「無法評估」，應該顯示「0 次」。
    這裡故意只塞BodyMetrics(沒有Hygiene/Urination)驗證三個子項都正確歸零。"""
    current = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 58.0}},
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-02T07:30:00", "Content": {"weightKg": 58.0}},
    ]
    vm = build_v9_view_model(current, {})
    water_card = next(c for c in vm["four_aspects"] if c["title"] == "體液代謝與容積")
    frailty_card = next(c for c in vm["four_aspects"] if c["title"] == "衰弱與功能")
    urination_chip, defecation_chip = water_card["chips"]
    hygiene_chip = next(c for c in frailty_card["chips"] if c[0] == "衛生協助項目")
    assert urination_chip[2] == "0 天"
    assert defecation_chip[2] == "0 天"
    assert hygiene_chip[2] == "0 次"


def test_hygiene_items_chip_reflects_real_hygiene_records():
    """衰弱卡「活動投入」佔位子項改用真實Hygiene record算出的協助項目數，
    2026-07-31改成月平均正規化（3項目/2天*30天=45次/月），跟夜間如廁次數
    同一套邏輯，避免baseline橫跨前三月時被期間長度誤導。"""
    current = [
        {"RecordType": "Hygiene", "OccurredAt": "2025-11-01T09:00:00", "Content": {"items": ["洗澡", "更衣"]}},
        {"RecordType": "Hygiene", "OccurredAt": "2025-11-02T09:00:00", "Content": {"items": ["洗澡"]}},
    ]
    # 資料只涵蓋11/1–11/2，落入情境1(無個人基線)分支，chips前值為None。
    vm = build_v9_view_model(current, {})
    frailty_card = next(c for c in vm["four_aspects"] if c["title"] == "衰弱與功能")
    hygiene_chip = next(c for c in frailty_card["chips"] if c[0] == "衛生協助項目")
    assert hygiene_chip == ("衛生協助項目", None, "45 次")


def test_daily_status_rows_reflect_real_behavior_codes_not_placeholder():
    """每日狀態四列改用真實Behavior代碼：精神=irritable/depressed/complain，
    情緒=emotional_abnormal，睡眠=day_sleep，各自的異常天數要對得上實際
    發生的天數，不能再是寫死的「系統無來源（佔位）」。"""
    current = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 58.0}},
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-02T07:30:00", "Content": {"weightKg": 58.0}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-01T20:00:00", "Content": {"behaviors": ["irritable"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-02T20:00:00", "Content": {"behaviors": ["emotional_abnormal", "day_sleep"]}},
    ]
    vm = build_v9_view_model(current, {})
    rows = {r["label"]: r for r in vm["daily_status"]["rows"]}
    assert rows["精神"]["note"] == "異常 1次"
    assert rows["情緒"]["note"] == "異常 1次"
    assert rows["睡眠"]["note"] == "白天睡多 1次"
    assert "佔位" not in rows["精神"]["note"]


def test_events_row_prioritizes_high_priority_codes_over_general():
    """偶發事件軸：本月若出現安全疑慮類代碼(如肢體攻擊)，一定會被選中顯示，
    即使一般行為(如抱怨)次數更多也不能把它擠掉——但名額還有剩時，一般行為
    仍可以遞補進來（2026-08-06改法，見下面test_events_row_fills_remaining_
    slots_with_general_when_room），這裡只驗證高優先級「一定入選」這件事。"""
    current = [
        {"RecordType": "Behavior", "OccurredAt": "2025-11-01T20:00:00", "Content": {"behaviors": ["physical_aggression"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-02T20:00:00", "Content": {"behaviors": ["complain"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-03T20:00:00", "Content": {"behaviors": ["complain"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-04T20:00:00", "Content": {"behaviors": ["complain"]}},
    ]
    vm = build_v9_view_model(current, {})
    labels = [item["label"] for item in vm["daily_status"]["events_row"]["legend_items"]]
    assert "肢體攻擊" in labels


def test_events_row_falls_back_to_general_behavior_when_no_high_priority():
    """完全沒有安全疑慮類代碼時，改顯示一般行為裡本月次數最高的類型。"""
    current = [
        {"RecordType": "Behavior", "OccurredAt": "2025-11-01T20:00:00", "Content": {"behaviors": ["complain"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-02T20:00:00", "Content": {"behaviors": ["complain"]}},
    ]
    vm = build_v9_view_model(current, {})
    labels = [item["label"] for item in vm["daily_status"]["events_row"]["legend_items"]]
    assert "抱怨" in labels


def test_events_row_fills_remaining_slots_with_general_when_room():
    """2026-08-06新增：高優先級代碼不到4種時，剩餘名額改由一般行為依本月
    次數高到低遞補填滿，不再像舊版一樣只要出現任何高優先級代碼，一般行為
    就完全被排除在外——只有1種肢體攻擊+3種一般行為，名額4個剛好都塞得下，
    全部都該出現在legend裡。"""
    current = [
        {"RecordType": "Behavior", "OccurredAt": "2025-11-01T20:00:00", "Content": {"behaviors": ["physical_aggression"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-02T20:00:00", "Content": {"behaviors": ["complain"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-03T20:00:00", "Content": {"behaviors": ["wander"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-04T20:00:00", "Content": {"behaviors": ["day_sleep"]}},
    ]
    vm = build_v9_view_model(current, {})
    legend = vm["daily_status"]["events_row"]["legend_items"]
    labels = [item["label"] for item in legend]
    assert set(labels) == {"肢體攻擊", "抱怨", "遊走", "白天睡多"}
    assert not any(item["label"] == "其他行為" for item in legend)


def test_events_row_aggregates_overflow_into_other_count():
    """2026-08-06新增：本月出現的行為類型超過4種時，選不進前4名額的代碼
    不逐一顯示，改彙總成一則「其他行為（N次）」，N是這些被排除代碼的
    次數加總（不是排除的類型數）。次數並列時依BEHAVIOR_LABELS定義順序決定
    性排序（見_BEHAVIOR_ORDER_INDEX），這裡的期望值就是照這個順序算出來的：
    肢體攻擊(高優先級必入選)+遊走/抱怨(各2次，次數並列最高，wander在
    BEHAVIOR_LABELS裡排在complain前面)+curse(1次，三個並列1次的代碼裡
    curse在BEHAVIOR_LABELS排最前面)佔滿4個名額；剩下hoard(1次)+
    day_sleep(1次)＝2次被排除彙總。"""
    current = [
        {"RecordType": "Behavior", "OccurredAt": "2025-11-01T20:00:00", "Content": {"behaviors": ["physical_aggression"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-02T20:00:00", "Content": {"behaviors": ["complain"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-03T20:00:00", "Content": {"behaviors": ["complain"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-04T20:00:00", "Content": {"behaviors": ["wander"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-05T20:00:00", "Content": {"behaviors": ["wander"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-06T20:00:00", "Content": {"behaviors": ["day_sleep"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-07T20:00:00", "Content": {"behaviors": ["hoard"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-08T20:00:00", "Content": {"behaviors": ["curse"]}},
    ]
    vm = build_v9_view_model(current, {})
    legend = vm["daily_status"]["events_row"]["legend_items"]
    labels = [item["label"] for item in legend]
    assert set(labels) == {"肢體攻擊", "遊走", "抱怨", "罵人或詛咒人", "其他行為"}
    other = next(item for item in legend if item["label"] == "其他行為")
    assert other["count"] == 2


def test_incontinence_mentioned_in_trend_note_only_when_present():
    """尿失禁/大便失禁不做獨立子項卡片，只在體液代謝卡trend_note裡提一句
    （使用者裁示：描寫在趨勢部分即可，若有發生）——沒發生時不該出現這句話。"""
    no_incontinence = build_v9_view_model([], {})
    water_card = next(c for c in no_incontinence["four_aspects"] if c["title"] == "體液代謝與容積")
    assert "尿失禁" not in "".join(water_card["trend_note"])

    current = [{"RecordType": "Behavior", "OccurredAt": "2025-11-01T20:00:00", "Content": {"behaviors": ["urine_incont"]}}]
    with_incontinence = build_v9_view_model(current, {})
    water_card2 = next(c for c in with_incontinence["four_aspects"] if c["title"] == "體液代謝與容積")
    assert "尿失禁" in "".join(water_card2["trend_note"])


def test_incontinence_urine_and_bowel_are_separate_bullet_items():
    """2026-08-11修正：trend_note改成條列式清單後，尿失禁跟大便失禁應該是
    兩個各自獨立的list項目，不再像舊版用「、」黏成一句——這裡讓兩種都
    發生，驗證清單裡各自是獨立元素，不是合併成單一字串。"""
    current = [
        {"RecordType": "Behavior", "OccurredAt": "2025-11-01T20:00:00", "Content": {"behaviors": ["urine_incont"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-02T20:00:00", "Content": {"behaviors": ["bowel_incont"]}},
    ]
    vm = build_v9_view_model(current, {})
    water_card = next(c for c in vm["four_aspects"] if c["title"] == "體液代謝與容積")
    urine_items = [item for item in water_card["trend_note"] if item.startswith("尿失禁")]
    bowel_items = [item for item in water_card["trend_note"] if item.startswith("大便失禁")]
    assert len(urine_items) == 1
    assert len(bowel_items) == 1


def _daily_records(days=27, start="2025-11-01"):
    """每天塞一筆BodyMetrics，讓current_df涵蓋固定天數，方便用day index算日期。"""
    import datetime
    d0 = datetime.date.fromisoformat(start)
    return [
        {"RecordType": "BodyMetrics", "OccurredAt": f"{(d0 + datetime.timedelta(days=i)).isoformat()}T07:30:00",
         "Content": {"weightKg": 58.0}}
        for i in range(days)
    ]


def test_five_consecutive_abnormal_days_are_marked_as_cluster_not_just_abnormal():
    """2026-07-31新增：橘紅線(偏離集中期)原本是寫死佔位，完全沒有真的判斷。
    使用者裁示規則：連續異常就算集中期，該列那幾天的格子要標橘紅色(不是
    單純異常的灰色)，且cluster_label要顯示真實日期範圍，不能再是「系統無
    來源（佔位）」。2026-08-13門檻從5天改成2天(見_CLUSTER_MIN_CONSECUTIVE)，
    這裡用5天(比新門檻更長)驗證仍然正確觸發，2天邊界的專屬測試見下面
    test_two_consecutive_abnormal_days_now_triggers_cluster_after_threshold_change。"""
    current = _daily_records() + [
        {"RecordType": "Behavior", "OccurredAt": f"2025-11-{4 + i:02d}T20:00:00", "Content": {"behaviors": ["irritable"]}}
        for i in range(5)
    ]
    vm = build_v9_view_model(current, {})
    ds = vm["daily_status"]
    assert ds["cluster_segments"] == [(3, 7)]
    assert ds["cluster_label"] == "11/4–11/8"
    mental_row = next(r for r in ds["rows"] if r["label"] == "精神")
    assert mental_row["colors"][3:8] == [_COLOR_CLUSTER] * 5
    assert all(c == _COLOR_NORMAL for c in mental_row["colors"][:3])


def test_isolated_single_abnormal_day_is_gray_not_orange():
    """單一一天的異常（沒有連續5天、也沒有10天內超過5天）不算集中期，
    格子要顯示_COLOR_ABNORMAL(2026-08-12改成黃色，先前是灰色)，不是
    橘紅色——集中期跟一般異常要能視覺上區分開來，不能只要異常就一律標橘。"""
    current = _daily_records() + [
        {"RecordType": "Behavior", "OccurredAt": "2025-11-15T20:00:00", "Content": {"behaviors": ["irritable"]}},
    ]
    vm = build_v9_view_model(current, {})
    ds = vm["daily_status"]
    assert ds["cluster_segments"] == []
    assert ds["cluster_label"] == "本月無明顯集中異常期"
    mental_row = next(r for r in ds["rows"] if r["label"] == "精神")
    assert mental_row["colors"][14] == _COLOR_ABNORMAL


def test_two_consecutive_abnormal_days_now_triggers_cluster_after_threshold_change():
    """2026-08-13使用者定案（情境5）：連續異常門檻從5天改成2天——這裡讓
    irritable連續2天(day14-15)異常，在舊門檻(5天)下不該算集中期，新門檻
    (2天)下應該要標橘色，鎖住這次的門檻變更。"""
    current = _daily_records() + [
        {"RecordType": "Behavior", "OccurredAt": f"2025-11-{15 + i:02d}T20:00:00", "Content": {"behaviors": ["irritable"]}}
        for i in range(2)
    ]
    vm = build_v9_view_model(current, {})
    ds = vm["daily_status"]
    mental_row = next(r for r in ds["rows"] if r["label"] == "精神")
    assert mental_row["colors"][14:16] == [_COLOR_CLUSTER] * 2


def test_other_rows_not_flagged_orange_just_because_another_row_is_clustered():
    """使用者明確要求：集中期只影響「該事件那行」，同一天如果別的列本身沒有
    異常，不該因為別的列在集中期裡就被連帶標橘（甚至標灰）——沒異常就是綠色。"""
    current = _daily_records() + [
        {"RecordType": "Behavior", "OccurredAt": f"2025-11-{4 + i:02d}T20:00:00", "Content": {"behaviors": ["irritable"]}}
        for i in range(5)
    ]
    vm = build_v9_view_model(current, {})
    ds = vm["daily_status"]
    sleep_row = next(r for r in ds["rows"] if r["label"] == "睡眠")
    assert sleep_row["colors"][3:8] == [_COLOR_NORMAL] * 5


def test_row_with_sparse_abnormal_days_stays_gray_even_inside_another_rows_cluster():
    """2026-08-03修正：使用者實測抓到的真bug——集中期偵測原本是「四列合併
    訊號」一起判斷，導致情緒/睡眠列只有零星1-2天孤立異常（自己完全沒有
    連續型態），卻因為剛好落在精神列造成的集中期日期範圍內，被連帶標成
    橘色。改成每列各自獨立判斷集中期後，這裡讓精神列連續5天(day3-7)異常
    （形成自己的集中期），情緒列只在day5(精神集中期範圍內)孤立異常1天——
    情緒列day5應該維持_COLOR_ABNORMAL(異常但不成群)，不能因為精神列橘
    就連帶被標橘。"""
    current = _daily_records() + [
        {"RecordType": "Behavior", "OccurredAt": f"2025-11-{4 + i:02d}T20:00:00", "Content": {"behaviors": ["irritable"]}}
        for i in range(5)
    ] + [
        {"RecordType": "Behavior", "OccurredAt": "2025-11-06T20:00:00", "Content": {"behaviors": ["emotional_abnormal"]}},
    ]
    vm = build_v9_view_model(current, {})
    ds = vm["daily_status"]
    mental_row = next(r for r in ds["rows"] if r["label"] == "精神")
    mood_row = next(r for r in ds["rows"] if r["label"] == "情緒")
    assert mental_row["colors"][3:8] == [_COLOR_CLUSTER] * 5, "精神列自己連續5天，應該標橘"
    assert mood_row["colors"][5] == _COLOR_ABNORMAL, "情緒列day6只是孤立1天，即使落在精神列的集中期範圍內，也只能標_COLOR_ABNORMAL不能標橘"


def test_bp_crisis_dates_capped_at_three_with_ellipsis():
    """2026-08-05：低血壓(<90)跟危象(>=160)合併成單一「危急」chip後，最壞情況
    日期數是兩層加總，固定寬度的23mm欄位撐不住原本4筆的量，上限降到3筆。
    2026-08-07依醫師會議門檻改為90/100/130/160，chips新增「偏低」後危急
    chip索引從[2]改為[3]。"""
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 185, "DiastolicPressure": 105}
        for d in (2, 9, 16, 23, 28, 30)
    ]
    vm = build_v9_view_model(current, {})
    crisis_chip = vm["bp_distribution"]["chips"][3]
    assert crisis_chip[0] == "≥160／<90 mmHg"
    assert crisis_chip[2] == "11/2、11/9、11/16…"

    few = [{"RecordTime": "2025-11-02T08:00:00", "SystolicPressure": 185, "DiastolicPressure": 105}]
    vm_few = build_v9_view_model(few, {})
    assert vm_few["bp_distribution"]["chips"][3][2] == "11/2"


def test_bp_low_and_crisis_merge_into_one_chip():
    """低血壓(<90)跟危象(>=160)是同一個「危急」狀態：count相加、日期合併排序、
    顏色一致（橘紅#D85A30），不再各自獨立呈現；中間新增的「偏低」([90,100))
    則是獨立顯示，不併入危急。segments現在是5段(危急低/偏低/正常/偏高/
    危急高)，兩端危急索引為[0]跟[4]。"""
    current = [
        {"RecordTime": "2025-11-02T08:00:00", "SystolicPressure": 185, "DiastolicPressure": 105},
        {"RecordTime": "2025-11-09T08:00:00", "SystolicPressure": 85, "DiastolicPressure": 60},
    ]
    vm = build_v9_view_model(current, {})
    dist = vm["bp_distribution"]
    crisis_chip = dist["chips"][3]
    assert crisis_chip[1] == "2 次"
    assert crisis_chip[2] == "11/2、11/9"
    assert dist["segments"][0][1] == "#D85A30"
    assert dist["segments"][4][1] == "#D85A30"


def test_bp_target_chip_shows_ok_count_not_total_count():
    """2026-08-06修正使用者截圖抓到的問題：「目標內」chip底下的次數原本
    誤植成總讀數(跟上方count_label的次數一樣)，不管百分比多少都顯示同一個
    數字，邏輯不通。2026-08-07門檻改為90/100/130/160後重新設計資料：
    10天、每天只有1筆早上讀數(data_parser.py的bp_mean()會用早上讀數當
    pm/eve缺測時的fallback，所以每天算3筆，10天共30筆)——85/88兩天落在
    危急(低)<90；100/110/120三天落在正常[100,130)；130/145/150三天落在
    偏高[130,160)；160/185兩天落在危急(高)>=160。驗證「目標內」chip顯示的
    是3天*3=9次，不是總數30次。"""
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 85, "DiastolicPressure": 60},
        {"RecordTime": "2025-11-02T08:00:00", "SystolicPressure": 88, "DiastolicPressure": 60},
        {"RecordTime": "2025-11-03T08:00:00", "SystolicPressure": 100, "DiastolicPressure": 70},
        {"RecordTime": "2025-11-04T08:00:00", "SystolicPressure": 110, "DiastolicPressure": 70},
        {"RecordTime": "2025-11-05T08:00:00", "SystolicPressure": 120, "DiastolicPressure": 75},
        {"RecordTime": "2025-11-06T08:00:00", "SystolicPressure": 130, "DiastolicPressure": 75},
        {"RecordTime": "2025-11-07T08:00:00", "SystolicPressure": 145, "DiastolicPressure": 85},
        {"RecordTime": "2025-11-08T08:00:00", "SystolicPressure": 150, "DiastolicPressure": 85},
        {"RecordTime": "2025-11-09T08:00:00", "SystolicPressure": 160, "DiastolicPressure": 90},
        {"RecordTime": "2025-11-10T08:00:00", "SystolicPressure": 185, "DiastolicPressure": 100},
    ]
    vm = build_v9_view_model(current, {})
    dist = vm["bp_distribution"]
    assert dist["count_label"].startswith("30 次量測／10 天")
    ok_chip = dist["chips"][0]
    assert ok_chip[0] == "目標內"
    assert ok_chip[2] == "9 次"


def test_bp_ticks_positioned_at_actual_segment_boundaries():
    """2026-08-06修正：刻度原本是寫死的固定百分比位置，跟色塊實際交界處
    對不上——這幾個數字本身就是分色門檻值，理論上該畫在色塊換色的那條線
    上。2026-08-07門檻改為90/100/130/160(四個刻度)，用跟上面同一組資料
    (危急低2天/偏低0天/正常3天/偏高3天/危急高2天，每天*3筆共30筆，佔比
    20%/0%/30%/30%/20%)驗證刻度位置正確累加：90刻度在20%處(危急低佔比
    結束)、100刻度在20%處(+偏低0%不變)、130刻度在50%處(20+0+30)、
    160刻度在80%處(20+0+30+30)。"""
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 85, "DiastolicPressure": 60},
        {"RecordTime": "2025-11-02T08:00:00", "SystolicPressure": 88, "DiastolicPressure": 60},
        {"RecordTime": "2025-11-03T08:00:00", "SystolicPressure": 100, "DiastolicPressure": 70},
        {"RecordTime": "2025-11-04T08:00:00", "SystolicPressure": 110, "DiastolicPressure": 70},
        {"RecordTime": "2025-11-05T08:00:00", "SystolicPressure": 120, "DiastolicPressure": 75},
        {"RecordTime": "2025-11-06T08:00:00", "SystolicPressure": 130, "DiastolicPressure": 75},
        {"RecordTime": "2025-11-07T08:00:00", "SystolicPressure": 145, "DiastolicPressure": 85},
        {"RecordTime": "2025-11-08T08:00:00", "SystolicPressure": 150, "DiastolicPressure": 85},
        {"RecordTime": "2025-11-09T08:00:00", "SystolicPressure": 160, "DiastolicPressure": 90},
        {"RecordTime": "2025-11-10T08:00:00", "SystolicPressure": 185, "DiastolicPressure": 100},
    ]
    vm = build_v9_view_model(current, {})
    ticks = vm["bp_distribution"]["ticks"]
    assert ticks == [(20, "90"), (20, "100"), (50, "130"), (80, "160")]


def test_bp_low_tier_shown_as_independent_orange_chip():
    """2026-08-07新增：依醫師會議確認，[90,100)這個「偏低」級距不再併入
    危急，是獨立顯示的橘色區塊——格式比照「偏高」(只顯示%＋次數，不列
    日期)。這裡3天落在偏低[90,100)、其餘7天落在正常，驗證「偏低」chip
    正確顯示、且不會被算進危急chip的count/日期裡。
    注意：low_count/crisis_count沿用既有(crisis_detail同款)以天為單位的
    df.iterrows()寫法，只看每天的systolic_am，不算pm/eve的fallback複製值，
    這跟low_pct/crisis_pct用all_sys(含fallback)算百分比的分母不一樣——
    這是延續既有crisis chip就有的設計，不是這次新增的行為，count=3天
    （不是9，9是讀數為分母乘以fallback的算法，這裡count維持沿用原本的
    天數算法）。"""
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 92, "DiastolicPressure": 60},
        {"RecordTime": "2025-11-02T08:00:00", "SystolicPressure": 95, "DiastolicPressure": 60},
        {"RecordTime": "2025-11-03T08:00:00", "SystolicPressure": 99, "DiastolicPressure": 65},
        {"RecordTime": "2025-11-04T08:00:00", "SystolicPressure": 110, "DiastolicPressure": 70},
        {"RecordTime": "2025-11-05T08:00:00", "SystolicPressure": 110, "DiastolicPressure": 70},
        {"RecordTime": "2025-11-06T08:00:00", "SystolicPressure": 110, "DiastolicPressure": 70},
        {"RecordTime": "2025-11-07T08:00:00", "SystolicPressure": 110, "DiastolicPressure": 70},
        {"RecordTime": "2025-11-08T08:00:00", "SystolicPressure": 110, "DiastolicPressure": 70},
        {"RecordTime": "2025-11-09T08:00:00", "SystolicPressure": 110, "DiastolicPressure": 70},
        {"RecordTime": "2025-11-10T08:00:00", "SystolicPressure": 110, "DiastolicPressure": 70},
    ]
    vm = build_v9_view_model(current, {})
    dist = vm["bp_distribution"]
    low_chip = dist["chips"][2]
    assert low_chip[0] == "偏低"
    assert low_chip[1] == "30%"
    assert low_chip[2] == "3 次"
    crisis_chip = dist["chips"][3]
    assert crisis_chip[1] == "0 次"
    assert dist["segments"][1][1] == "#EF9F27"


def test_card2_trend_note_describes_concentrated_vs_scattered_deviation():
    """2026-07-31：體液代謝卡trend_note原本是固定句子，改成依實際逐日型態
    描述——完全沒事寫安全句；零星幾天寫「自X起共N天」；連續>=3天寫
    「多集中於X–Y」。"""
    no_deviation = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 58.0}},
    ]
    vm_none = build_v9_view_model(no_deviation, {})
    water_card_none = next(c for c in vm_none["four_aspects"] if c["title"] == "體液代謝與容積")
    assert water_card_none["trend_note"] == ["本月飲水與排便狀況皆在合理範圍內"]

    concentrated = [
        {"RecordType": "Water", "OccurredAt": f"2025-11-{d:02d}T09:00:00", "Content": {"amountMl": 400}}
        for d in (5, 6, 7, 8)
    ]
    vm_c = build_v9_view_model(concentrated, {})
    water_card_c = next(c for c in vm_c["four_aspects"] if c["title"] == "體液代謝與容積")
    assert "飲水未達標多集中於11/5–11/8" in water_card_c["trend_note"]


def test_card3_trend_note_combines_multiple_real_signals():
    """感染監測卡trend_note要能同時反映體溫/遊走/精神不佳三個訊號，
    都沒事時給安全句，不能只看單一訊號就下結論。"""
    current = [
        {"RecordType": "Behavior", "OccurredAt": "2025-11-10T20:00:00", "Content": {"behaviors": ["wander"]}},
        {"RecordType": "Behavior", "OccurredAt": "2025-11-11T20:00:00", "Content": {"behaviors": ["wander"]}},
    ]
    vm = build_v9_view_model(current, {})
    infection_card = next(c for c in vm["four_aspects"] if c["title"] == "感染監測")
    assert "遊走自11/10起共2天" in infection_card["trend_note"]

    vm_clean = build_v9_view_model([] + [{"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 58.0}}], {})
    infection_card_clean = next(c for c in vm_clean["four_aspects"] if c["title"] == "感染監測")
    assert infection_card_clean["trend_note"] == ["本月體溫、遊走、精神狀態皆無明顯異常"]


def test_card4_trend_note_reflects_weight_food_hygiene_direction():
    """衰弱卡trend_note要依體重減輕幅度/進食量相對變化/衛生協助項目月平均
    的實際方向組句，不是固定寫死的句子。"""
    baseline = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-08-01T07:30:00", "Content": {"weightKg": 60.0}}]
    current = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 56.0}}]  # -6.7%
    vm = build_v9_view_model(baseline + current, {})
    frailty_card = next(c for c in vm["four_aspects"] if c["title"] == "衰弱與功能")
    assert "體重顯著減輕" in "".join(frailty_card["trend_note"])


def test_food_intake_is_a_candidate_pool_item_alongside_vitals():
    """2026-08-19二次修正（情境4：生理數值穩定但生活照護逐步變差）：使用者
    確認候選池不能只看生理量測——飲水/進食（上一輪一度移出候選池，見已
    廢棄的test_food_and_water_are_excluded_from_candidate_pool）現在跟血壓/
    血氧等一起併回同一個候選池，用同一套deviation_pct/severity排序直接
    競爭top3，不分生理/生活照護類別。這裡本月三餐全部不足(100%)、前三月
    全部正常(0%)，落差50%應該直接觸發重度分級，變成top3第一名。"""
    baseline = [
        {"RecordType": "Meal", "OccurredAt": f"2025-08-{d:02d}T08:00:00", "Content": {"mealKind": "breakfast", "intakeAmount": "all"}}
        for d in range(1, 11)
    ]
    current = [
        {"RecordType": "Meal", "OccurredAt": f"2025-11-{d:02d}T08:00:00", "Content": {"mealKind": "breakfast", "intakeAmount": "lt_half"}}
        for d in range(1, 11)
    ]
    vm = build_v9_view_model(baseline + current, {})
    assert vm["top3"], "進食量嚴重偏離應該被選進頂部三項"
    assert vm["top3"][0]["name"] == "進食不足天數"
    assert vm["top3"][0]["value"] == "0 天 → 10 天"


def test_defecation_mental_sleep_are_candidate_pool_items():
    """2026-08-19新增（情境4）：排便異常/精神不佳/白天睡多三項生活照護
    行為指標補進候選池，跟飲水/進食/血氧同一套「月異常天數佔比%」算法。
    這裡本月三種行為代碼連續10天全數出現(100%)、前三月完全正常(0%)，
    落差50%應觸發重度分級，三項都該進入候選池並帶有severity。精神不佳/
    白天睡多刻意不套用sample_limited判斷（行為代碼沒登打不等於沒測量），
    排便異常則沿用水/食同一套sample_limited判斷。"""
    baseline = [
        {"RecordType": "Meal", "OccurredAt": f"2025-08-{d:02d}T08:00:00", "Content": {"mealKind": "breakfast", "intakeAmount": "all"}}
        for d in range(1, 11)
    ]
    current = [
        {"RecordType": "Defecation", "OccurredAt": f"2025-11-{d:02d}T09:00:00",
         "Content": {"status": "special", "note": "性狀異常"}}
        for d in range(1, 11)
    ] + [
        {"RecordType": "Behavior", "OccurredAt": f"2025-11-{d:02d}T20:00:00", "Content": {"behaviors": ["irritable"]}}
        for d in range(1, 11)
    ] + [
        {"RecordType": "Behavior", "OccurredAt": f"2025-11-{d:02d}T20:01:00", "Content": {"behaviors": ["day_sleep"]}}
        for d in range(1, 11)
    ] + [
        {"RecordType": "Meal", "OccurredAt": f"2025-11-{d:02d}T08:00:00", "Content": {"mealKind": "breakfast", "intakeAmount": "all"}}
        for d in range(1, 11)
    ]
    vm = build_v9_view_model(baseline + current, {})
    top3_names = {item["name"] for item in vm["top3"]}
    remaining_names = {name for name, *_ in vm["check_notes"]["remaining_deviations"]}
    shown_names = top3_names | remaining_names
    assert "排便異常天數" in shown_names
    assert "精神不佳天數" in shown_names
    assert "白天睡多天數" in shown_names


def test_spo2_is_a_candidate_pool_item():
    """2026-08-19新增：血氧補進候選池，跟飲水/進食移除前同一套算法(月偏低
    天數佔比%當分級基準)。這裡本月血氧全部偏低(100%)、前三月全部正常(0%)，
    落差100%遠超重度門檻(40%)，應該被選進top3第一名。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "BloodOxygen": 98}
        for d in range(1, 11)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "BloodOxygen": 85}
        for d in range(1, 11)
    ]
    vm = build_v9_view_model(baseline + current, {})
    assert vm["top3"], "血氧嚴重偏離應該被選進頂部三項"
    assert vm["top3"][0]["name"] == "血氧偏低天數"
    assert vm["top3"][0]["value"] == "0 天 → 10 天"


def test_top3_cards_show_onset_or_threshold_qualifier():
    """2026-07-31新增：原稿三張卡片各自有「11月中旬起」/「已達界線」/
    「整月上行」這種依情況而定的補充句，之前完全沒做。這裡構造體重減輕
    超過顯著界(WEIGHT_LOSS_SIG=5%)，預期qualifier顯示「已達顯著界」。
    2026-08-06修正：baseline(8/1)到current(11/1)實際只跨92天(約3個月)，
    候選池的體重項名稱改用_weight_span_label()動態算(見下面test_weight_
    candidate_pool_label_matches_frailty_card_not_hardcoded)，這裡改用value
    比對找到該項，不再依賴寫死的「體重・近六個月」名稱字串。"""
    baseline = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-08-01T07:30:00", "Content": {"weightKg": 60.0}}]
    current = [{"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 56.0}}]  # -6.7%
    vm = build_v9_view_model(baseline + current, {})
    weight_item = next(t for t in vm["top3"] if t["value"] == "60.0 → 56.0")
    assert weight_item["qualifier"] == "已達顯著界"


def test_top3_qualifier_shows_onset_date_for_scattered_deviation():
    """血壓/心率/飲水/進食/體溫用逐日型態產生「MM/DD起」或「MM/DD–MM/DD集中」，
    這裡構造本月唯一一天血壓大幅偏高(孤立、非連續)，預期是「起」不是「集中」。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 120, "DiastolicPressure": 78}
        for d in range(1, 11)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 155 if d == 5 else 118, "DiastolicPressure": 78}
        for d in range(1, 11)
    ]
    vm = build_v9_view_model(baseline + current, {})
    # 血壓若進top3才驗證qualifier格式；若severity不足沒進top3，至少確認沒崩潰
    bp_item = next((t for t in vm["top3"] if t["name"] == "收縮壓"), None)
    if bp_item is not None:
        assert bp_item["qualifier"] == "11/5起"


def test_spo2_distribution_count_label_uses_real_reading_count_not_day_count():
    """2026-07-31修正：原本顯示的是期間總天數(c_sfw['total'])冒充「次量測」，
    這裡故意讓某天量測2次血氧、某天0次，驗證顯示的是真正的血氧筆數(3筆)，
    不是天數(2天)。2026-08-06再修正：count_label加回天數(比照血壓分布
    「X次量測／Y天」格式)，因為血氧分層%用的分母是天不是次，只顯示次數
    會讓人誤以為下面的%是用次數當分母，這裡3天(11/1~11/3)都有進到df。"""
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "BloodOxygen": 96},
        {"RecordTime": "2025-11-01T14:00:00", "BloodOxygen": 95},
        {"RecordTime": "2025-11-02T08:00:00", "BloodOxygen": 97},
        {"RecordTime": "2025-11-03T08:00:00", "SystolicPressure": 120, "DiastolicPressure": 78},
    ]
    vm = build_v9_view_model(current, {})
    assert vm["spo2_distribution"]["count_label"] == "3 次量測／3 天"


def test_temp_candidate_pool_uses_percentage_algorithm_like_water_and_food():
    """2026-08-03修正：體溫偏離基線改用跟飲水/進食同一套算法——兩期都換算
    成「偏離天數佔比%」，本月佔比當分級基準(temp_pct門檻15/30/50)，兩期
    落差當顯示的偏離%。這裡前三月20天全部正常(0%)，本月10天有5天發燒
    (>=37.2°C絕對值門檻，50%)，預期偏離50%、觸發重度分級。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "Temperature": 36.5}
        for d in range(1, 21)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "Temperature": 38.0 if d <= 5 else 36.5}
        for d in range(1, 11)
    ]
    vm = build_v9_view_model(baseline + current, {})
    temp_item = next((t for t in vm["top3"] if t["name"] == "體溫偏離基線"), None)
    assert temp_item is not None, "體溫偏離基線50%落差應該觸發重度分級進top3"
    assert temp_item["value"] == "0 天 → 5 天"
    assert temp_item["delta_line"] == "偏離 50%"


def test_current_month_temp_deviation_uses_historical_baseline_not_self():
    """2026-08-06修正真實bug：current_df原本沒有把baseline_df算出來的
    temp_baseline傳進去，data_parser.py會用「本月自己的體溫中位數」自我
    計算基線——這樣只要整個月同步偏移（例如慢性低燒整月都偏高），本月會
    拿自己的中位數當基線，跟自己比delta永遠是0，完全偵測不到，不管歷史
    基線資料多完整都一樣。這裡baseline三個月穩定36.0°C、本月整月37.1°C
    (比真正的歷史基線高1.1°C，剛好達偏高門檻，但低於37.2°C絕對發燒門檻，
    刻意排除絕對門檻救援，只測delta比較這條路徑)，修正前應該顯示0天，
    修正後應該全部30天都被抓到。"""
    baseline = [
        {"RecordTime": f"2025-{m:02d}-{d:02d}T08:00:00", "Temperature": 36.0}
        for m in (8, 9, 10) for d in range(1, 29)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "Temperature": 37.1}
        for d in range(1, 31)
    ]
    vm = build_v9_view_model(baseline + current, {})
    infection_card = next(c for c in vm["four_aspects"] if c["title"] == "感染監測")
    assert infection_card["primary_before"] == "0 天"
    assert infection_card["primary_after"] == "30 天"


def test_spo2_distribution_computes_real_critical_tier_not_placeholder_dash():
    """2026-08-03新增：血氧分布<90%原本永遠顯示「—」(沒有計算邏輯)，改成
    真的算——10天觀察，2天<90%(危急)、3天在[90,94)之間(偏低但不到危急)，
    5天>=94%(達標)，三段應該互斥加總，不能是<90%不變的「—」。
    2026-08-06二次修正：分層改以「次數」為主(不是天數)，這裡每天只有1筆
    讀數，次數/天數剛好相等，用來確認基本分層邏輯還是對的；「次vs天」
    真的不同的情境見下面test_spo2_tiers_count_readings_not_days。"""
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "BloodOxygen": spo2}
        for d, spo2 in zip(range(1, 11), [96, 96, 96, 96, 96, 92, 92, 92, 88, 88])
    ]
    vm = build_v9_view_model(current, {})
    seg_pcts = [pct for pct, _color in vm["spo2_distribution"]["segments"]]
    assert seg_pcts[2] == 20.0, "<90%那段應該是2/10=20%，不是佔位0"
    chips = {label: (before, after) for label, before, after in vm["spo2_distribution"]["chips"]}
    assert chips["<90%"] == ("2 次", "11/9、11/10")
    assert chips["<94%"] == ("30%", "3 次")
    assert sum(seg_pcts) == 100.0


def test_pulse_distribution_replaces_spo2_slot_but_spo2_still_present():
    """2026-08-07新增：依醫師會議確認，「本月量測分布」把血氧分布區域換成
    脈搏分布，五級門檻極低<50／偏低[50,60)／正常[60,101)／偏高[101,120)
    ／極高>=120。spo2_distribution本身不能被拿掉——report_pdf_v9.py(舊版
    reportlab渲染器，凍結不能改)還在讀這個key，這裡只是新增pulse_
    distribution，兩個key要同時存在於vm裡。"""
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "BloodOxygen": 96},
        {"RecordTime": "2025-11-01T09:00:00", "Pulse": 70},
    ]
    vm = build_v9_view_model(current, {})
    assert "spo2_distribution" in vm
    assert "pulse_distribution" in vm


def test_pulse_distribution_count_label_uses_reading_count_and_hr_days():
    """脈搏分布標題格式比照血壓/血氧分布，「N次量測／M天」——這裡11/1量測
    2次脈搏(同一天)、11/2量測1次，count_label的次數是3(讀數)、天數是2
    (hr_days，data_parser.py把同一天多筆脈搏平均成一個每日值)。"""
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "Pulse": 68},
        {"RecordTime": "2025-11-01T14:00:00", "Pulse": 72},
        {"RecordTime": "2025-11-02T08:00:00", "Pulse": 70},
    ]
    vm = build_v9_view_model(current, {})
    assert vm["pulse_distribution"]["count_label"] == "3 次量測／2 天"


def test_pulse_five_tier_split_matches_doctor_meeting_thresholds():
    """2026-08-07新增、2026-08-12改版：脈搏五級分布，色塊仍是五段，但chip
    呈現改成比照血壓分布的四欄格式——極低/極高合併成一個危急風格的chip
    「≥120／<50 次/分」，偏低/正常/偏高各自獨立(%為主、次數為輔)。20筆
    讀數(每天1筆，20天)：2筆<50(極低)、3筆[50,60)(偏低)、10筆[60,101)
    (正常)、3筆[101,120)(偏高)、2筆>=120(極高)，驗證五段百分比互斥加總
    100%、四個chip都正確，合併chip次數相加(2+2=4)、日期合併排序。"""
    values = [45, 48, 52, 55, 58, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 105, 110, 115, 125, 130]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "Pulse": v}
        for d, v in zip(range(1, 21), values)
    ]
    vm = build_v9_view_model(current, {})
    dist = vm["pulse_distribution"]
    seg_pcts = [pct for pct, _color in dist["segments"]]
    assert seg_pcts == [10.0, 15.0, 50.0, 15.0, 10.0]
    assert sum(seg_pcts) == 100.0
    assert len(dist["chips"]) == 4
    chips = {label: (val, sub) for label, val, sub in dist["chips"]}
    assert chips["正常"] == ("50%", "10 次")
    assert chips["偏高"] == ("15%", "3 次")
    assert chips["偏低"] == ("15%", "3 次")
    assert chips["≥120／<50 次/分"] == ("4 次", "11/1、11/2、11/19…")


def test_pulse_ticks_positioned_at_actual_segment_boundaries():
    """脈搏分布刻度(50/60/101/120)比照血壓分布，畫在色塊實際累積百分比
    交界處，不是寫死的固定位置——沿用上面同一組資料驗證累加：50在10%
    (極低佔比結束)、60在25%(+偏低15%)、101在75%(+正常50%)、120在90%
    (+偏高15%)。"""
    values = [45, 48, 52, 55, 58, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 105, 110, 115, 125, 130]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "Pulse": v}
        for d, v in zip(range(1, 21), values)
    ]
    vm = build_v9_view_model(current, {})
    assert vm["pulse_distribution"]["ticks"] == [(10.0, "50"), (25.0, "60"), (75.0, "101"), (90.0, "120")]


def test_pulse_distribution_no_data_falls_back_to_dash_not_crash():
    """完全沒有脈搏讀數時(_pulse_reading_tiers回傳None)，pulse_distribution
    不應該崩潰，chips應該顯示佔位符「—」而不是拋例外。"""
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "BloodOxygen": 96},
    ]
    vm = build_v9_view_model(current, {})
    dist = vm["pulse_distribution"]
    assert dist["chips"][0][1] == "—"
    assert dist["chips"][3][1] == "— 次"
    assert all(pct == 0 for pct, _color in dist["segments"])


def test_spo2_tiers_count_readings_not_days():
    """2026-08-06新增：使用者反映血氧分布原本天數跟次數被畫成同一個數字，
    但同一天可能量測不只一次，天跟次應該分開算。這裡讓11/1當天量測3次
    (2次正常94+以上、1次危急<90)，驗證分層是照「次」算，不是照「天」——
    如果照天算，11/1只會被算進1天且該天多筆讀數會被平均掉，量不到這次
    危急事件；照次算則3筆讀數各自獨立判斷，危急層應該抓到這1次。
    2026-08-06二次修正：chip完全比照血壓格式，只顯示次數，不摻雜天數——
    count_label(標題)仍保留「X次量測／Y天」雙單位，但個別chip底下只留
    次數這一種單位，不要「3次・2天」這種混雜寫法。"""
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "BloodOxygen": 96},
        {"RecordTime": "2025-11-01T13:00:00", "BloodOxygen": 97},
        {"RecordTime": "2025-11-01T19:00:00", "BloodOxygen": 88},
        {"RecordTime": "2025-11-02T08:00:00", "BloodOxygen": 96},
    ]
    vm = build_v9_view_model(current, {})
    dist = vm["spo2_distribution"]
    assert dist["count_label"] == "4 次量測／2 天"
    chips = {label: (before, after) for label, before, after in dist["chips"]}
    # 4次讀數：3次>=95%(ok) + 1次<90%(危急)，chip只顯示次數，不顯示天數
    assert chips["≥95%"] == ("75%", "3 次")
    assert chips["<90%"] == ("1 次", "11/1")


def test_spo2_ticks_positioned_at_actual_segment_boundaries_only():
    """2026-08-06新增：血氧分布刻度原本寫死3個(95/94/90)在固定百分比位置，
    但血氧真正的分層門檻只有94(SPO2_LOW)跟90(SPO2_CRITICAL)兩個——原本的
    「95」刻度不對應任何色塊交界，是誤植，改成只畫真正對應交界的94跟90，
    位置依實際佔比動態算：94在「達標」佔比結束處(50%)、90在「達標+偏低」
    佔比結束處(80%)，用跟上一個測試同一組資料(5天達標/3天偏低/2天危急，
    佔比50%/30%/20%)驗證。"""
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "BloodOxygen": spo2}
        for d, spo2 in zip(range(1, 11), [96, 96, 96, 96, 96, 92, 92, 92, 88, 88])
    ]
    vm = build_v9_view_model(current, {})
    ticks = vm["spo2_distribution"]["ticks"]
    assert ticks == [(50, "94"), (80, "90")]


def test_multi_signal_overlap_detected_for_detail_clause():
    """2026-08-03新增：頂部摘要detail句改用真的跨指標同期關聯偵測，不再是
    固定的「待後續版本補上」。這裡讓心率上升(pulse>個人基線)跟血氧下降
    (<94%)在同一段連續5天(11/5-11/9)同時出現，預期detail_clause組出
    「11/5–11/9期間，心率上升、血氧下降同時出現異常。」"""
    baseline = [{"RecordTime": "2025-08-01T08:00:00", "Pulse": 70}]
    current = [
        {"RecordType": "BodyMetrics", "OccurredAt": f"2025-11-{d:02d}T07:30:00", "Content": {"weightKg": 58.0}}
        for d in range(1, 21)
    ] + [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "Pulse": 95, "BloodOxygen": 90}
        for d in range(5, 10)
    ]
    vm = build_v9_view_model(baseline + current, {})
    assert vm["top_summary"]["detail_clause"] == "11/5–11/9期間，心率上升、血氧下降同時出現異常。"
    assert vm["top_summary"]["detail"] == vm["top_summary"]["detail_clause"]


def test_overlap_clause_caps_signal_list_at_three_with_more_suffix():
    """2026-08-05修正：使用者反映同期關聯的訊號名稱全部列出來(可能多達
    10種)讀起來太雜，改成最多顯示3種，超過的用「等N項」收尾(N是總項數)。
    這裡故意讓5種訊號同時重疊(心率上升/血氧下降/情緒異常/精神不佳/血壓偏高
    其中至少5種在11/5-11/9同時出現)，detail_clause只該列出3項+「等5種訊號」。
    required_terms(detail_signals)要包含日期範圍跟「等5種訊號」本身，確保LLM
    潤飾時這兩段不會被重新排版到找不回來。2026-08-19修正：「N項」改成
    「N種訊號」，避免跟頂部「N項紀錄偏離參考值」（不同統計口徑）看起來
    像同一組數字。"""
    baseline = [{"RecordTime": "2025-08-01T08:00:00", "Pulse": 70, "SystolicPressure": 120}]
    current = [
        {"RecordType": "BodyMetrics", "OccurredAt": f"2025-11-{d:02d}T07:30:00", "Content": {"weightKg": 58.0}}
        for d in range(1, 21)
    ] + [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 190, "Pulse": 95, "BloodOxygen": 90}
        for d in range(5, 10)
    ] + [
        {"RecordType": "Behavior", "OccurredAt": f"2025-11-{d:02d}T20:00:00", "Content": {"behaviors": ["irritable"]}}
        for d in range(5, 10)
    ] + [
        {"RecordType": "Behavior", "OccurredAt": f"2025-11-{d:02d}T20:01:00", "Content": {"behaviors": ["emotional_abnormal"]}}
        for d in range(5, 10)
    ]
    vm = build_v9_view_model(baseline + current, {})
    ts = vm["top_summary"]
    assert ts["detail_clause"] == "11/5–11/9期間，心率上升、情緒異常、精神不佳等5種訊號同時出現異常。"
    # 2026-08-05二次修正：使用者確認要上色的是日期範圍、訊號名稱、「N項」
    # 計數數字這幾個「項目本身」，「等」這個連接字維持黑色——所以required_
    # terms/detail_signals存的是"5種訊號"(不含「等」)，跟detail_highlight_terms
    # 內容相同，兩者都是實際要上色/驗證倖存的片段清單。
    assert ts["detail_signals"] == ["11/5–11/9", "心率上升", "情緒異常", "精神不佳", "5種訊號"]
    assert ts["detail_highlight_terms"] == ["11/5–11/9", "心率上升", "情緒異常", "精神不佳", "5種訊號"]


def test_headline_exposes_structured_count_and_top_name_for_styling():
    """headline_count/headline_top_name是給report_html_v9.py上色用的結構化
    欄位——headline不會被LLM潤飾，用它們重組句子是安全的。"""
    baseline = [{"RecordTime": "2025-08-01T08:00:00", "SystolicPressure": 120}]
    current = [{"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 190}]
    vm = build_v9_view_model(baseline + current, {})
    ts = vm["top_summary"]
    assert ts["headline_count"] == len(vm["top3"])
    if vm["top3"]:
        assert ts["headline_top_name"] == vm["top3"][0]["name"]
    else:
        assert ts["headline_top_name"] is None


def test_multi_signal_overlap_none_when_no_real_overlap():
    """完全沒有多訊號同期重疊時，detail_clause要是None，detail維持安全句，
    不能硬湊一個不存在的關聯出來。這裡只有單一天資料(span=0天)，落入情境1
    (無個人基線)分支，安全句改成說明基線尚未累積滿的內容，不再是舊版
    「未偵測到多項訊號同期重疊異常」那句（那句是baseline-relative分支
    專用的fallback，見test_multi_signal_overlap_none_when_no_real_overlap_
    with_sufficient_baseline）。"""
    current = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 58.0}},
    ]
    vm = build_v9_view_model(current, {})
    assert vm["top_summary"]["detail_clause"] is None
    assert "已累積" in vm["top_summary"]["detail"]


def test_multi_signal_overlap_none_when_no_real_overlap_with_sufficient_baseline():
    """跟上一則測試同樣的「沒有多訊號重疊」情境，但資料涵蓋超過3個月
    （落入baseline-relative分支），detail要維持舊版的安全句，不能硬湊一個
    不存在的關聯出來。"""
    baseline = [{"RecordTime": "2025-08-01T08:00:00", "SystolicPressure": 120}]
    current = [{"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 122}]
    vm = build_v9_view_model(baseline + current, {})
    assert vm["top_summary"]["detail_clause"] is None
    assert "未偵測到多項訊號同期重疊異常" in vm["top_summary"]["detail"]


def test_multi_signal_overlap_requires_at_least_two_signals_same_day():
    """只有單一訊號異常(即使連續很多天)不算「同期關聯」，至少要2種訊號
    同一天都異常才算——這裡只有心率上升，沒有其他訊號同時發生。"""
    baseline = [{"RecordTime": "2025-08-01T08:00:00", "Pulse": 70}]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "Pulse": 95}
        for d in range(1, 11)
    ]
    vm = build_v9_view_model(baseline + current, {})
    assert vm["top_summary"]["detail_clause"] is None


def test_diastolic_bp_is_a_candidate_pool_item_alongside_systolic():
    """2026-08-03新增：舒張壓比照收縮壓加入候選池——之前只有收縮壓被納入
    「偏離幅度最大」的候選池，舒張壓完全沒有。這裡前三月舒張壓平均80，
    本月平均100，落差25%應該觸發中度分級(門檻10/25/40)。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 120, "DiastolicPressure": 80}
        for d in range(1, 11)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 120, "DiastolicPressure": 100}
        for d in range(1, 11)
    ]
    vm = build_v9_view_model(baseline + current, {})
    dia_item = next((t for t in vm["top3"] if t["name"] == "舒張壓"), None)
    assert dia_item is not None, "舒張壓25%落差應該觸發中度分級進top3"
    assert dia_item["value"] == "80.0 → 100.0"
    assert dia_item["unit"] == "mmHg"
    assert dia_item["delta_line"] == "偏離 25%"


# ── 情境1（首月無個人基線，2026-08-12新增）──────────────────────────────
# 資料實際涵蓋不到3個月（_MIN_BASELINE_DAYS=90天）時，改用公定參考值
# 判讀，不比較前三月基線。見analysis_v9.py模組上方"情境1"區塊的說明。

def test_insufficient_baseline_top3_uses_absolute_standard_not_baseline_delta():
    """資料只涵蓋11月一個月（遠低於90天門檻），本月收縮壓月均170(危急偏高)、
    脈搏55(偏低)，應該用公定標準分級呈現，不是baseline delta%——value是
    單一數字不是「before → after」的箭頭格式，delta_line是「整體OO」不是
    「偏離 OO%」。"""
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 170, "DiastolicPressure": 75, "Pulse": 55}
        for d in range(1, 11)
    ]
    vm = build_v9_view_model(current, {})
    items = {t["name"]: t for t in vm["top3"]}
    assert items["收縮壓"]["value"] == "170"
    assert items["收縮壓"]["delta_line"] == "整體危急偏高"
    assert items["舒張壓"]["delta_line"] == "整體正常"
    assert items["脈搏"]["delta_line"] == "整體偏低"
    assert vm["top_summary"]["section_title"] == "本月生理數值提醒"
    assert "尚無個人基線" in vm["top_summary"]["headline"]
    # headline_count/headline_top_name不能出現在情境1的top_summary裡——
    # report_html_v9.py的_render_headline()只要看到這個key存在(即使值是
    # None)就會拿baseline-relative的「本月共N項...」模板重組headline，
    # 蓋掉這裡寫好的原文，2026-08-12實測抓到這個bug（畫面顯示「本月共
    # 主要生理量測較前三月基線出現明顯變化」，count被吃成空白）。
    assert "headline_count" not in vm["top_summary"]
    assert "headline_top_name" not in vm["top_summary"]


def test_sufficient_baseline_span_still_uses_baseline_relative_branch():
    """資料涵蓋超過90天(3個月)時，維持原本baseline-relative呈現方式，
    不套用情境1——這是regression guard，確保情境1只在資料真的不足時生效，
    不會誤觸發到正常累積基線的個案。"""
    baseline = [{"RecordTime": "2025-08-01T08:00:00", "SystolicPressure": 120, "DiastolicPressure": 80}]
    current = [{"RecordTime": "2025-11-05T08:00:00", "SystolicPressure": 122, "DiastolicPressure": 80}]
    vm = build_v9_view_model(baseline + current, {})
    assert "section_title" not in vm["top_summary"]
    assert "尚無個人基線" not in vm["top_summary"]["headline"]


def test_insufficient_baseline_frailty_card_uses_bmi_when_height_available():
    """有身高資料時，衰弱卡改用BMI分級（使用者2026-08-12決定的門檻：
    18.5/24/27）取代baseline比較——這裡身高158cm、體重70kg，BMI≈28.0，
    屬於「肥胖」分級，應該出現在chips裡。"""
    current = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00",
         "Content": {"weightKg": 70.0, "heightCm": 158}},
    ]
    vm = build_v9_view_model(current, {})
    frailty_card = next(c for c in vm["four_aspects"] if c["title"] == "衰弱與功能")
    assert frailty_card["primary_before"] is None
    bmi_chip = next(c for c in frailty_card["chips"] if c[0] == "BMI")
    assert "28.0" in bmi_chip[2] and "肥胖" in bmi_chip[2]


def test_insufficient_baseline_frailty_card_falls_back_to_avg_weight_without_height():
    """沒有身高資料時（BodyMetrics從未帶heightCm），衰弱卡不能算BMI，改直接
    顯示本月平均體重（使用者2026-08-12決定：「如果沒有身高的話，資料不足時
    我覺得可以直接顯示平均體重即可」），chips不該出現BMI項目。"""
    current = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 60.0}},
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-15T07:30:00", "Content": {"weightKg": 58.0}},
    ]
    vm = build_v9_view_model(current, {})
    frailty_card = next(c for c in vm["four_aspects"] if c["title"] == "衰弱與功能")
    assert not any(c[0] == "BMI" for c in frailty_card["chips"])
    assert frailty_card["primary_label"] == "體重・月平均"
    assert frailty_card["primary_after"] == "59.0 kg"


def test_insufficient_baseline_reference_standards_exclude_items_already_in_top3():
    """查核與說明「參考標準」不重複列出已經在「本月生理數值提醒」(top3)
    出現過的項目——使用者2026-08-12要求：「如果上面已經出現過的基準，
    下面就不要再寫一次，保持版面乾淨」。這裡收縮壓/舒張壓/脈搏都有資料、
    會出現在top3，參考標準應該只剩體溫/飲水/BMI三項。"""
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 130, "DiastolicPressure": 75, "Pulse": 70}
        for d in range(1, 6)
    ]
    vm = build_v9_view_model(current, {})
    top3_names = {t["name"] for t in vm["top3"]}
    assert top3_names == {"收縮壓", "舒張壓", "脈搏"}
    todo = dict(vm["check_notes"]["baseline_todo"])
    reference_names = [line.split(" ", 1)[0] for line in todo["參考標準"]]
    assert reference_names == ["體溫", "飲水", "BMI"]
    assert not (set(reference_names) & top3_names)


# ── 情境3（基線期間資料不完整/稀疏，2026-08-12新增）──────────────────────
# 資料時間跨度足夠(>=90天，不觸發情境1)，但某項指標本身測得稀疏——本月或
# 基線任一期間的量測密度低於1/4門檻，就視為「樣本有限」：不列入本月生理
# 數值提醒(top3)（不管偏離百分比多大），改列進其餘偏離並加註記；紀錄
# 完整度該項也加上提示badge。體重刻意不套用（設計上就是每月量測1-2次）。

def test_sparse_current_month_metric_excluded_from_top3_and_annotated():
    """本月血壓只在11/1、11/28兩天量測(密度2/28天≈7%，遠低於1/4門檻)，
    即使跟基線比起來偏離幅度很大，也不該出現在top3，應該改列進
    remaining_deviations並附註「樣本有限」。基線本身(8月整月每天量測)
    密度足夠，用來確認是「本月端」稀疏觸發的，不是基線端。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 120}
        for d in range(1, 32)
    ]
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 185},
        {"RecordTime": "2025-11-28T08:00:00", "SystolicPressure": 185},
    ]
    vm = build_v9_view_model(baseline + current, {})
    assert not any(t["name"] == "收縮壓" for t in vm["top3"])
    # 2026-08-13改法：remaining_deviations是4-tuple(name, delta_line,
    # sample_limited, acute)，樣本有限/急性事件都不再烤進delta_line文字裡，
    # 是獨立欄位（report_html_v9.py才能各自渲染成獨立的badge方塊，不是
    # 文字尾巴）。
    remaining = {name: (val, sl) for name, val, sl, _acute in vm["check_notes"]["remaining_deviations"]}
    assert "收縮壓" in remaining
    assert remaining["收縮壓"][1] is True


def test_sparse_baseline_metric_also_excluded_from_top3():
    """跟上一則相反：本月密度足夠，但基線只測了2天(密度遠低於1/4)——
    候選池比的是本月vs基線兩期，任一邊不可信，整個比較就不可信，一樣要
    被排除在top3外。"""
    baseline = [
        {"RecordTime": "2025-08-01T08:00:00", "SystolicPressure": 120},
        {"RecordTime": "2025-08-28T08:00:00", "SystolicPressure": 120},
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 185}
        for d in range(1, 29)
    ]
    vm = build_v9_view_model(baseline + current, {})
    assert not any(t["name"] == "收縮壓" for t in vm["top3"])
    remaining = {name: (val, sl) for name, val, sl, _acute in vm["check_notes"]["remaining_deviations"]}
    assert "收縮壓" in remaining
    assert remaining["收縮壓"][1] is True


def test_dense_data_not_flagged_sample_limited():
    """對照組：本月跟基線都是每天量測(密度100%)，不該被誤判樣本有限，
    正常大幅偏離時應該照舊出現在top3、delta_line不帶「樣本有限」字樣。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 120}
        for d in range(1, 32)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 185}
        for d in range(1, 29)
    ]
    vm = build_v9_view_model(baseline + current, {})
    bp_item = next((t for t in vm["top3"] if t["name"] == "收縮壓"), None)
    assert bp_item is not None
    assert "樣本有限" not in bp_item["delta_line"]


def test_weight_not_subject_to_sample_limited_rule():
    """體重設計上就是每月只測1-2次，稀疏是正常量測頻率——即使密度遠低於
    1/4門檻，也不該被排除在top3外或標「樣本有限」，這是使用者明確決定
    的例外。"""
    baseline = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-08-01T07:30:00", "Content": {"weightKg": 62.0}},
    ]
    current = [
        {"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 55.0}},
    ]
    vm = build_v9_view_model(baseline + current, {})
    weight_item = next((t for t in vm["top3"] if "體重" in t["name"]), None)
    assert weight_item is not None, "體重大幅減輕應該正常出現在top3，不受樣本有限規則影響"
    assert "樣本有限" not in weight_item["delta_line"]


def test_completeness_badge_shown_for_sparse_metric_when_baseline_sufficient():
    """紀錄完整度：本月血壓只測了2天(密度遠低於1/4)、基線密度足夠時，
    「血壓」這一項要帶上「樣本有限」badge（第4個tuple元素）；密度正常的
    體溫等其他項目不該帶badge。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 120, "Temperature": 36.5}
        for d in range(1, 32)
    ]
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 185, "Temperature": 36.6},
        {"RecordTime": "2025-11-28T08:00:00", "SystolicPressure": 185, "Temperature": 36.6},
    ]
    # 體溫密度也很稀疏(跟血壓同一批事件)，這裡改成每天都補一筆單獨的體溫
    # 事件，讓體溫密度正常、只有血壓稀疏，才能驗證「只有血壓帶badge」。
    current += [
        {"RecordTime": f"2025-11-{d:02d}T09:00:00", "Temperature": 36.6}
        for d in range(2, 28)
    ]
    vm = build_v9_view_model(baseline + current, {})
    completeness = {item[0]: item for item in vm["check_notes"]["completeness"]}
    assert len(completeness["血壓"]) == 4 and completeness["血壓"][3] == "樣本有限"
    assert len(completeness["體溫"]) == 3


def test_completeness_badge_suppressed_during_scenario1_no_baseline():
    """情境1(首月無個人基線)時，baseline_df本身通常空的或極稀疏，每項幾乎
    都會被判定樣本有限——但那個狀態已經在標題/查核說明顯性交代過了，
    紀錄完整度不需要逐項再貼一次「樣本有限」造成版面雜訊，這裡驗證badge
    被抑制、只顯示原始次數（3-tuple，不是4-tuple）。"""
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 130},
        {"RecordTime": "2025-11-02T08:00:00", "SystolicPressure": 132},
    ]
    vm = build_v9_view_model(current, {})
    completeness = {item[0]: item for item in vm["check_notes"]["completeness"]}
    assert len(completeness["血壓"]) == 3


def test_four_aspect_card_carries_sample_limited_flag_matching_candidate_pool():
    """2026-08-13新增：疾病監測面向卡片的主要數值(收縮壓達標率/飲水未達標
    天數/體溫偏離基線)底層跟候選池讀同一份資料，樣本有限的判斷要跟候選池
    一致——不能「其餘偏離」標了樣本有限，卡片上同一個指標卻完全沒提示。
    這裡本月血壓/舒張壓稀疏(2/28天)，高血壓卡要帶sample_limited=True；
    水/溫度密度正常，體液代謝卡跟感染監測卡不該被誤標。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 120, "Temperature": 36.5}
        for d in range(1, 32)
    ]
    current = [
        {"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 185},
        {"RecordTime": "2025-11-28T08:00:00", "SystolicPressure": 185},
    ] + [
        {"RecordType": "Water", "OccurredAt": f"2025-11-{d:02d}T09:00:00", "Content": {"amountMl": 2000}}
        for d in range(1, 29)
    ] + [
        {"RecordTime": f"2025-11-{d:02d}T10:00:00", "Temperature": 36.6}
        for d in range(1, 29)
    ]
    vm = build_v9_view_model(baseline + current, {})
    cards = {c["title"]: c for c in vm["four_aspects"]}
    assert cards["高血壓與心血管"]["sample_limited"] is True
    assert cards["體液代謝與容積"]["sample_limited"] is False
    assert cards["感染監測"]["sample_limited"] is False
    assert "sample_limited" not in cards["衰弱與功能"] or cards["衰弱與功能"]["sample_limited"] is False


def test_distribution_chart_flags_sample_limited_from_current_month_density_only():
    """2026-08-13新增：本月量測分布圖只描述當月自己的讀數，樣本有限判斷
    只看本月密度，不看基線——這裡故意讓基線也很稀疏（跟本月一樣2天），
    驗證bp_distribution/pulse_distribution的sample_limited只受本月密度
    影響，不受基線密度影響（基線密度低不該讓分布圖跟著被標記，那是候選池
    /卡片的責任，分布圖只講本月）。"""
    baseline = [
        {"RecordTime": "2025-08-01T08:00:00", "SystolicPressure": 120, "Pulse": 70},
        {"RecordTime": "2025-08-28T08:00:00", "SystolicPressure": 120, "Pulse": 70},
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 130, "Pulse": 75}
        for d in range(1, 29)
    ]
    vm = build_v9_view_model(baseline + current, {})
    assert vm["bp_distribution"]["sample_limited"] is False
    assert vm["pulse_distribution"]["sample_limited"] is False

    current_sparse = [
        {"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 130, "Pulse": 75},
        {"RecordTime": "2025-11-28T08:00:00", "SystolicPressure": 130, "Pulse": 75},
    ]
    vm_sparse = build_v9_view_model(baseline + current_sparse, {})
    assert vm_sparse["bp_distribution"]["sample_limited"] is True


def test_overlap_detail_annotates_sample_limited_signal_but_keeps_it():
    """2026-08-13使用者定案：跨指標同期關聯偵測(頂端detail句)如果引用到
    樣本有限的訊號，不整個排除——仍然參與偵測、算進句子裡，但訊號名稱後面
    要附加「(樣本有限)」標註。這裡血壓只在11/1、11/10-12、11/28五天有
    讀數(密度5/28≈18%<1/4，樣本有限)，11/10-12剛好偏高；心率每天都測
    (密度足夠，不受影響)，同樣在11/10-12偏高——兩訊號在這3天同時異常，
    應該被偵測為關聯事件，句子裡血壓要帶標註、心率不用。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 118, "Pulse": 70}
        for d in range(1, 32)
    ]
    current = (
        [{"RecordTime": "2025-11-01T08:00:00", "SystolicPressure": 120, "Pulse": 70}]
        + [{"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 140, "Pulse": 95} for d in (10, 11, 12)]
        + [{"RecordTime": "2025-11-28T08:00:00", "SystolicPressure": 120, "Pulse": 70}]
        + [{"RecordTime": f"2025-11-{d:02d}T09:00:00", "Pulse": 70} for d in range(2, 10)]
        + [{"RecordTime": f"2025-11-{d:02d}T09:00:00", "Pulse": 70} for d in range(13, 28)]
    )
    vm = build_v9_view_model(baseline + current, {})
    assert vm["top_summary"]["detail_clause"] is not None
    assert "血壓偏高(樣本有限)" in vm["top_summary"]["detail_clause"]
    assert "心率上升(樣本有限)" not in vm["top_summary"]["detail_clause"]
    assert "心率上升" in vm["top_summary"]["detail_clause"]


# ── 情境5（短期急性異常，2026-08-13新增）─────────────────────────────────
# 連續>=2天危急等級(見_CLUSTER_MIN_CONSECUTIVE)就算「急性事件」，一定要
# 進top3(不管月平均偏離%多小)，只有血壓/舒張壓/心率/體溫套用(有明確危急
# 門檻)，飲水/進食不套用(沒有危急概念)。

def test_two_consecutive_crisis_days_forces_bp_into_top3_despite_small_average_deviation():
    """本月30天血壓幾乎都跟基線一樣(120)，只有11/10-11/11兩天危急(185)——
    月平均落差很小，如果照舊有的「月平均偏離%」排序邏輯，這項血壓的
    deviation_pct可能小到排不進top3，但使用者定案：只要有連續>=2天危急，
    不管平均落差多小都要強制進top3。這裡另外安排一項跟血壓無關、平均
    偏離明顯更大的指標(舒張壓穩定上升)當對照，驗證急性事件不會被平均
    偏離更大的一般項目擠掉。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 120, "DiastolicPressure": 75}
        for d in range(1, 32)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 185 if d in (10, 11) else 121,
         "DiastolicPressure": 95}
        for d in range(1, 29)
    ]
    vm = build_v9_view_model(baseline + current, {})
    assert any(t["name"] == "收縮壓" for t in vm["top3"]), "連續2天危急的血壓應該被強制列入top3"
    bp_item = next(t for t in vm["top3"] if t["name"] == "收縮壓")
    assert bp_item["acute"] == "11/10–11/11連續危急"


def test_acute_top3_card_shows_dedicated_message_not_normal_delta_line():
    """急性事件的top3卡片，"acute"欄位要有值，供report_html_v9.py整段
    取代原本delta_line/qualifier的呈現——這裡只驗證資料面的欄位存在，
    渲染面的視覺驗證見手動/截圖驗證。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 120}
        for d in range(1, 32)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 185 if d in (5, 6, 7) else 121}
        for d in range(1, 29)
    ]
    vm = build_v9_view_model(baseline + current, {})
    bp_item = next(t for t in vm["top3"] if t["name"] == "收縮壓")
    assert bp_item["acute"] == "11/5–11/7連續危急"


def test_isolated_single_crisis_day_is_not_acute():
    """單一一天危急(沒有連續2天)不算急性事件——跟每日狀態格子的集中期
    判斷同一套規則，孤立1天不夠格。這裡讓本月大部分天數都中度偏高(145，
    足以靠一般嚴重度排序自然進top3)，只有1天真的到危急(185)但孤立，
    驗證acute欄位是None(不是被強制列入，是靠一般偏離%排序進來的)。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 120}
        for d in range(1, 32)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 185 if d == 10 else 145}
        for d in range(1, 29)
    ]
    vm = build_v9_view_model(baseline + current, {})
    bp_item = next(t for t in vm["top3"] if t["name"] == "收縮壓")
    assert bp_item["acute"] is None


def test_water_and_food_never_flagged_acute_even_with_extreme_values():
    """飲水/進食沒有「危急」等級概念，即使連續多天完全不達標，也不該被
    判定為急性事件——使用者確認：「沒有危急等級概念的就不套用該規則」。"""
    baseline = [
        {"RecordType": "Water", "OccurredAt": f"2025-08-{d:02d}T09:00:00", "Content": {"amountMl": 2000}}
        for d in range(1, 32)
    ] + [
        {"RecordType": "Meal", "OccurredAt": f"2025-08-{d:02d}T08:00:00", "Content": {"mealKind": "breakfast", "intakeAmount": "all"}}
        for d in range(1, 32)
    ]
    current = [
        {"RecordType": "Water", "OccurredAt": f"2025-11-{d:02d}T09:00:00", "Content": {"amountMl": 100}}
        for d in range(1, 29)
    ] + [
        {"RecordType": "Meal", "OccurredAt": f"2025-11-{d:02d}T08:00:00", "Content": {"mealKind": "breakfast", "intakeAmount": "lt_half"}}
        for d in range(1, 29)
    ]
    vm = build_v9_view_model(baseline + current, {})
    remaining_names = {name for name, _, _, _ in vm["check_notes"]["remaining_deviations"]}
    top3_names = {t["name"] for t in vm["top3"]}
    for name in ("飲水未達標天數", "進食不足天數"):
        if name in top3_names:
            item = next(t for t in vm["top3"] if t["name"] == name)
            assert item["acute"] is None
        if name in remaining_names:
            item = next(r for r in vm["check_notes"]["remaining_deviations"] if r[0] == name)
            assert item[3] is None


def test_more_than_three_acute_items_overflow_to_remaining_with_acute_badge():
    """4項生理數值(血壓/舒張壓/心率/體溫)同時都出現連續2天危急——top3只有
    3個名額，第4項(照嚴重度分級排序墊底的那項)應該落到「其餘偏離」，
    而且要一樣帶acute標記，不能因為沒進top3就把「這其實是急性事件」弄丟。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00",
         "SystolicPressure": 120, "DiastolicPressure": 75, "Pulse": 70, "Temperature": 36.5}
        for d in range(1, 32)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00",
         "SystolicPressure": 185 if d in (5, 6) else 121,
         "DiastolicPressure": 105 if d in (5, 6) else 76,
         "Pulse": 130 if d in (5, 6) else 71,
         "Temperature": 38.0 if d in (5, 6) else 36.5}
        for d in range(1, 29)
    ]
    vm = build_v9_view_model(baseline + current, {})
    acute_in_top3 = [t["name"] for t in vm["top3"] if t.get("acute")]
    acute_in_remaining = [r[0] for r in vm["check_notes"]["remaining_deviations"] if r[3]]
    assert len(acute_in_top3) == 3, "top3只有3個名額，全部應該被急性項目佔滿"
    assert len(acute_in_remaining) >= 1, "第4個急性項目應該落到其餘偏離，且仍帶acute標記"


def test_deviation_count_includes_acute_items_with_no_severity():
    """2026-08-13實測抓到的真bug：急性事件的severity可能是None(月平均落差
    不夠大，是acute規則強制列入top3的)，如果deviation_count只算severity
    truthy的項目，畫面左上角大數字卡會跟「本月共N項...」headline文字對
    不起來(例如headline說「共1項」，大數字卡卻顯示0)。這裡只安排一項
    純急性(月平均幾乎不變、只有2天危急)、其餘指標都正常，驗證
    deviation_count跟headline_count(=len(top3))兩者一致。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 120}
        for d in range(1, 32)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 185 if d in (5, 6) else 121}
        for d in range(1, 29)
    ]
    vm = build_v9_view_model(baseline + current, {})
    assert vm["top_summary"]["deviation_count"] == len(vm["top3"])


def test_two_separate_acute_clusters_in_same_month_both_shown():
    """2026-08-13使用者定案：一個月內出現兩段以上急性集中期時，全部都要
    列出來，不能只顯示最早那一段。這裡讓血壓在11/3-11/4、11/20-11/21
    各自獨立出現一段連續2天危急(中間隔了超過10天正常值，確保是兩段獨立
    的集中期，不是同一段延伸)，acute欄位應該同時包含兩段日期範圍。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 120}
        for d in range(1, 32)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00",
         "SystolicPressure": 185 if d in (3, 4, 20, 21) else 121}
        for d in range(1, 29)
    ]
    vm = build_v9_view_model(baseline + current, {})
    bp_item = next(t for t in vm["top3"] if t["name"] == "收縮壓")
    assert bp_item["acute"] == "11/3–11/4、11/20–11/21連續危急"


def test_four_aspects_bp_and_temp_cards_carry_acute_field():
    """2026-08-13使用者要求：疾病監測面向卡片也要新增急性標記——高血壓卡
    (bp)、感染監測卡(temp)的primary數值底層有明確危急門檻，acute欄位
    應該跟候選池的判斷一致；體液代謝卡(water)、衰弱卡(weight)沒有危急
    概念，acute應該是None/False。"""
    baseline = [
        {"RecordTime": f"2025-08-{d:02d}T08:00:00", "SystolicPressure": 120, "Temperature": 36.5}
        for d in range(1, 32)
    ]
    current = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00",
         "SystolicPressure": 185 if d in (5, 6) else 121,
         "Temperature": 38.0 if d in (5, 6) else 36.5}
        for d in range(1, 29)
    ]
    vm = build_v9_view_model(baseline + current, {})
    cards = {c["title"]: c for c in vm["four_aspects"]}
    assert cards["高血壓與心血管"]["acute"] == "11/5–11/6連續危急"
    assert cards["感染監測"]["acute"] == "11/5–11/6連續危急"
    assert not cards["體液代謝與容積"].get("acute")
    assert not cards["衰弱與功能"].get("acute")
