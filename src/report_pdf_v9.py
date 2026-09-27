"""
v9 版面驗證用 PDF 產生器 —— 對照主管提供的 `care-summary-v9.html` 示意稿逐段落實作。

刻意獨立於 `report_pdf.py`（目前生產環境在用的 v2 版面），不動到既有輸出，
純粹拿來驗證 v9 新版面在 A4 紙上排起來的樣子。吃的資料是
`mock_data_v9.generate_v9_view_model()` 產生的假資料 view-model（欄位定義見
該檔案），還沒接任何真實資料計算邏輯。

版面對照 care-summary-v9.html 的段落順序：
    1. 頁首（姓名/性別/年齡/CFS/分組 + 月份標題）
    2. 頂部偏離摘要（偏離數量 + 敘述）
    3. 偏離幅度最大的三項
    4. 本月紀錄佐證（血壓/血氧分布長條）
    5. 四個面向（前三月 → 本月 對照卡）
    6. 每日狀態與事件（精神/情緒/睡眠/排泄 30 天格 + 偶發事件列）
    7. 查核與說明（其餘偏離/完整度/比較基準）
    8. 頁尾免責聲明

單位直接沿用 html 稿的 mm，方便逐項跟原始設計稿核對比例是否一致。
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas

from .report_pdf import _register_fonts, _wrap_lines

PAGE_W, PAGE_H = A4
MARGIN_TOP, MARGIN_SIDE, MARGIN_BOTTOM = 9 * mm, 12 * mm, 6 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_SIDE

C_TEAL = colors.HexColor("#0F5F54")
C_HEAD = colors.HexColor("#1A2E2A")
C_ORANGE = colors.HexColor("#D85A30")
C_SUB = colors.HexColor("#5C6B68")
C_MUTE = colors.HexColor("#9AA5A1")
C_BORDER = colors.HexColor("#D3DCD9")
C_CARDBG = colors.HexColor("#F6F8F6")
C_CARDBG_WARN = colors.HexColor("#FBF1EC")
C_CARDBG_TODO = colors.HexColor("#EEF3F0")
C_GREEN = colors.HexColor("#639922")


def _hx(h):
    return colors.HexColor(h)


class _Cursor:
    """Y 游標，單位 mm，從頁面頂部算起（跟 html 版面的閱讀順序一致）。"""
    def __init__(self, y0_mm):
        self.y = y0_mm

    def canvas_y(self, extra_mm=0):
        return PAGE_H - (self.y + extra_mm) * mm

    def advance(self, dy_mm):
        self.y += dy_mm


def _text(c, x_mm, y, s, font="MSJH", size=8, color=C_HEAD, align="left", max_w_mm=None):
    c.setFont(font, size)
    c.setFillColor(color)
    x = x_mm * mm
    yy = y.canvas_y()
    if align == "left":
        c.drawString(x, yy, s)
    elif align == "right":
        c.drawRightString((x_mm + (max_w_mm or 0)) * mm, yy, s)


def _wrap_block(c, x_mm, y, s, width_mm, font="MSJH", size=8, color=C_HEAD, lh_mm=1.5):
    lines = _wrap_lines(c, s, width_mm * mm, font, size)
    c.setFont(font, size)
    c.setFillColor(color)
    for ln in lines:
        c.drawString(x_mm * mm, y.canvas_y(), ln)
        y.advance(lh_mm)
    return len(lines)


def _section_header(c, x_mm, y, title, note=None, dot_color=C_ORANGE):
    c.setFillColor(dot_color)
    c.rect(x_mm * mm, y.canvas_y(0.8), 0.85 * mm, 0.85 * mm, fill=1, stroke=0)
    _text(c, x_mm + 2, y, title, "MSJH-Bold", 9.4, C_TEAL)
    if note:
        note_x = x_mm + 2 + c.stringWidth(title, "MSJH-Bold", 9.4) / mm + 2
        _text(c, note_x, y, note, "MSJH", 7.4, C_MUTE)
    y.advance(4.2)


def _chip(c, x_mm, y_top_mm, w_mm, h_mm, label, before, after, bg=C_CARDBG, warn=False):
    """一格『前三月→本月』小卡：label(灰) + before → after。"""
    c.setFillColor(bg)
    c.roundRect(x_mm * mm, (PAGE_H / mm - y_top_mm - h_mm) * mm, w_mm * mm, h_mm * mm, 0.4 * mm, fill=1, stroke=0)
    c.setFont("MSJH", 6.2)
    c.setFillColor(C_ORANGE if warn else C_SUB)
    c.drawString((x_mm + 0.5) * mm, (PAGE_H / mm - y_top_mm - 1.7) * mm, label)
    c.setFont("MSJH-Bold", 7.2)
    line = f"{before} → " if before else ""
    bw = c.stringWidth(line, "MSJH", 7.2)
    ty = (PAGE_H / mm - y_top_mm - h_mm + 1.1) * mm
    c.setFont("MSJH", 7.2)
    c.setFillColor(C_MUTE)
    c.drawString((x_mm + 0.5) * mm, ty, line)
    c.setFont("MSJH-Bold", 7.2)
    c.setFillColor(C_ORANGE)
    c.drawString((x_mm + 0.5) * mm + bw, ty, str(after))


def generate_v9_report(vm: dict, output_path: str):
    _register_fonts()
    c = canvas.Canvas(output_path, pagesize=A4)
    y = _Cursor(MARGIN_TOP / mm)
    L = MARGIN_SIDE / mm
    W = CONTENT_W / mm

    # ── 1. 頁首 ──────────────────────────────────────────────────────────
    h = vm["header"]
    c.setFont("MSJH-Bold", 13); c.setFillColor(C_HEAD)
    c.drawString(L * mm, y.canvas_y(3.6), h["name"])
    name_w = c.stringWidth(h["name"], "MSJH-Bold", 13) / mm
    c.setFont("MSJH", 8.4); c.setFillColor(C_SUB)
    c.drawString((L + name_w + 3) * mm, y.canvas_y(3.2),
                 f"{h['gender']}・{h['age']} 歲・CFS {h['cfs']}・{h['group']}")
    c.setFont("MSJH-Bold", 9.4); c.setFillColor(C_TEAL)
    c.drawRightString((L + W) * mm, y.canvas_y(3.6), h["period_label"])
    y.advance(5.2)
    c.setStrokeColor(C_TEAL); c.setLineWidth(2.4 * 0.35)
    c.line(L * mm, y.canvas_y(), (L + W) * mm, y.canvas_y())
    y.advance(4.5)

    # ── 2. 頂部偏離摘要 ───────────────────────────────────────────────────
    ts = vm["top_summary"]
    big_w = 26
    c.setFont("MSJH-Bold", 34); c.setFillColor(C_ORANGE)
    c.drawString(L * mm, y.canvas_y(6.5), str(ts["deviation_count"]))
    num_w = c.stringWidth(str(ts["deviation_count"]), "MSJH-Bold", 34) / mm
    c.setFont("MSJH-Bold", 10); c.setFillColor(C_HEAD)
    c.drawString((L + num_w + 2) * mm, y.canvas_y(3.2), "項紀錄")
    c.drawString((L + num_w + 2) * mm, y.canvas_y(6.6), "偏離參考值")
    c.setStrokeColor(C_BORDER); c.setLineWidth(0.3)
    c.line((L + big_w) * mm, y.canvas_y(-1), (L + big_w) * mm, y.canvas_y(9))
    text_x = L + big_w + 6
    y2 = _Cursor(y.y)
    _wrap_block(c, text_x, y2, ts["headline"], W - big_w - 6, "MSJH-Bold", 11.5, C_HEAD, 4.6)
    y2.advance(0.6)
    _wrap_block(c, text_x, y2, ts["detail"], W - big_w - 6, "MSJH", 8.4, C_SUB, 3.4)
    y.advance(max(9.5, y2.y - y.y) + 2.2)

    # ── 3. 偏離幅度最大的三項 ─────────────────────────────────────────────
    _section_header(c, L, y, "偏離幅度最大的三項")
    card_gap, card_w = 4, (W - 2 * 4) / 3
    card_h = 15
    for i, item in enumerate(vm["top3"]):
        cx = L + i * (card_w + card_gap)
        c.setFillColor(C_CARDBG)
        c.roundRect(cx * mm, y.canvas_y(card_h) , card_w * mm, card_h * mm, 0.7 * mm, fill=1, stroke=0)
        c.setFillColor(C_ORANGE); c.rect(cx * mm, y.canvas_y(card_h), 1.2 * mm, card_h * mm, fill=1, stroke=0)
        ix = cx + 2
        _text(c, ix, y, item["name"], "MSJH-Bold", 8.8, C_HEAD)
        c.setFont("MSJH-Bold", 13); c.setFillColor(C_HEAD)
        c.drawString(ix * mm, y.canvas_y(5.6), item["value"])
        vw = c.stringWidth(item["value"], "MSJH-Bold", 13) / mm
        c.setFont("MSJH", 7.5); c.setFillColor(C_SUB)
        c.drawString((ix + vw + 1.2) * mm, y.canvas_y(5.2), item["unit"])
        c.setFont("MSJH", 7); c.setFillColor(C_SUB)
        c.drawString(ix * mm, y.canvas_y(8.6), item["ref_line"])
        c.setFont("MSJH-Bold", 7); c.setFillColor(C_ORANGE)
        c.drawString(ix * mm, y.canvas_y(11.4), item["delta_line"])
    y.advance(card_h + 3)

    # ── 4. 本月紀錄佐證：血壓/血氧分布 ────────────────────────────────────
    _section_header(c, L, y, "本月紀錄佐證", "數值可於下段查核區逐項核對")
    col_w = (W - 8) / 2
    for col, dist in enumerate((vm["bp_distribution"], vm["spo2_distribution"])):
        cx = L + col * (col_w + 8)
        title = "收縮壓分布" if col == 0 else "血氧分布"
        c.setFont("MSJH-Bold", 8.6); c.setFillColor(C_HEAD)
        c.drawString(cx * mm, y.canvas_y(0), title)
        tw = c.stringWidth(title, "MSJH-Bold", 8.6) / mm
        c.setFont("MSJH", 7.6); c.setFillColor(C_SUB)
        c.drawString((cx + tw + 2) * mm, y.canvas_y(0), dist["count_label"])

        bar_y = y.y + 3
        bx = cx
        for pct, hexcol in dist["segments"]:
            seg_w = col_w * pct / 100.0
            c.setFillColor(_hx(hexcol))
            c.rect(bx * mm, (PAGE_H / mm - bar_y - 4) * mm, seg_w * mm, 4 * mm, fill=1, stroke=0)
            bx += seg_w
        for pct, label in dist["ticks"]:
            tx = cx + col_w * pct / 100.0
            c.setFont("MSJH", 5.6); c.setFillColor(C_SUB)
            c.drawCentredString(tx * mm, (PAGE_H / mm - bar_y - 8) * mm, label)

        chip_y = bar_y + 9.5
        chip_w = (col_w - 2 * 1.4) / 3
        for ci, (label, val, sub) in enumerate(dist["chips"]):
            chx = cx + ci * (chip_w + 1.4)
            warn = "≥180" in label or "<94" in label or "<90" in label
            bg = C_CARDBG_WARN if warn else C_CARDBG
            c.setFillColor(bg)
            c.roundRect(chx * mm, (PAGE_H / mm - chip_y - 5.5) * mm, chip_w * mm, 5.5 * mm, 0.4 * mm, fill=1, stroke=0)
            c.setFont("MSJH", 6.4); c.setFillColor(C_ORANGE if warn else C_SUB)
            c.drawString((chx + 0.5) * mm, (PAGE_H / mm - chip_y - 2) * mm, label)
            c.setFont("MSJH-Bold", 7.8); c.setFillColor(C_ORANGE if warn else C_HEAD)
            c.drawString((chx + 0.5) * mm, (PAGE_H / mm - chip_y - 5) * mm, str(val))
            if sub:
                sw = c.stringWidth(str(val), "MSJH-Bold", 7.8) / mm
                c.setFont("MSJH", 5.8); c.setFillColor(C_SUB if not warn else C_ORANGE)
                c.drawString((chx + 0.5 + sw + 1) * mm, (PAGE_H / mm - chip_y - 5) * mm, str(sub))
    y.advance(3 + 4 + 5.5 + 5.5 + 4)

    # ── 5. 四個面向 ──────────────────────────────────────────────────────
    _section_header(c, L, y, "四個面向", "箭頭一律 前三月 → 本月；衰弱卡為長期趨勢")
    fa_gap, fa_w = 3.2, (W - 3 * 3.2) / 4
    # 卡片內容從頂端到「趨勢註記」第一行的固定距離（等於下面堆疊的 iy.advance 總和：
    # 2.2+3.2+4.4+4.6+5.2+6.4+1.6+2.2），卡高需隨最長的 trend_note 動態調整，
    # 不然文字會溢出卡片邊框、跟下一節標題疊在一起（原本固定34mm在文字較長時會發生）。
    FA_CONTENT_TOP, FA_LINE_H, FA_BOTTOM_PAD = 29.8, 2.8, 2.5
    line_counts = [len(_wrap_lines(c, card["trend_note"], (fa_w - 2.2 * 2) * mm, "MSJH", 6.8))
                   for card in vm["four_aspects"]]
    fa_h = FA_CONTENT_TOP + max(line_counts) * FA_LINE_H + FA_BOTTOM_PAD
    for i, card in enumerate(vm["four_aspects"]):
        cx = L + i * (fa_w + fa_gap)
        c.setStrokeColor(_hx(card["accent"])); c.setLineWidth(2.4 * 0.35)
        c.line(cx * mm, y.canvas_y(), (cx + fa_w) * mm, y.canvas_y())
        c.setStrokeColor(C_BORDER); c.setLineWidth(0.3)
        c.rect(cx * mm, y.canvas_y(fa_h), fa_w * mm, fa_h * mm, fill=0, stroke=1)
        iy = _Cursor(y.y + 2.2)
        ix = cx + 2.2
        _text(c, ix, iy, card["title"], "MSJH-Bold", 8.4, C_TEAL)
        iy.advance(3.2)
        _text(c, ix, iy, card["period_label"], "MSJH", 6.4,
              _hx(card["accent"]) if card["accent"] == "#D85A30" else C_MUTE)
        iy.advance(4.4)
        _text(c, ix, iy, card["primary_label"], "MSJH", 7.6, C_SUB)
        iy.advance(4.6)
        c.setFont("MSJH-Bold", 11.5); c.setFillColor(C_MUTE)
        c.drawString(ix * mm, iy.canvas_y(), f"{card['primary_before']} → ")
        bw = c.stringWidth(f"{card['primary_before']} → ", "MSJH-Bold", 11.5) / mm
        c.setFillColor(C_ORANGE)
        c.drawString((ix + bw) * mm, iy.canvas_y(), str(card["primary_after"]))
        iy.advance(5.2)
        chip_w2 = (fa_w - 2.2 * 2 - 1.6) / 2
        for ci, (clabel, cbefore, cafter) in enumerate(card["chips"]):
            chx = ix + ci * (chip_w2 + 1.6)
            _chip(c, chx, iy.y, chip_w2, 4.6, clabel, cbefore, cafter)
        iy.advance(6.4)
        c.setStrokeColor(C_BORDER); c.setDash(1, 1); c.setLineWidth(0.3)
        c.line(ix * mm, iy.canvas_y(), (cx + fa_w - 2.2) * mm, iy.canvas_y())
        c.setDash()
        iy.advance(1.6)
        _text(c, ix, iy, "趨勢註記", "MSJH", 6.2, C_MUTE)
        iy.advance(2.2)
        _wrap_block(c, ix, iy, card["trend_note"], fa_w - 2.2 * 2, "MSJH", 6.8, C_SUB, 2.8)
    y.advance(fa_h + 4)

    # ── 6. 每日狀態與事件 ─────────────────────────────────────────────────
    _section_header(c, L, y, "每日狀態與事件", "綠＝正常，灰＝異常，橘＝異常且落在偏離集中期")
    ds = vm["daily_status"]
    label_w, note_w = 14, 23
    grid_w = W - label_w - note_w - 2 * 1.8
    grid_x = L + label_w + 1.8
    cell_w = grid_w / ds["days"]

    # 集中期高亮條
    c.setFillColor(C_ORANGE)
    hl_x = grid_x + ds["cluster_start_i"] * cell_w
    hl_w = (ds["cluster_end_i"] - ds["cluster_start_i"] + 1) * cell_w
    c.roundRect(hl_x * mm, y.canvas_y(1.6), hl_w * mm, 1.6 * mm, 0.8 * mm, fill=1, stroke=0)
    c.setFont("MSJH-Bold", 7.2); c.setFillColor(C_ORANGE)
    c.drawString((L + label_w + grid_w + 1.8 * 2) * mm, y.canvas_y(1.4), ds["cluster_label"])
    y.advance(4.2)

    for row in ds["rows"]:
        c.setFont("MSJH-Bold", 8.6); c.setFillColor(C_HEAD)
        c.drawString(L * mm, y.canvas_y(2.2), row["label"])
        for i, hexcol in enumerate(row["colors"]):
            cx = grid_x + i * cell_w
            c.setFillColor(_hx(hexcol))
            c.roundRect(cx * mm, y.canvas_y(3), (cell_w - 0.55) * mm, 3 * mm, 0.4 * mm, fill=1, stroke=0)
        c.setFont("MSJH", 8); c.setFillColor(C_SUB)
        c.drawString((L + label_w + grid_w + 1.8 * 2) * mm, y.canvas_y(2.2), row["note"])
        y.advance(3.9)

    # 偶發事件列
    c.setStrokeColor(C_BORDER); c.setDash(1, 1); c.setLineWidth(0.3)
    c.line(L * mm, y.canvas_y(0), (L + W) * mm, y.canvas_y(0))
    c.setDash()
    y.advance(2)
    ev = ds["events_row"]
    c.setFont("MSJH-Bold", 8.6); c.setFillColor(C_SUB)
    c.drawString(L * mm, y.canvas_y(2.2), "偶發事件")
    from .mock_data_v9 import EVENT_ICONS
    for i, kinds in enumerate(ev["icons_by_day"]):
        if not kinds:
            continue
        cx = grid_x + i * cell_w + cell_w / 2
        c.setFont("MSJH", 5.6)
        gx = cx - (len(kinds) - 1) * 1.6 / 2
        for k in kinds:
            glyph, hexcol = EVENT_ICONS[k]
            c.setFillColor(_hx(hexcol))
            c.drawCentredString(gx * mm, y.canvas_y(2.2), glyph)
            gx += 1.8
    lines = _wrap_lines(c, ev["legend"], note_w * mm, "MSJH", 7)
    c.setFont("MSJH", 7); c.setFillColor(C_SUB)
    ly = y.canvas_y(2.2)
    for ln in lines:
        c.drawString((L + label_w + grid_w + 1.8 * 2) * mm, ly, ln)
        ly -= 3.2 * mm
    y.advance(4.6)

    # 日期刻度
    c.setFont("MSJH", 7); c.setFillColor(C_MUTE)
    for i, label in ds["date_ticks"].items():
        c.drawString((grid_x + i * cell_w) * mm, y.canvas_y(2), label)
    y.advance(4.5)

    # ── 7. 查核與說明 ─────────────────────────────────────────────────────
    _section_header(c, L, y, "查核與說明", "供第一次檢視或需要時查對，門診中可略過", dot_color=C_MUTE)
    cn = vm["check_notes"]
    col3_gap, col3_w = 5, (W - 2 * 5) / 3
    y_top = y.y

    # 其餘偏離3項
    iy = _Cursor(y_top)
    _text(c, L, iy, "其餘偏離 3 項", "MSJH-Bold", 7.6, C_SUB)
    iy.advance(3)
    for label, val in cn["remaining_deviations"]:
        c.setFillColor(C_CARDBG)
        box_h = 4.6
        c.roundRect(L * mm, iy.canvas_y(box_h), col3_w * mm, box_h * mm, 0.4 * mm, fill=1, stroke=0)
        c.setFont("MSJH-Bold", 7.2); c.setFillColor(C_HEAD)
        c.drawString((L + 0.6) * mm, iy.canvas_y(3), label)
        lw = c.stringWidth(label, "MSJH-Bold", 7.2) / mm
        c.setFont("MSJH", 7.2); c.setFillColor(C_SUB)
        c.drawString((L + 0.6 + lw + 1.4) * mm, iy.canvas_y(3), val)
        iy.advance(box_h + 1.2)
    col1_end = iy.y

    # 紀錄完整度
    cx2 = L + col3_w + col3_gap
    iy = _Cursor(y_top)
    _text(c, cx2, iy, "紀錄完整度", "MSJH-Bold", 7.6, C_SUB)
    iy.advance(3)
    chip_w3 = (col3_w - 1.6) / 2
    for i, (label, val, dim) in enumerate(cn["completeness"]):
        row_i, col_i = divmod(i, 2)
        chx = cx2 + col_i * (chip_w3 + 1.6)
        chy = iy.y + row_i * (5.6)
        c.setFillColor(C_CARDBG)
        c.roundRect(chx * mm, (PAGE_H / mm - chy - 5) * mm, chip_w3 * mm, 5 * mm, 0.4 * mm, fill=1, stroke=0)
        c.setFont("MSJH", 6.4); c.setFillColor(C_MUTE)
        c.drawString((chx + 0.5) * mm, (PAGE_H / mm - chy - 2) * mm, label)
        c.setFont("MSJH-Bold", 7); c.setFillColor(C_MUTE if dim else C_HEAD)
        c.drawString((chx + 0.5) * mm, (PAGE_H / mm - chy - 4.6) * mm, val)
    row_i_max = (len(cn["completeness"]) - 1) // 2 if cn["completeness"] else 0
    col2_end = y_top + 3 + row_i_max * 5.6 + 5

    # 比較基準與待設定
    cx3 = cx2 + col3_w + col3_gap
    iy = _Cursor(y_top)
    _text(c, cx3, iy, "比較基準與待設定", "MSJH-Bold", 7.6, C_SUB)
    iy.advance(3)
    for label, val in cn["baseline_todo"]:
        box_h = 4.6
        c.setFillColor(C_CARDBG)
        c.roundRect(cx3 * mm, iy.canvas_y(box_h), col3_w * mm, box_h * mm, 0.4 * mm, fill=1, stroke=0)
        c.setFont("MSJH-Bold", 7.2); c.setFillColor(C_HEAD)
        c.drawString((cx3 + 0.6) * mm, iy.canvas_y(3), label)
        lw = c.stringWidth(label, "MSJH-Bold", 7.2) / mm
        c.setFont("MSJH", 7.2); c.setFillColor(C_SUB)
        c.drawString((cx3 + 0.6 + lw + 1.4) * mm, iy.canvas_y(3), val)
        iy.advance(box_h + 1.2)
    box_h = 4.6
    c.setFillColor(C_CARDBG_TODO)
    c.roundRect(cx3 * mm, iy.canvas_y(box_h), col3_w * mm, box_h * mm, 0.4 * mm, fill=1, stroke=0)
    c.setFont("MSJH", 7.2); c.setFillColor(C_SUB)
    c.drawString((cx3 + 0.6) * mm, iy.canvas_y(3), cn["reference_pending"])
    iy.advance(box_h + 1.2)
    _wrap_block(c, cx3, iy, cn["todo_flag"], col3_w, "MSJH-Bold", 7.2, C_ORANGE, 3.2)
    col3_end = iy.y

    # ── 8. 頁尾 ──────────────────────────────────────────────────────────
    # 緊接在查核與說明內容後面，不強制貼齊頁面底部——內容較短時（例如佔位欄位
    # 多、真實資料少）硬貼底部會在中段留下一大塊空白，緊接內容比較自然。
    # 三欄各自獨立算高度，頁尾要接在「最高那一欄」底下，不能只看某一欄，
    # 不然頁尾線會畫在還沒印完的欄位中間。
    y.y = max(col1_end, col2_end, col3_end)
    y.advance(10)
    c.setStrokeColor(C_TEAL); c.setLineWidth(0.35)
    c.line(L * mm, y.canvas_y(), (L + W) * mm, y.canvas_y())
    y.advance(2.4)
    fo = vm["footer"]
    _wrap_block(c, L, y, fo["disclaimer"], W, "MSJH", 6.3, C_SUB, 2.6)
    _wrap_block(c, L, y, f"報告產生 {fo['generated_date']}　｜　{fo['source_note']}", W, "MSJH", 6.3, C_SUB, 2.6)

    c.save()
    print(f"[report_pdf_v9] v9 版面已儲存：{output_path}")
