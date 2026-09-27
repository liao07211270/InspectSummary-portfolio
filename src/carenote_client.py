"""carenote（照護隨行本後端）整合客戶端。

上游合作機構專案 2026-08-12 起提供 server-to-server 端點：

    GET {CARENOTE_BASE_URL}/api/integration/cases/{caseID}/care-data?from=&to=
    Header: X-API-Key: {CARENOTE_API_KEY}

回傳 `{caseID, from, to, profile, data}`，其中 `data` 已經是本服務
`POST /api/v1/v9/generate` 要吃的單一整段事件陣列（VitalSigns／CareRecords
兩種 row 形狀混排、台北 wall-clock naive ISO、時間升冪），`profile` 也已對齊
view-model 需要的 name/gender/age/group 欄位——所以這裡拿到回應後不需要
再做任何欄位轉換，直接餵進 pipeline。

環境變數（.env，經 docker-compose env_file 帶入）：
  CARENOTE_BASE_URL  上游站台，需於部署環境自行設定
  CARENOTE_API_KEY   必填；未設定時 fetch_care_data() 直接拋 CarenoteConfigError，
                     不會對外發任何請求（上游未設 key 時端點也會回 404 裝死）
  CARENOTE_TIMEOUT_SECONDS  單次請求逾時，預設 30 秒
"""
import os

import requests

CARENOTE_BASE_URL = os.environ.get("CARENOTE_BASE_URL", "").rstrip("/")
CARENOTE_API_KEY = os.environ.get("CARENOTE_API_KEY")
CARENOTE_TIMEOUT_SECONDS = float(os.environ.get("CARENOTE_TIMEOUT_SECONDS", "30"))


class CarenoteError(RuntimeError):
    """呼叫 carenote 整合 API 失敗（連線、逾時、非 2xx、回應格式不對）。"""


class CarenoteConfigError(CarenoteError):
    """本服務缺 CARENOTE_API_KEY 設定，尚未啟用串接。"""


class CarenoteCaseNotFound(CarenoteError):
    """上游查無此個案（caseID 錯誤或個案不存在）。"""


def fetch_care_data(case_id: int, date_from: str | None = None, date_to: str | None = None) -> dict:
    """拉取某個案一段期間的全部登打資料。

    不帶 from/to 時沿用上游預設「前三月月初～今天」，正好等於
    analysis_v9.split_events_by_current_month() 期待的資料範圍。
    回傳上游原始 JSON：{caseID, from, to, profile, data}。
    """
    if not CARENOTE_API_KEY:
        raise CarenoteConfigError("未設定 CARENOTE_API_KEY，無法向 carenote 拉取資料（見 .env）")

    url = f"{CARENOTE_BASE_URL}/api/integration/cases/{int(case_id)}/care-data"
    params = {}
    if date_from:
        params["from"] = date_from
    if date_to:
        params["to"] = date_to

    try:
        resp = requests.get(
            url,
            headers={"X-API-Key": CARENOTE_API_KEY},
            params=params,
            timeout=CARENOTE_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        raise CarenoteError(f"連線 carenote 失敗：{e}") from e

    if resp.status_code == 404:
        # 上游兩種 404：key 未設定（端點停用）與個案不存在，訊息以個案為主——
        # 端點停用屬於部署設定問題，會在上線串接時第一次就被發現。
        raise CarenoteCaseNotFound(f"carenote 查無個案 caseID={case_id}（或上游整合端點未啟用）")
    if resp.status_code in (401, 403):
        raise CarenoteError("carenote 拒絕存取：X-API-Key 錯誤或未帶（檢查兩邊 .env 的 key 是否一致）")
    if resp.status_code != 200:
        raise CarenoteError(f"carenote 回應異常 HTTP {resp.status_code}：{resp.text[:200]}")

    try:
        payload = resp.json()
    except ValueError as e:
        raise CarenoteError("carenote 回應不是合法 JSON") from e

    if not isinstance(payload.get("data"), list) or not isinstance(payload.get("profile"), dict):
        raise CarenoteError("carenote 回應缺少 data 陣列或 profile 物件，介接格式可能已變動")
    return payload
