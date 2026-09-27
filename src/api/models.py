from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    patient_id:   str       = "P001"
    patient_name: str       = "王大明"
    age:          int       = 82
    gender:       str       = "男"
    diagnoses:    List[str] = ["高齡高血壓", "衰弱 CFS 5"]
    period_start: str       = "2025-11-01"
    days:         int       = Field(30, ge=7, le=90)
    seed:         int       = 42
    # 真實 DB 格式事件 list；有帶就取代 mock 資料。型別限定成「list of dict」
    # 讓 FastAPI 在進到我們的程式碼前就先擋掉明顯不對的形狀（例如傳一個 dict
    # 而不是 list、或 list 裡塞了字串/數字），回乾淨的 422 而不是後面某處才炸。
    raw_data:     Optional[List[Dict[str, Any]]] = None


class JobFiles(BaseModel):
    pdf:          Optional[str] = None
    txt:          Optional[str] = None
    analysis:     Optional[str] = None   # path to the analysis JSON file


class JobStatusResponse(BaseModel):
    job_id:       str
    status:       str          # pending | running | done | error
    patient_id:   Optional[str] = None
    patient_name: Optional[str] = None
    period:       Optional[dict] = None
    created_at:   str
    completed_at: Optional[str] = None
    duration_sec: Optional[float] = None
    error:        Optional[str] = None
    files:        Optional[JobFiles] = None
    generation_method: Optional[str] = None   # "llm" | "fallback"，摘要文字怎麼生成的
