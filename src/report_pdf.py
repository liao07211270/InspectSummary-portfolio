"""
Single-page A4 care summary — v2 layout.

    HEADER         : resident line + data-completeness line
    第一部分 (左)  : 全數據觀察 — 3 neutral data groups
        1 血壓          達標分布長條 + 晨間收縮壓趨勢折線（目標140/個人基線）
        2 血氧・進食・飲水   達標長條（達標/未達標天數）
        3 心率・體重・體溫   方向 / 長期 / 偏離日期
    第二部分 (右)  : 疾病訊號 — 3 disease blocks (metric rows + 摘要 chip)
        BLOCK 1 高血壓與心血管
        BLOCK 2 體重・體液與營養（原體液代謝 + 肌少衰弱合併，2026-07 真實資料
                 改版：拿掉睡眠/活動/社交/尿色/握力等真實資料庫無來源的子項）
        BLOCK 3 感染監測
    A 核心走勢 / B 關聯因子 : full-width objective summary
    頁尾  : 標註目前系統無法採集的欄位，避免讀者誤以為「這個月沒發生」

Font: Microsoft JhengHei (Windows built-in Traditional Chinese)
"""
import re
from datetime import date
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas

from .analysis_core import NO_DATA

FONT_PATH_REG  = r"C:\Windows\Fonts\msjh.ttc"
FONT_PATH_BOLD = r"C:\Windows\Fonts\msjhbd.ttc"


# ── NO_DATA-safe formatters ──────────────────────────────────────────────────
# report_pdf.py 目前只給隨行本版本 mock demo 用；真實資料（真實照護 DB）常見月量體重
# 只有 1 筆、上月/6個月前錨點缺失等情況，analysis_core.py 會回傳 NO_DATA 字串常數。
# 這裡只做「不要崩潰、顯示合理占位文字」的最小防呆，不改動版面配置。
def _i(v, no_data="—"):
    return no_data if v == NO_DATA else str(int(v))


def _iv(v, default=0):
    """數值型 fallback，給畫圖函式需要真的數字的地方用（例如長條圖/折線圖）。"""
    return default if v == NO_DATA else int(v)


def _pct(v, no_data="—"):
    return no_data if v == NO_DATA else f"{v:g}%"


def _cv_dir(cv_pct, cv_baseline):
    """CV 相對個人/族群基線的方向註記，資料不足時不加註。"""
    if cv_pct == NO_DATA or cv_baseline == NO_DATA:
        return ""
    if cv_pct > cv_baseline:
        return " ↑高於基線"
    if cv_pct < cv_baseline:
        return " ↓低於基線"
    return ""


def _register_fonts():
    if "MSJH" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("MSJH",      FONT_PATH_REG,  subfontIndex=0))
        pdfmetrics.registerFont(TTFont("MSJH-Bold", FONT_PATH_BOLD, subfontIndex=0))


# ── Palette (AGP visual language, per guide §7.2 / §7.5) ────────────────────
C_TEAL    = colors.HexColor("#0F5F54")   # 標題主色
C_HEAD    = colors.HexColor("#16302D")   # 主色（深）
C_TEAL_LT = colors.HexColor("#E8F1F1")
C_GREEN   = colors.HexColor("#639922")   # 目標／達標
C_AMBER   = colors.HexColor("#EF9F27")   # 偏高／接近門檻
C_ORANGE  = colors.HexColor("#D85A30")   # 危象／危急
C_RED     = colors.HexColor("#A32D2D")   # 偏低（血壓往下掉）
C_GRAY    = colors.HexColor("#C2BFB6")   # 未達標（是非旗標非達標段）
C_BLUE    = colors.HexColor("#1F6FB2")   # 折線資料線
C_TEXT    = colors.HexColor("#2C2C2C")
C_SUB     = colors.HexColor("#5C6B68")   # 次要灰
C_BORDER  = colors.HexColor("#D8D0C4")
C_CARD    = colors.HexColor("#FBFAF8")
C_CHIP    = colors.HexColor("#EAF1EC")   # 解讀行淺底
C_CRISIS_BG = colors.HexColor("#FDF0EE")  # 危象條框淺底

PAGE_W, PAGE_H = A4
MARGIN  = 22
COL_GAP = 16
LEFT_X  = MARGIN
COL_W   = (PAGE_W - 2 * MARGIN - COL_GAP) / 2     # ≈ 257 pt
RIGHT_X = LEFT_X + COL_W + COL_GAP


# ── Low-level helpers ───────────────────────────────────────────────────────
def _wrap_lines(c, text, width, font="MSJH", size=8):
    toks = re.findall(r"[a-zA-Z0-9/>=<.%+\-:()~°]+|[^\x00-\x7F]|.", text)
    lines, line = [], ""
    for t in toks:
        if c.stringWidth(line + t, font, size) > width:
            lines.append(line.rstrip()); line = t
        else:
            line += t
    if line.strip():
        lines.append(line)
    return lines


def _wrap(c, x, y, text, width, font="MSJH", size=8, colour=C_TEXT, lh=12):
    c.setFont(font, size); c.setFillColor(colour)
    for ln in _wrap_lines(c, text, width, font, size):
        c.drawString(x, y, ln); y -= lh
    return y


def _group_title(c, x, y, title, note=None, w=COL_W, color=C_TEAL, underline=False):
    """Bold group title; optional right-aligned note and (off by default) underline."""
    c.setFont("MSJH-Bold", 11); c.setFillColor(color)
    c.drawString(x, y, title)
    if note:
        c.setFont("MSJH", 8); c.setFillColor(C_SUB)
        c.drawRightString(x + w, y + 1, note)
    if underline:
        c.setStrokeColor(C_TEAL); c.setLineWidth(0.7)
        c.line(x, y - 4, x + w, y - 4)
        return y - 17
    return y - 15


def _stacked_bar(c, x, y, w, segs, h=9):
    """segs = [(pct, colour), ...] — horizontal stacked proportion bar."""
    cx = x
    for pct, col in segs:
        seg_w = w * pct / 100.0
        c.setFillColor(col)
        c.rect(cx, y - h, seg_w, h, fill=1, stroke=0)
        cx += seg_w
    c.setStrokeColor(C_BORDER); c.setLineWidth(0.4)
    c.rect(x, y - h, w, h, fill=0, stroke=1)
    return y - h


def _progress(c, x, y, w, label, ok_days, total, count_txt, h=9):
    """One row: label (left) + green/gray TIR bar (middle) + count text (right)。
    ok_days 資料不足時是 NO_DATA 字串（例如整段觀察期完全沒有血氧讀數），
    不能直接拿去乘除——顯性退場成空bar（全灰），不能讓字串滲進數學運算。"""
    label_w, bar_w = 34, 112
    c.setFont("MSJH-Bold", 9); c.setFillColor(C_TEXT)
    c.drawString(x, y - h + 1, label)
    bx = x + label_w
    ok_w = bar_w * ok_days / total if total and ok_days != NO_DATA else 0
    c.setFillColor(C_GREEN); c.rect(bx, y - h, ok_w, h, fill=1, stroke=0)
    c.setFillColor(C_GRAY);  c.rect(bx + ok_w, y - h, bar_w - ok_w, h, fill=1, stroke=0)
    c.setStrokeColor(C_BORDER); c.setLineWidth(0.4); c.rect(bx, y - h, bar_w, h, fill=0, stroke=1)
    c.setFont("MSJH", 8.5); c.setFillColor(C_SUB)
    c.drawString(bx + bar_w + 8, y - h + 1, count_txt)
    return y - h - 10


def _kv_row(c, x, y, label, value, w=COL_W, alert=False):
    """Label (left) + value (right), with a faint divider above."""
    c.setStrokeColor(colors.HexColor("#ECE7DD")); c.setLineWidth(0.4)
    c.line(x, y + 9, x + w, y + 9)
    c.setFont("MSJH-Bold", 7.5); c.setFillColor(C_TEXT)
    c.drawString(x, y, label)
    c.setFont("MSJH", 7.5); c.setFillColor(C_RED if alert else C_TEXT)
    c.drawRightString(x + w, y, value)
    return y - 13


def _inline_flow(c, x, y, w, segs, gap=20, lh=19, size=9.5):
    """Pack 'bold-label value' segments left-to-right, wrapping to new lines.

    segs = [(label, value), ...]. Matches the guideline's compact inline style.
    """
    cx = x
    for label, value in segs:
        lw = c.stringWidth(label, "MSJH-Bold", size)
        vw = c.stringWidth(value, "MSJH", size)
        seg_w = lw + 4 + vw
        if cx > x and cx + seg_w > x + w:          # wrap to next line
            cx = x; y -= lh
        c.setFont("MSJH-Bold", size); c.setFillColor(C_TEXT)
        c.drawString(cx, y, label)
        c.setFont("MSJH", size); c.setFillColor(C_TEXT)
        c.drawString(cx + lw + 4, y, value)
        cx += seg_w + gap
    return y - lh


# ── Trend line chart (晨間收縮壓) ───────────────────────────────────────────
def _trend_chart(c, x, y, w, h, weekly, target, baseline, title, drawable=True):
    c.setFont("MSJH", 7); c.setFillColor(C_TEXT)
    c.drawString(x, y, title)
    # Gray-state retreat (原則六 / §7.3): too few days → don't draw the line
    if not drawable or not weekly or len(weekly) < 2:
        ay = y - h
        c.setFillColor(colors.HexColor("#F4F2EE")); c.setStrokeColor(C_BORDER); c.setLineWidth(0.4)
        c.roundRect(x, ay, w, h - 8, 3, fill=1, stroke=1)
        c.setFont("MSJH", 7); c.setFillColor(C_SUB)
        c.drawCentredString(x + w / 2, ay + (h - 8) / 2 - 3, "晨間量測天數不足，折線資料不足（僅供參考）")
        return ay - 4
    ax, ay = x + 20, y - h
    aw, ah = w - 24, h - 12
    lo = min(min(weekly), target, baseline) - 6
    hi = max(max(weekly), target, baseline) + 6
    rng = hi - lo or 1

    def py(v): return ay + (v - lo) / rng * ah

    c.setStrokeColor(C_BORDER); c.setLineWidth(0.4); c.rect(ax, ay, aw, ah, fill=0, stroke=1)
    # y ticks
    for v in (int(lo + 2), int((lo + hi) / 2), int(hi - 2)):
        yy = py(v)
        c.setFont("MSJH", 5.5); c.setFillColor(C_SUB)
        c.drawRightString(ax - 2, yy - 2, str(v))

    # target (green dashed) + baseline (gray dashed); labels at line ends
    c.setDash(2, 2)
    c.setStrokeColor(C_GREEN); c.setLineWidth(0.8); c.line(ax, py(target), ax + aw, py(target))
    c.setFont("MSJH", 5.5); c.setFillColor(C_GREEN); c.drawString(ax + 2, py(target) + 2, f"目標 {target}")
    c.setStrokeColor(C_SUB); c.setLineWidth(0.7); c.line(ax, py(baseline), ax + aw, py(baseline))
    c.setFillColor(C_SUB); c.drawString(ax + 2, py(baseline) - 6, f"基線 {int(baseline)}")
    c.setDash()

    pts = [(ax + i / (len(weekly) - 1) * aw, py(v)) for i, v in enumerate(weekly)]
    path = c.beginPath(); path.moveTo(*pts[0])
    for p in pts[1:]:
        path.lineTo(*p)
    c.setStrokeColor(C_BLUE); c.setLineWidth(1.6); c.drawPath(path)
    for i, (px, pp) in enumerate(pts):
        c.setFillColor(C_BLUE); c.circle(px, pp, 2.2, fill=1, stroke=0)
        c.setFont("MSJH-Bold", 5.5); c.setFillColor(C_TEXT)
        c.drawCentredString(px, pp + 4, str(int(weekly[i])))
        c.setFont("MSJH", 5); c.setFillColor(C_SUB)
        c.drawCentredString(px, ay - 8, f"W{i+1}")
    return ay - 12


# ── Right-column disease block ──────────────────────────────────────────────
def _bullet(c, x, y, text, w, alert=False):
    """• marker + wrapped inline text (label and value combined).

    Uniform muted dot like the sample (orange is reserved for 危象/危急, §7.2).
    """
    c.setFillColor(C_SUB)
    c.circle(x + 2.2, y + 2.8, 1.7, fill=1, stroke=0)
    lines = _wrap_lines(c, text, w - 13, "MSJH", 9)
    c.setFont("MSJH", 9); c.setFillColor(C_TEXT)
    ty = y
    for ln in lines:
        c.drawString(x + 11, ty, ln); ty -= 15
    return ty - 4


def _block(c, x, y, w, title, rows, chip, gap=30):
    """Black bold heading (no underline) + • bullet rows + 解讀 chip (per sample)."""
    y = _group_title(c, x, y, title, w=w, color=C_HEAD, underline=False)

    for text, alert in rows:
        y = _bullet(c, x, y, text, w, alert)

    # summary chip (解讀行) — light tinted box; text + › chevron vertically centred
    chip_lines = _wrap_lines(c, chip, w - 20, "MSJH", 8.5)
    n, line_h = len(chip_lines), 11.5
    chip_box = n * line_h + 10
    cy = y + 1
    c.setFillColor(C_CHIP); c.roundRect(x, cy - chip_box, w, chip_box, 2, fill=1, stroke=0)
    box_mid = cy - chip_box / 2
    ty = box_mid - 2.6 + (n - 1) * line_h / 2     # first baseline → text block centred
    # › chevron aligned to the first text line
    chx, chy = x + 8, ty + 2.8
    c.setStrokeColor(C_TEAL); c.setLineWidth(1.2); c.setLineCap(1)
    c.line(chx, chy + 2.6, chx + 3, chy)
    c.line(chx + 3, chy, chx, chy - 2.6)
    c.setFont("MSJH", 8.5); c.setFillColor(C_TEAL)
    for ln in chip_lines:
        c.drawString(x + 16, ty, ln); ty -= line_h
    return cy - chip_box - gap


def _crisis_box(c, x, y, w, count, total, pct, detail, gap=10):
    """危象事件獨立條框：橘色左框線 + 淺色底，只在有危象事件時呼叫。"""
    dates = "、".join(f"{d['date']} {d['sys']}" for d in detail)
    lines = _wrap_lines(c, dates, w - 20, "MSJH", 8.5) if dates else ["—"]
    n, line_h = len(lines), 11.5
    box_h = line_h * (n + 1) + 12
    c.setFillColor(C_CRISIS_BG); c.rect(x, y - box_h, w, box_h, fill=1, stroke=0)
    c.setFillColor(C_ORANGE); c.rect(x, y - box_h, 3, box_h, fill=1, stroke=0)
    ty = y - 12
    c.setFont("MSJH-Bold", 9); c.setFillColor(C_ORANGE)
    c.drawString(x + 10, ty, f"⚠ 危象事件（收縮壓 >=180）　{count}次/{total}次（{pct}%）")
    ty -= line_h
    c.setFont("MSJH", 8.5); c.setFillColor(C_TEXT)
    for ln in lines:
        c.drawString(x + 10, ty, ln); ty -= line_h
    return y - box_h - gap


_NUM_TOKEN = re.compile(r"^[+\-]?\d")


def _wrap_hl(c, x, y, text, width, font="MSJH", bold_font="MSJH-Bold", size=9.5,
             colour=C_TEXT, lh=15):
    """像 _wrap()，但把數字開頭的 token 自動加粗＋底線（A/B 段標記關鍵數字用）。
    文字是 LLM/規則式動態生成，無法人工標記，改用「數字開頭視為關鍵數字」的
    規則自動判斷，效果上達到「關鍵數字醒目化」，但不做語意篩選。"""
    toks = re.findall(r"[a-zA-Z0-9/>=<.%+\-:()~°]+|[^\x00-\x7F]|.", text)
    lines, line = [], []
    line_w = 0.0
    for t in toks:
        is_num = bool(_NUM_TOKEN.match(t))
        tw = c.stringWidth(t, bold_font if is_num else font, size)
        if line_w + tw > width and line:
            lines.append(line); line, line_w = [], 0.0
        line.append((t, is_num)); line_w += tw
    if line:
        lines.append(line)

    for ln in lines:
        cx = x
        for t, is_num in ln:
            f = bold_font if is_num else font
            c.setFont(f, size); c.setFillColor(colour)
            c.drawString(cx, y, t)
            tw = c.stringWidth(t, f, size)
            if is_num and t.strip():
                c.setStrokeColor(colour); c.setLineWidth(0.7)
                c.line(cx, y - 2, cx + tw, y - 2)
            cx += tw
        y -= lh
    return y


# ── Main render ─────────────────────────────────────────────────────────────
def generate_report(patient, period, analysis, summary, output_path):
    _register_fonts()
    c = canvas.Canvas(output_path, pagesize=A4)
    H = analysis["header"]
    obs = analysis["obs"]
    sig = analysis["signals"]

    # ── HEADER ──────────────────────────────────────────────────────────
    yt = PAGE_H - MARGIN
    c.setFont("MSJH-Bold", 12); c.setFillColor(C_TEAL)
    diag = " · ".join(patient.get("diagnoses", []))
    c.drawString(MARGIN, yt - 4, f"{patient['name']}　{patient.get('gender','')} {patient.get('age','')} · {diag}")
    c.setFont("MSJH", 8); c.setFillColor(C_SUB)
    c.drawRightString(PAGE_W - MARGIN, yt - 4, f"觀察期 {period[0]} — {period[1]} · 基準線已建立")
    # 姓名資訊下方的實心黑線（§7.1 頂部框線）
    c.setStrokeColor(C_HEAD); c.setLineWidth(1.3); c.line(MARGIN, yt - 11, PAGE_W - MARGIN, yt - 11)

    c.setFont("MSJH", 8.5); c.setFillColor(C_TEXT)
    comp = (f"資料完整度　血壓 {H['bp_days']}天/{H['bp_count']}次 · 體溫 {H['temp_days']}天 · "
            f"心率 {H['hr_days']}天 · 體重 {H['weight_note']} · 生活照護 {H['care_days']}天")
    c.drawString(MARGIN, yt - 23, comp)

    # ── Section headers ─────────────────────────────────────────────────
    sy = yt - 48
    c.setFont("MSJH-Bold", 12); c.setFillColor(C_TEAL)
    c.drawString(LEFT_X, sy, "第一部分・全數據觀察")
    c.drawString(RIGHT_X, sy, "第二部分・疾病訊號")

    # ════════════════ LEFT COLUMN ════════════════
    y = sy - 20
    bp = obs["bp"]

    # G1 血壓
    y = _group_title(c, LEFT_X, y, "血壓・收縮壓達標分布",
                     f"{bp['total_count']}次・多在上午", color=C_HEAD)
    crisis_pct = max(round(bp["crisis_count"] / bp["total_count"] * 100), 2) if bp["crisis_count"] else 0
    ok_pct = bp["ok_pct"]
    high_pct = max(0, 100 - ok_pct - crisis_pct) if ok_pct != NO_DATA else NO_DATA
    y = _stacked_bar(c, LEFT_X, y, COL_W,
                     [(_iv(ok_pct), C_GREEN), (_iv(high_pct), C_AMBER), (crisis_pct, C_ORANGE)])
    limited = "　（樣本有限）" if bp.get("sample_limited") else ""
    legend = f"達標 90–140 {_pct(ok_pct)}　偏高 {_pct(high_pct)}　危象 >=180 {_pct(crisis_pct if bp['crisis_count'] else 0)}{limited}"
    y = _wrap(c, LEFT_X, y - 11, legend, COL_W, "MSJH", 8, C_SUB, 11) - 6
    c.setFont("MSJH-Bold", 9.5); c.setFillColor(C_TEXT)
    c.drawString(LEFT_X, y, f"晨 {_i(bp['am_mean'])} · 午 {_i(bp['pm_mean'])} · 晚 {_i(bp['eve_mean'])}")
    c.setFont("MSJH", 9.5); c.setFillColor(C_TEXT)
    c.drawRightString(LEFT_X + COL_W, y, f"CV {bp['cv_pct']}%（基線{bp['cv_baseline']}%{_cv_dir(bp['cv_pct'], bp['cv_baseline'])}）")
    y -= 18
    y = _trend_chart(c, LEFT_X, y, COL_W, 165, bp["weekly"], bp["target"], _iv(bp["baseline"]),
                     "晨間收縮壓趨勢（mmHg）", drawable=bp.get("trend_drawable", True))
    y -= 20
    # 危象事件的獨立條框只在右欄「疾病訊號」放一次（臨床警示屬性），
    # 左欄「全數據觀察」定位是中性資料呈現，不重複顯示同一件事。

    # G2 血氧・進食・飲水（無分組標題，行內標籤自帶）
    sfw = obs["spo2_food_water"]
    y = _progress(c, LEFT_X, y, COL_W, "血氧", sfw["spo2_ok_days"], sfw["total"],
                  f"<94% {_i(sfw['spo2_low_days'])}天/{sfw['total']}")
    y = _progress(c, LEFT_X, y, COL_W, "進食", sfw["food_ok_days"], sfw["total"],
                  f"未達標 {_i(sfw['food_short_days'])}天/{sfw['total']}·連續{_i(sfw['food_streak'])}天")
    y = _progress(c, LEFT_X, y, COL_W, "飲水", sfw["water_ok_days"], sfw["total"],
                  f"未達標 {_i(sfw['water_short_days'])}天/{sfw['total']}·連續{_i(sfw['water_streak'])}天")
    y -= 18

    # G3 心率・體重・體溫（無分組標題）
    hwt = obs["hr_weight_temp"]
    y -= 16
    arrow = "↑" if hwt["hr_dir"] == "上升" else "↓" if hwt["hr_dir"] == "下降" else "→"
    temp_evs = sorted(hwt["temp_low"] + hwt["temp_high"], key=lambda e: e["date"])
    temp_str = "、".join(f"{e['t']}({e['date']})" for e in temp_evs) or "—"
    y = _inline_flow(c, LEFT_X, y, COL_W, [
        ("心率", f"{_i(hwt['hr_start'])}→{_i(hwt['hr_end'])} {arrow}"),
        ("體重", f"{_i(hwt['weight_6mo'])}→{_i(hwt['weight_now'])}kg（{_pct(hwt['weight_pct'])}）"),
        ("體溫", f"基線{hwt['temp_baseline']}｜{temp_str}"),
    ])
    # G4「睡眠・活動・社交」已移除——真實資料完全無來源（見頁尾註記），
    # 不是「這個月沒有資料」而是系統目前沒有對應的登打欄位。

    # ════════════════ RIGHT COLUMN ════════════════
    ry = sy - 20
    b1, b2, b3, b4 = sig["block1"], sig["block2"], sig["block3"], sig["block4"]

    # BLOCK 1 高血壓與心血管（危象事件另外用獨立條框呈現，見下方）
    ry = _block(c, RIGHT_X, ry, COL_W, "高血壓與心血管", [
        (f"達標率（族群）目標<140/90　{b1['reach_days']}天/{b1['total']}（{b1['reach_pct']}%）",
         b1["reach_pct"] != NO_DATA and b1["reach_pct"] < 70),
        (f"晨間收縮壓 月均{_i(b1['am_mean'])}；{_i(b1['first_week'])}→{_i(b1['last_week'])} {b1['trend_dir']}｜CV {b1['cv_pct']}%（基線{b1['cv_baseline']}%{_cv_dir(b1['cv_pct'], b1['cv_baseline'])}）", True),
        (f"低血壓/低心跳 {b1['low_bp_days']}/{b1['low_hr_days']}次", False),
    ], b1["summary"], gap=10)
    if b1["crisis_count"]:
        ry = _crisis_box(c, RIGHT_X, ry, COL_W, b1["crisis_count"], b1["total_readings"], b1["crisis_pct"], b1["crisis_detail"], gap=20)

    # BLOCK 2 體重・體液與營養（原 BLOCK2 體液代謝 + BLOCK4 肌少衰弱 合併，
    # 拿掉尿色/較上月變化/活動這幾項真實資料完全無來源的子項）
    if b2["retention_support"] == NO_DATA:
        retention_text = "滯留判斷資料不足"
    else:
        retention_text = "支持滯留" if b2["retention_support"] else "不支持滯留"
    ry = _block(c, RIGHT_X, ry, COL_W, "體重・體液與營養", [
        (f"體重 {_i(b4['weight_now'])}kg（{_pct(b4['weight_pct'])}；顯著界>=5%）；{retention_text}", b4["weight_significant"]),
        (f"滯留複合 心率{_i(b2['hr_start'])}→{_i(b2['hr_end'])}＋血氧{_i(b2['spo2_start'])}→{_i(b2['spo2_end'])}＋夜間如廁↑（{b2['cluster_start']}起）", True),
        (f"飲水未達標{b2['water_short_days']}天/{b2['total']}；進食未達標{b4['food_short_days']}天/{b4['food_total']}（連續{_i(b4['food_streak'])}天）",
         b2["water_short_days"] != NO_DATA and b2["water_short_days"] > 5),
    ], f"{b2['summary']} {b4['summary']}")

    # BLOCK 3 感染監測
    lo3 = "、".join(f"{e['t']}，{e['date']}，{e['delta']}" for e in b3["low"]) or "—"
    hi3 = "、".join(f"{e['t']}，{e['date']}" for e in b3["high"]) or "—"
    bh = b3["behavior"]
    if bh:
        from collections import Counter
        kinds = Counter(x["kind"] for x in bh)
        dates = [x["date"] for x in bh]
        rng = f"{dates[0]}–{dates[-1]}" if len(dates) > 1 else dates[0]
        bh_txt = "、".join(f"{k}{v}次" for k, v in kinds.items()) + f"（{rng}）"
    else:
        bh_txt = "無新增"
    td = b3.get("temp_total", b1["total"])
    ry = _block(c, RIGHT_X, ry, COL_W, "感染監測", [
        (f"體溫（對基線{b3['temp_baseline']}）偏低{len(b3['low'])}次/{td}（{lo3}）、偏高{len(b3['high'])}次/{td}（{hi3}）｜CV {b3['temp_cv']}%", bool(b3["low"])),
        (f"急性功能衰退 {('、'.join(b3['acute_days']) or '無')} 體溫異常＋食慾同日下降", bool(b3["acute_days"])),
        (f"異常行為突現 {bh_txt}", bool(bh)),
    ], b3["summary"])

    # ════════════════ A / B SUMMARY (full width) ════════════════
    # 深色粗線：框出底部文字總結（§7.1）
    # A/B 位置直接貼著左右欄內容（不是寫死釘住某個高度），只設下限，保證內容
    # 太長時至少留夠 A/B 文字＋頁尾的空間——2026-07 真實資料改版拿掉部分區塊
    # 後，內容變短，原本寫死 150 的釘住高度會在內容跟 A/B 之間留下一大段空白；
    # 貼齊內容比「空一段再貼底」自然，底部留白落在頁尾上方，是正常的頁面邊界，
    # 不會讓讀者以為中間漏了東西。
    AB_TOP_MIN = 100
    by = max(AB_TOP_MIN, min(y, ry) - 20)
    c.setStrokeColor(C_HEAD); c.setLineWidth(1.3); c.line(MARGIN, by, PAGE_W - MARGIN, by)
    by -= 16
    c.setFont("MSJH-Bold", 10); c.setFillColor(C_TEAL); c.drawString(MARGIN, by, "A　核心走勢")
    by = _wrap_hl(c, MARGIN + 64, by, summary["A"], PAGE_W - 2 * MARGIN - 64, "MSJH", "MSJH-Bold", 9.5, C_TEXT, 15) - 6
    c.setFont("MSJH-Bold", 10); c.setFillColor(C_TEAL); c.drawString(MARGIN, by, "B　關聯因子")
    by = _wrap_hl(c, MARGIN + 64, by, summary["B"], PAGE_W - 2 * MARGIN - 64, "MSJH", "MSJH-Bold", 9.5, C_TEXT, 15)

    # ── FOOTER ──────────────────────────────────────────────────────────
    c.setFont("MSJH", 6.5); c.setFillColor(C_SUB)
    c.drawString(MARGIN, MARGIN + 2,
                 "目前系統無法採集：社交互動、復健／活動出席、睡眠日夜節律、握力、體脂——以上項目暫不顯示於本報告。")
    c.drawString(MARGIN, MARGIN - 8,
                 f"報告產生 {date.today().isoformat()} · 資料來源：照護記錄 · 本摘要為客觀數據彙整，僅供臨床參考，不取代醫師判斷。")

    c.save()
    print(f"[report] 報告已儲存：{output_path}")
