"""
v9 手機高齡友善版（分頁互動 HTML）。

跟 report_html_v9.py（桌面單頁版，印成PNG）是同一份 build_v9_view_model()
輸出、兩種呈現方式——這裡不重算候選池/嚴重度/急性事件等任何邏輯，只是把
同一份資料拆成「一個畫面一次只講一件事」的分頁介面，供手機瀏覽器（預期
透過LINE LIFF內嵌瀏覽器開啟）使用。

跟桌面版的關鍵差異：
- 桌面版用mm為單位、走無頭瀏覽器截圖成單一PNG（列印邏輯）；這裡用px/rem、
  走真實互動網頁（JS切換分頁），不產生圖片。
- 字級/按鈕觸控範圍明顯放大，色彩沿用PALETTE維持品牌一致，但不直接沿用
  桌面版的HTML字串組裝函式（那些函式的CSS是為印刷密度設計，不適合手機）。
- 2026-08-20使用者定案：10個內容頁 + 1個新增的首頁選單（可跳頁，不用一路
  點下一頁）；朗讀鍵先做UI佔位，不接實際TTS；第4頁（原稿是血氧分布）改成
  脈搏分布——桌面版render_v9_html()其實也已經改用pulse_distribution，
  這裡跟桌面版一致，不是額外決策。
"""
import html as _html
import os
import re

from .report_html_v9 import PALETTE, _visible_ticks_5tier

# ── 朗讀文字轉換（2026-08-20新增，使用者審過文字稿定案）──────────────────
# 畫面顯示用的字串（例如「129.0 → 134.0 mmHg」）念起來會很生硬，這裡把同一份
# view-model資料轉寫成口語化的朗讀腳本，跟畫面HTML分開產生。轉寫規則是
# 使用者審過文字稿後定案的三條：
#   1. 數字結尾是「.0」時不用念小數點（58.0→58），其餘小數維持原樣讓語音
#      引擎自然讀出（6.9維持6.9，不用手動拼「點9」）。
#   2. 毫米汞柱這類單位改用英文縮寫（mmHg），中文唸法生硬——好在資料本身
#      candidate pool存的unit欄位跟dist chips的label就已經是"mmHg"字串
#      （不是「毫米汞柱」），這裡不用另外轉換，維持原樣即可。
#   3. 日期用「M/D」或「M/D–M/D」這種畫面格式，念的時候要轉成「M月D號」
#      /「M月D號到M月D號」。


def _sp_num(s):
    """數值字串結尾是「.0」時去掉小數點（58.0 -> 58），其餘原樣返回。"""
    s = str(s)
    return re.sub(r'(-?\d+)\.0(?!\d)', r'\1', s)


_DATE_RANGE_RE = re.compile(r'(\d{1,2})/(\d{1,2})[–\-](\d{1,2})/(\d{1,2})')
_DATE_SINGLE_RE = re.compile(r'(\d{1,2})/(\d{1,2})')


def _sp_dates(text):
    """把「11/9–11/13」轉成「11月9號到11月13號」，「11/9」轉成「11月9號」；
    同時套用_sp_num()去掉不必要的小數點。範圍先轉、避免範圍裡的單一日期
    被_DATE_SINGLE_RE搶先吃掉、少了「到」這個連接詞。"""
    text = _sp_num(text)
    text = _DATE_RANGE_RE.sub(r'\1月\2號到\3月\4號', text)
    text = _DATE_SINGLE_RE.sub(r'\1月\2號', text)
    return text


def _sp_pct(s):
    """"68%" -> "百分之68"。"""
    s = _sp_num(str(s))
    return f"百分之{s[:-1]}" if s.endswith('%') else s


def _sp_delta(delta_line):
    """"偏離 12%" -> "偏離幅度百分之12"，"改善 7%" -> "改善幅度百分之7"。"""
    m = re.match(r'(偏離|改善)\s*([\d.]+)%', delta_line)
    if not m:
        return delta_line
    word, num = m.groups()
    return f"{word}幅度百分之{_sp_num(num)}"


def _sp_symbols(text):
    """收尾用的符號清理——把畫面用的符號(→／≥＜這類)換成口語詞，在每個
    _speak_*()組完整段文字的最後統一套用一次，不用在每個小片段各自轉換。
    全形斜線「／」跟日期用的半形斜線「/」是不同字元，這裡只處理前者，
    不會誤動到_sp_dates()已經轉換過的「M月D號」字串。"""
    text = text.replace('→', '到')
    text = text.replace('≥', '大於等於')
    text = text.replace('／', '或')
    text = re.sub(r'<(?=\d)', '小於', text)
    text = re.sub(r'\s+vs\s+', '對', text)
    return text


def _sp_arrow_value(value, unit=""):
    """"129.0 → 134.0"（unit="mmHg"）-> "從129 mmHg變成134 mmHg"；
    "1 天 → 4 天"（unit=""）-> "從1天變成4天"（value本身已經帶單位時，
    不再重複補一次unit）。2026-08-28新增：前後數值相同時（例如"0 天 → 0 天"、
    "29% → 29%"），念「從X變成Y」會很奇怪（聽起來像有變化但其實沒有）——
    改成「維持X不變」。"""
    parts = [p.strip() for p in value.split('→')]
    parts = [_sp_pct(p) if p.endswith('%') else _sp_num(p) for p in parts]
    if unit and not any(unit in p for p in parts):
        parts = [f"{p}{unit}" for p in parts]
    if len(parts) == 2:
        # 候選池資料裡同一組before/after有時型別不一致（例如遊走chip是
        # (0, '0 次')，前面是裸數字、後面帶單位字尾），字串比對"0"=="0 次"
        # 會判斷失敗——改成比對開頭的數字本身，數字相同就算「沒變化」，
        # 顯示的文字選兩者中資訊比較完整（字串較長）的那個。
        num0 = re.match(r'-?\d+(\.\d+)?', parts[0])
        num1 = re.match(r'-?\d+(\.\d+)?', parts[1])
        if parts[0] == parts[1] or (num0 and num1 and num0.group() == num1.group()):
            rep = parts[1] if len(parts[1]) >= len(parts[0]) else parts[0]
            return f"維持{rep}不變"
        return f"從{parts[0]}變成{parts[1]}"
    return parts[0]

# 2026-08-20：跟桌面版report_html_v9.py的_spread_tick_labels()同樣的「防止
# 刻度數字互相重疊」演算法，但字寬/緩衝常數重新校準——桌面版是針對89mm
# 欄寬、4.8pt字級算出來的，手機版欄寬(約339px，375px裝置扣掉左右18px
# padding)跟字級(12px)完全不同單位系統，不能直接套用同一組常數。
_MOBILE_TICK_CHAR_WIDTH_PCT = 2.2
_MOBILE_TICK_GAP_BUFFER_PCT = 1.5


def _mobile_spread_tick_labels(ticks):
    if not ticks:
        return []
    positions = [float(ticks[0][0])]
    prev_label = ticks[0][1]
    for pct, label in ticks[1:]:
        min_gap = (
            (len(str(prev_label)) + len(str(label))) / 2 * _MOBILE_TICK_CHAR_WIDTH_PCT
            + _MOBILE_TICK_GAP_BUFFER_PCT
        )
        positions.append(max(float(pct), positions[-1] + min_gap))
        prev_label = label
    return positions

_SECTIONS = [
    ("top_summary", "本月照護重點"),
    ("top3", "偏離最大的3項"),
    ("bp_dist", "收縮壓分布"),
    ("pulse_dist", "脈搏分布"),
    ("aspect_0", "高血壓與心血管"),
    ("aspect_1", "體液代謝與容積"),
    ("aspect_2", "感染監測"),
    ("aspect_3", "衰弱與功能"),
    ("daily_status", "每日狀態與事件"),
    ("check_notes", "查核與說明"),
]


def _e(v):
    return _html.escape(str(v))


def _page_header(h, page_no, speak_text=""):
    # 2026-08-20：朗讀文字用data屬性帶到前端，JS的toggleSpeak()讀這個屬性
    # 餵給SpeechSynthesisUtterance——用data屬性而不是內嵌在onclick字串裡，
    # 是因為朗讀文字可能包含引號/特殊字元，塞進onclick="..."容易破壞HTML。
    return f'''<div class="ph-topbar">
      <div class="ph-name">{_e(h["name"])}<span class="ph-meta">{_e(h["gender"])}・{_e(h["age"])}歲・CFS {_e(h["cfs"])}</span></div>
      <div class="ph-period">{_e(h["period_label"])}</div>
    </div>
    <div class="ph-subbar">
      <button class="ph-speak" type="button" data-speak="{_e(speak_text)}" onclick="toggleSpeak(this)">🔊 朗讀</button>
      <div class="ph-pageno">{page_no}／10</div>
    </div>'''


def _home_page(h):
    rows = "".join(
        f'<button class="home-row" type="button" onclick="showPage({i + 1})">'
        f'<span class="home-row-no">{i + 1}</span><span class="home-row-label">{_e(label)}</span>'
        f'<span class="home-row-arrow">›</span></button>'
        for i, (_key, label) in enumerate(_SECTIONS)
    )
    return f'''<section class="page" id="page-0">
      <div class="home-hero">
        <div class="home-hero-name">{_e(h["name"])}</div>
        <div class="home-hero-meta">{_e(h["gender"])}・{_e(h["age"])}歲・CFS {_e(h["cfs"])}・{_e(h["group"])}</div>
        <div class="home-hero-period">{_e(h["period_label"])}</div>
      </div>
      <div class="home-list">{rows}</div>
    </section>'''


def _speak_top_summary(ts, h):
    parts = [
        f"您好，這是{h['name']}，{h['gender']}，{h['age']}歲，"
        f"CFS衰弱等級{h['cfs']}分，{h['period_label']}。",
        _sp_dates(ts["headline"]),
    ]
    if ts.get("detail"):
        parts.append(_sp_dates(ts["detail"]))
    parts.append("以下請看偏離最大的三項。")
    return _sp_symbols("".join(parts))


def _top_summary_page(ts, h):
    return f'''<section class="page" id="page-1">
      {_page_header(h, 1, _speak_top_summary(ts, h))}
      <div class="content">
        <div class="hero-number">
          <div class="hero-big">{_e(ts["deviation_count"])}</div>
          <div class="hero-big-label">項紀錄<br>偏離參考值</div>
        </div>
        <div class="headline">{_e(ts["headline"])}</div>
        {f'<div class="detail-box"><div class="detail-box-label">集中期間</div><div class="detail-box-text">{_e(ts["detail"])}</div></div>' if ts.get("detail") else ""}
        <button class="cta-btn" type="button" onclick="showPage(2)">先看最重要的3項變化<br><span class="cta-sub">查看偏離最大的3項 ›</span></button>
        <div class="footnote">摘要用於資訊整理與照護參考，不取代醫療診斷。</div>
      </div>
    </section>'''


_ORDINALS = ["第一項", "第二項", "第三項", "第四項", "第五項"]


def _speak_top3(top3):
    if not top3:
        return "本期沒有明顯偏離的項目。"
    sentences = []
    for i, item in enumerate(top3):
        ordinal = _ORDINALS[i] if i < len(_ORDINALS) else f"第{i + 1}項"
        seg = f"{ordinal}，{item['name']}，{_sp_arrow_value(item['value'], item.get('unit'))}，{_sp_delta(item['delta_line'])}"
        if item.get("acute"):
            seg += f"，{_sp_dates(item['acute'])}，列為急性事件，請優先留意"
        elif item.get("qualifier"):
            seg += f"，{_sp_dates(item['qualifier'])}"
        sentences.append(seg + "。")
    return _sp_symbols("偏離最大的三項如下。" + "".join(sentences))


def _top3_page(top3, h):
    if not top3:
        cards = '<div class="empty-note">本期無明顯偏離項目</div>'
    else:
        cards = "".join(_top3_card_mobile(item) for item in top3)
    return f'''<section class="page" id="page-2">
      {_page_header(h, 2, _speak_top3(top3))}
      <div class="content">
        <div class="section-title">偏離最大的{len(top3)}項</div>
        {cards}
        <div class="how-to-read">
          <div class="how-to-read-label">如何閱讀</div>
          <div class="how-to-read-text">箭頭左側為前三月基線，右側為本月結果。橘色表示需要優先留意。</div>
        </div>
      </div>
    </section>'''


def _top3_card_mobile(item):
    # item["value"] 已經是 analysis_v9.py 的 _fmt_item() 組好的
    # "{before} → {after}" 字串，這裡直接顯示，不用自己再切分。
    acute = item.get("acute")
    if acute:
        accent = PALETTE["acute"]
        third_line = f'<span class="mc-acute">{_e(acute)}</span><span class="mc-badge mc-badge-acute">急性事件</span>'
    else:
        is_normal = item["delta_line"].endswith("正常")
        accent = PALETTE["green"] if is_normal else PALETTE["orange"]
        third = _e(item["delta_line"])
        if item.get("qualifier"):
            third += f"・{_e(item['qualifier'])}"
        third_line = f'<span style="color:{accent};">{third}</span>'
    return f'''<div class="mc-card" style="border-left-color:{accent};">
      <div class="mc-title">{_e(item["name"])}</div>
      <div class="mc-value" style="color:{accent};">{_e(item["value"])}<span class="mc-unit">{_e(item["unit"])}</span></div>
      <div class="mc-ref">{_e(item["ref_line"])}</div>
      <div class="mc-third">{third_line}</div>
    </div>'''


def _speak_count_label(count_label):
    """count_label裡的「／」是「共X次、涵蓋Y天」的意思（次數/天數），跟
    警示chip「≥160／<90」的「或」是兩種完全不同的語意，不能套用同一條
    全域轉換規則——這裡在_sp_symbols()的全域「／→或」規則生效之前，
    先把這個已知格式處理成正確的口語說法。"""
    return re.sub(r'(\d+)\s*次量測／(\d+)\s*天', r'共\1次量測，涵蓋\2天', count_label)


def _speak_dist(title, dist):
    parts = [f"本頁呈現{title}。{_sp_dates(_speak_count_label(dist['count_label']))}。"]
    for label, val, sub in dist["chips"]:
        label_sp = _sp_dates(label)
        val_sp = _sp_pct(val) if str(val).endswith('%') else _sp_num(val)
        seg = f"{label_sp}，{val_sp}"
        if sub:
            seg += f"，{_sp_dates(sub)}"
        parts.append(seg + "。")
    return _sp_symbols("".join(parts))


def _dist_page(title, dist, page_key, page_no, h, footnote):
    segs = "".join(f'<div style="width:{pct}%;background:{col};"></div>' for pct, col in dist["segments"])
    # 2026-08-20修正：直接用dist["ticks"]的原始%位置畫刻度數字，血壓/脈搏
    # 分布常見「90」「100」這種相鄰刻度只差幾%的情況，數字會疊在一起——
    # 跟桌面版同樣道理，用_visible_ticks_5tier()先濾掉沒有資料的層級對應
    # 的外側刻度，再用_mobile_spread_tick_labels()把數字位置往右推開到
    # 不重疊為止（刻度線本身仍精準畫在原始%，只有數字文字的位置被調整）。
    tick_visibility = _visible_ticks_5tier(dist["segments"])
    visible_ticks = (
        [t for t, v in zip(dist["ticks"], tick_visibility) if v]
        if len(tick_visibility) == len(dist["ticks"]) else dist["ticks"]
    )
    tick_lines = "".join(
        f'<div class="dist-tick-line" style="left:{pct}%;"></div>' for pct, _label in visible_ticks
    )
    label_positions = _mobile_spread_tick_labels(visible_ticks)
    tick_labels = "".join(
        f'<div class="dist-tick" style="left:{pos}%;">{_e(label)}</div>'
        for pos, (_pct, label) in zip(label_positions, visible_ticks)
    )
    ticks = tick_lines + tick_labels
    chips = "".join(
        f'''<div class="dist-chip{" dist-chip-warn" if ("≥" in str(label) or "<9" in str(label)) else ""}">
          <div class="dist-chip-label">{_e(label)}</div>
          <div class="dist-chip-val">{_e(val)}</div>
          {f'<div class="dist-chip-sub">{_e(sub)}</div>' if sub else ""}
        </div>'''
        for label, val, sub in dist["chips"]
    )
    badge = '<span class="mc-badge mc-badge-warn">樣本有限</span>' if dist.get("sample_limited") else ""
    return f'''<section class="page" id="page-{page_no}">
      {_page_header(h, page_no, _speak_dist(title, dist))}
      <div class="content">
        <div class="section-title">{_e(title)}{badge}</div>
        <div class="dist-count">{_e(dist["count_label"])}</div>
        <div class="dist-bar">{segs}</div>
        <div class="dist-ticks">{ticks}</div>
        <div class="dist-chips">{chips}</div>
        <div class="how-to-read">
          <div class="how-to-read-label">閱讀提醒</div>
          <div class="how-to-read-text">{_e(footnote)}</div>
        </div>
      </div>
    </section>'''


def _speak_aspect(card):
    parts = [f"{card['title']}面向，比較{card['period_label']}。"]
    primary_before = card.get("primary_before")
    primary_after = card["primary_after"]
    if primary_before not in (None, ""):
        arrow_str = f"{primary_before} → {primary_after}"
        parts.append(f"{card['primary_label']}{_sp_arrow_value(arrow_str)}。")
    else:
        parts.append(f"{card['primary_label']}{_sp_num(primary_after)}。")
    if card.get("acute"):
        parts.append(f"{_sp_dates(card['acute'])}，列為急性事件。")
    for label, before, after in card["chips"]:
        if before not in (None, ""):
            arrow_str = f"{before} → {after}"
            parts.append(f"{label}{_sp_arrow_value(arrow_str)}。")
        else:
            parts.append(f"{label}{_sp_num(after)}。")
    for t in card["trend_note"]:
        parts.append(_sp_dates(t) + "。")
    return _sp_symbols("".join(parts))


def _aspect_page(card, idx, h):
    page_no = 5 + idx
    chips = "".join(
        f'''<div class="chip-box">
          <div class="chip-box-label">{_e(label)}</div>
          <div class="chip-box-val">{f'{_e(before)} → ' if before not in (None, "") else ""}<span style="color:{PALETTE["orange"]};">{_e(after)}</span></div>
        </div>'''
        for label, before, after in card["chips"]
    )
    trend = "".join(f'<div class="trend-line">・{_e(t)}</div>' for t in card["trend_note"])
    primary_before = card.get("primary_before")
    primary_html = (
        f'<span class="mc-before">{_e(primary_before)}</span> <span class="mc-arrow">→</span> <span class="mc-after" style="color:{PALETTE["orange"]};">{_e(card["primary_after"])}</span>'
        if primary_before not in (None, "") else f'<span class="mc-after" style="color:{PALETTE["orange"]};">{_e(card["primary_after"])}</span>'
    )
    badges = ""
    if card.get("acute"):
        badges += '<span class="mc-badge mc-badge-acute">急性事件</span>'
    if card.get("sample_limited"):
        badges += '<span class="mc-badge mc-badge-warn">樣本有限</span>'
    return f'''<section class="page" id="page-{page_no}">
      {_page_header(h, page_no, _speak_aspect(card))}
      <div class="content">
        <div class="section-title">{_e(card["title"])}</div>
        <div class="aspect-period">{_e(card["period_label"])}</div>
        <div class="aspect-primary">
          <div class="aspect-primary-label">{_e(card["primary_label"])}{badges}</div>
          <div class="aspect-primary-val">{primary_html}</div>
        </div>
        <div class="chip-grid">{chips}</div>
        <div class="trend-box">
          <div class="trend-box-label">趨勢註記</div>
          {trend}
        </div>
      </div>
    </section>'''


def _speak_daily_status(ds):
    parts = ["每日狀態與事件。"]
    for row in ds["rows"]:
        parts.append(f"{row['label']}，{row['note']}。")
    legend_items = ds["events_row"]["legend_items"]
    if legend_items:
        events = "，".join(f"{item['label']}{item['count']}次" for item in legend_items)
        parts.append(f"本月行為事件：{events}。")
    else:
        parts.append("本月沒有任何行為事件記錄。")
    return _sp_symbols("".join(parts))


def _daily_status_page(ds, h):
    # 2026-08-20修正：天數格子grid-template-columns原本寫死repeat(30,...)，
    # 當月天數不是30天時（2月、31天月份、或資料本身沒滿一個月）欄位會對
    # 不齊——改成依實際days動態帶inline style，min-width也跟著天數等比例
    # 給（每天至少18px觸控寬度），不再用固定600px。
    days = ds["days"]
    grid_style = f"grid-template-columns:repeat({days},1fr);min-width:{days * 18}px;"
    ticks_html = "".join(
        f'<div class="ds-tick">{_e(ds["date_ticks"].get(i, "").replace("-", "/"))}</div>' for i in range(days)
    )
    rows_html = ""
    for row in ds["rows"]:
        cells = "".join(
            f'<div class="ds-cell" style="background:{col or PALETTE["mute"]};"></div>' for col in row["colors"]
        )
        # 2026-08-20修正：row-note(「異常N次」這類右側註記)原本擠進同一個
        # 會橫向捲動的flex容器裡，捲到最後幾天時右側註記文字才會出現、
        # 使用者得先捲到底才看得到——改成獨立顯示在捲動區塊右側外面，
        # 固定可見，不用跟著捲。
        rows_html += f'''<div class="ds-row">
          <div class="ds-row-label">{_e(row["label"])}</div>
          <div class="ds-row-cells" style="{grid_style}">{cells}</div>
        </div>'''
    notes_html = "".join(
        f'<div class="ds-note-item"><b>{_e(row["label"])}</b>{_e(row["note"])}</div>' for row in ds["rows"]
    )
    ev = ds["events_row"]
    ev_cells = "".join(
        '<div class="ds-ev-cell">' + "".join(f'<span style="color:{color};">{_e(g)}</span>' for g, color in icons) + '</div>'
        for icons in ev["icons_by_day"]
    )
    ev_cells_style = f"grid-template-columns:repeat({days},1fr);min-width:{days * 18}px;"
    legend = "".join(
        f'<div class="ds-legend-item"><span style="color:{item["color"]};">{_e(item["glyph"])}</span> {_e(item["label"])}（{item["count"]}次）</div>'
        for item in ev["legend_items"]
    ) or '<div class="ds-legend-item">本月無行為事件記錄</div>'
    return f'''<section class="page" id="page-9">
      {_page_header(h, 9, _speak_daily_status(ds))}
      <div class="content">
        <div class="section-title">每日狀態與事件</div>
        <div class="ds-legend-key">綠＝正常　灰＝異常　橘＝集中期異常</div>
        <div class="ds-scroll-hint">← 左右滑動查看整月 →</div>
        <div class="ds-scroll">
          <div class="ds-row"><div class="ds-row-label ds-row-label-spacer"></div><div class="ds-ticks" style="{grid_style}">{ticks_html}</div></div>
          {rows_html}
          <div class="ds-row">
            <div class="ds-row-label">偶發事件</div>
            <div class="ds-row-cells" style="{ev_cells_style}">{ev_cells}</div>
          </div>
        </div>
        <div class="ds-notes">{notes_html}</div>
        <div class="ds-legend">{legend}</div>
        <div class="how-to-read">
          <div class="how-to-read-text">手機版先呈現週期概覽；點選任一列可查看每日明細。</div>
        </div>
      </div>
    </section>'''


def _speak_check_notes(cn):
    parts = ["查核與說明。"]
    remaining = cn["remaining_deviations"]
    if remaining:
        items = "；".join(f"{name}，{_sp_delta(val)}" for name, val, *_rest in remaining)
        parts.append(f"除了前面提到的項目，還有其餘偏離：{items}。")
    completeness = "、".join(f"{label}{_sp_num(val)}" for label, val, *_rest in cn["completeness"])
    period = cn.get("completeness_period", "")
    parts.append(f"紀錄完整度方面（{_sp_dates(period)}）：{completeness}。" if period else f"紀錄完整度方面：{completeness}。")
    baseline = "，".join(f"{label}採用{_sp_dates(val)}" if not isinstance(val, list) else label for label, val in cn["baseline_todo"])
    parts.append(f"比較基準：{baseline}。")
    # 2026-08-20使用者定案：朗讀版免責聲明用精簡口語版，不逐字照念畫面上
    # 較正式的完整法律免責文字（fo["disclaimer_intro"/"disclaimer_key"]），
    # 兩者刻意不同调——畫面文字要嚴謹完整，朗讀給長者/家屬聽的版本要簡短
    # 好懂，意思等價但不是同一句話。
    parts.append("本頁僅為照護紀錄彙整，不作為醫療診斷，請以醫師判斷為準。")
    return _sp_symbols("".join(parts))


def _check_notes_page(cn, fo, h):
    remaining = "".join(
        f'''<div class="cn-item"><b>{_e(name)}</b> <span style="color:{PALETTE["orange"] if val.startswith("偏離") else PALETTE["green"]};">{_e(val)}</span></div>'''
        for name, val, *_rest in cn["remaining_deviations"]
    ) or '<div class="empty-note">（無其餘顯著偏離）</div>'
    completeness = "".join(
        f'<div class="cn-item"><b>{_e(label)}</b> {_e(val)}</div>' for label, val, *_rest in cn["completeness"]
    )
    baseline = "".join(
        f'<div class="cn-item"><b>{_e(label)}</b> {_e(val) if not isinstance(val, list) else "、".join(val)}</div>'
        for label, val in cn["baseline_todo"]
    )
    return f'''<section class="page" id="page-10">
      {_page_header(h, 10, _speak_check_notes(cn))}
      <div class="content">
        <div class="section-title">查核與說明</div>
        <div class="cn-block">
          <div class="cn-block-label">其餘偏離{len(cn["remaining_deviations"])}項</div>
          {remaining}
        </div>
        <div class="cn-block">
          <div class="cn-block-label">紀錄完整度<span style="font-weight:400;color:{PALETTE["mute"]};margin-left:1mm;">（{_e(cn.get("completeness_period", ""))}）</span></div>
          {completeness}
        </div>
        <div class="cn-block">
          <div class="cn-block-label">比較基準</div>
          {baseline}
        </div>
        <div class="disclaimer">
          <div>{_e(fo["disclaimer_intro"])}{_e(fo["disclaimer_key"])}</div>
          <div class="disclaimer-note">{_e(fo["disclaimer_note"])}</div>
        </div>
      </div>
    </section>'''


_CSS = '''
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; background:#EEF2F0; }
  body { font-family:"Microsoft JhengHei","PingFang TC","Heiti TC",sans-serif; color:%(head)s; }
  .app { max-width:480px; margin:0 auto; background:#fff; min-height:100vh; display:flex; flex-direction:column; }
  .page { display:none; flex:1; flex-direction:column; }
  .page.active { display:flex; }
  .ph-topbar { display:flex; justify-content:space-between; align-items:baseline; padding:16px 18px 4px; }
  .ph-name { font-size:20px; font-weight:700; }
  .ph-meta { font-size:14px; color:%(sub)s; margin-left:10px; font-weight:400; }
  .ph-period { font-size:13px; color:%(teal)s; font-weight:700; white-space:nowrap; }
  .ph-subbar { display:flex; justify-content:space-between; align-items:center; padding:6px 18px 14px; border-bottom:2px solid %(teal)s; }
  .ph-speak { font-size:15px; padding:8px 16px; border-radius:20px; border:1.5px solid %(teal)s; background:#fff; color:%(teal)s; font-weight:700; }
  .ph-speak:active { background:%(teal)s; color:#fff; }
  .ph-pageno { font-size:14px; color:%(mute)s; font-weight:700; }
  .content { padding:18px 18px 100px; flex:1; }
  .section-title { font-size:22px; font-weight:700; color:%(teal)s; margin-bottom:14px; }

  /* 首頁 */
  .home-hero { background:%(teal)s; color:#fff; padding:28px 22px 24px; }
  .home-hero-name { font-size:26px; font-weight:700; }
  .home-hero-meta { font-size:14px; opacity:.85; margin-top:4px; }
  .home-hero-period { font-size:15px; margin-top:10px; font-weight:700; }
  .home-list { padding:10px 14px 30px; display:flex; flex-direction:column; gap:10px; }
  .home-row { display:flex; align-items:center; gap:14px; background:%(cardbg)s; border:none; border-radius:12px;
              padding:16px 16px; font-size:17px; font-weight:700; color:%(head)s; text-align:left; width:100%%; }
  .home-row-no { width:28px; height:28px; border-radius:50%%; background:%(teal)s; color:#fff; font-size:14px;
                 display:flex; align-items:center; justify-content:center; flex-shrink:0; }
  .home-row-label { flex:1; }
  .home-row-arrow { color:%(mute)s; font-size:20px; }
  .home-row:active { background:%(warnbg)s; }

  /* 本月照護重點 */
  .hero-number { display:flex; align-items:center; gap:14px; padding:10px 0 18px; border-bottom:1px solid %(border)s; margin-bottom:16px; }
  .hero-big { font-size:56px; font-weight:900; color:%(orange)s; line-height:1; }
  .hero-big-label { font-size:17px; font-weight:700; line-height:1.3; }
  .headline { font-size:19px; font-weight:700; line-height:1.5; margin-bottom:14px; }
  .detail-box { background:%(warnbg)s; border-radius:10px; padding:12px 14px; margin-bottom:20px; }
  .detail-box-label { font-size:13px; font-weight:700; color:%(orange)s; margin-bottom:4px; }
  .detail-box-text { font-size:15px; line-height:1.5; color:%(head)s; }
  .cta-btn { width:100%%; background:%(teal)s; color:#fff; border:none; border-radius:14px; padding:18px 16px;
             font-size:18px; font-weight:700; line-height:1.4; margin-bottom:16px; }
  .cta-btn:active { background:%(accent)s; }
  .cta-sub { font-size:14px; font-weight:400; opacity:.9; }
  .footnote { font-size:13px; color:%(mute)s; text-align:center; }

  /* top3 卡片 */
  .mc-card { border-left:6px solid %(orange)s; background:%(cardbg)s; border-radius:0 10px 10px 0; padding:16px 16px; margin-bottom:14px; }
  .mc-title { font-size:16px; font-weight:700; margin-bottom:4px; }
  .mc-value { font-size:26px; font-weight:700; margin-bottom:6px; }
  .mc-unit { font-size:14px; font-weight:400; color:%(sub)s; margin-left:6px; }
  .mc-ref { font-size:13px; color:%(sub)s; margin-bottom:4px; }
  .mc-third { font-size:15px; font-weight:700; }
  .mc-before { color:%(mute)s; }
  .mc-arrow { color:%(mute)s; }
  .mc-acute { color:%(acute)s; font-weight:700; }
  .mc-badge { display:inline-block; font-size:12px; font-weight:700; border-radius:10px; padding:2px 10px; margin-left:8px; }
  .mc-badge-warn { color:%(orange)s; background:%(warnbg)s; border:1px solid %(orange)s; }
  .mc-badge-acute { color:%(acute)s; background:%(acutebg)s; border:1px solid %(acute)s; }
  .how-to-read { background:%(todobg)s; border-radius:10px; padding:14px 16px; margin-top:10px; }
  .how-to-read-label { font-size:13px; font-weight:700; color:%(sub)s; margin-bottom:4px; }
  .how-to-read-text { font-size:14px; color:%(sub)s; line-height:1.5; }
  .empty-note { font-size:15px; color:%(mute)s; padding:20px 0; text-align:center; }

  /* 分布圖 */
  .dist-count { font-size:14px; color:%(sub)s; margin-bottom:10px; }
  .dist-bar { display:flex; height:20px; border-radius:10px; overflow:hidden; margin-bottom:8px; }
  .dist-ticks { position:relative; height:24px; font-size:12px; color:%(sub)s; margin-bottom:16px; }
  .dist-tick { position:absolute; top:8px; transform:translateX(-50%%); white-space:nowrap; }
  .dist-tick-line { position:absolute; top:0; width:0; height:6px; border-left:1px solid %(mute)s; transform:translateX(-50%%); }
  .dist-chips { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .dist-chip { background:%(cardbg)s; border-radius:10px; padding:12px 14px; }
  .dist-chip-warn { background:%(warnbg)s; }
  .dist-chip-label { font-size:13px; color:%(sub)s; }
  .dist-chip-warn .dist-chip-label { color:%(orange)s; }
  .dist-chip-val { font-size:20px; font-weight:700; margin-top:2px; }
  .dist-chip-warn .dist-chip-val { color:%(orange)s; }
  .dist-chip-sub { font-size:13px; color:%(sub)s; margin-top:2px; }

  /* 四面向卡 */
  .aspect-period { font-size:13px; color:%(mute)s; margin-bottom:16px; }
  .aspect-primary { background:%(cardbg)s; border-radius:12px; padding:16px; margin-bottom:16px; }
  .aspect-primary-label { font-size:14px; color:%(sub)s; margin-bottom:6px; }
  .aspect-primary-val { font-size:28px; font-weight:700; }
  .chip-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:18px; }
  .chip-box { background:%(cardbg)s; border-radius:10px; padding:12px 14px; }
  .chip-box-label { font-size:13px; color:%(sub)s; }
  .chip-box-val { font-size:17px; font-weight:700; margin-top:4px; }
  .trend-box { background:%(todobg)s; border-radius:10px; padding:14px 16px; }
  .trend-box-label { font-size:13px; font-weight:700; color:%(sub)s; margin-bottom:6px; }
  .trend-line { font-size:14px; color:%(sub)s; line-height:1.6; }

  /* 每日狀態 */
  .ds-legend-key { font-size:13px; color:%(mute)s; margin-bottom:8px; }
  .ds-scroll-hint { font-size:12px; color:%(teal)s; font-weight:700; text-align:center; margin-bottom:6px; }
  .ds-scroll { overflow-x:auto; padding-bottom:6px; }
  .ds-ticks { display:grid; font-size:11px; color:%(mute)s; }
  .ds-tick { text-align:center; }
  .ds-row { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
  /* 2026-08-20修正：標籤原本是ds-scroll內部flex子項，橫向捲動時會跟著
     格子一起被捲走，捲到後面幾天就看不出這一列是「精神」還是「排泄」——
     改成position:sticky釘在捲動視窗左緣，捲動的只有格子本身。背景色蓋住
     格子，避免捲動時格子從標籤下方透出來。 */
  .ds-row-label { flex:0 0 58px; font-size:13px; font-weight:700; position:sticky; left:0; z-index:1;
                  background:#fff; padding-right:4px; }
  .ds-row-label-spacer { background:transparent; }
  .ds-row-cells { flex:0 0 auto; display:grid; gap:2px; }
  .ds-cell { height:18px; border-radius:3px; }
  .ds-ev-cell { min-height:18px; display:flex; align-items:center; justify-content:center; font-size:12px; }
  .ds-notes { margin-top:10px; display:flex; flex-direction:column; gap:4px; }
  .ds-note-item { font-size:13px; color:%(sub)s; }
  .ds-note-item b { color:%(head)s; font-weight:700; margin-right:6px; }
  .ds-legend { margin-top:10px; font-size:13px; color:%(sub)s; line-height:1.8; }

  /* 查核與說明 */
  .cn-block { margin-bottom:18px; }
  .cn-block-label { font-size:14px; font-weight:700; color:%(sub)s; margin-bottom:8px; }
  .cn-item { font-size:14px; color:%(sub)s; background:%(cardbg)s; border-radius:8px; padding:10px 12px; margin-bottom:6px; }
  .cn-item b { color:%(head)s; font-weight:700; margin-right:6px; }
  .disclaimer { font-size:12px; color:%(sub)s; line-height:1.6; border-top:1px solid %(teal)s; padding-top:12px; margin-top:10px; }
  .disclaimer-note { margin-top:4px; }

  /* 底部導覽 */
  .bottom-nav { position:sticky; bottom:0; left:0; right:0; display:flex; gap:10px; padding:12px 16px;
                background:#fff; border-top:1px solid %(border)s; max-width:480px; margin:0 auto; width:100%%; }
  .nav-btn { flex:1; font-size:16px; font-weight:700; padding:14px 10px; border-radius:12px; border:1.5px solid %(teal)s;
             background:#fff; color:%(teal)s; }
  .nav-btn.primary { background:%(teal)s; color:#fff; }
  .nav-btn:disabled { opacity:.35; }
'''


def render_v9_mobile_html(vm: dict) -> str:
    h = vm["header"]
    ts = vm["top_summary"]
    css = _CSS % PALETTE

    pages = [_home_page(h)]
    pages.append(_top_summary_page(ts, h))
    pages.append(_top3_page(vm["top3"], h))
    pages.append(_dist_page("收縮壓分布", vm["bp_distribution"], "bp_dist", 3, h,
                             "本頁呈現量測分布；個別數值與身體狀況仍需由醫療專業人員判斷。"))
    pages.append(_dist_page("脈搏分布", vm["pulse_distribution"], "pulse_dist", 4, h,
                             "本頁呈現量測分布；個別數值與身體狀況仍需由醫療專業人員判斷。"))
    for idx, card in enumerate(vm["four_aspects"]):
        pages.append(_aspect_page(card, idx, h))
    pages.append(_daily_status_page(vm["daily_status"], h))
    pages.append(_check_notes_page(vm["check_notes"], vm["footer"], h))

    pages_html = "\n".join(pages)

    return f'''<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>AI 照護月摘要（手機高齡友善版）</title>
<style>{css}</style>
</head>
<body>
<div class="app">
  {pages_html}
  <div class="bottom-nav" id="bottomNav" style="display:none;">
    <button class="nav-btn" id="prevBtn" type="button" onclick="prevPage()">‹ 上一頁</button>
    <button class="nav-btn" id="homeBtn" type="button" onclick="showPage(0)">回首頁</button>
    <button class="nav-btn primary" id="nextBtn" type="button" onclick="nextPage()">下一頁 ›</button>
  </div>
</div>
<script>
var TOTAL_PAGES = 10;
var current = 0;

function showPage(n) {{
  // 2026-08-20新增：換頁時如果上一頁的朗讀還在播放，要先停掉——不然使用者
  // 切到第5頁，耳朵卻還在聽第2頁念的內容，畫面跟聲音對不起來。
  stopSpeak();
  document.querySelectorAll('.page').forEach(function(p) {{ p.classList.remove('active'); }});
  document.getElementById('page-' + n).classList.add('active');
  current = n;
  var nav = document.getElementById('bottomNav');
  if (n === 0) {{
    nav.style.display = 'none';
  }} else {{
    nav.style.display = 'flex';
    document.getElementById('prevBtn').disabled = (n <= 1);
    document.getElementById('nextBtn').disabled = (n >= TOTAL_PAGES);
  }}
  window.scrollTo(0, 0);
}}

function nextPage() {{ if (current < TOTAL_PAGES) showPage(current + 1); }}
function prevPage() {{ if (current > 1) showPage(current - 1); }}

// 朗讀功能（2026-08-20接上瀏覽器內建語音合成 Web Speech API）。
// 已知風險：LINE內嵌瀏覽器（尤其iOS）過去對Web Speech API支援不完整，
// 桌面瀏覽器測過能出聲音不代表LINE環境也能用，需要另外在真機LINE裡實測。
// 2026-08-28修正：真機實測抓到「第一次有聲音、後面幾次沒聲音」的問題，
// 根源是WebKit（iOS上LINE內嵌瀏覽器用的引擎）有個已知bug——對著「其實
// 沒有東西在播放」的speechSynthesis引擎重複呼叫cancel()，幾次之後引擎
// 會卡死，之後speak()呼叫會被靜默吃掉、不出聲也不報錯。原本showPage()
// 每次換頁都無條件呼叫cancel()，即使根本沒在播放，正好會踩到這個bug。
// 改法：(1) 只在真的有東西在播放/排隊時才呼叫cancel()；(2) 真的取消後，
// 給引擎一點緩衝時間再開始念新內容，不要cancel()跟speak()前後腳緊貼著呼叫。
var speakBtn = null; // 記錄目前正在朗讀的按鈕，換頁/再次點擊時要能找到它復原文字

function stopSpeak() {{
  if ('speechSynthesis' in window && (window.speechSynthesis.speaking || window.speechSynthesis.pending)) {{
    window.speechSynthesis.cancel();
  }}
  if (speakBtn) {{
    speakBtn.textContent = '🔊 朗讀';
    speakBtn = null;
  }}
}}

function toggleSpeak(btn) {{
  if (!('speechSynthesis' in window)) {{
    alert('這個瀏覽器不支援語音朗讀功能');
    return;
  }}
  // 同一顆按鈕再點一次＝停止；點別頁的按鈕＝先停掉舊的再念新的
  var wasSpeakingThisBtn = (speakBtn === btn);
  var hadSomethingPlaying = window.speechSynthesis.speaking || window.speechSynthesis.pending;
  stopSpeak();
  if (wasSpeakingThisBtn) {{
    return;
  }}
  var text = btn.getAttribute('data-speak');
  if (!text) {{
    return;
  }}
  var startSpeaking = function() {{
    var utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'zh-TW';
    utterance.onend = function() {{
      if (speakBtn === btn) {{
        btn.textContent = '🔊 朗讀';
        speakBtn = null;
      }}
    }};
    utterance.onerror = function() {{
      if (speakBtn === btn) {{
        btn.textContent = '🔊 朗讀';
        speakBtn = null;
      }}
    }};
    speakBtn = btn;
    btn.textContent = '⏸ 停止朗讀';
    window.speechSynthesis.speak(utterance);
  }};
  // 剛剛真的取消過上一段朗讀時，給WebKit一點時間把cancel()處理完，
  // 不要跟speak()前後腳緊貼著呼叫，避免新的一段被吃掉。
  if (hadSomethingPlaying) {{
    setTimeout(startSpeaking, 120);
  }} else {{
    startSpeaking();
  }}
}}

showPage(0);
</script>
</body>
</html>
'''


def generate_v9_mobile_html(vm: dict, output_path: str):
    html_str = render_v9_mobile_html(vm)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True) if os.path.dirname(output_path) else None
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_str)
    print(f"[report_html_mobile_v9] 手機版（互動HTML）已儲存：{output_path}")
    return output_path
