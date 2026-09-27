# InspectSummary（隨行本版本）— 系統架構與流程說明

## 版本說明

這個repo目前並行維護**兩條產生報告的pipeline**，容易混淆，先講清楚差異：

- **v2**：現有的一頁式PDF展示pipeline，資料來源是`mock_data.py`產生的**模擬資料**，不是真實照護紀錄。`/api/v1/reports/generate`走這條路。**2026-07-31起v2已無專屬Web UI頁面**（原本的`static/index.html`被下面v9的測試頁取代），只能透過API／Swagger使用，不影響API本身的功能。
- **v9**（2026-07-30新增）：新版面設計，資料來源是真實schema形狀的單一整段事件陣列（2026-08-04起改成一包`data`，不再分baseline/current兩包，後端自己依資料裡最新一筆紀錄的日曆月份切分前三月/本月），會實際運算「前三月→本月」的比較。輸出格式2026-08-07起是**圖片(PNG)，不再是PDF**。手動測試頁面是`static/index.html`（2026-07-31起是根路徑`/`預設顯示的頁面，原檔名`v9_test.html`），對應API是`/api/v1/v9/...`，還沒接上正式對外的Web UI（目前是手動測試性質）。

兩者共用 `src/data_parser.py`（把真實schema事件陣列解析成`analyze_all()`看得懂的DataFrame）跟 `src/analysis_core.py`（核心指標計算函式庫，`analyze_all()`是唯一對外入口）。v9額外多一層`src/analysis_v9.py`做「兩期比較」的配對邏輯。

---

# 一、v2　模擬資料展示版

## 1. 整體流程

```
Web UI（2026-07-31起已無專屬頁面，改用API/Swagger）
    │  POST /api/v1/reports/generate
    ▼
FastAPI (server.py → src/api/routes.py)
    │  job_id 立即回傳，後台 ThreadPoolExecutor 執行（src/api/pipeline.py）
    ▼
Pipeline
    ├─ 1. 產生模擬資料       → mock_data.py（generate_vitals）
    ├─ 2. 指標計算           → analysis_core.py（analyze_all，唯一入口）
    ├─ 3. LLM 文字摘要       → llm_summary.py（generate_summary）
    └─ 4. PDF 報表輸出       → report_pdf.py（generate_report）
         │
         └─ output/report_<id>_<date>.pdf
            output/summary_<id>_<date>.txt
            output/analysis_<id>_<date>.json
```

> 舊版文件曾把 `features.py`／`disease_signals.py`／`flags.py` 列為pipeline的一部分，
> 但這三個檔案其實從未被 `analyze_all()` 呼叫過（更早期版本留下的死碼），已於
> 2026-07-30 確認並刪除。指標計算邏輯完全在 `analysis_core.py` 裡。

## 2. 指標計算邏輯

核心函式 `analyze_all(df)`，輸出 `{header, obs, signals}`。四個疾病訊號區塊
（block1~4）的文字判讀邏輯（怎麼依實際數值組字、什麼情況顯示中性句）**詳見
[docs/summary-logic.md](docs/summary-logic.md)**，那份文件逐一對應實際函式，
比在這裡重複維護一份容易更準確。

## 3. LLM 文字摘要（`llm_summary.py`）

呼叫**遠端OpenAI相容端點**（預設模型 `holo-fast`，端點位址由
`LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY`環境變數設定），**不是本機Ollama**
（這是舊版文件的錯誤資訊）。無法連線或輸出驗證失敗時，轉規則式`_fallback()`，
保證PDF一定能產出。

三層防護（2026-07從簡易版移植過來的穩定性技巧，見檔案自己的module docstring）：
1. **白名單制驗證**（`_validate()`）：只允許中文字、數字、常見標點、明列的英文單位縮寫，其他文字（英文字、簡體字、韓/日文）一律視為異常
2. **LLM當評審**（`_llm_judge()`）：格式驗證過後再檢查語意是否通順、有無疊字/缺字/亂碼
3. **重試採獨立呼叫**：不帶前次失敗輸出進context，避免模型在同一個詞反覆犯錯

## 4. PDF 報表輸出（`report_pdf.py`）

```
HEADER         : 個案資訊 + 資料完整度
第一部分 (左)  : 全數據觀察 — 3 組中性資料
    1 血壓          達標分布長條 + 晨間收縮壓趨勢折線
    2 血氧・進食・飲水   達標長條
    3 心率・體重・體溫   方向 / 長期 / 偏離日期
第二部分 (右)  : 疾病訊號 — 3 個疾病區塊
    BLOCK 1 高血壓與心血管
    BLOCK 2 體重・體液與營養
    BLOCK 3 感染監測
A 核心走勢 / B 關聯因子 : 全寬文字總結
頁尾  : 標註系統目前無法採集的欄位
```
字型：Microsoft JhengHei。

## 5. 驗算功能

`GET /api/v1/debug/analysis?seed=42&days=30&start=2025-11-01` —— 回傳
`verification`（逐項對應輸出規則的驗算表格）+ `raw_data`（模擬生命徵象原始資料）。
Web UI「驗算模擬資料」區塊呼叫此端點。

## 6. API 端點（`src/api/routes.py`）

| 端點 | 說明 |
|---|---|
| `POST /reports/generate` | 建job，背景執行v2 pipeline |
| `GET /reports/{job_id}/status` `/pdf` `/txt` `/json` | 查job狀態、下載結果 |
| `GET /reports` | 列出所有job |
| `GET /debug/analysis` | 模擬資料驗算 |
| `GET /debug/real-sample` | 真實schema形狀範例資料 |
| `POST /reports/analyze` | 吃真實schema資料，直接跑`analyze_all()`回傳JSON（不產PDF） |

---

# 二、v9　真實資料比較版（2026-07-30 新增）

## 1. 整體流程

```
static/index.html（手動測試頁，2026-07-31起是根路徑「/」預設顯示的頁面，原檔名v9_test.html）
    │  POST /api/v1/v9/generate  {profile, data}   ← 2026-08-04起只吃一包完整事件陣列
    ▼
FastAPI (src/api/routes_v9.py)
    │  立刻回傳 {job_id, status:"pending"}（202），背景執行緒跑pipeline
    │  2026-08-03起改成非同步：跨指標關聯偵測的detail句要呼叫LLM潤飾語序，
    │  最壞情況重試到數分鐘，不適合卡住HTTP請求
    ▼
    ├─ 1. 切分＋兩期解析  → analysis_v9.split_events_by_current_month()自動依
    │                        資料裡最新一筆紀錄的日曆月份切成baseline(前3月)/
    │                        current(本月)，各自再用data_parser.py解析
    ├─ 2. 兩期指標計算    → analysis_core.py（analyze_all，baseline/current各跑一次）
    ├─ 3. 比較配對        → analysis_v9.py（build_v9_view_model，把兩期結果配成
    │                        「前三月→本月」的view-model）
    ├─ 4. LLM潤飾（條件式）→ llm_summary_v9.polish_clause()，只有偵測到跨指標
    │                        同期關聯時才呼叫，沒有重疊就跳過
    └─ 5. HTML→PNG        → report_html_v9.py（render_v9_html + headless
                              Chrome/Edge截圖，2026-08-07起輸出圖片不再是PDF）
         │
         └─ output/v9_test_<job_id>.png
    ▼
前端輪詢 GET /v9/{job_id}/status 直到 done → GET /v9/{job_id}/png 取得圖片
```

## 2. 比較運算層（`analysis_v9.py`）

`build_v9_view_model(events, profile)` 是唯一對外入口，`events`是單一整段事件
陣列（不分前三月/本月，切分邏輯在函式內部呼叫`split_events_by_current_month()`
處理）。輸出的view-model字典形狀固定：

```
header / top_summary / top3 / bp_distribution / spo2_distribution /
pulse_distribution / four_aspects / daily_status / check_notes / footer
```

`report_html_v9.py`只認這個形狀，不管數字從哪來。`spo2_distribution`雖然畫面上
已經被`pulse_distribution`取代（見下方「本月量測分布」說明），但**仍然保留在
vm裡不能拿掉**——`report_pdf_v9.py`（已被取代的舊版reportlab渲染器）還在讀
這個key。

## 3. HTML → 圖片（`report_html_v9.py`）

先前v9第一版是用reportlab手動計算每個元素的mm座標（`report_pdf_v9.py`，
**現在已經不在主要路徑上，只有`scripts/gen_report_v9.py`這支純版面demo腳本
還在用它**），遇到文字長度不固定的地方常常算錯間距、文字重疊。現在改成：
產生一份沿用flex/grid排版的HTML字串，再用本機已安裝的Chrome/Edge headless
模式截圖（`--screenshot`）——排版交給瀏覽器引擎處理，不用手算座標。
2026-08-07依醫師會議需求，輸出格式從PDF改成PNG（`html_to_png()`取代
`html_to_pdf()`），固定視窗尺寸794×1123px（A4@96dpi）、2倍畫質截圖。

## 4. 手動測試

- **前端**：`static/index.html`（2026-07-31起是根路徑「/」預設顯示的頁面，原檔名`v9_test.html`；原本v2的Web UI頁面已被取代）——個案資訊欄位 + **單一**「照護紀錄資料」文字框（貼一整段真實schema事件陣列JSON，不用自己分前三月/本月）+ 執行按鈕，右側`<img>`預覽產出的圖片 + 下載按鈕
- **真實資料串接頁**：`static/carenote.html`（2026-08-12新增，路由`/carenote`）——只需輸入carenote個案編號（日期區間選填，預設前三月月初～今天），走`POST /v9/generate-from-carenote`由後端向照護隨行本拉資料，不用手貼JSON；個案姓名/性別/年齡由上游帶回。兩頁header有互相連結
- **範例測試資料**：`tests/fixtures/v9_scenarios/`（11組情境，見該資料夾README；2026-08-11起已改成單一整段事件陣列格式，由`scripts/gen_v9_manual_test_data.py`產生、部分情境另有手動微調），也是提供給上游介接（照護隨行本後端）工程師比對真實資料格式用的範例。**`scripts/gen_v9_test_scenarios.py`是舊版產生器（baseline/current分兩檔的舊格式），已標記棄用，不要再執行**
- **測試套件**：`tests/test_analysis_v9.py`、`tests/test_report_html_v9.py`

## 5. 已知限制

- **偶發事件的「通報」（Event型別）、「活動投入」、「拒絕照顧」**：真實資料庫schema
  裡`Event`型別（欄位`eventType`+`description`）未解析；「活動投入」「拒絕照顧」
  則是舊版demo（`mock_data_v9.py`）才有的項目，並非真實schema裡的獨立分類，
  真實資料路徑（`analysis_v9.py`）本來就沒有對應欄位。**除了這幾項，
  view-model其餘欄位（含每日狀態四列/特殊排尿/排便異常/精神不佳/遊走/
  尿失禁等）都已經是真實資料算出來的，不是佔位內容**——這點2026-07-31後
  已修正，舊文件曾誤寫「每日狀態四列全部是佔位」，已更正。
- **CareDiary型別的`spirit`/`emotion`/`activityPerformance`欄位未串接**：真實
  schema裡CareDiary（日照限定）本身就有專門的精神/情緒選單式欄位，比目前
  用Behavior代碼推論「精神不佳」更直接，值得評估要不要改用/併用（詳見上游
  提供的完整schema說明文件）
- **Accompaniment（陪診）型別未解析**：真實schema有專門的陪診紀錄（院所/科別/
  結果/下次回診），目前每日事件列完全沒有顯示就醫紀錄
- **前三月歷史資料介接**：2026-08-12已定案並實作——上游（照護隨行本後端）提供
  `GET /api/integration/cases/{caseID}/care-data?from=&to=`（X-API-Key保護，
  預設區間即「前三月月初～今天」，一次回傳本月+前三月），本服務用
  `src/carenote_client.py`＋`POST /v9/generate-from-carenote`串接。啟用需在
  兩邊`.env`設同一把key（上游`INTEGRATION_API_KEY`、本服務`CARENOTE_API_KEY`）；
  上游該端點在key未設定時回404（停用狀態）
- **跨指標日期集中偵測**：v9原稿設計的「偏離集中在X–Y，7天內...多項同向」這種敘述
  需要新運算邏輯，目前`頂部偏離摘要`的detail句只是通用免責聲明
- **衰弱卡「體重・OO」標籤**：設計目標是六個月長期趨勢，實際依`baseline_df`最早
  一筆體重讀數跟`current_df`最新一筆的日期差動態判斷，不足六個月會如實顯示
  「近N個月」而不是謊報六個月

## 6. API 端點（`src/api/routes_v9.py`）

| 端點 | 說明 |
|---|---|
| `POST /v9/generate` | 吃`{"profile": {...}, "data": [...]}`（單一整段事件陣列），回傳`{"job_id": ..., "status": "pending"}`（202） |
| `POST /v9/generate-from-carenote` | 真實資料串接（2026-08-12）：吃`{"caseID": 68, "from"?: ..., "to"?: ...}`，由後端向carenote整合端點（`carenote_client.py`）拉該個案區間內全部登打資料後走同一條pipeline；拉取失敗同步回4xx/502不建job，成功回傳job_id+`event_count`，之後輪詢流程與`/v9/generate`相同 |
| `GET /v9/{job_id}/status` | 查job狀態（pending/done/error） |
| `GET /v9/reports` | 列出歷史紀錄（只回傳圖片檔案仍存在的，新的在前） |
| `GET /v9/{file_id}/png` | 依id回傳PNG圖片檔案 |

---

# 三、檔案結構

```
InspectSummary_original/
├── server.py                    # uvicorn 啟動點
├── src/
│   ├── data_parser.py           # 【共用】真實schema事件陣列解析
│   ├── analysis_core.py           # 【共用】核心指標計算（analyze_all）
│   │
│   ├── mock_data.py             # 【v2】模擬資料生成
│   ├── llm_summary.py           # 【v2】LLM文字摘要 + 三層防護
│   ├── report_pdf.py            # 【v2】ReportLab PDF排版
│   │
│   ├── analysis_v9.py           # 【v9】兩期比較運算層
│   ├── carenote_client.py       # 【v9】carenote整合客戶端（拉真實登打資料，2026-08-12）
│   ├── report_html_v9.py        # 【v9】HTML產生 + headless Chrome截圖成PNG（主要路徑）
│   ├── llm_summary_v9.py        # 【v9】跨指標關聯detail句的LLM潤飾（條件式呼叫）
│   ├── report_pdf_v9.py         # 【v9・已被取代】reportlab版渲染器，只剩demo腳本在用
│   ├── mock_data_v9.py          # 【v9・已被取代】純版面驗證假資料，只剩demo腳本在用
│   │
│   └── api/
│       ├── app.py               # FastAPI app 初始化，掛載 v2+v9 兩個router
│       ├── routes.py            # 【v2】所有 API 端點
│       ├── routes_v9.py         # 【v9】所有 API 端點（非同步job queue+輪詢）
│       ├── pipeline.py          # 【v2】背景工作管理
│       └── models.py            # 【v2】Pydantic 輸入輸出模型
├── static/
│   └── index.html               # 【v9】手動測試頁（2026-07-31起是根路徑「/」預設頁面，
│                                 #      原檔名v9_test.html；v2的Web UI已被取代；單一
│                                 #      「照護紀錄資料」文字框，<img>預覽PNG）
├── scripts/
│   ├── gen_report_v9.py         # 【v9】純版面demo（用mock_data_v9，輸出PNG）
│   ├── gen_v9_manual_test_data.py # 【v9】產生手動測試情境資料（單一整段陣列格式，現行版）
│   ├── gen_v9_test_scenarios.py # 【v9・已棄用】舊版產生器(baseline/current分兩檔格式)，不要執行
│   └── gen_real_schema_mock.py  # 【共用】產生真實schema形狀假資料
├── tests/                       # pytest 測試套件（v2+v9都有）
│   └── fixtures/v9_scenarios/   # 【v9】11組測試情境，供介接工程師比對真實資料格式用
├── output/                      # 生成的 PDF/PNG/TXT/JSON
├── docs/summary-logic.md        # 【v2】block1~4 summary句邏輯詳解
├── docs/thresholds-references.md # 【共用】判斷邏輯門檻的文獻查證對照表
└── sample-summary/              # 【v2】輸出規格定義
```
