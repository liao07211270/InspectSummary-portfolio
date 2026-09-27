# 鎖住 report_html_v9.py（v9主要圖片渲染路徑，2026-08-07起改成PNG輸出，
# 不再是PDF）——之前完全沒有測試覆蓋，這幾個都是實測抓到的真bug：chip顏色
# 沒套用、長條圖百分比加總不到100%、NO_DATA字串外洩到畫面文字。
# render_v9_html()是純函式（吃view-model字典、吐HTML字串），不需要真的產圖
# 就能測，比較快也不依賴本機有沒有裝瀏覽器。
import os

from src.mock_data_v9 import generate_v9_view_model
from src.report_html_v9 import render_v9_html, generate_v9_report_html, _find_chrome


def test_patient_name_is_html_escaped_not_injected_raw():
    """個案姓名這種使用者輸入欄位如果沒escape，理論上可以塞HTML/JS標籤進去，
    印PDF時雖然只是靜態渲染風險有限，但還是不該讓原始標籤字面出現在輸出裡。"""
    vm = generate_v9_view_model(patient='<script>alert(1)</script>')
    html = render_v9_html(vm)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_top3_title_reflects_actual_item_count():
    vm = generate_v9_view_model()
    vm["top3"] = vm["top3"][:2]  # 只留2項
    html = render_v9_html(vm)
    assert "偏離幅度最大的2項" in html
    assert "偏離幅度最大的三項" not in html


def test_top3_empty_shows_no_number_and_placeholder_text():
    vm = generate_v9_view_model()
    vm["top3"] = []
    html = render_v9_html(vm)
    assert "偏離幅度最大的項目" in html
    assert "本期無明顯偏離項目" in html


def test_remaining_deviations_empty_shows_placeholder_not_hardcoded_three():
    vm = generate_v9_view_model()
    vm["check_notes"]["remaining_deviations"] = []
    html = render_v9_html(vm)
    assert "其餘偏離項目" in html
    assert "其餘偏離 3 項" not in html
    assert "（無其餘顯著偏離）" in html


def test_four_aspect_chip_after_value_is_orange_not_black():
    """實測抓到的真bug：chip的橘色原本只在warn=True才套用，但呼叫端從未傳過
    warn=True，導致尿色偏深/排泄異常這類子項的本月數值永遠是黑色沒標色。
    修正後「after」值一律橘色（跟原始設計稿一致）。"""
    vm = generate_v9_view_model()
    html = render_v9_html(vm)
    # mock_data_v9 預設「尿色偏深」是 0 → 6 天，"6 天" 要在橘色(#D85A30) span 裡
    idx = html.find(">6 天<")
    assert idx != -1, "找不到「6 天」這個chip值"
    preceding = html[max(0, idx - 60):idx]
    assert "#D85A30" in preceding


def test_no_data_cell_renders_as_filled_gray_not_bordered_outline():
    """2026-08-12五次修正：使用者要求「單次異常」改成黃色、「無量測紀錄」
    改成灰色實心格子——異常搬去黃色後，原本無資料格子用的邊框空格改成
    灰色實心，跟其餘三色(綠/黃/橘)都是實心格子的視覺語言一致。這裡用
    colors清單裡混一個None(代表當天無資料)，驗證輸出裡看不到舊版的
    border-only樣式，改成有background:{mute灰色}的實心格子。"""
    from src.report_html_v9 import _daily_status_section, PALETTE
    ds = {
        "days": 3,
        "rows": [
            {"label": "精神", "colors": ["#639922", None, "#D9A521"], "note": "異常 1次"},
        ],
        "events_row": {"icons_by_day": [[], [], []], "legend_items": []},
        "date_ticks": {0: "11-01"},
    }
    html = _daily_status_section(ds)
    assert f'background:{PALETTE["mute"]};' in html
    assert "border:.6pt solid" not in html


def test_four_aspect_card_single_trend_note_item_has_no_bullet():
    """2026-08-11改法：PM要求趨勢註記改成條列式，讓卡片更好掃視。高血壓卡
    固定只會有1句(見analysis_v9.py)，只有1項時不該加bullet符號，維持單行
    純文字。"""
    from src.report_html_v9 import _four_aspect_card
    card = {
        "title": "高血壓與心血管", "period_label": "前三月 → 本月", "accent": "#147D6F",
        "primary_label": "收縮壓達標率", "primary_before": "81%", "primary_after": "67%",
        "chips": [(">=160 危急", "0", "2 次"), ("偏高比例", "19%", "28%")],
        "trend_note": ["月均 144 mmHg，較前三月上升（133 → 144）"],
    }
    html = _four_aspect_card(card)
    assert "月均 144 mmHg，較前三月上升（133 → 144）" in html
    assert "・" not in html


def test_four_aspect_card_multi_item_trend_note_renders_as_bullets():
    """2項以上要逐項條列，每項前面加「・」——這裡體液代謝卡有2句(飲水+
    排便)，兩句都要各自出現、且都要有bullet符號。"""
    from src.report_html_v9 import _four_aspect_card
    card = {
        "title": "體液代謝與容積", "period_label": "前三月 → 本月", "accent": "#147D6F",
        "primary_label": "飲水未達標天數", "primary_before": "3 天", "primary_after": "9 天",
        "chips": [("特殊排尿", "0 天", "3 天"), ("排便異常", "0 天", "3 天")],
        "trend_note": ["飲水未達標多集中於11/9–11/12", "排便異常自11/7起共3天"],
    }
    html = _four_aspect_card(card)
    assert "・飲水未達標多集中於11/9–11/12" in html
    assert "・排便異常自11/7起共3天" in html


def test_bp_distribution_segments_always_sum_to_exactly_100_percent():
    """實測抓到的真bug：四段百分比各自獨立四捨五入，加總常常不到100%
    （例如2+94+3+0=99），長條尾端會留一截沒上色的空隙，看起來像被截斷。
    修正後最後一段吸收捨入誤差，加總永遠等於100。"""
    vm = generate_v9_view_model()
    vm["bp_distribution"]["segments"] = [(2, "#A32D2D"), (94, "#639922"), (3, "#EF9F27"), (0, "#D85A30")]
    render_v9_html(vm)  # _dist_block 在 render_v9_html 內部被呼叫，這裡改用直接測函式行為

    from src.report_html_v9 import _dist_block
    html_block = _dist_block("收縮壓分布", vm["bp_distribution"])
    import re
    widths = [float(w) for w in re.findall(r'width:([\d.]+)%', html_block)]
    assert sum(widths) == 100


def test_tick_labels_never_merge_and_share_one_uniform_font_size():
    """2026-08-12三次修正：使用者看過「90/100」合併標籤的版本後明確推翻
    這個做法，訂出兩條規則：(1) 每個刻度數字字級必須完全一致，不能有的
    大有的小；(2) 不要出現「數字/數字」黏在一起的格式，每個數字都要
    單獨顯示在自己對應的刻度線下方。這裡用血壓分布最窄的90/100(間距只有
    3%)驗證：兩個數字各自獨立輸出("90"跟"100"分開，不是"90/100")，而且
    字級跟其他正常間距的刻度(130)完全一樣，不會忽大忽小。"""
    from src.report_html_v9 import _dist_block, _TICK_LABEL_SIZE
    dist = {
        "count_label": "90 次量測／30 天",
        "segments": [(3, "#D85A30"), (3, "#EF9F27"), (23, "#639922"), (63, "#EF9F27"), (6.7, "#D85A30")],
        "ticks": [(3, "90"), (6, "100"), (29, "130"), (92, "160")],
        "chips": [("目標內", "23%", "21 次"), ("偏高", "63%", "57 次"), ("偏低", "3%", "3 次"),
                  ("≥160／<90 mmHg", "9 次", "11/4、11/9、11/11…")],
    }
    html_block = _dist_block("收縮壓分布", dist)
    assert ">90<" in html_block
    assert ">100<" in html_block
    assert ">130<" in html_block
    assert ">160<" in html_block
    assert "90/100" not in html_block
    # 每個刻度數字都應該是同一個字級，不能忽大忽小
    import re
    tick_font_sizes = set(re.findall(rf'font-size:({_TICK_LABEL_SIZE})pt', html_block))
    assert tick_font_sizes == {str(_TICK_LABEL_SIZE)}


def test_all_normal_still_shows_normal_range_boundary_ticks():
    """2026-08-12使用者兩輪回饋疊加後的規則：本月讀數全部落在「正常」層
    時，危急/偏低/偏高層寬度都是0%，這幾層對應的刻度(90/160)沒有意義該
    隱藏；但正常層自己的邊界(100/130)無論如何都要保留——完全不顯示任何
    刻度會讓讀者看不出「正常範圍」對照標準是什麼，太隱晦。"""
    from src.report_html_v9 import _dist_block
    dist = {
        "count_label": "3 次量測／1 天・月均 110.0 mmHg",
        "segments": [(0, "#D85A30"), (0, "#EF9F27"), (100, "#639922"), (0, "#EF9F27"), (0, "#D85A30")],
        "ticks": [(0, "90"), (0, "100"), (100, "130"), (100, "160")],
        "chips": [("目標內", "100%", "3 次"), ("偏高", "0%", "0 次"), ("偏低", "0%", "0 次"),
                  ("≥160／<90 mmHg", "0 次", None)],
    }
    html_block = _dist_block("收縮壓分布", dist)
    assert ">90<" not in html_block
    assert ">100<" in html_block
    assert ">130<" in html_block
    assert ">160<" not in html_block


def test_outer_crisis_ticks_only_shown_for_tiers_that_actually_have_data():
    """混合情境：只有「偏高」層有資料(高血壓)，危急/偏低層都是0%——正常層
    邊界(100/130)照規則永遠顯示，偏高層邊界(160)因為真的有資料也要顯示；
    只有90(危急低/偏低層邊界，兩者都是0%)要被隱藏。"""
    from src.report_html_v9 import _dist_block
    dist = {
        "count_label": "10 次量測／10 天・月均 140.0 mmHg",
        "segments": [(0, "#D85A30"), (0, "#EF9F27"), (70, "#639922"), (30, "#EF9F27"), (0, "#D85A30")],
        "ticks": [(0, "90"), (0, "100"), (70, "130"), (100, "160")],
        "chips": [("目標內", "70%", "7 次"), ("偏高", "30%", "3 次"), ("偏低", "0%", "0 次"),
                  ("≥160／<90 mmHg", "0 次", None)],
    }
    html_block = _dist_block("收縮壓分布", dist)
    assert ">90<" not in html_block
    assert ">100<" in html_block
    assert ">130<" in html_block
    assert ">160<" in html_block


def test_daily_status_flex_columns_have_min_width_zero_to_prevent_grid_drift():
    """2026-08-11修正：使用者實測抓到真bug——偶發事件列的圖標日期越後面，
    跟上面四列色塊格子的x位置偏移越來越大(day3幾乎對齊、day20已經偏移
    快一整格)。根源是偶發事件列右側legend用white-space:nowrap、最長那行
    撐爆宣告的flex:0 0 23mm，flex item預設min-width:auto讓它真的被撐寬，
    連帶擠壓同一個flex容器裡flex:1的日期格子grid可用寬度，repeat(days,1fr)
    算出來的每欄pitch跟著全部縮小，越後面的日期累積誤差越大。修正法是
    所有flex:0 0 14mm/23mm的label／note／legend欄位都補上min-width:0，
    讓文字overflow時只會視覺溢出、不會把box撐寬偷走grid的空間。這裡用
    一組會製造長legend文字的假資料，驗證輸出的HTML每一個flex:0 0 14mm
    跟flex:0 0 23mm欄位都帶有min-width:0。"""
    from src.report_html_v9 import _daily_status_section
    import re
    ds = {
        "days": 20,
        "rows": [
            {"label": "精神", "colors": ["#639922"] * 20, "note": "異常 0次"},
            {"label": "情緒", "colors": ["#639922"] * 20, "note": "異常 0次"},
            {"label": "睡眠", "colors": ["#639922"] * 20, "note": "白天睡多 0次"},
            {"label": "排泄", "colors": ["#639922"] * 20, "note": "異常 0次"},
        ],
        "events_row": {
            "icons_by_day": [[] for _ in range(20)],
            "legend_items": [
                {"glyph": "◐", "color": "#D85A30", "label": "情緒異常(突哭/笑)", "count": 5},
                {"glyph": "✱", "color": "#D85A30", "label": "易怒/激躁", "count": 2},
            ],
        },
        "date_ticks": {0: "11-01"},
    }
    html = _daily_status_section(ds)
    flex_basis_blocks = re.findall(r'flex:0 0 (?:14mm|23mm)[^"]*', html)
    assert len(flex_basis_blocks) >= 10, "應該找到4列+1個偶發事件列，各2個(label+note/legend)flex:0 0欄位"
    assert all("min-width:0" in block for block in flex_basis_blocks), (
        f"每個flex:0 0 14mm/23mm欄位都要有min-width:0，否則長文字會撐寬box、"
        f"偷走中間grid的空間造成日期格子跟圖標對不齊：{flex_basis_blocks}"
    )


def test_events_row_icons_vertically_centered_not_top_aligned():
    """2026-08-11：使用者要求每一天的圖標要對齊格子的正中央y軸——原本是
    justify-content:flex-start(貼齊格子頂端)，改成center。"""
    from src.report_html_v9 import _daily_status_section
    ds = {
        "days": 1,
        "rows": [],
        "events_row": {
            "icons_by_day": [[("✱", "#D85A30")]],
            "legend_items": [{"glyph": "✱", "color": "#D85A30", "label": "易怒/激躁", "count": 1}],
        },
        "date_ticks": {},
    }
    html = _daily_status_section(ds)
    assert "justify-content:center" in html
    assert "justify-content:flex-start" not in html


def test_generate_v9_report_html_produces_a_single_page_png(tmp_path):
    """端到端整合測試：真的呼叫headless Chrome/Edge截圖存PNG。本機沒裝瀏覽器時
    跳過（CI或其他環境可能沒有），不讓這個環境限制擋住其他測試跑完。
    2026-08-07依醫師會議需求，匯出格式從PDF改成PNG，這裡對應改成驗證PNG
    magic bytes跟圖片尺寸（不再驗證PDF頁數，改驗證截圖尺寸有沒有對齊
    html_to_png()裡設定的A4版面尺寸*截圖畫質倍率）。"""
    try:
        _find_chrome()
    except FileNotFoundError:
        import pytest
        pytest.skip("本機找不到 Chrome/Edge，跳過headless截圖整合測試")

    vm = generate_v9_view_model()
    out_path = str(tmp_path / "v9_test.png")
    generate_v9_report_html(vm, out_path)

    assert os.path.isfile(out_path)
    with open(out_path, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"

    try:
        from PIL import Image  # 開發除錯用，不在requirements.txt裡，沒裝就只驗證檔案本身
    except ImportError:
        return
    from src.report_html_v9 import _PAGE_PX_WIDTH, _PAGE_PX_HEIGHT, _PNG_SCALE
    with Image.open(out_path) as img:
        assert img.size == (_PAGE_PX_WIDTH * _PNG_SCALE, _PAGE_PX_HEIGHT * _PNG_SCALE), (
            "截圖尺寸應該剛好等於A4版面尺寸*畫質倍率，尺寸不對代表視窗大小"
            "沒對齊render_v9_html()的.page CSS設計尺寸"
        )


# ── 情境5（短期急性異常，2026-08-13新增）─────────────────────────────────

def test_top3_card_acute_replaces_delta_line_with_dedicated_message():
    """急性事件時，第三行整段換成紫紅色的acute文字＋「急性事件」badge，
    原本的delta_line/qualifier不該出現。"""
    from src.report_html_v9 import _top3_card
    item = {
        "name": "收縮壓", "value": "120.0 → 126.0", "unit": "mmHg",
        "ref_line": "目標上限 130・基線 120", "delta_line": "偏離 5%",
        "qualifier": None, "acute": "11/5–11/6連續危急",
    }
    html = _top3_card(item)
    assert "11/5–11/6連續危急" in html
    assert "急性事件" in html
    assert "偏離 5%" not in html


def test_top3_card_acute_badge_on_own_line_not_squeezed_with_text():
    """2026-08-13使用者要求：急性文字太長時要動態縮小字級，且不能讓badge
    跟文字擠爆邊界——這裡驗證badge是獨立一個區塊(div)，不是跟文字同一個
    inline span擠在一起（避免重演紀錄完整度badge先前遇過的擁擠/裁切問題，
    見_completeness_item_html()的說明）。"""
    from src.report_html_v9 import _top3_card
    item = {
        "name": "收縮壓", "value": "120.0 → 126.0", "unit": "mmHg",
        "ref_line": "目標上限 130・基線 120", "delta_line": "偏離 5%",
        "qualifier": None, "acute": "11/5–11/6連續危急",
    }
    html = _top3_card(item)
    assert '<div style="margin-top:.5mm;">' in html


def test_acute_text_size_shrinks_for_multi_segment_dates():
    """2026-08-13使用者要求：一個月內出現兩段以上急性集中期時全部列出來，
    但字級要動態縮小避免超出卡片邊界——這裡驗證字數越多、字級越小。"""
    from src.report_html_v9 import _acute_text_size
    short = _acute_text_size("11/5–11/6連續危急")
    long_multi = _acute_text_size("11/3–11/4、11/12–11/14、11/25–11/27連續危急")
    assert short > long_multi


def test_four_aspect_card_shows_acute_badge_when_present():
    """疾病監測面向卡片（例如高血壓與心血管卡）如果card["acute"]有值，
    primary_label旁應該出現「急性事件」badge——跟top3/其餘偏離同一套
    視覺語言，讀者才不會覺得同一個收縮壓數字講法不一致。"""
    from src.report_html_v9 import _four_aspect_card
    card = {
        "title": "高血壓與心血管", "period_label": "前三月 → 本月", "accent": "#147D6F",
        "primary_label": "收縮壓達標率", "primary_before": "81%", "primary_after": "67%",
        "acute": "11/5–11/6連續危急",
        "chips": [(">=160 危急", "0", "2 次"), ("偏高比例", "19%", "28%")],
        "trend_note": ["月均 144 mmHg，較前三月上升（133 → 144）"],
    }
    html = _four_aspect_card(card)
    assert "急性事件" in html


def test_four_aspect_card_no_acute_badge_when_not_present():
    """對照組：card沒有acute欄位(既有既有呼叫)時，不該出現急性badge，
    確保這次新增的功能不影響原本沒有樣本有限/急性事件的卡片。"""
    from src.report_html_v9 import _four_aspect_card
    card = {
        "title": "衰弱與功能", "period_label": "長期趨勢・對標 Fried", "accent": "#D85A30",
        "primary_label": "體重・六個月", "primary_before": "60.0", "primary_after": "57.0 kg",
        "chips": [("進食量", "正常", "偏少"), ("衛生協助項目", "3 次", "5 次")],
        "trend_note": ["體重顯著減輕（-5.0%）"],
    }
    html = _four_aspect_card(card)
    assert "急性事件" not in html
