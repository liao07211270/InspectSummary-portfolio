# InspectSummary（隨行本版本 / 複雜版）

> ## 專案說明
>
> 原始開發 repo 底下維護兩條分支，對應兩個資料來源、部署位置皆不同的專案：
>
> | 分支 | 專案 | 資料來源 |
> |---|---|---|
> | **`main`**（本分支，本 repo 展示的內容） | 隨行本版本／複雜版 | 照護隨行本 LINE 系統完整欄位（本文件說明的對象） |
> | `InspectSummary_JUBO` | 簡易版 | 手機 App 單次量測資料（`POST /api/measurement/analysis`） |
>
> 本 repo 為作品集展示用途，從原始團隊 repo 的 `main` 分支整理而來，已移除內部部署位址與合作方識別資訊，僅保留程式邏輯與架構說明。

AI 照護摘要報告服務——FastAPI + uvicorn，讀取照護量測/事件資料，計算指標並產生一頁式摘要報告。

> 本分支（`main`）是「隨行本版本」（照護隨行本 LINE 系統完整欄位版），目前並行維護兩條 pipeline：
> - **v2**：模擬資料展示版，輸出 **PDF**，只剩 API／Swagger 可用（原本的 Web UI 頁面已在
>   2026-07-31 被 v9 測試頁取代，見下方說明）
> - **v9**（2026-07-30 新增）：真實資料比較版（前三月 vs 本月），輸出**圖片（PNG，
>   2026-08-07起不再是PDF）**，`static/index.html` 是它的手動測試頁，目前是根路徑
>   `/` 預設顯示的頁面
>
> 完整架構、流程圖、各檔案職責、已知限制，請見 **[NOTE.md](NOTE.md)**。

## 目前功能狀態（2026-08-28）

v9 除了基本的「前三月 vs 本月」比較，目前已支援以下情境：

- **情境1・首月無個人基線**：資料涵蓋不足3個月時，改用公定標準值判讀
- **情境3・資料樣本有限**：本月或基線任一期間登打密度過低時，該指標退出排序、標示「樣本有限」
- **情境4・生理與生活照護指標統一排序**：飲水、進食、排便、精神、睡眠等生活照護指標，
  與血壓、血氧等生理量測併入同一個候選池排序，不分類別
- **情境5・短期急性事件**：連續2天以上危急值獨立標示為「急性事件」，優先排入偏離最大3項

尚未支援：情境2（基線期間排除疾病異常月）、情境6（月中用藥/照護目標變更後分段呈現）。

核心計算引擎已由 `src/analysis_v2.py` 更名為 **`src/analysis_core.py`**（v2/v9 共用底層，
名稱與「v2」版本無關，純粹是核心統計函式庫）。

### 手機高齡友善版（開發中）

新增獨立的互動式手機版報告，跟桌面版讀取同一份 `build_v9_view_model()` 輸出，但呈現成
分頁瀏覽介面，供 LINE 內嵌瀏覽器等行動裝置使用：

- **程式碼位置**：[`src/report_html_mobile_v9.py`](src/report_html_mobile_v9.py)
- **輸出方式**：跟桌面版（`report_html_v9.py`）不同，桌面版是無頭瀏覽器截圖產生 PNG
  圖片；手機版是產生**一個獨立、可互動的 HTML 檔案**（含內嵌 JavaScript，不需要額外的
  前端框架或建置流程）
- **內容**：1個首頁選單 + 10個內容頁（本月照護重點、偏離最大3項、收縮壓/脈搏分布、
  疾病監測四面向卡各一頁、每日狀態與事件、查核與說明），字體與按鈕觸控範圍明顯放大
- **朗讀功能**：串接瀏覽器內建語音合成（Web Speech API），已在真機 LINE 內嵌瀏覽器
  環境測試出聲音正常
- **目前限制**：**尚未串接對外 API 路由**，還沒有像 `/v9/generate` 那樣的端點可以呼叫，
  目前只能在本機直接呼叫 `generate_v9_mobile_html(vm, output_path)` 這個 Python 函式
  手動產生檔案，供工程師/PM 測試用

手動產生測試檔案的方式：

```python
from src.analysis_v9 import build_v9_view_model
from src.report_html_mobile_v9 import generate_v9_mobile_html

vm = build_v9_view_model(events, profile)
generate_v9_mobile_html(vm, "output/mobile_test.html")
```

產生後用瀏覽器直接開啟該 HTML 檔案即可預覽；若要在手機上測試，需另外啟動一個綁定
`0.0.0.0` 的簡易 HTTP 伺服器（例如 `python -m http.server 8080 --bind 0.0.0.0`），
讓手機連上你電腦所在的區域網路 IP 存取。

## 快速開始

```bash
python -m venv venv
# Windows
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# 設定 LLM 端點金鑰（v2 文字摘要用，缺少時會自動轉規則式 fallback，不影響 PDF 產出）
$env:LLM_API_KEY = "your-api-key"

.\venv\Scripts\python.exe server.py
```

啟動後：
- v9 手動測試頁：`http://127.0.0.1:8000/`（根路徑，原本的 v2 Web UI 已被取代，
  v2 只能透過 API／Swagger 使用）
- Swagger：`http://127.0.0.1:8000/docs`

## 測試

```bash
.\venv\Scripts\python.exe -m pytest
```

## 環境變數

| 變數 | 說明 | 預設值 |
|---|---|---|
| `LLM_BASE_URL` | 遠端 LLM 端點（OpenAI 相容介面） | `<internal-llm-endpoint>`（部署時自行設定） |
| `LLM_API_KEY` | LLM 端點金鑰 | 無，缺少時 v2 文字摘要會轉規則式 fallback |
| `LLM_MODEL` | 模型名稱 | `holo-fast` |
| `CARENOTE_BASE_URL` | 上游整合系統站台，v9 真實資料串接用 | `<upstream-integration-endpoint>`（部署時自行設定） |
| `CARENOTE_API_KEY` | 整合端點的 X-API-Key（與上游 `.env` 的 `INTEGRATION_API_KEY` 同值）；未設定時 `/v9/generate-from-carenote` 回 503 | 無 |
| `CARENOTE_TIMEOUT_SECONDS` | 拉取 carenote 資料的單次請求逾時（秒） | `30` |

## 文件

- [NOTE.md](NOTE.md) — 系統架構與流程完整說明（v2/v9 兩版都有）
- [docs/summary-logic.md](docs/summary-logic.md) — v2 疾病訊號區塊文字判讀邏輯詳解
- [docs/thresholds-references.md](docs/thresholds-references.md) — v9 判斷邏輯門檻值文獻查證對照表，
  標明哪些門檻有臨床文獻依據、哪些仍是待醫師確認的工程判斷值
- [tests/fixtures/v9_scenarios/README.md](tests/fixtures/v9_scenarios/README.md) — v9 測試情境資料說明（目前共14組情境）
