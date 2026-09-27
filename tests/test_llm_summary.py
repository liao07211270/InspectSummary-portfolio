# 鎖住今天實測抓到的 _parse_two() bug：str.lstrip() 吃的是字元集合不是前綴
# 字串，模型把標題單獨換行時會把整段內容清空。詳情見 llm_summary.py 的
# _parse_two() docstring。
from src.llm_summary import _parse_two, _fi, _fs, _strip_no_data, NO_DATA, _repair, _validate


def test_parse_two_inline_format():
    text = "A 核心走勢：偏離個人基準最明顯者為晨間收縮壓，月初約133緩升至月底約147mmHg。\nB 關聯因子：血壓與心率上移的時段與飲水偏少的紀錄重疊。"
    r = _parse_two(text)
    assert r["A"].startswith("偏離")
    assert r["B"].startswith("血壓")


def test_parse_two_header_on_own_line():
    """這是實測抓到的 bug 場景：標題單獨一行，內容在下一行。"""
    text = "A 核心走勢：\n偏離個人基準最明顯者為晨間收縮壓，月初約133緩升至月底約147mmHg。\nB 關聯因子：\n血壓與心率上移的時段與飲水偏少的紀錄重疊。"
    r = _parse_two(text)
    assert r["A"] != ""
    assert r["B"] != ""
    assert r["A"].startswith("偏離")
    assert r["B"].startswith("血壓")


def test_parse_two_multiline_paragraph():
    text = "A 核心走勢：\n偏離個人基準最明顯者為晨間收縮壓，\n月初約133緩升至月底約147mmHg。\nB 關聯因子：\n血壓與心率上移的時段\n與飲水偏少的紀錄重疊。"
    r = _parse_two(text)
    assert "晨間收縮壓" in r["A"]
    assert "月初約133" in r["A"]
    assert "與飲水偏少" in r["B"]


def test_parse_two_no_colon():
    text = "A 核心走勢\n偏離個人基準最明顯者為晨間收縮壓，月初約133緩升至月底約147mmHg。\nB 關聯因子\n血壓與心率上移的時段與飲水偏少的紀錄重疊。"
    r = _parse_two(text)
    assert r["A"] != ""
    assert r["B"] != ""


def test_fi_and_fs_no_data_safe():
    assert _fi(NO_DATA) == "—"
    assert _fi(5) == "5"
    assert _fs(NO_DATA) == "—"
    assert _fs("緩升") == "緩升"


def test_strip_no_data_prunes_nested_keys():
    facts = {
        "血壓": {"月初": 130, "趨勢": NO_DATA, "目標": 140},
        "活動": {"起始每週": NO_DATA, "近期每週": NO_DATA},
        "飲水未達標天數": 12,
    }
    cleaned = _strip_no_data(facts)
    assert "趨勢" not in cleaned["血壓"]
    assert cleaned["血壓"]["月初"] == 130
    assert "活動" not in cleaned          # 整組都是 NO_DATA，父層也該被拿掉
    assert cleaned["飲水未達標天數"] == 12


def test_repair_preserves_en_dash_in_date_ranges():
    """2026-08-13實測抓到的bug：v9的_fmt_md()系列函式組日期區間固定用
    「–」(en dash, U+2013)——例如「11/1–11/30」，跟一般ASCII連字號「-」
    (U+002D)是不同字元。_repair()底層的_strip_unresolved()原本的允許
    符號清單漏收這個字元，把它當成「翻不出來的外語片段」整個砍掉，
    「11/1–11/30」被改成「11/111/30」（數字黏在一起，日期範圍整個壞掉）。
    這不是LLM輸出有問題——用戶回報v9的跨指標關聯句子每次都轉fallback，
    根源就是這裡：LLM給的答案是對的，是_repair()把它改錯了，導致
    llm_summary_v9.py的_terms_survived()逐字比對必定失敗。"""
    text = "自11/1–11/30期間起，心率上升與情緒異常同時出現異常跡象。"
    repaired = _repair(text)
    assert "11/1–11/30" in repaired
    assert "11/111/30" not in repaired


def test_validate_does_not_flag_en_dash_as_foreign_character():
    """跟上一則測試同一個bug的另一半——_validate()的「非允許字元殘留」
    檢查也共用同一份_ALLOWED_SYMBOLS，en dash沒收錄的話，即使_repair()
    沒把它砍掉，_validate()也會錯誤地把它當成外語字元標記成問題，一樣
    會觸發重試/fallback。"""
    text = "自11/1–11/30期間起，心率上升與情緒異常同時出現異常跡象。"
    problems = _validate(text)
    assert not any("非允許字元殘留" in p for p in problems)
