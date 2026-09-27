# 鎖住今天實測抓到的 report_pdf.py 顯示層問題：資料不足時要顯示「—」，
# 不能把內部標記字串 "__NO_DATA__" 原樣印到 PDF 上，也不能把 NO_DATA
# 誤判成「有資料」（三態判斷：True／False／NO_DATA，不是單純 if 真假值）。
import os

from src.report_pdf import _i, _pct, _cv_dir, generate_report
from src.analysis_core import NO_DATA, analyze_all
from src.data_parser import build_dataframe_from_request


def test_i_and_pct_no_data_safe():
    assert _i(NO_DATA) == "—"
    assert _i(57) == "57"
    assert _pct(NO_DATA) == "—"
    assert _pct(11.2) == "11.2%"


def test_cv_dir_labels_direction_correctly():
    assert "高於基線" in _cv_dir(11.2, 9)
    assert "低於基線" in _cv_dir(7, 9)
    assert _cv_dir(9, 9) == ""
    assert _cv_dir(NO_DATA, 9) == ""   # 資料不足不加註方向


def test_generate_report_produces_a_real_pdf_on_empty_data(tmp_path):
    """目前唯一涵蓋到`generate_report()`本身有沒有跑得起來的測試——之前只測過
    幾個NO_DATA安全格式化的小函式，`generate_report()`整條路徑完全沒被測過。
    刻意用空資料(全部欄位都是NO_DATA)跑，這是版面最容易崩潰的邊界情況
    （危象條框、趨勢折線等區塊在完全沒資料時都要顯性退場，不能對None做運算）。"""
    df = build_dataframe_from_request({"_data": []})
    analysis = analyze_all(df)
    summary = {"A": "本期無明顯偏離。", "B": "無關聯因子可報告。"}
    patient = {"name": "測試個案", "gender": "男", "age": 82, "diagnoses": ["高齡高血壓"]}
    out_path = str(tmp_path / "empty_data_report.pdf")

    generate_report(patient=patient, period=("2025-11-01", "2025-11-30"),
                     analysis=analysis, summary=summary, output_path=out_path)

    assert os.path.isfile(out_path)
    with open(out_path, "rb") as f:
        assert f.read(5) == b"%PDF-"


def test_generate_report_crashes_when_only_bp_data_present(tmp_path):
    """實測抓到的真bug：只有血壓資料、完全沒有血氧/進食/飲水時，
    `_block1_cardio`的`reach_pct`跟`_block2_fluid`的`water_short_days`都是
    NO_DATA字串，但`generate_report()`直接拿去跟數字比大小(`reach_pct < 70`)，
    TypeError直接crash——不是「沒有資料的邊界情況」才會發生，這比全空資料
    更常見（真實個案幾乎一定有些欄位缺測），已修正成NO_DATA-safe比較。"""
    data = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 130, "DiastolicPressure": 80}
        for d in range(1, 15)
    ]
    df = build_dataframe_from_request({"_data": data})
    analysis = analyze_all(df)
    summary = {"A": "測試", "B": "測試"}
    patient = {"name": "測試", "gender": "男", "age": 82, "diagnoses": []}

    # 不應該拋出例外——這行本身就是斷言：修正前這裡會是TypeError
    generate_report(patient=patient, period=("2025-11-01", "2025-11-14"),
                     analysis=analysis, summary=summary, output_path=str(tmp_path / "bp_only.pdf"))


def test_generate_report_crashes_when_no_spo2_or_temp_data(tmp_path):
    """另一個真bug：`_progress()`畫血氧/進食/飲水達標長條時，`ok_days`若是
    NO_DATA字串，`bar_w * ok_days / total`會TypeError——只要`total`(觀察天數)
    非零、但某一項照護觀察完全沒登打，就會觸發，這是partial data的常見情況，
    不需要整段觀察期完全沒資料。"""
    data = [
        {"RecordTime": f"2025-11-{d:02d}T08:00:00", "SystolicPressure": 135, "DiastolicPressure": 82, "Pulse": 76}
        for d in range(1, 20)
    ]
    data.append({"RecordType": "BodyMetrics", "OccurredAt": "2025-11-01T07:30:00", "Content": {"weightKg": 60.0}})
    df = build_dataframe_from_request({"_data": data})
    analysis = analyze_all(df)
    summary = {"A": "測試", "B": "測試"}
    patient = {"name": "測試", "gender": "男", "age": 82, "diagnoses": []}

    generate_report(patient=patient, period=("2025-11-01", "2025-11-19"),
                     analysis=analysis, summary=summary, output_path=str(tmp_path / "no_spo2_temp.pdf"))
