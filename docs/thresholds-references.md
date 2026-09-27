# v9 判斷邏輯門檻對照表（文獻查證，2026-08-03）

這份文件整理 `analysis_core.py`／`analysis_v9.py`／`data_parser.py` 裡跟「醫療判斷邏輯」有關的門檻常數，逐項標明是否查到對應文獻、文獻怎麼說、跟目前程式碼設定值是否吻合。**沒有查到文獻依據的項目，數值維持原樣，並明確標記「待醫師確認」**，不代表這些數字是錯的，只是目前沒有外部依據，需要跟醫師/機構確認才能定案。

## 一、有明確文獻依據，且與現有設定值吻合

| 常數 | 檔案 | 現有值 | 文獻依據 |
|---|---|---|---|
| `_NIGHT_VOID_ABNORMAL_THRESHOLD` | analysis_v9.py | 2 次/晚 | 國際尿控協會(ICS)對夜尿症的terminology report：技術定義為「醒來排尿≥1次」，但**臨床上認定有意義（clinically relevant）的夜尿是≥2次/晚**，≥3次屬嚴重 |
| `SPO2_CRITICAL` | analysis_core.py | 90% | 多篇文獻指出SpO2<90%屬於「臨床顯著低血氧」（clinically significant hypoxemia），需要臨床處置；90-94%屬輕度低血氧 |
| `BP_DIA_TARGET` | analysis_core.py | 90 mmHg | 2017 ACC/AHA高血壓指引：舒張壓≥90 mmHg歸類為第二期高血壓（Stage 2） |
| `WEIGHT_LOSS_SIG` | analysis_core.py | 5.0%（6個月內） | Fried衰弱表型（Frailty Phenotype, 2001）的非刻意體重減輕診斷標準之一：追蹤期間內≥5%體重減輕 |
| `WATER_TARGET_ML` | data_parser.py | 1500 ml/天 | 落在老年人每日建議飲水量1500–2000ml區間下限（ESPEN／EFSA相關文獻建議65歲以上族群） |
| `SPO2_LOW` | analysis_core.py | 94% | 常見血氧篩檢門檻，多篇文獻以SpO2≤94%作為偵測異常血氧的篩檢起點 |

## 二、有方法論層級文獻支持，但沒有查到精確對應數字（維持原值，補充引用脈絡）

| 常數 | 檔案 | 現有值 | 文獻能提供什麼 |
|---|---|---|---|
| `bp_reach_gap_pct` / `diastolic_pct` 的中度門檻 | analysis_core.py（SEVERITY_THRESHOLDS） | 25% | 遠端照護監測文獻常引用「25%變化幅度」當通用臨床警示門檻；心臟科特定族群（ICU病史冠心病患者）用20%。中度門檻25%落在這個常見範圍內，但輕度10%／重度40%是內插值，沒有直接文獻對應 |
| `_TREND_RATE_TOLERANCE`（進食量趨勢容忍度） | analysis_v9.py | ±20% | 同上，20-25%是文獻常見的臨床顯著變化幅度；這裡用在「進食不足天數佔比」的趨勢判斷，跟原文獻的量測對象（生命徵象數值）不完全相同，屬類比借用 |
| `_CLUSTER_MIN_CONSECUTIVE`／`_CLUSTER_WINDOW_DAYS`／`_TREND_MIN_CONSECUTIVE`（集中期／趨勢措辭連續天數） | analysis_v9.py | 5天／10天內5天／3天 | 健康監測與群聚事件（cluster）偵測文獻常用2-8天的連續窗口（例如流行病學「3天內3例以上」的群聚定義）；我們3-10天的設定落在這個常見範圍內，但原文獻是族群層級疾病監測，跟我們單一個案縱向症狀監測的情境不同，沒有直接數字對應 |

## 三、查無文獻依據，純屬工程判斷（維持原值，明確標記待確認）

| 常數 | 檔案 | 現有值 | 說明 |
|---|---|---|---|
| `water_short_pct`／`food_short_pct`／`temp_pct` | analysis_core.py（SEVERITY_THRESHOLDS） | 15%／30%／50% | 查到的文獻多半是「單次進食量佔需求量的百分比」（如GLIM營養不良篩檢標準），跟這裡「一段觀察期內有幾天不足」的**頻率式**計算是不同的指標，沒有找到可以直接對應的文獻 |
| `hr_delta` | analysis_core.py（SEVERITY_THRESHOLDS） | 5／10／20 bpm（絕對值） | 沒有查到絕對bpm變化量的分級文獻，現有數字是既有的佔位值 |
| `_OVERLAP_MIN_SIGNALS`／`_OVERLAP_MIN_CONSECUTIVE`（跨指標同期關聯偵測） | analysis_v9.py | 2種訊號／連續3天 | 純屬報告設計邏輯，沒有對應的臨床文獻 |

## 查證方式

以上文獻依據透過網路搜尋取得（2026-08-03），未逐篇查證原始文獻全文，僅供設計參考——正式採用前仍須由醫師/機構專業判斷確認，尤其是「二、三」類項目。
