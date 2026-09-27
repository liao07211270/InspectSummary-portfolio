"""
v9 版面產生器（HTML → PDF 版）。

上一版 `report_pdf_v9.py` 用 reportlab 手動計算每個元素的 mm 座標，遇到文字
長度/行數不固定的地方（趨勢註記、查核說明各欄）就容易算錯間距，導致文字
重疊或版面跟原始設計稿的呼吸感對不起來——這是 reportlab 沒有 flexbox/grid
自動排版能力的根本限制，不是單一數字調一調就能一次修完的問題。

這一版改成：產生跟主管提供的 `care-summary-v9.html` 同一套 CSS/DOM 結構的
HTML（沿用 flex/grid做排版、瀏覽器引擎自己處理換行與間距），再用本機已安裝
的 Chrome/Edge headless 模式把 HTML 印成 PDF（Chromium 的列印引擎，跟預覽
這份設計稿用的是同一顆排版引擎，理論上排版行為要嘛跟設計稿一致，要嘛至少
不會再出現「reportlab手算漏一格」這類問題）。

吃的資料 view-model 跟原本 `report_pdf_v9.py`／`mock_data_v9.py`／
`analysis_v9.py` 完全共用同一種形狀，這三個檔案都不用改。
"""
import html as _html
import os
import shutil
import subprocess
import tempfile

PALETTE = {
    "teal": "#0F5F54", "head": "#1A2E2A", "orange": "#D85A30", "sub": "#5C6B68",
    "mute": "#9AA5A1", "border": "#D3DCD9", "cardbg": "#F6F8F6", "warnbg": "#FBF1EC",
    "todobg": "#EEF3F0", "green": "#639922", "accent": "#147D6F",
    # 2026-08-13新增（情境5：短期急性異常）：紫紅色，使用者明確要求要跟
    # 「樣本有限」的橘色（本身跟危急chip/集中期橘紅線共用同一色）明確
    # 區分開，避免兩種完全不同性質的提示（資料不可信 vs. 短期急性事件）
    # 被讀者混為一談。
    "acute": "#A6265B", "acutebg": "#F5E7EE",
}

# 2026-08-12：本月量測分布的刻度數字（收縮壓/脈搏分布色塊下方的90/100/
# 130/160這類標籤）統一用這個字級，不再依刻度間距動態放大/縮小或合併成
# 「90/100」這種黏在一起的格式——使用者明確要求所有刻度數字字級一致、
# 且每個數字都要各自獨立顯示在自己的刻度線下方。4.8pt是實測量出來的
# 安全值：血壓分布最窄的90/100間距只有3%(≈2.67mm)，兩個數字在4.8pt時
# 左右半寬總和約2.38mm，還留有安全餘裕；5.2pt以上這個最窄情境就會開始
# 貼在一起。
_TICK_LABEL_SIZE = 4.8


def _e(v):
    return _html.escape(str(v))


# 2026-08-13新增（情境3）：「樣本有限」目前散落在4個地方（其餘偏離／紀錄
# 完整度／疾病監測卡片主要數值／本月量測分布圖），原本都只是同色文字，
# 使用者反映不夠明顯，要求做成有底色的方塊——這裡統一成一個共用的badge
# 樣式，4個地方都呼叫同一個函式，視覺上才會一致，之後要調整樣式也只要
# 改一個地方。2026-08-13再修正（情境5）：新增color/bg參數，讓「急性事件」
# badge能重用同一個函式、只是換一組顏色（PALETTE["acute"]/["acutebg"]），
# 不用另外複製一份幾乎一樣的HTML樣板。
def _badge_html(text, size="6pt", margin_left="1.4mm", color=None, bg=None):
    color = color or PALETTE["orange"]
    bg = bg or PALETTE["warnbg"]
    return (
        f'<span style="display:inline-block;font-size:{size};font-weight:700;'
        f'color:{color};background:{bg};'
        f'border:.6pt solid {color};border-radius:1.8mm;'
        f'padding:.3mm 1.6mm;margin-left:{margin_left};white-space:nowrap;'
        f'vertical-align:middle;">{_e(text)}</span>'
    )


def _acute_badge_html(text="急性事件", size="6pt", margin_left="1.4mm"):
    return _badge_html(text, size=size, margin_left=margin_left, color=PALETTE["acute"], bg=PALETTE["acutebg"])


# 2026-08-13新增（情境3）：其餘偏離的delta_line(「偏離41%」／「改善12%」)
# 現在也要上色——依方向決定顏色，跟_top3_card()「整體正常→綠色」的判斷是
# 不同的兩套邏輯（remaining_deviations只會出現有severity的項目，不會是
# 「正常」，這裡看的是惡化/改善方向，不是正常與否）。
def _delta_direction_color(delta_line):
    if delta_line.startswith("改善"):
        return PALETTE["green"]
    if delta_line.startswith("偏離"):
        return PALETTE["orange"]
    return PALETTE["head"]


def _chip_html(label, before, after):
    # 「after」值一律用橘色標示（跟原始設計稿一致——不是只有超標/危急才標色，
    # 每個子項的本月數值都要讓讀者一眼看到跟前三月不一樣，之前誤把顏色跟
    # 「warn」旗標綁在一起，導致尿色偏深/排泄異常這類一般子項的數字沒有標色）。
    bg, color = PALETTE["cardbg"], PALETTE["orange"]
    arrow = f'<span style="color:{PALETTE["mute"]};">{_e(before)}</span> → ' if before not in (None, "") else ""
    return f'''<div style="background:{bg};border-radius:1mm;padding:1.3mm 1.5mm;">
      <div style="font-size:6.6pt;color:{PALETTE["sub"]};line-height:1.2;">{_e(label)}</div>
      <div style="font-size:7.6pt;font-weight:600;line-height:1.2;white-space:nowrap;margin-top:.4mm;">{arrow}<span style="color:{color};">{_e(after)}</span></div>
    </div>'''


# 2026-08-13新增（情境5）：急性事件文字（「M/D–M/D連續危急」）如果本月
# 出現不只一段集中期，會變成「M/D–M/D、M/D–M/D連續危急」這種更長的字串
# ——跟_chip_label_size()同樣的「文字越長字級越小」精神，但top3卡片第三
# 行的容器比chip寬一些，門檻不能直接照搬，這裡用實測抓出來的字元數
# 級距，確保多段日期不會超出卡片邊界。
def _acute_text_size(text):
    n = len(text)
    if n <= 11:
        return 8
    if n <= 17:
        return 7
    if n <= 23:
        return 6.2
    return 5.4


def _top3_card(item):
    # 「附加說明」（2026-07-31新增）：原稿三張卡片各自有一句依情況而定的
    # 補充句（起始日期／已達顯著界／整月上行），用「・」接在偏離%後面，
    # 跟原稿排版一致；沒有附加說明(qualifier=None)時就不印這段。
    # 2026-08-12新增：情境1(無個人基線)的delta_line可能是「整體正常」——
    # 原本左側直條跟判讀文字一律用橘色，正常項目也被標成警示色，容易讓
    # 閱讀的人誤以為每一項都異常。改成依delta_line是不是以「正常」結尾
    # 決定用綠色還是橘色——baseline-relative分支的delta_line固定是「偏離
    # /改善 N%」，不會以「正常」結尾，兩條路徑共用這個判斷不會誤判。
    # 2026-08-13新增（情境5）：急性事件時，第三行整段換成專屬提醒文字＋
    # 紫紅色badge，取代原本的delta_line/qualifier那行——急性訊息比一般的
    # 偏離%/集中期描述重要，值得整段換掉，不是並排顯示造成擁擠混淆。
    # 2026-08-13再修正：多段集中期文字可能很長，跟badge硬擠在同一行容易
    # 超出卡片邊界（早先「樣本有限」badge在紀錄完整度就踩過這個坑，見
    # _completeness_item_html()的說明）——改成badge獨立一行、文字本身依
    # 長度動態縮小字級，兩個手段一起用，確保長文字也能完整顯示不溢出。
    acute = item.get("acute")
    if acute:
        accent = PALETTE["acute"]
        acute_size = _acute_text_size(acute)
        third_line = (
            f'<span style="color:{PALETTE["acute"]};font-weight:700;font-size:{acute_size}pt;line-height:1.3;">{_e(acute)}</span>'
            f'<div style="margin-top:.5mm;">{_acute_badge_html()}</div>'
        )
    else:
        is_normal = item["delta_line"].endswith("正常")
        accent = PALETTE["green"] if is_normal else PALETTE["orange"]
        delta_html = _e(item["delta_line"])
        if item.get("qualifier"):
            delta_html += f"・{_e(item['qualifier'])}"
        third_line = f'<span style="color:{accent};font-weight:700;">{delta_html}</span>'
    return f'''<div style="border-left:3.4pt solid {accent};background:{PALETTE["cardbg"]};border-radius:0 1.4mm 1.4mm 0;padding:2.4mm 3mm 2.6mm;display:grid;grid-template-rows:auto auto auto;gap:1mm;">
      <div style="font-size:8.8pt;font-weight:700;">{_e(item["name"])}</div>
      <div style="font-size:15pt;font-weight:700;line-height:1.05;">{_e(item["value"])}<span style="font-size:8.5pt;font-weight:400;color:{PALETTE["sub"]};margin-left:1.2mm;">{_e(item["unit"])}</span></div>
      <div style="font-size:8pt;color:{PALETTE["sub"]};line-height:1.45;">{_e(item["ref_line"])}<br>{third_line}</div>
    </div>'''


# 2026-08-12五次修正：使用者截圖抓到「90」「100」兩個刻度數字在色塊
# 交界間距很窄時會疊在一起(數字本身還是各自獨立置中在自己的真實交界%，
# 只是相鄰兩個交界太近，字寬超過可用空間)。使用者明確要求「數字自動分開
# 不貼在一起」的優先度要高於「精準對齊色塊交界」——兩者衝突時，防重疊
# 勝出，數字位置可以偏離真實交界，但刻度線(_tick_line)本身仍然精準畫在
# 交界上不受影響，讀者還是看得出線在哪裡、數字只是視覺上被推開。
#
# 這兩個常數是估計值，依現有版面實際欄寬換算：本月量測分布區塊寬度固定
# 是89mm(210mm頁面 - 左右12mm邊界 - 兩欄間8mm縫隙，再除以2欄，見
# render_v9_html()的.page padding設定)，_TICK_LABEL_SIZE=4.8pt字級下，
# 數字字元(等寬數字，body已設定font-variant-numeric:tabular-nums)的
# 視覺寬度約落在字級的0.62倍，換算成89mm欄寬的百分比：
# 4.8pt*0.62/2.83465(pt→mm)/89mm*100% ≈ 1.18%／字元。
_TICK_LABEL_CHAR_WIDTH_PCT = 1.2
# 兩個數字之間除了字寬本身，還要留一點呼吸空間才不會看起來像貼在一起，
# 這個緩衝量本身也用百分比表示（跟字元寬度同一個89mm欄寬基準）。
_TICK_LABEL_GAP_BUFFER_PCT = 1.0


def _spread_tick_labels(ticks):
    """依序(dist["ticks"]已經是遞增排序)算出每個刻度數字「防重疊調整後」
    的顯示位置——由左到右逐一檢查，若目前這個數字跟前一個算出來的位置
    距離小於兩者字寬總和的一半＋緩衝間距，就把它往右推到剛好不重疊的
    位置。單向從左往右推是刻意的：多數重疊都是「右邊那個被往右推開」
    比較符合直覺（血壓/脈搏分布的刻度天生由小到大排列），且避免雙向
    推擠互相影響、算出不穩定的結果。極端情況下(例如4個刻度全部擠在一起)
    最右邊的數字可能被推到超過100%，這是物理上的顯示極限——同一個
    89mm欄寬要塞下所有數字，多數真實資料的間距不會這麼極端。"""
    if not ticks:
        return []
    positions = [float(ticks[0][0])]
    prev_label = ticks[0][1]
    for pct, label in ticks[1:]:
        min_gap = (
            (len(str(prev_label)) + len(str(label))) / 2 * _TICK_LABEL_CHAR_WIDTH_PCT
            + _TICK_LABEL_GAP_BUFFER_PCT
        )
        positions.append(max(float(pct), positions[-1] + min_gap))
        prev_label = label
    return positions


def _visible_ticks_5tier(segments):
    """血壓/脈搏分布共用的5段(危急低／偏低／正常／偏高／危急高)、4個刻度
    (90/100/130/160或50/60/101/120)——使用者截圖抓到兩個問題，這裡的規則
    是兩次回饋疊加後的結果：
    (1) 某個月讀數全部落在單一「正常」層時，另外4個危急/偏低/偏高層寬度
        都是0%，理論上不存在的分層邊界卻仍然畫出4個刻度數字，疊在長條
        左右兩端造成溢出/重疊。
    (2) 但完全不顯示任何刻度又太隱晦——使用者接著反映即使全部落在正常層，
        還是要標出「大於多少、在什麼區間算正常」這組基本參考值，不能整條
        線光禿禿的看不出對照標準。
    折衷規則：內側兩個刻度(100/130，正常層自己的上下界)永遠顯示，不管
    旁邊的偏低/偏高層有沒有資料——這兩個數字本身就是在告訴讀者「正常
    範圍是多少」，跟旁邊有沒有偏離資料無關，全綠時只剩這兩個數字也還是
    有意義的參考值。外側兩個刻度(90/160，危急層邊界)則維持「該側危急層
    或偏低/偏高層任一有資料才顯示」的規則——這兩個數字只有在真的發生
    偏離時才有意義，沒有偏離資料時執意畫出來只是徒增邊緣擁擠/溢出風險。
    當外側刻度真的顯示、又剛好跟內側刻度落在同一個%位置時（例如只有
    危急層有資料、偏低層是0%，90跟100會疊在同一點），交給
    _spread_tick_labels()負責把兩個數字推開，不會重疊。非5段結構
    （理論上不會發生，兩個呼叫端都固定傳5段）保守地全部顯示，不做任何
    隱藏。"""
    if len(segments) != 5:
        return [True] * max(len(segments) - 1, 0)
    pcts = [p for p, _ in segments]
    return [
        pcts[0] > 0 or pcts[1] > 0,
        True,
        True,
        pcts[3] > 0 or pcts[4] > 0,
    ]


def _dist_block(title, dist):
    # 各段百分比是各自獨立四捨五入算出來的（analysis_core.py／analysis_v9.py），
    # 加總常常不滿100%（例如2+94+3+0=99），長條尾端就會留一截沒上色的空隙，
    # 被容器的圓角(overflow:hidden)裁切後看起來像最後一段被截斷/消失。這裡把捨入
    # 誤差吸收進最後一段，讓長條永遠剛好補滿100%。
    segs_data = list(dist["segments"])
    if segs_data:
        prior_sum = sum(p for p, _ in segs_data[:-1])
        last_pct, last_col = segs_data[-1]
        segs_data[-1] = (max(0, 100 - prior_sum), last_col)
    segs = "".join(f'<div style="width:{pct}%;background:{col};"></div>' for pct, col in segs_data)
    # 2026-08-06修正：使用者截圖抓到刻度數字沒對齊色塊交界——原本tick線跟
    # 文字用同一個translateX(-50%)置中在{pct}%位置，為了避免數字在接近
    # 邊緣時被裁掉，先前在analysis_v9.py把pct夾在2~95%之間，結果數字位置
    # 準了、但線跟數字綁在一起被迫跟著偏移，反而對不上真正的色塊交界。
    # 改法：拆開處理——tick線永遠畫在真正的{pct}%（精準對齊色塊交界），
    # 文字的定位錨點依位置調整（靠右／靠左／置中）避免被裁到，不動線的位置。
    def _tick_line(pct):
        return (
            f'<div style="position:absolute;left:{pct}%;top:0;transform:translateX(-50%);">'
            f'<div style="height:1.6mm;border-left:.4pt solid {PALETTE["mute"]};margin:0 auto;width:0;"></div></div>'
        )

    # 2026-08-12三次修正：使用者看過「90/100」這種合併標籤的版本後，明確
    # 訂出兩條規則推翻先前的合併呈現法：(1) 每個刻度數字的字級必須完全
    # 一致，不能有的大有的小；(2) 不要出現「數字/數字」這種黏在一起的
    # 格式，每個數字都要單獨顯示在自己對應的刻度線正下方。這裡整段拿掉
    # 「間距太近就合併成一個標籤」的邏輯(_TICK_MERGE_GAP機制)，改成每個
    # 刻度永遠各自獨立畫一個數字，統一套用模組層級的_TICK_LABEL_SIZE字級
    # （見上方常數說明）。極端情況下(間距趨近0%)兩個數字還是可能貼得很近
    # 甚至微幅重疊——這是物理上的顯示極限(把兩個獨立數字塞進趨近於0的
    # 空間)，不是字級或定位邏輯能完全解決的，多數真實資料的間距不會這麼
    # 極端。
    def _tick_label(pos, label):
        # 2026-08-07修正曾經改成「靠邊界錨定」(left:pct%或right:100-pct%
        # 不用transform)，是為了當時較寬的合併標籤(例如「90/100」「≥160／
        # <90 mmHg」)在接近0%/100%邊緣時不被裁掉。2026-08-12四次修正：
        # 使用者實測抓到——現在改成每個刻度各自獨立顯示單一數字後，這組
        # 邊界特例反而讓「50」「120」這種靠邊的數字看起來跟置中呈現的
        # 「101」不一致(邊界錨定會讓數字整個往格子內側偏，不是置中在
        # 刻度線正下方)。這個特例當初是為了避免寬字串超出容器邊界，但
        # 這個ticks容器本身沒有overflow:hidden(色塊長條才有，刻度文字在
        # 它下方獨立的div)，數字字級也縮小成統一的4.8pt、寬度很窄，就算
        # 置中剛好落在0%/100%正上方，最多只會溢出到旁邊的留白區(色塊
        # 區塊之間的間距／頁面邊界margin)，不會被裁切、也不會撞到別的
        # 內容——改成統一置中，不再需要邊界特例。pos是_spread_tick_labels()
        # 算出來的「防重疊調整後」位置，不是原始色塊交界pct（那個位置給
        # _tick_line()畫線用，永遠精準對齊交界）。
        return (
            f'<div style="position:absolute;left:{pos}%;transform:translateX(-50%);'
            f'white-space:nowrap;top:1.6mm;font-size:{_TICK_LABEL_SIZE}pt;">{_e(label)}</div>'
        )

    # 2026-08-12六次修正：只保留「對應分層真的存在(寬度>0%)」的刻度——見
    # _visible_ticks_5tier()。過濾放在_spread_tick_labels()之前，被隱藏的
    # 刻度連帶不參與防重疊推擠計算，不會佔用左右鄰居的間距預算。
    tick_visibility = _visible_ticks_5tier(dist["segments"])
    visible_ticks = (
        [t for t, v in zip(dist["ticks"], tick_visibility) if v]
        if len(tick_visibility) == len(dist["ticks"]) else dist["ticks"]
    )
    tick_lines = "".join(_tick_line(pct) for pct, _label in visible_ticks)
    label_positions = _spread_tick_labels(visible_ticks)
    tick_labels = "".join(_tick_label(pos, label) for pos, (_pct, label) in zip(label_positions, visible_ticks))
    ticks = tick_lines + tick_labels
    # 2026-08-07修正：使用者截圖抓到「≥160／<90 mmHg」這種比一般chip標籤
    # (2-4字)長很多的文字，在變窄的四欄版面裡會被硬折行、把"mmHg"斷成兩截，
    # 很難看。改成標籤字級依文字長度動態縮小＋強制不換行，短標籤字級不變，
    # 長標籤縮小到擠得下同一行為止。
    def _chip_label_size(label):
        return 5.6 if len(str(label)) >= 8 else 6.6

    # 2026-08-12改法：脈搏分布也改成比照血壓分布的合併危急chip格式
    # 「≥120／<50 次/分」，跟血壓的「≥160／<90 mmHg」是同一種「≥上限／
    # <下限」標籤語法，所以警示判斷條件改成認"≥"這個共同特徵，取代原本
    # 個別認"mmHg"／"<9"／"極"字——這樣未來任何分布圖只要合併呈現極端層，
    # 沿用同一套「≥X／<Y」命名就會自動套用警示樣式，不用每次新增分布圖
    # 都回來改這個函式。血氧分布的"<90%"這種只有下限、沒有"≥"的極端chip
    # 維持原本的"<9"判斷不受影響。
    def _is_warn_chip(label):
        s = str(label)
        return "≥" in s or "<9" in s

    # 2026-08-12改法：使用者反映「%數跟次數」（例如73.3%跟22次）擠在同一行
    # 不好讀，改成val（%或次數主值）自己一行、sub（輔助的次數或日期清單）
    # 換到下一行——兩個區塊(收縮壓分布/脈搏分布)共用同一套chip模板，統一
    # 套用，不是只改脈搏。
    chips = "".join(
        f'''<div style="background:{PALETTE["warnbg"] if _is_warn_chip(label) else PALETTE["cardbg"]};border-radius:1mm;padding:.9mm 1.4mm;">
          <div style="font-size:{_chip_label_size(label)}pt;white-space:nowrap;color:{PALETTE["orange"] if _is_warn_chip(label) else PALETTE["sub"]};line-height:1.2;">{_e(label)}</div>
          <div style="font-size:8.6pt;font-weight:700;color:{PALETTE["orange"] if _is_warn_chip(label) else PALETTE["head"]};line-height:1.2;margin-top:.3mm;">{_e(val)}</div>
          {f'<div style="font-size:6.6pt;font-weight:400;color:{PALETTE["orange"] if _is_warn_chip(label) else PALETTE["head"]};line-height:1.2;margin-top:.2mm;">{_e(sub)}</div>' if sub else ""}
        </div>'''
        for label, val, sub in dist["chips"]
    )
    # 2026-08-13新增（情境3）：分布圖標題旁的count_label雖然會誠實顯示
    # 「2次量測/2天」這種原始數字，但沒有額外視覺提示時，使用者得自己
    # 心算這樣算不算稀疏——比照四張卡片/紀錄完整度同一套「樣本有限」
    # badge，讓稀疏的分布圖也有明確警示，不用只靠讀者自己換算。
    dist_badge = _badge_html("樣本有限") if dist.get("sample_limited") else ""
    return f'''<div>
      <div style="font-size:8.6pt;font-weight:700;">{_e(title)}<span style="font-size:7.6pt;font-weight:400;color:{PALETTE["sub"]};margin-left:2mm;">{_e(dist["count_label"])}</span>{dist_badge}</div>
      <div style="display:flex;height:4mm;margin-top:1.4mm;border-radius:2mm;overflow:hidden;">{segs}</div>
      <div style="position:relative;height:4mm;margin-top:.6mm;font-size:7.2pt;color:{PALETTE["sub"]};">{ticks}</div>
      <div style="display:grid;grid-template-columns:repeat({len(dist["chips"])},1fr);gap:1.4mm;margin-top:1mm;">{chips}</div>
    </div>'''


def _four_aspect_card(card):
    # 2026-07-31修正：這4張卡在外層是同一個grid row的4個item，CSS Grid預設
    # align-items:stretch會把每張卡撐到跟最高的那張一樣高；卡片內部原本用
    # grid-template-rows的1fr+align-self:end把「趨勢註記」推到卡片最底部，
    # 結果卡片被撐高時，空隙出現在「chips」跟「趨勢註記」中間，不是卡片最下方，
    # 使用者截圖抓到這個「內容中間留白」的問題。改成全部auto列＋
    # align-content:start，讓內容永遠從最上面依序往下排，卡片被撐高時多出來
    # 的空間留在整張卡最底下，不會插進內容中間。
    period_color = PALETTE["orange"] if card["accent"] == "#D85A30" else PALETTE["mute"]
    chips = "".join(_chip_html(l, b, a) for l, b, a in card["chips"])
    # 2026-08-12新增：情境1(無個人基線)沒有「前三月」數字可比較，卡片主要
    # 數值(primary_before留空/None)時只顯示單一數值、不畫箭頭——比照
    # _chip_html()既有的同一套「before為空就不畫箭頭」判斷邏輯，兩處保持
    # 一致，見下方primary的渲染。
    # 2026-08-11改法：PM要求趨勢註記改成條列式，讓卡片更簡潔好掃視——
    # 原本是單一字串（多個子句用「；」黏成一句，各卡片還混用「、」「／」
    # 等不同分隔符號，讀起來不一致）。trend_note現在是清單，這裡只有1項
    # 時維持單行文字不加bullet（例如高血壓卡固定只有1句，加bullet沒意義）；
    # 2項以上才逐項條列，每項一行、前面加「・」。
    # 2026-08-12修正：使用者實測抓到條列式趨勢註記讓整份報告的實際內容
    # 高度超出A4版面(297mm)約1-2mm，PNG是固定視窗截圖(不會自動換頁/縮小)，
    # 超出的部分被硬生生切掉，最底下「資料來源」那行因此被切掉一截。這裡
    # 用最壞情況(體液代謝卡最多4句、衰弱卡最長的衛生協助項目句子在雙位數
    # 次數時會換行變相當於4行)去抓需要的壓縮量：字級從6.8pt降到6.6pt、
    # 行高從1.4降到1.25、bullet間margin-top從.6mm降到.35mm，四行的高度
    # 抓回約2.5mm，蓋過目前量到的1-2mm超出量還留有餘裕。
    trend_items = card["trend_note"]
    if len(trend_items) <= 1:
        trend_html = f'<div style="font-size:6.6pt;color:{PALETTE["sub"]};line-height:1.25;text-wrap:pretty;">{_e(trend_items[0]) if trend_items else ""}</div>'
    else:
        trend_html = "".join(
            f'<div style="font-size:6.6pt;color:{PALETTE["sub"]};line-height:1.25;text-wrap:pretty;'
            f'{"margin-top:.35mm;" if i else ""}">・{_e(item)}</div>'
            for i, item in enumerate(trend_items)
        )
    # 2026-08-13新增（情境3）：卡片主要數值底層指標如果被判定樣本有限
    # （見analysis_v9.py的_is_sample_limited()），在primary_label旁加同一套
    # 「樣本有限」badge——跟「其餘偏離」/「紀錄完整度」用同一個措辭跟橘色
    # 系統，讀者才不會覺得同一個收縮壓數字在報告不同角落講法不一致（一邊
    # 標樣本有限、一邊完全沒提示）。card沒有這個key的既有呼叫（例如衰弱卡，
    # 體重刻意不套用這條規則）完全不受影響。
    sample_limited_badge = _badge_html("樣本有限", size="5.8pt") if card.get("sample_limited") else ""
    # 2026-08-13新增（情境5）：疾病監測卡片主要數值底層指標如果被判定
    # 急性事件（見analysis_v9.py的_acute_cluster_label()），primary_label
    # 旁加「急性事件」badge——跟top3/其餘偏離同一套紫紅色系統，讀者才不會
    # 覺得同一個收縮壓數字在報告不同角落講法不一致。急性badge放在樣本
    # 有限badge前面（急性事件較緊急，優先顯示），兩者理論上可能同時出現
    # （急性事件剛好也樣本不足），不互斥。
    acute_badge = _acute_badge_html(size="5.8pt") if card.get("acute") else ""
    return f'''<div style="border:.8pt solid #E4E9E4;border-top-width:2.4pt;border-top-color:{card["accent"]};border-radius:1mm;padding:2.2mm 2.4mm 2.4mm;display:grid;grid-template-rows:auto auto auto auto auto;align-content:start;">
      <div style="font-size:8.4pt;font-weight:700;color:{PALETTE["teal"]};">{_e(card["title"])}</div>
      <div style="font-size:6.4pt;color:{period_color};margin-top:.4mm;">{_e(card["period_label"])}</div>
      <div style="margin-top:1.8mm;">
        <div style="font-size:7.6pt;color:{PALETTE["sub"]};">{_e(card["primary_label"])}{acute_badge}{sample_limited_badge}</div>
        <div style="font-size:12.5pt;font-weight:700;line-height:1.1;margin-top:.4mm;">{
          f'<span style="color:{PALETTE["orange"]};">{_e(card["primary_after"])}</span>'
          if card.get("primary_before") in (None, "")
          else f'<span style="color:{PALETTE["mute"]};">{_e(card["primary_before"])}</span> → <span style="color:{PALETTE["orange"]};">{_e(card["primary_after"])}</span>'
        }</div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 1.6mm;margin-top:1.8mm;">{chips}</div>
      <div style="margin-top:1.4mm;padding-top:1mm;border-top:.5pt dotted {PALETTE["border"]};">
        <div style="font-size:6.2pt;color:{PALETTE["mute"]};letter-spacing:.04em;margin-bottom:.4mm;">趨勢註記</div>
        {trend_html}
      </div>
    </div>'''


def _daily_status_section(ds):
    # 2026-08-11修正：使用者實測抓到真bug——偶發事件列的圖標，隨著日期
    # 越往右，跟上面四列色塊格子的x位置偏移越來越大(day3幾乎對齊、day20
    # 已經明顯偏左一整格)。根源是flex item預設min-width:auto，偶發事件列
    # 右側legend文字用white-space:nowrap(避免「抱怨」跟「（3次）」被硬折
    # 行截斷)，最長那行(例如「◐情緒異常(突哭/笑)（5次）」)撐爆了宣告的
    # flex:0 0 23mm，把它「擠」寬——這一擠會佔掉同一個flex容器裡其他手足
    # 的空間，害中間flex:1的日期格子grid實際可用寬度變窄，repeat(days,1fr)
    # 算出來的每欄pitch跟著全部縮小，越後面的日期累積誤差越大，反而是最前
    # 面的欄位看起來還算準(這也是使用者形容「標題沒偏移、後面才偏移」的
    # 原因——「偶發事件」四字標籤本身沒撐爆14mm，只有legend撐爆了23mm)。
    # 這是每一列都共用的flex結構通病，不只偶發事件列會中——這裡對所有
    # flex:0 0 14mm/23mm的label／note／legend欄位統一補上min-width:0，
    # 讓文字overflow時只會視覺溢出(蓋到旁邊空白處)而不是把box撐寬、偷走
    # grid欄位的空間，中間的日期格子grid永遠拿到宣告好的滿版寬度。
    days = ds["days"]
    rows_html = ""
    for row in ds["rows"]:
        # 2026-08-07依醫師反映：未測量(col為None)原本是完全空白(連框線都沒有)，
        # 容易被誤認成排版縫隙、看不出「這格有格子只是沒資料」——改成畫出格子
        # 框線、裡面不上色，讓「有格子沒資料」跟「單純沒有這一格」的視覺區別
        # 更明確。2026-08-12五次修正：使用者要求「單次異常」改成黃色、
        # 「無量測紀錄」改成灰色實心格子——異常搬去黃色後，原本框線空格
        # 用的灰色不會再跟異常搞混，改成灰色實心格子跟其餘三色(綠/黃/橘)
        # 視覺語言一致，都是實心格子，不再需要框線這種第四種樣式表達。
        cells = "".join(
            f'<div style="height:3mm;border-radius:.4mm;background:{col};"></div>' if col
            else f'<div style="height:3mm;border-radius:.4mm;background:{PALETTE["mute"]};"></div>'
            for col in row["colors"]
        )
        rows_html += f'''<div style="display:flex;align-items:center;gap:1.8mm;margin-bottom:.7mm;">
          <div style="flex:0 0 14mm;min-width:0;font-size:8.6pt;font-weight:700;">{_e(row["label"])}</div>
          <div style="flex:1;display:grid;grid-template-columns:repeat({days},1fr);gap:.55mm;">{cells}</div>
          <div style="flex:0 0 23mm;min-width:0;font-size:8pt;color:{PALETTE["sub"]};">{_e(row["note"])}</div>
        </div>'''

    # 2026-08-04改法：格子裡原本塞BEHAVIOR_LABELS中文全名，窄格子放不下、
    # 逐格換行擠壓——改成格子只放EVENT_ICONS的單一符號，中文說明收斂到
    # 右側legend（「▲肢體攻擊(3次)」格式，最多4種類型、每行2種避免爆版）。
    # 2026-08-06改法：同一天撞到多種入選行為時，原本橫向並排會擠成一團、
    # 符號幾乎疊在一起難以分辨——改成直向由上到下自然排列（不用跟右側
    # legend文字逐行對齊，格子本身高度會依當天最多符號數的那一天自動撐高，
    # 沒有事件的天數維持min-height當底，不會被拉得比別的天矮）。
    ev = ds["events_row"]
    ev_cells = ""
    for icons in ev["icons_by_day"]:
        glyphs = "".join(f'<span style="color:{color};">{_e(glyph)}</span>' for glyph, color in icons) if icons else ""
        ev_cells += (
            f'<div style="min-height:3.3mm;display:flex;flex-direction:column;align-items:center;'
            f'justify-content:center;gap:.3mm;font-size:6pt;line-height:1.15;">{glyphs}</div>'
        )
    # 2026-08-06改法：原本兩則一行用全形空白接起來，欄寬不夠時瀏覽器會在
    # 文字中間硬折行（例如「抱怨」跟「（3次）」被拆成兩行）；改成一則一行、
    # 每行加white-space:nowrap，行數變多但每行本身不會再被截斷。
    legend_entries = [
        f'<span style="color:{item["color"]};">{_e(item["glyph"])}</span>{_e(item["label"])}（{item["count"]}次）'
        for item in ev["legend_items"]
    ] or ["本月無行為事件記錄"]
    legend_html = "<br>".join(
        f'<span style="white-space:nowrap;">{entry}</span>' for entry in legend_entries
    )

    # 2026-08-05改法：拿掉頂部偏離集中期的橘紅色橫槓＋右側日期範圍文字
    # （原本讀ds["cluster_segments"]/ds["cluster_label"]），這個位置改放
    # 日期刻度（原本在四列格子下方，7天一個刻度，見_date_ticks()）；
    # 個別格子本身還是會用橘色標出集中期(_row_colors)，只是拿掉這條額外的
    # 彙總橫槓跟日期文字。偶發事件列跟著往上移到四列格子正下方，緊接
    # 在格子後面，不再隔著日期刻度列。
    ticks_html = "".join(
        f'<div style="white-space:nowrap;">{_e(ds["date_ticks"].get(i, "").replace("-", "/"))}</div>' for i in range(days)
    )

    # 2026-08-11二次修正：使用者實測抓到——偶發事件列的legend行數一多
    # （例如這裡5種行為代碼各佔一行），前一版改的align-items:center會把
    # 整列撐高到legend的高度，再把label／圖標格子這些天生較矮的手足
    # 「置中」進這片被撐出來的高度裡，視覺上變成圖標／文字浮在格子中間、
    # 跟上面四列色塊格子的頂端對不齊。改回align-items:flex-start——不管
    # legend多長，label跟圖標格子都貼齊這一列的頂端(跟色塊格子頂端同一
    # 條線)，legend文字本身也從頂端開始往下排，不會被置中推進列中間的
    # 空白裡。
    return f'''<div style="display:flex;align-items:baseline;gap:2mm;margin:1.8mm 0 1.4mm;">
      <div style="width:2.4mm;height:2.4mm;background:{PALETTE["orange"]};position:relative;top:-.4mm;"></div>
      <div style="font-size:9.4pt;font-weight:700;color:{PALETTE["teal"]};">每日狀態與事件</div>
      <div style="font-size:7.4pt;color:{PALETTE["mute"]};">綠＝正常，黃＝異常，橘＝異常且落在偏離集中期，灰＝無量測記錄</div>
    </div>
    <div style="display:flex;align-items:center;gap:1.8mm;margin-bottom:.6mm;">
      <div style="flex:0 0 14mm;min-width:0;"></div>
      <div style="flex:1;display:grid;grid-template-columns:repeat({days},1fr);font-size:7pt;color:{PALETTE["mute"]};">{ticks_html}</div>
      <div style="flex:0 0 23mm;min-width:0;"></div>
    </div>
    {rows_html}
    <div style="display:flex;align-items:flex-start;gap:1.8mm;margin-top:.6mm;padding-top:.8mm;border-top:.5pt dotted {PALETTE["border"]};">
      <div style="flex:0 0 14mm;min-width:0;font-size:8.6pt;font-weight:700;color:{PALETTE["sub"]};">偶發事件</div>
      <div style="flex:1;display:grid;grid-template-columns:repeat({days},1fr);gap:.55mm;">{ev_cells}</div>
      <div style="flex:0 0 23mm;min-width:0;font-size:7pt;color:{PALETTE["sub"]};line-height:1.35;">{legend_html}</div>
    </div>'''


def _check_notes_section(cn):
    remaining_count = len(cn["remaining_deviations"])
    remaining_title = f"其餘偏離 {remaining_count} 項" if remaining_count else "其餘偏離項目"
    # 2026-08-13改法：val(delta_line，例如「偏離41%」)依方向上色，
    # sample_limited(第3個tuple元素)為真時附加橘色「樣本有限」badge——見
    # analysis_v9.py 2026-08-13的說明。2026-08-13再修正（情境5）：第4個
    # tuple元素(可省略)是acute急性事件文字，有值時額外附加紫紅色「急性
    # 事件」badge——兩個badge理論上可能同時出現(急性事件剛好也樣本有限)，
    # 不互斥，都顯示。
    def _remaining_item_html(item):
        label, val, sample_limited = item[0], item[1], item[2]
        acute = item[3] if len(item) > 3 else None
        badges = (_acute_badge_html() if acute else "") + (_badge_html("樣本有限") if sample_limited else "")
        return (
            f'''<div style="background:{PALETTE["cardbg"]};border-radius:1mm;padding:1.2mm 1.6mm;font-size:7.2pt;color:{PALETTE["sub"]};line-height:1.4;">'''
            f'''<b style="color:{PALETTE["head"]};font-weight:600;margin-right:1.4mm;">{_e(label)}</b>'''
            f'''<span style="color:{_delta_direction_color(val)};font-weight:700;">{_e(val)}</span>'''
            f'''{badges}</div>'''
        )

    remaining = "".join(_remaining_item_html(item) for item in cn["remaining_deviations"]) or (
        f'<div style="font-size:7.2pt;color:{PALETTE["mute"]};">（無其餘顯著偏離）</div>'
    )
    # 2026-08-12新增（情境3）：紀錄完整度每一項可選擇性帶第4個元素（樣本
    # 有限提示文字），3-tuple的既有呼叫（例如體重/服藥・照護，還沒套用
    # 這條規則）完全不受影響，val不變、dim不變，只是多了一個可選badge。
    def _completeness_item_html(item):
        label, val, dim = item[0], item[1], item[2]
        badge = item[3] if len(item) > 3 else None
        badge_html = f'<div style="margin-top:.6mm;">{_badge_html(badge, size="5.8pt", margin_left="0")}</div>' if badge else ""
        return (
            f'''<div style="background:{PALETTE["warnbg"] if badge else PALETTE["cardbg"]};border-radius:1mm;padding:1.1mm 1.4mm;font-size:7pt;font-weight:600;color:{PALETTE["mute"] if dim else PALETTE["head"]};">'''
            f'''<div style="font-size:6.4pt;font-weight:400;color:{PALETTE["mute"]};line-height:1.2;">{_e(label)}</div>{_e(val)}{badge_html}</div>'''
        )

    completeness = "".join(_completeness_item_html(item) for item in cn["completeness"])
    # 2026-08-12新增：情境1(無個人基線)的「參考標準」項目內容較多(收縮壓/
    # 舒張壓/脈搏/體溫/飲水/BMI六項)，塞成一句用「・」接起來的長字串會被
    # 容器寬度硬折行、換行位置不受控，使用者實測反映不好看。改成val可以
    # 是list——是list時逐項條列(比照trend_note的bullet寫法，每項一行、
    # 前面加「・」)，是既有的字串就維持原本單行顯示，兩種呼叫方式並存、
    # 不影響其他既有欄位(例如「個人基線」「比較方式」還是單行字串)。
    def _todo_item_html(label, val):
        if isinstance(val, list):
            # list時label自己一行(當小標題)，底下逐項條列，不跟值擠在同一行
            bullets = "".join(
                f'<div style="{"margin-top:.3mm;" if i else ""}">・{_e(item)}</div>'
                for i, item in enumerate(val)
            )
            return (
                f'<b style="color:{PALETTE["head"]};font-weight:600;display:block;margin-bottom:.3mm;">{_e(label)}</b>{bullets}'
            )
        return f'<b style="color:{PALETTE["head"]};font-weight:600;margin-right:1.4mm;">{_e(label)}</b>{_e(val)}'

    baseline_todo = "".join(
        f'''<div style="background:{PALETTE["cardbg"]};border-radius:1mm;padding:1.2mm 1.6mm;font-size:7.2pt;color:{PALETTE["sub"]};line-height:1.4;">'''
        f'''{_todo_item_html(label, val)}</div>'''
        for label, val in cn["baseline_todo"]
    )
    return f'''<div style="border-top:.8pt solid {PALETTE["border"]};margin-top:2.6mm;padding-top:2.2mm;">
      <div style="display:flex;align-items:baseline;gap:2mm;margin-bottom:2mm;">
        <div style="width:2.4mm;height:2.4mm;background:{PALETTE["mute"]};position:relative;top:-.4mm;"></div>
        <div style="font-size:9.4pt;font-weight:700;color:{PALETTE["sub"]};">查核與說明</div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:0 5mm;align-items:start;">
        <div>
          <div style="font-size:7.6pt;font-weight:700;color:{PALETTE["sub"]};margin-bottom:1.4mm;">{_e(remaining_title)}</div>
          <div style="display:flex;flex-direction:column;gap:1.2mm;">{remaining}</div>
        </div>
        <div>
          <div style="font-size:7.6pt;font-weight:700;color:{PALETTE["sub"]};margin-bottom:1.4mm;">紀錄完整度<span style="font-weight:400;color:{PALETTE["mute"]};margin-left:1mm;">（{_e(cn.get("completeness_period", ""))}）</span></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.2mm 1.6mm;">{completeness}</div>
        </div>
        <div>
          <div style="font-size:7.6pt;font-weight:700;color:{PALETTE["sub"]};margin-bottom:1.4mm;">比較基準</div>
          <div style="display:flex;flex-direction:column;gap:1.2mm;">
            {baseline_todo}
            <div style="background:{PALETTE["todobg"]};border-radius:1mm;padding:1.2mm 1.6mm;font-size:7.2pt;color:{PALETTE["sub"]};line-height:1.4;">{_e(cn["reference_pending"])}</div>
          </div>
        </div>
      </div>
    </div>'''


def _render_headline(ts):
    """把「N項」的數字跟「項目本身」(top_name，例如「進食不足天數」)上色，
    「以」「變化最為明顯」這類純連接字維持黑色——2026-08-05二次修正：
    使用者確認要上色的是「3項」跟「進食不足天數」，不是整段「以OO變化
    最為明顯」。headline是純規則式產生的句子，不會被LLM潤飾(只有detail
    會)，直接用結構化欄位(headline_count/headline_top_name)重新組字串是
    安全的。若view-model沒有這兩個結構化欄位（例如mock_data_v9.py這個
    舊版demo資料，headline是完全客製化的句子），直接退回顯示原始headline
    字串，不硬套模板。"""
    if "headline_count" not in ts:
        return _e(ts["headline"])
    count = ts.get("headline_count")
    top_name = ts.get("headline_top_name")
    count_span = f'<span style="color:{PALETTE["orange"]};">{_e(f"{count} 項")}</span>' if count is not None else ""
    head = f'{_e("本月共 ")}{count_span}{_e("主要生理量測較前三月基線出現明顯變化")}'
    if top_name:
        name_span = f'<span style="color:{PALETTE["orange"]};">{_e(top_name)}</span>'
        # 2026-08-18修正：使用者希望「以OO變化最為明顯」這句固定另起一行，
        # 不要讓瀏覽器依可用寬度自動決定斷行點(text-wrap:pretty有時會斷在
        # 「以體溫偏離基線」中間，切斷語意)——直接插入<br>強制在逗號後換行。
        return f'{head}，<br>{_e("以")}{name_span}{_e("變化最為明顯")}。'
    return f"{head}。"


def _render_detail(ts):
    """只把實際的訊號名稱(detail_highlight_terms，例如「心率上升」)上色，
    日期範圍跟「等N項」維持黑色——2026-08-05修正，跟_render_headline()
    同一個原則：只有具體的項目名稱才需要視覺強調，數字/日期/收尾這類連接
    資訊不需要跟著變色。這幾個訊號名稱經過required_terms機制保證在detail
    (不管是LLM潤飾成功還是回退用原始子句)裡逐字存在（見analysis_v9.py的
    _overlap_clause()說明），所以可以安全地在跳脫後的文字上做字串取代；
    沒有跨指標關聯時(detail_highlight_terms是空清單)原樣輸出安全句，不做
    任何上色。"""
    escaped = _e(ts["detail"])
    for term in ts.get("detail_highlight_terms") or []:
        needle = _e(term)
        styled = f'<span style="color:{PALETTE["orange"]};">{needle}</span>'
        escaped = escaped.replace(needle, styled, 1)
    return escaped


def render_v9_html(vm: dict) -> str:
    h = vm["header"]
    ts = vm["top_summary"]
    top3_count = len(vm["top3"])
    # 2026-08-12新增：情境1(無個人基線)這個區塊會從「偏離幅度排序」改成
    # 「本月生理數值提醒」，標題文字本身要能整段替換，不是只有數量字不同——
    # top_summary裡可以帶一個section_title覆蓋預設的動態標題，沒有帶的話
    # (現行有基線的正常情況)維持原本「偏離幅度最大的N項」這套邏輯不變。
    top3_title = ts.get("section_title") or (
        f"偏離幅度最大的{top3_count}項" if top3_count else "偏離幅度最大的項目"
    )
    top3_html = "".join(_top3_card(item) for item in vm["top3"]) or (
        f'<div style="grid-column:1/-1;font-size:8.5pt;color:{PALETTE["mute"]};">本期無明顯偏離項目</div>'
    )
    fo = vm["footer"]

    return f'''<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>AI 照護月摘要 v9</title>
<style>
  @page{{size:A4 portrait;margin:0;}}
  html,body{{margin:0;padding:0;}}
  body{{font-family:"Microsoft JhengHei","PingFang TC","Heiti TC",sans-serif;
       -webkit-print-color-adjust:exact;print-color-adjust:exact;}}
  .page{{width:210mm;min-height:297mm;box-sizing:border-box;}}
</style>
</head>
<body>
<section class="page" style="padding:2mm 12mm 2mm;color:{PALETTE["head"]};font-variant-numeric:tabular-nums;background:#fff;">

  <div style="display:flex;justify-content:space-between;align-items:baseline;border-bottom:2.4pt solid {PALETTE["teal"]};padding-bottom:2.4mm;">
    <div style="display:flex;align-items:baseline;gap:3mm;">
      <div style="font-size:13pt;font-weight:700;letter-spacing:.01em;">{_e(h["name"])}</div>
      <div style="font-size:8.4pt;color:{PALETTE["sub"]};">{_e(h["gender"])}・{_e(h["age"])} 歲・CFS {_e(h["cfs"])}・{_e(h["group"])}</div>
    </div>
    <div style="font-size:9.4pt;font-weight:700;color:{PALETTE["teal"]};white-space:nowrap;">{_e(h["period_label"])}</div>
  </div>

  <div style="display:grid;grid-template-columns:auto 1fr;gap:0 6mm;align-items:center;margin-top:3.4mm;">
    <div style="display:flex;align-items:baseline;gap:2mm;padding-right:6mm;border-right:.8pt solid {PALETTE["border"]};">
      <div style="font-size:40pt;font-weight:900;line-height:.82;color:{PALETTE["orange"]};">{_e(ts["deviation_count"])}</div>
      <div style="font-size:11pt;font-weight:700;line-height:1.25;">項紀錄<br>偏離參考值</div>
    </div>
    <div>
      <div style="font-size:13pt;font-weight:700;line-height:1.45;text-wrap:pretty;">{_render_headline(ts)}</div>
      <div style="font-size:8.8pt;color:{PALETTE["sub"]};line-height:1.5;margin-top:1.6mm;text-wrap:pretty;">{_render_detail(ts)}</div>
    </div>
  </div>

  <div style="border-top:.8pt solid {PALETTE["border"]};margin-top:2mm;padding-top:2mm;">
    <div style="display:flex;align-items:center;gap:2mm;margin-bottom:1.4mm;">
      <div style="width:2.4mm;height:2.4mm;background:{PALETTE["orange"]};"></div>
      <div style="font-size:9.4pt;font-weight:700;color:{PALETTE["teal"]};">{_e(top3_title)}</div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:0 4mm;">{top3_html}</div>
  </div>

  <div style="border-top:.8pt solid {PALETTE["border"]};margin-top:2.6mm;padding-top:2.2mm;">
    <div style="display:flex;align-items:baseline;gap:2mm;margin-bottom:2.4mm;">
      <div style="width:2.4mm;height:2.4mm;background:{PALETTE["orange"]};position:relative;top:-.4mm;"></div>
      <div style="font-size:9.4pt;font-weight:700;color:{PALETTE["teal"]};">本月量測分布</div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 8mm;">
      {_dist_block("收縮壓分布", vm["bp_distribution"])}
      {_dist_block("脈搏分布", vm["pulse_distribution"])}
    </div>
  </div>

  <div style="border-top:.8pt solid {PALETTE["border"]};margin-top:2.6mm;padding-top:2.2mm;">
    <div style="display:flex;align-items:baseline;gap:2mm;margin-bottom:1.6mm;">
      <div style="width:2.4mm;height:2.4mm;background:{PALETTE["orange"]};position:relative;top:-.4mm;"></div>
      <div style="font-size:9.4pt;font-weight:700;color:{PALETTE["teal"]};">疾病監測面向</div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0 3.2mm;">
      {"".join(_four_aspect_card(c) for c in vm["four_aspects"])}
    </div>
  </div>

  <div style="border-top:.8pt solid {PALETTE["border"]};margin-top:2.6mm;padding-top:2.2mm;">
    {_daily_status_section(vm["daily_status"])}
  </div>

  {_check_notes_section(vm["check_notes"])}

  <div style="margin-top:3mm;padding-top:2.2mm;border-top:1pt solid {PALETTE["teal"]};font-size:6.8pt;color:{PALETTE["head"]};font-weight:700;line-height:1.7;">
    <div>{_e(fo["disclaimer_intro"])}{_e(fo["disclaimer_key"])}</div>
    <div>{_e(fo["disclaimer_note"])}</div>
    <div style="margin-top:1.8mm;font-weight:400;color:{PALETTE["sub"]};">
      <div><b style="color:{PALETTE["head"]};">報告產生</b>　{_e(fo["generated_date"])}</div>
      <div style="margin-top:.6mm;"><b style="color:{PALETTE["head"]};">資料來源</b>　{_e(fo["data_source"])}</div>
    </div>
  </div>

</section>
</body>
</html>
'''


def _find_chrome():
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    for name in ("chrome", "msedge", "chromium"):
        p = shutil.which(name)
        if p:
            return p
    raise FileNotFoundError("找不到 Chrome/Edge，無法把 HTML 印成 PDF——請確認本機已安裝其中一個瀏覽器。")


# A4 @96dpi的CSS像素尺寸（210mm/297mm換算），跟render_v9_html()裡
# `.page{width:210mm;min-height:297mm}`的實際版面尺寸對齊——PNG改用截圖
# 而非列印引擎，視窗大小必須跟頁面設計尺寸一致，不然會截到多餘留白或
# 內容被裁掉。
_PAGE_PX_WIDTH = 794
_PAGE_PX_HEIGHT = 1123
_PNG_SCALE = 2  # 螢幕截圖畫質倍率，避免文字/線條在放大檢視時模糊


def html_to_png(html_str: str, output_path: str, timeout: int = 30):
    """用本機 headless Chrome/Edge 把 HTML 截圖存成 PNG——2026-08-07依醫師
    會議需求，匯出格式從PDF改成圖片，不再產生PDF。沿用跟html_to_pdf()
    同一顆瀏覽器引擎，只是把「列印成PDF」換成「截圖存PNG」，排版邏輯
    (flexbox/grid/自動換行)完全不變。"""
    browser = _find_chrome()
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html_str)
        html_path = f.name
    try:
        output_path = os.path.abspath(output_path)
        args = [
            browser, "--headless=new", "--disable-gpu", "--no-sandbox",
            f"--window-size={_PAGE_PX_WIDTH},{_PAGE_PX_HEIGHT}",
            f"--force-device-scale-factor={_PNG_SCALE}",
            "--hide-scrollbars",
            "--default-background-color=FFFFFF",
            f"--screenshot={output_path}",
            f"file:///{html_path.replace(os.sep, '/')}",
        ]
        result = subprocess.run(args, capture_output=True, timeout=timeout)
        if not os.path.isfile(output_path):
            raise RuntimeError(f"Chrome/Edge 截圖失敗：{result.stderr.decode(errors='replace')}")
    finally:
        os.unlink(html_path)


def generate_v9_report_html(vm: dict, output_path: str):
    html_str = render_v9_html(vm)
    html_to_png(html_str, output_path)
    print(f"[report_html_v9] v9 版面（HTML引擎）已儲存：{output_path}")
