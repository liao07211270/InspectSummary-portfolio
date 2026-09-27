"""
Background pipeline runner.
Each job runs in a ThreadPoolExecutor so the FastAPI event loop stays free.
Job state is stored in an in-memory dict (suitable for single-process MVP).
"""
import json
import os
import time
import uuid
import threading
from datetime import datetime
from typing import Dict

from ..mock_data import generate_vitals
from ..data_parser import build_dataframe_from_request
from ..analysis_core import analyze_all
from ..llm_summary import generate_summary
from ..report_pdf import generate_report

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

_lock: threading.Lock = threading.Lock()
_jobs: Dict[str, dict] = {}


# ── Job state helpers ──────────────────────────────────────────────────────
def new_job(patient_id: str, patient_name: str) -> str:
    job_id = uuid.uuid4().hex[:10]
    with _lock:
        _jobs[job_id] = {
            "job_id":       job_id,
            "status":       "pending",
            "patient_id":   patient_id,
            "patient_name": patient_name,
            "period":       None,
            "created_at":   datetime.now().isoformat(timespec="seconds"),
            "completed_at": None,
            "duration_sec": None,
            "error":        None,
            "files":        None,
            "generation_method": None,
        }
    return job_id


def get_job(job_id: str) -> dict | None:
    with _lock:
        return dict(_jobs.get(job_id, {}))


def list_jobs() -> list:
    with _lock:
        return [dict(j) for j in _jobs.values()]


def _update(job_id: str, **kwargs):
    with _lock:
        _jobs[job_id].update(kwargs)


# ── Pipeline ───────────────────────────────────────────────────────────────
def run_pipeline(job_id: str, req) -> None:
    """Blocking pipeline — runs in a ThreadPoolExecutor thread."""
    t0 = time.time()
    _update(job_id, status="running")

    try:
        patient = {
            "id":        req.patient_id,
            "name":      req.patient_name,
            "age":       req.age,
            "gender":    req.gender,
            "diagnoses": req.diagnoses,
        }

        # 1. Data source — real data if provided, otherwise mock
        if getattr(req, "raw_data", None):
            df = build_dataframe_from_request({"_data": req.raw_data})
            if df.empty:
                raise ValueError("raw_data 解析後沒有任何有效紀錄")
        else:
            df = generate_vitals(days=req.days, start=req.period_start, seed=req.seed)
        period = (df["date"].iloc[0], df["date"].iloc[-1])
        _update(job_id, period={"start": period[0], "end": period[1]})

        # 2. Analysis (v2 — full nested dict)
        analysis = analyze_all(df)

        # 3. Save JSON
        slug = f"{req.patient_id}_{period[1]}"
        json_path = os.path.join(OUTPUT_DIR, f"analysis_{slug}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"patient": patient,
                       "period": {"start": period[0], "end": period[1]},
                       **analysis}, f, ensure_ascii=False, indent=2)

        # 4. LLM summary (A 核心走勢 / B 關聯因子)
        summary = generate_summary(analysis)
        txt_path = os.path.join(OUTPUT_DIR, f"summary_{slug}.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(
                f"生成方式：{summary['generation_method']}\n\n"
                f"A 核心走勢\n{summary['A']}\n\nB 關聯因子\n{summary['B']}\n"
            )

        # 5. PDF
        pdf_path = os.path.join(OUTPUT_DIR, f"report_{slug}.pdf")
        generate_report(
            patient=patient, period=period,
            analysis=analysis, summary=summary, output_path=pdf_path,
        )

        elapsed = round(time.time() - t0, 1)
        _update(
            job_id,
            status="done",
            completed_at=datetime.now().isoformat(timespec="seconds"),
            duration_sec=elapsed,
            files={"pdf": pdf_path, "txt": txt_path, "analysis": json_path},
            generation_method=summary["generation_method"],
        )

    except Exception as exc:
        _update(
            job_id,
            status="error",
            completed_at=datetime.now().isoformat(timespec="seconds"),
            duration_sec=round(time.time() - t0, 1),
            error=str(exc),
        )
        raise
