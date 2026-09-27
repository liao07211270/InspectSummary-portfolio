"""
v9 版面示意稿的假資料產生器。

只做「版面驗證用」的資料模型，數值預設沿用 `care-summary-v9.html`（主管提供的
設計稿）裡的範例數字，讓 PDF 產出可以直接跟原始 HTML 逐項核對版面是否一致。

跟 `mock_data.py`（現有 v2 report 的假資料）刻意分開成獨立檔案，避免動到舊版
report_pdf.py 目前吃的資料格式。

**目前狀態（2026-07-29）**：這是「先做版面、之後再接資料」階段的產物——
四個面向卡的「前三月 → 本月」比較、每日狀態的精神/情緒/睡眠/排泄，
系統目前都還沒有真實資料來源（見 [[project_inspectsummary_v9_redesign_task]]
的兩個待討論問題）。這裡的數字全部是佔位假資料，不要被拿去當真實輸出使用。
"""
import random
from datetime import date, timedelta

# 事件圖示（跟 v9 html 圖例一致）
EVENT_ICONS = {
    "拒服": ("X", "#5C6B68"),
    "遊走": ("◆", "#D85A30"),
    "拒絕照顧": ("▲", "#A32D2D"),
    "通報": ("●", "#0F5F54"),
}

# 每日狀態的顏色語意（跟 v9 html 一致）：綠=正常，黃=異常，橘=異常且落在偏離集中期
# 2026-08-12：異常從灰色改成黃色，跟analysis_v9.py的_COLOR_ABNORMAL同步
# （這裡先前是"#C9CFC8"淺灰，其實從沒跟到2026-08-07那次深灰的調整——
# mock demo路徑跟真實資料路徑的這個顏色本來就已經不同步，這次一併對齊）。
COLOR_OK = "#639922"
COLOR_ABNORMAL = "#D9A521"
COLOR_ABNORMAL_CLUSTER = "#D85A30"


def _fmt_md(d: date) -> str:
    return f"{d.month}/{d.day}"


def generate_v9_view_model(seed: int = 42, days: int = 30, start: str = "2025-11-01",
                            patient: str = "王○○") -> dict:
    rng = random.Random(seed)
    start_date = date.fromisoformat(start)
    dates = [start_date + timedelta(days=i) for i in range(days)]

    # 偏離集中期：固定落在本期中段（v9 html 範例是 11/14–11/21，這裡用比例定位，
    # 讓 --days 改變時集中期還是落在合理位置）。
    cluster_start_i = int(days * 0.44)
    cluster_end_i = min(days - 1, cluster_start_i + 7)

    header = {
        "name": patient, "gender": "男", "age": 82, "cfs": 5, "group": "高齡高血壓組",
        "period_label": f"{start_date.year} 年 {start_date.month} 月・照護摘要",
    }

    top_summary = {
        "deviation_count": 6,
        "headline": f"偏離集中在 {_fmt_md(dates[cluster_start_i])}–{_fmt_md(dates[cluster_end_i])}，"
                    f"{cluster_end_i - cluster_start_i + 1} 天內心率↑、血氧↓、活動↓、精神轉差多項同向。",
        "detail": f"{_fmt_md(dates[cluster_start_i + 6])} 精神、睡眠、排泄同日異常，"
                  "並有遊走與異常通報。系統僅挑出偏離與同時發生的項目，不作病因推論。",
    }

    top3 = [
        {"name": "靜態心率", "value": "70 → 88", "unit": "次/分",
         "ref_line": "個人基線 72", "delta_line": "偏離 +22%・11 月中旬起"},
        {"name": "體重・近六個月", "value": "60.0 → 57.0", "unit": "kg",
         "ref_line": "參考值 減輕 ≥5%", "delta_line": "實際 -5.0%・已達界線"},
        {"name": "收縮壓", "value": "月均 144", "unit": "mmHg",
         "ref_line": "目標上限 130・基線 133", "delta_line": "整月上行"},
    ]

    # 2026-08-12修正：這裡沿用的是2026-08-07醫師會議改版前的四級門檻
    # (90/140/180)跟三個chip，跟真實資料路徑(analysis_v9.py)現行的90/
    # 100/130/160五級＋合併危急chip格式已經對不起來，是使用者實測mockup
    # 版面時抓到的真bug——mock demo這條路徑當時沒有跟著改版更新。
    bp_distribution = {
        "count_label": "42 次量測／26 天・月均 144 mmHg",
        "segments": [(1, "#D85A30"), (5, "#EF9F27"), (61, "#639922"), (28, "#EF9F27"), (5, "#D85A30")],
        "ticks": [(1, "90"), (6, "100"), (67, "130"), (95, "160")],
        "chips": [("目標內", "61%", "26 次"), ("偏高", "28%", "12 次"), ("偏低", "5%", "2 次"),
                  ("≥160／<90 mmHg", "2 次", f"{_fmt_md(dates[cluster_end_i])}、{_fmt_md(dates[-2])}")],
    }
    spo2_distribution = {
        "count_label": "24 次量測",
        "segments": [(46, "#639922"), (25, "#EF9F27"), (27.8, "#D85A30"), (1.2, "#A32D2D")],
        "ticks": [(46, "95"), (71, "94"), (98.8, "90")],
        "chips": [("≥95%", "46%", None), ("<94%", "7 次", "29%"), ("<90%", "0 次", None)],
    }
    # 2026-08-07新增、2026-08-12改版：脈搏分布（依醫師會議確認，取代原本
    # 「本月量測分布」的血氧分布區域），五級門檻極低<50／偏低[50,60)／
    # 正常[60,101)／偏高[101,120)／極高>=120，色塊仍是五段，但chip呈現
    # 改成比照收縮壓分布的四欄格式——極低/極高合併成一個危急風格的chip
    # 「≥120／<50 次/分」，跟真實資料路徑(analysis_v9.py的
    # pulse_distribution)同一套格式契約，這裡是mock demo專用假資料，
    # 各自獨立算，只是要滿足render_v9_html()共用的vm欄位契約。
    pulse_distribution = {
        "count_label": "42 次量測／26 天",
        "segments": [(2.4, "#D85A30"), (9.5, "#EF9F27"), (78.6, "#639922"), (7.1, "#EF9F27"), (2.4, "#D85A30")],
        "ticks": [(2.4, "50"), (11.9, "60"), (90.5, "101"), (97.6, "120")],
        "chips": [("正常", "78.6%", "33 次"),
                  ("偏高", "7.1%", "3 次"),
                  ("偏低", "9.5%", "4 次"),
                  ("≥120／<50 次/分", "2 次", f"{_fmt_md(dates[0])}、{_fmt_md(dates[cluster_end_i])}")],
    }

    four_aspects = [
        {"title": "高血壓與心血管", "period_label": "前三月 → 本月", "accent": "#147D6F",
         "primary_label": "收縮壓達標率", "primary_before": "81%", "primary_after": "67%",
         "chips": [("≥160 危急", "0", "2 次"), ("偏高比例", "19%", "28%")],
         "trend_note": ["月均 144 mmHg，較前三月上升（133 → 144）"]},
        {"title": "體液代謝與容積", "period_label": "前三月 → 本月", "accent": "#147D6F",
         "primary_label": "飲水評估偏少", "primary_before": "3 天", "primary_after": "9 天",
         "chips": [("尿色偏深", "0", "6 天"), ("排泄異常", "1 天", "4 天")],
         "trend_note": [f"集中於 {_fmt_md(dates[cluster_start_i])} 起", "體重每月一次，短期容積無法判讀"]},
        {"title": "感染監測", "period_label": "前三月 → 本月", "accent": "#147D6F",
         "primary_label": "精神不佳", "primary_before": "1 天", "primary_after": "5 天",
         "chips": [("體溫偏離基線", "0", "2 天"), ("遊走", "0", "3 次")],
         "trend_note": [f"{_fmt_md(dates[cluster_start_i + 6])} 精神、食慾、活動同日轉差，並有通報"]},
        {"title": "衰弱與功能", "period_label": "長期趨勢・對標 Fried", "accent": "#D85A30",
         "primary_label": "體重・六個月", "primary_before": "60.0", "primary_after": "57.0 kg",
         "chips": [("進食量", "全吃", "偏少"), ("活動投入", "每週 4", "1–2 次")],
         "trend_note": ["六個月減輕 5.0%，達顯著界", "體重、進食、活動同向下行"]},
    ]

    # ── 每日狀態四列（精神/情緒/睡眠/排泄）：目前無真實資料來源，用假資料佔位 ──
    def _row(base_abnormal_days, note_label):
        colors = []
        abnormal_idx = set(rng.sample(range(days), min(base_abnormal_days, days)))
        for i in range(days):
            if i in abnormal_idx:
                in_cluster = cluster_start_i <= i <= cluster_end_i
                colors.append(COLOR_ABNORMAL_CLUSTER if in_cluster else COLOR_ABNORMAL)
            else:
                colors.append(COLOR_OK)
        return {"label": note_label[0], "colors": colors, "note": note_label[1]}

    daily_status_rows = [
        _row(5, ("精神", "不佳 5 天")),
        _row(4, ("情緒", "焦慮 4 天")),
        _row(8, ("睡眠", "異常 8 天")),
        _row(6, ("排泄", "尿色偏深 6 天")),
    ]

    # ── 偶發事件列：拒服/遊走/拒絕照顧/通報，各自散落在集中期附近 ──────────
    event_days = {i: [] for i in range(days)}
    for i in rng.sample(range(max(cluster_start_i - 3, 0), cluster_end_i + 1), min(4, days)):
        event_days[i].append("拒服")
    for i in rng.sample(range(cluster_start_i, cluster_end_i + 1), min(3, cluster_end_i - cluster_start_i + 1)):
        event_days[i].append("遊走")
    for i in rng.sample(range(cluster_start_i, cluster_end_i + 1), min(2, cluster_end_i - cluster_start_i + 1)):
        event_days[i].append("拒絕照顧")
    report_day = rng.choice(range(cluster_start_i, cluster_end_i + 1))
    event_days[report_day].append("通報")

    counts = {"拒服": 0, "遊走": 0, "拒絕照顧": 0, "通報": 0}
    for kinds in event_days.values():
        for k in kinds:
            counts[k] += 1
    # 2026-08-04改法：report_html_v9.py的events_row契約改成icons_by_day吃
    # (glyph,color) tuple、legend改吃legend_items清單（見analysis_v9.py
    # 同名函式的說明）——這裡的假資料生成器也要跟著改，兩邊共用同一個
    # render_v9_html()，形狀對不上會直接噴錯（ValueError: too many values
    # to unpack），不是可以各自维持舊格式的獨立路徑。
    events_row = {
        "icons_by_day": [
            [(EVENT_ICONS[k][0], EVENT_ICONS[k][1]) for k in event_days.get(i, [])]
            for i in range(days)
        ],
        "legend_items": [
            {"glyph": EVENT_ICONS[k][0], "color": EVENT_ICONS[k][1], "label": k, "count": n}
            for k, n in counts.items() if n
        ],
        "cluster_start_i": cluster_start_i, "cluster_end_i": cluster_end_i,
    }

    date_ticks = {i: _fmt_md(dates[i]) for i in range(0, days, 7)}

    daily_status = {
        # 2026-07-31：report_html_v9.py改讀cluster_segments（多段橘紅線清單），
        # 不再讀cluster_start_i/cluster_end_i這組單一區間——這裡固定給一段，
        # 純假資料demo用途不需要真的算多段。
        "days": days, "cluster_segments": [(cluster_start_i, cluster_end_i)],
        "cluster_label": f"{_fmt_md(dates[cluster_start_i])}–{_fmt_md(dates[cluster_end_i])}",
        "rows": daily_status_rows, "events_row": events_row, "date_ticks": date_ticks,
    }

    check_notes = {
        # 2026-08-13改法：remaining_deviations改成3或4-tuple，第3個元素是
        # sample_limited、第4個(可省略)是acute急性事件文字(見analysis_v9.py
        # 同一天的說明)——mock demo資料全部維持3-tuple(sample_limited=
        # False)，這裡只是展示範例，不是真的樣本不足/急性事件；
        # report_html_v9.py的渲染邏輯兩種長度都相容。
        "remaining_deviations": [
            ("進食量偏少", "近兩週 12/30 天低於平時", False),
            ("飲水評估偏少", "9/30 天", False),
            (f"{_fmt_md(dates[cluster_start_i + 6])} 體溫", "35.6°C（基線 36.8，-1.2）", False),
        ],
        # 2026-08-12修正：拿掉「握力」「呼吸次數」兩個佔位項目——這是
        # 更早期版本demo稿留下的，真實資料路徑(analysis_v9.py)的
        # completeness清單從來就只有血壓/體溫/心率/血氧/體重/服藥・照護
        # 六項，沒有握力/呼吸次數這兩項，mock demo這裡沒跟著同步，是
        # 使用者實測mockup版面時抓到的另一處真bug。
        "completeness": [
            ("血壓", "26/30 天・42 次", False), ("體溫", "28/30 天", False),
            ("心率", "26/30 天", False), ("血氧", "24 次", False),
            ("體重", "每月 1 次", False), ("服藥・照護", "30/30 天", False),
        ],
        "baseline_todo": [("個人基線", "2025/08–10 三個月"), ("跨月比較", "同期對照")],
        "reference_pending": "參考值由　　　醫師／　　　設定",
        "todo_flag": "待設定：舒張壓是否納入・血氧與進食飲水對照值",
    }

    # 2026-08-05改法：report_html_v9.py的footer契約改成分段欄位（見
    # analysis_v9.py同一段footer的說明），這裡的假資料生成器也要跟著改，
    # 形狀對不上會直接噴KeyError。
    footer = {
        "generated_date": (dates[-1] + timedelta(days=1)).isoformat().replace("-", "/"),
        "disclaimer_intro": "本頁為照護紀錄之彙整與統計，依設定參考值挑出偏離紀錄並標示同時發生的項目；",
        "disclaimer_key": "未進行病因推論，不判定疾病、嚴重程度或處置方式，所有臨床判斷由醫師為之。",
        "disclaimer_note": "所有數值取自本頁，可逐項核對。",
        "data_source": "照護隨行本（生理數值為機構量測，生活照護為照服員每日觀察）",
        "tech_note": "本頁為設計討論用示意資料，非真實個案（v9 版面驗證，資料為假資料）。",
    }

    return {
        "header": header, "top_summary": top_summary, "top3": top3,
        "bp_distribution": bp_distribution, "spo2_distribution": spo2_distribution,
        "pulse_distribution": pulse_distribution,
        "four_aspects": four_aspects, "daily_status": daily_status,
        "check_notes": check_notes, "footer": footer,
    }
