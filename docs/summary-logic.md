# 摘要句怎麼來的 — 四個疾病訊號區塊的 summary 邏輯

本檔說明 InspectSummary 各疾病訊號區塊（block1–block4）的一句 `summary` 是如何產生的，
以及**換成不同資料時這句話會怎麼變**。

> 代表例（block2）：
> 「非經由體重的惡化方向（11/14 起）：心率上行、血氧下降、夜間如廁增、飲水偏少。」

> **本版重點**：原本 block2 / block3 / block4 的敘述文字多為固定模板（不論資料好壞都印同一句）。
> 現已全面改為**依實際數值條件組字**——資料若顯示好轉或穩定，句子會自動少掉片語或整句改寫，
> 不再杜撰不存在的惡化。改動在 [src/analysis_core.py](../src/analysis_core.py)。

---

## 1. 設計原則

- 所有 summary **只描述程式算出來的事實**，不下診斷、不建議調藥。
- 「明確偏離才寫」（原則五）：每個片語都掛一個門檻條件，未達門檻就不出現。
- 沒有任何偏離時，輸出中性句（例如「本期未見明顯惡化集群」），而非空白或硬塞惡化詞。
- 所有門檻集中在檔頭常數區，可調、可稽核。

新增參數（[analysis_core.py 檔頭](../src/analysis_core.py#L23-L39)）：

| 參數 | 預設 | 意義 |
|---|---|---|
| `WATER_SHORT_FRAC` | 0.2 | 飲水未達標天數佔比 ≥ 此值 → 視為「偏少」 |
| `FOOD_SHORT_FRAC` | 0.2 | 進食不足天數佔比 ≥ 此值 → 視為「不足」 |

---

## 2. block1・心血管（晨間血壓）

[`_block1_cardio`](../src/analysis_core.py#L190)。**本來就已是資料驅動**，未改動：

```python
if bp["am_mean"] > BP_SYS_TARGET + 1:      notes.append("晨間月均…高於目標…")
if bp["cv_pct"] > bp["cv_baseline"] + CV_HIGH_DELTA: notes.append("CV …高於基線")
summary = "；".join(notes) + "。" if notes else "晨間血壓大致落在族群目標附近。"
```

- 血壓在目標內、CV 不高 → 「晨間血壓大致落在族群目標附近。」
- 偏高或變異大 → 只列出實際成立的那幾條。

---

## 3. block2・體液滯留（已修正）

[`_block2_fluid`](../src/analysis_core.py#L220)。**日期一直是資料驅動，方向文字本版才改為條件組字。**

```python
parts = []
if obs_hwt["hr_end"] - obs_hwt["hr_start"] > 3:        parts.append("心率上行")
if obs_hwt["spo2_end"] - obs_hwt["spo2_start"] < -1:   parts.append("血氧下降")
if night_void_late - night_void_early >= 0.3:          parts.append("夜間如廁增")
if water_short:                                         parts.append("飲水偏少")
summary = f"非經由體重的惡化方向（{起始日} 起）：" + "、".join(parts) + "。" \
          if parts else "本期未見明顯非體重相關的惡化集群。"
```

| 條件 | 觸發片語 |
|---|---|
| 末 5 日心率 − 首 5 日心率 > 3 | 心率上行 |
| 末 5 日血氧 − 首 5 日血氧 < −1 | 血氧下降 |
| 後半月夜間如廁均次 − 前半月 ≥ 0.3 | 夜間如廁增 |
| 飲水未達標天數 ≥ max(2, 0.2×天數) | 飲水偏少 |

**不同資料的效果**
- 心率反而下降、血氧回升、飲水達標 → 對應片語全部消失。
- 全部都沒惡化 → 整句改成「本期未見明顯非體重相關的惡化集群。」
- 「（11/14 起）」的日期仍取觀察期第 45% 天，隨天數/起始日移動。
- 真實數值另見指標列（[report_pdf.py:347](../src/report_pdf.py#L347)）：`心率 71→89＋血氧 97→94…`。

---

## 4. block3・感染／譫妄（已修正）

[`_block3_infection`](../src/analysis_core.py#L248)。原本只判斷「有沒有低溫事件」，且不論如何都接「食慾、活動同日下降」。
現改為依 `acute_days`（**已驗證**體溫偏離且同日食慾或活動下降的日子）分層：

```python
if acute_days:        summary = f"{acute_days[0]} 體溫偏離基線且食慾或活動同日下降。"
elif low and high:    summary = f"體溫偏低 {len(low)} 次、偏高 {len(high)} 次，惟未與食慾、活動同步下降。"
elif low:             summary = f"體溫偏低 {len(low)} 次（首見 …），惟未與食慾、活動同步下降。"
elif high:            summary = f"體溫偏高 {len(high)} 次（首見 …），惟未與食慾、活動同步下降。"
else:                 summary = "本月體溫未見明顯偏離基線。"
```

**不同資料的效果**
- 有體溫偏離且同日食慾/活動下降 → 點名該日（急性功能下降，最該注意）。
- 只有體溫偏離、但食慾活動沒同步掉 → 明說「惟未與食慾、活動同步下降」，不再暗示惡化。
- 體溫全在基線附近 → 「本月體溫未見明顯偏離基線。」

---

## 5. block4・衰弱複合（已修正）

[`_block4_frailty`](../src/analysis_core.py#L289)。原本固定印「體重達顯著減輕（x%），伴活動與進食同步降低」。
現改為依體重方向、活動是否下降、進食是否不足三項分別組字：

```python
weight_significant = abs(weight_pct) >= WEIGHT_LOSS_SIG          # 5%
activity_down      = obs_sas["rehab_end_hi"] < obs_sas["rehab_start"]
food_meaningful    = food_short >= max(2, round(n * FOOD_SHORT_FRAC))

head = f"體重達顯著{'減輕' if weight_pct<0 else '增加'}（{weight_pct}%）" if weight_significant \
       else f"體重輕度下降（{weight_pct}%）" if weight_pct <= -1 \
       else "體重大致穩定"
co = [t for t, on in (("活動", activity_down), ("進食", food_meaningful)) if on]
summary = head + (f"，伴{'與'.join(co)}同步降低。" if co else "。")
```

**不同資料的效果**
- 體重掉 ≥5% → 「體重達顯著減輕（x%）」；增加 ≥5% → 「達顯著增加」；−1% 到 −5% → 「輕度下降」；其餘 → 「體重大致穩定」。
- 活動、進食是否接「伴…同步降低」各自獨立判斷；兩者都穩 → 不接尾句。
- 例：體重 −5% 但活動進食都穩 → 「體重達顯著減輕（−5.0%）。」（不再硬說同步降低）。

---

## 6. 驗證結果

以人工「好轉／穩定」資料（心率下行、血氧回升、體溫=基線、飲水達標、進食正常、活動回升）跑分析：

| 區塊 | 修正前（固定句） | 修正後（資料驅動） |
|---|---|---|
| block2 | 非經由體重的惡化方向…心率上行、血氧下降… | **本期未見明顯非體重相關的惡化集群。** |
| block3 | …體溫偏低且食慾、活動同日下降。 | **本月體溫未見明顯偏離基線。** |
| block4 | 體重達顯著減輕…伴活動與進食同步降低。 | **體重達顯著減輕（−5.0%）。**（尾句消失） |

端到端 `generate_report()` 仍正常產出 PDF（dict 結構鍵名未變，只有 `summary` 內容改為動態）。

> 註：目前 demo 的體重是 `mock_data.py` 的固定常數（WEIGHT_NOW 等），故 block4 體重百分比在各 seed 相同；
> 接真實資料時即會隨輸入變動。

---

## 7. 與 LLM A／B 段的關係

報告另有 LLM 生成的 A、B 兩段（[llm_summary.py](../src/llm_summary.py)）：把以上算好的事實打包成 JSON 餵給模型，
資料不同 → 輸出措辭不同；模型只准描述事實。遠端LLM端點（holo-fast）不可用或輸出驗證失敗時退回 `_fallback()` 字串模板，
該模板亦將真實數字代入。**本次修正的是上游 analysis 層的 summary，使三層（指標列、區塊 summary、LLM 段）口徑一致、皆資料驅動。**
