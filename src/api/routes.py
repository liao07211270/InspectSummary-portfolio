import asyncio
import math
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from .models import GenerateRequest, JobStatusResponse, JobFiles
from .pipeline import new_job, get_job, list_jobs, run_pipeline

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=4)


def _to_response(job: dict) -> JobStatusResponse:
    files = None
    if job.get("files"):
        files = JobFiles(
            pdf=job["files"].get("pdf"),
            txt=job["files"].get("txt"),
            analysis=job["files"].get("analysis"),
        )
    return JobStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        patient_id=job.get("patient_id"),
        patient_name=job.get("patient_name"),
        period=job.get("period"),
        created_at=job["created_at"],
        completed_at=job.get("completed_at"),
        duration_sec=job.get("duration_sec"),
        error=job.get("error"),
        files=files,
        generation_method=job.get("generation_method"),
    )


# ── Generate ───────────────────────────────────────────────────────────────
@router.post("/reports/generate", response_model=JobStatusResponse, status_code=202)
async def generate(req: GenerateRequest):
    """Start the pipeline in the background and return a job_id immediately."""
    job_id = new_job(req.patient_id, req.patient_name)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, run_pipeline, job_id, req)
    return _to_response(get_job(job_id))


# ── Status ─────────────────────────────────────────────────────────────────
@router.get("/reports/{job_id}/status", response_model=JobStatusResponse)
async def status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' not found")
    return _to_response(job)


# ── Download PDF ───────────────────────────────────────────────────────────
@router.get("/reports/{job_id}/pdf")
async def download_pdf(job_id: str):
    job = _require_done(job_id)
    path = job["files"]["pdf"]
    patient = job.get("patient_name", job_id)
    period  = job.get("period", {})
    filename = f"report_{job.get('patient_id', job_id)}_{period.get('end', '')}.pdf"
    return FileResponse(path, media_type="application/pdf",
                        filename=filename)


# ── Download summary text ──────────────────────────────────────────────────
@router.get("/reports/{job_id}/txt")
async def download_txt(job_id: str):
    job = _require_done(job_id)
    path = job["files"]["txt"]
    with open(path, encoding="utf-8") as f:
        content = f.read()
    return JSONResponse({"summary": content})


# ── Download analysis JSON ─────────────────────────────────────────────────
@router.get("/reports/{job_id}/json")
async def download_json(job_id: str):
    job = _require_done(job_id)
    import json
    with open(job["files"]["analysis"], encoding="utf-8") as f:
        data = json.load(f)
    return JSONResponse(data)


# ── List all jobs ──────────────────────────────────────────────────────────
@router.get("/reports", response_model=list[JobStatusResponse])
async def list_all():
    return [_to_response(j) for j in list_jobs()]


# ── Debug / verification endpoint ─────────────────────────────────────────
@router.get("/debug/analysis")
async def debug_analysis(seed: int = 42, days: int = 30, start: str = "2025-11-01"):
    """Return mock raw data + computed v2 metrics for spec verification."""
    import json as _json
    from ..mock_data import generate_vitals
    from ..analysis_core import analyze_all

    df = generate_vitals(days=days, start=start, seed=seed)
    a  = analyze_all(df)
    bp  = a["obs"]["bp"]
    sfw = a["obs"]["spo2_food_water"]
    hwt = a["obs"]["hr_weight_temp"]
    sas = a["obs"]["sleep_activity_social"]
    b1, b2, b3, b4 = (a["signals"][k] for k in ("block1", "block2", "block3", "block4"))

    crisis = "、".join(f"{d['date']}:{d['sys']}" for d in bp["crisis_detail"])
    low = "、".join(f"{e['date']} {e['t']}" for e in hwt["temp_low"]) or "—"
    high = "、".join(f"{e['date']} {e['t']}" for e in hwt["temp_high"]) or "—"
    behav = "、".join(f"{x['date']}{x['kind']}" for x in b3["behavior"]) or "無"

    verification = [
        # 第一部分・全數據觀察
        {"section": "觀察・血壓", "metric": "達標分布",
         "rule": "達標 / 偏高 / 危象 佔比",
         "value": f"達標 {bp['ok_pct']}%・偏高 {bp['high_pct']}%・危象 {bp['crisis_count']}次（{crisis}）"},
        {"section": "觀察・血壓", "metric": "晨午晚 / CV",
         "rule": "三時段均值與變異係數",
         "value": f"晨{int(bp['am_mean'])}・午{int(bp['pm_mean'])}・晚{int(bp['eve_mean'])}・CV {bp['cv_pct']}%（基線{bp['cv_baseline']}%）"},
        {"section": "觀察・血氧進食飲水", "metric": "未達標天數",
         "rule": "血氧<94 / 進食不足 / 飲水未達標",
         "value": f"血氧 {sfw['spo2_low_days']}・進食 {sfw['food_short_days']}・飲水 {sfw['water_short_days']} 天 / {sfw['total']}"},
        {"section": "觀察・心率體重體溫", "metric": "方向 / 長期 / 偏離",
         "rule": "心率方向、體重6月%、體溫對基線",
         "value": f"心率 {int(hwt['hr_start'])}→{int(hwt['hr_end'])}・體重 {hwt['weight_pct']}%・體溫低 {low}・高 {high}"},
        {"section": "觀察・睡眠活動社交", "metric": "非數值觀察",
         "rule": "日夜顛倒、復健頻率、社交",
         "value": f"日夜顛倒 {sas['reversal_days']}天・復健 {sas['rehab_start']}→{sas['rehab_end_lo']}-{sas['rehab_end_hi']}次/週・社交 {sas['social_trend']}"},
        # 第二部分・疾病訊號
        {"section": "BLOCK1 高血壓與心血管", "metric": "達標率（族群）",
         "rule": "目標 <140/90 量測日佔比",
         "value": f"{b1['reach_days']}/{b1['total']} 天（{b1['reach_pct']}%）"},
        {"section": "BLOCK1 高血壓與心血管", "metric": "晨間趨勢 / CV / 危象",
         "rule": "月均、週趨勢、CV對基線、危象>=180",
         "value": f"月均{int(b1['am_mean'])}・{int(b1['first_week'])}→{int(b1['last_week'])} {b1['trend_dir']}・CV{b1['cv_pct']}%・危象{b1['crisis_count']}次"},
        {"section": "BLOCK2 體液代謝與容積", "metric": "體重 / 乾體重 / 滯留",
         "rule": "月變化、對乾體重偏移、滯留複合",
         "value": f"月變化{b2['weight_delta_month']:+.1f}・乾體重{b2['dry_gap']:+.1f}（{'支持' if b2['retention_support'] else '不支持'}滯留）・夜尿{b2['night_void_early']}→{b2['night_void_late']}"},
        {"section": "BLOCK2 體液代謝與容積", "metric": "飲水 / 尿色",
         "rule": "飲水未達標天數、尿色偏深天數",
         "value": f"飲水未達標 {b2['water_short_days']}天・尿色偏深 {b2['urine_dark_days']}天"},
        {"section": "BLOCK3 感染監測", "metric": "體溫 / 急性衰退 / 行為",
         "rule": "對基線偏低偏高、同日下降、異常行為、NEWS",
         "value": f"低{low}・高{high}・同日下降 {('、'.join(b3['acute_days']) or '無')}・行為 {behav}・綜合分達門檻 {b3['news_over_days']}天"},
        {"section": "BLOCK4 肌少與衰弱", "metric": "體重% / 活動 / 進食 / 衰弱題組",
         "rule": "6月減重%、復健頻率、進食不足、衰弱項",
         "value": f"{b4['weight_pct']}%{'·顯著' if b4['weight_significant'] else ''}・復健{b4['rehab_start']}→{b4['rehab_end_lo']}-{b4['rehab_end_hi']}・進食不足{b4['food_short_days']}天（連續{b4['food_streak']}）・衰弱項{b4['frailty_items']}"},
    ]

    raw_cols = ["date", "systolic_am", "diastolic_am", "systolic_pm", "systolic_eve",
                "heart_rate", "spo2", "temperature", "weight",
                "water_intake", "urine_color", "night_void",
                "food_intake", "sleep_hrs", "day_night_reversal",
                "rehab_attended", "social", "mood", "abnormal_behavior"]
    raw = _json.loads(df[raw_cols].to_json(orient="records", force_ascii=False))

    return JSONResponse({"verification": verification, "raw_data": raw})


# ── Real-schema sample data (for the frontend's "載入範例真實資料" button) ──
@router.get("/debug/real-sample")
async def real_sample(seed: int = 42, days: int = 30, start: str = "2026-05-01"):
    from scripts.gen_real_schema_mock import generate_real_schema_events
    events = generate_real_schema_events(days=days, start=start, seed=seed)
    return JSONResponse(events)


# ── Helper ─────────────────────────────────────────────────────────────────
def _require_done(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' not found")
    if job["status"] != "done":
        raise HTTPException(409, f"Job status is '{job['status']}', not done yet")
    return job

@router.post("/reports/analyze")
async def analyze_measurement(req: dict):
    import math
    from ..data_parser import build_dataframe_from_request
    from ..analysis_core import analyze_all

    try:
        df = build_dataframe_from_request(req)
    except ValueError as e:
        raise HTTPException(422, f"請求格式錯誤：{e}")
    result = analyze_all(df)

    def clean(obj):
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return obj
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean(v) for v in obj]
        return obj

    return JSONResponse(clean(result))
