"""
v9 版面手動測試用端點 —— 獨立新檔案，不動 `routes.py`／`pipeline.py`。

2026-08-03改成非同步（跟v2的`/reports/generate`同一套job queue+輪詢模式）：
原本v9是同步處理，因為「不呼叫LLM，純運算+畫圖很快」；現在頂部摘要的
跨指標同期關聯偵測detail句會呼叫LLM潤飾語序（見`llm_summary_v9.py`），
最壞情況要等最多3次重試，可能長達數十秒到數分鐘，不適合再讓使用者的
HTTP請求整個卡住等結果，所以改成背景執行緒+job狀態查詢。

流程：POST 一整包真實schema形狀的事件陣列(不分前三月/本月，後端自己用
analysis_v9.split_events_by_current_month()切分——2026-08-04改法，真實
上游資料本來就是一整串，不會事先分好兩包) → 立刻回傳job_id → 前端輪詢
GET /v9/{job_id}/status 直到done → GET /v9/{job_id}/png 取得檔案。

2026-07-30：畫面產生改用 `report_html_v9.py`（HTML→headless Chrome列印），
不再用 `report_pdf_v9.py`（reportlab手動座標版）——後者手算mm座標在文字長度
不固定時容易重疊/間距跑掉，前者直接沿用瀏覽器的flex/grid排版引擎，跟原始
設計稿是同一套渲染邏輯。`report_pdf_v9.py`/`mock_data_v9.py` 保留但不再是
主要路徑，只剩 `scripts/gen_report_v9.py` 的舊版demo還在用。

2026-08-07依醫師會議需求，匯出格式從PDF改成圖片(PNG)，不再產生PDF——
`report_html_v9.py`的`html_to_pdf()`已改成`html_to_png()`，這裡對應把
檔案副檔名、下載端點路徑(`/pdf`→`/png`)、回應media_type都一併改掉。
"""
import asyncio
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..analysis_v9 import build_v9_view_model
from ..carenote_client import CarenoteCaseNotFound, CarenoteConfigError, CarenoteError, fetch_care_data
from ..report_html_v9 import generate_v9_report_html
from ..llm_summary_v9 import polish_clause

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=2)

_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "output")

# 產生紀錄存在 output/（volume 掛載），容器重啟/重建後紀錄與圖片都還在
_RECORDS_PATH = os.path.join(_OUTPUT_DIR, "v9_records.json")
_records_lock = threading.Lock()
_TZ_TAIPEI = timezone(timedelta(hours=8))

# job狀態存記憶體字典即可（跟v2的pipeline.py同樣的MVP做法），單一process內
# 有效；跟_RECORDS_PATH（產生歷史紀錄，給/v9/reports歷史列表用）是兩件事，
# job狀態只是給前端輪詢知道「現在做到哪了」，做完後歷史紀錄才落地存檔。
_jobs_lock = threading.Lock()
_jobs: dict = {}


def _load_records() -> list:
    if not os.path.isfile(_RECORDS_PATH):
        return []
    try:
        with open(_RECORDS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _append_record(record: dict) -> None:
    with _records_lock:
        records = _load_records()
        records.append(record)
        os.makedirs(_OUTPUT_DIR, exist_ok=True)
        with open(_RECORDS_PATH, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)


def _new_job() -> str:
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {"job_id": job_id, "status": "pending", "error": None, "image_url": None, "generation_method": None}
    return job_id


def _update_job(job_id: str, **kwargs) -> None:
    with _jobs_lock:
        _jobs[job_id].update(kwargs)


def _get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None


def _run_v9_pipeline(job_id: str, events: list, profile: dict) -> None:
    """背景執行緒跑的完整流程：建view-model（內部自己切分前三月/本月，見
    analysis_v9.split_events_by_current_month）→ (有跨指標關聯才)呼叫LLM
    潤飾 → 產PNG圖片 → 寫入產生紀錄 → 更新job狀態。任何一步失敗都讓job狀態變
    error，不讓背景執行緒的例外整個吃掉、前端永遠卡在pending。"""
    try:
        vm = build_v9_view_model(events, profile)

        # 只有偵測到跨指標同期關聯才需要呼叫LLM潤飾——完全沒有重疊時
        # detail已經是規則式的安全句，不用多打一次LLM浪費時間。
        clause = vm["top_summary"].get("detail_clause")
        if clause:
            required_terms = vm["top_summary"].get("detail_signals")
            polished = polish_clause(clause, required_terms)
            vm["top_summary"]["detail"] = polished["text"]
            generation_method = polished["generation_method"]
        else:
            generation_method = "rule"

        os.makedirs(_OUTPUT_DIR, exist_ok=True)
        path = os.path.join(_OUTPUT_DIR, f"v9_test_{job_id}.png")
        generate_v9_report_html(vm, path)

        _append_record({
            "id": job_id,
            "name": str(profile.get("name", "")) or "（未填姓名）",
            "created_at": datetime.now(_TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
            "image_url": f"/api/v1/v9/{job_id}/png",
        })
        _update_job(
            job_id, status="done", generation_method=generation_method,
            image_url=f"/api/v1/v9/{job_id}/png",
        )
    except Exception as e:
        _update_job(job_id, status="error", error=str(e))


@router.post("/v9/generate", status_code=202)
async def generate_v9(req: dict):
    """body: {"profile": {...}, "data": [...]}——2026-08-04改法：原本呼叫端要
    自己把資料分成baseline_data/current_data兩包，但真實上游資料是一整串
    事件不會這樣分好，改成只吃一包`data`，切分交給
    analysis_v9.split_events_by_current_month()在背景工作裡處理。
    立刻回傳job_id，實際運算跟LLM潤飾都在背景執行緒跑。"""
    data = req.get("data", [])
    profile = req.get("profile", {})
    if not isinstance(data, list):
        raise HTTPException(400, "data 必須是陣列")

    job_id = _new_job()
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_v9_pipeline, job_id, data, profile)
    return {"job_id": job_id, "status": "pending"}


@router.post("/v9/generate-from-carenote", status_code=202)
async def generate_v9_from_carenote(req: dict):
    """真實資料串接（2026-08-12）：不用手貼 JSON，直接從 carenote（照護隨行本
    後端）的 /api/integration 端點拉某個案一段期間的
    全部登打資料，餵進同一條 v9 pipeline。

    body: {"caseID": 68, "from": "YYYY-MM-DD"?, "to": "YYYY-MM-DD"?}
    from/to 可省略——上游預設「前三月月初～今天」，正好是 v9 切分需要的範圍。

    拉取失敗（key 沒設、個案不存在、上游掛了）在這裡就同步回 4xx/502，
    不會建 job——只有拿到資料後才進背景 pipeline，所以呼叫端拿到 job_id
    就代表資料已經到手，之後的輪詢流程跟 /v9/generate 完全相同。
    """
    case_id = req.get("caseID")
    if not isinstance(case_id, int) or case_id <= 0:
        raise HTTPException(400, "caseID 必須是正整數")

    # requests 是同步阻塞呼叫，丟 executor 避免卡住 event loop
    loop = asyncio.get_event_loop()
    try:
        payload = await loop.run_in_executor(
            _executor, fetch_care_data, case_id, req.get("from"), req.get("to"),
        )
    except CarenoteConfigError as e:
        raise HTTPException(503, str(e))
    except CarenoteCaseNotFound as e:
        raise HTTPException(404, str(e))
    except CarenoteError as e:
        raise HTTPException(502, str(e))

    job_id = _new_job()
    loop.run_in_executor(_executor, _run_v9_pipeline, job_id, payload["data"], payload["profile"])
    return {
        "job_id": job_id,
        "status": "pending",
        "caseID": case_id,
        "from": payload.get("from"),
        "to": payload.get("to"),
        "event_count": len(payload["data"]),
    }


@router.get("/v9/{job_id}/status")
async def get_v9_status(job_id: str):
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(404, f"找不到job '{job_id}'")
    return job


@router.get("/v9/reports")
async def list_v9_reports():
    """歷史紀錄：只回傳圖片檔案仍存在的紀錄，新的在前。"""
    records = _load_records()
    alive = [
        r for r in records
        if os.path.isfile(os.path.join(_OUTPUT_DIR, f"v9_test_{r['id']}.png"))
    ]
    return list(reversed(alive))


@router.get("/v9/{file_id}/png")
async def get_v9_png(file_id: str):
    path = os.path.join(_OUTPUT_DIR, f"v9_test_{file_id}.png")
    if not os.path.isfile(path):
        raise HTTPException(404, "找不到這個報告，可能還沒完成、已被清除或id錯誤")
    return FileResponse(path, media_type="image/png")
