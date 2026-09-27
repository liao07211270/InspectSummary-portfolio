# carenote 整合客戶端：不對真實上游發請求，用 unittest.mock 模擬 requests，
# 鎖住錯誤分類（設定缺失/個案不存在/key 錯/格式變動）與請求參數的組法。
import pytest
from unittest.mock import MagicMock, patch

import src.carenote_client as cc


def _fake_resp(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("not json")
    return resp


_OK_PAYLOAD = {
    "caseID": 68,
    "from": "2026-05-01",
    "to": "2026-08-12",
    "profile": {"name": "測試", "gender": "女", "age": 82, "group": "日照"},
    "data": [{"RecordType": "Water", "OccurredAt": "2026-08-01T09:30:00", "Content": {"amountMl": 250}}],
}


def test_missing_api_key_raises_config_error_without_request():
    with patch.object(cc, "CARENOTE_API_KEY", None), \
         patch.object(cc.requests, "get") as get:
        with pytest.raises(cc.CarenoteConfigError):
            cc.fetch_care_data(68)
        get.assert_not_called()


def test_success_returns_payload_and_builds_request_correctly():
    with patch.object(cc, "CARENOTE_API_KEY", "k" * 32), \
         patch.object(cc.requests, "get", return_value=_fake_resp(200, _OK_PAYLOAD)) as get:
        payload = cc.fetch_care_data(68, "2026-05-01", "2026-08-12")
    assert payload == _OK_PAYLOAD
    args, kwargs = get.call_args
    assert args[0].endswith("/api/integration/cases/68/care-data")
    assert kwargs["headers"]["X-API-Key"] == "k" * 32
    assert kwargs["params"] == {"from": "2026-05-01", "to": "2026-08-12"}


def test_omitted_dates_are_not_sent_so_upstream_default_applies():
    """from/to 不帶時 params 要是空的——讓上游用「前三月月初～今天」預設值。"""
    with patch.object(cc, "CARENOTE_API_KEY", "k" * 32), \
         patch.object(cc.requests, "get", return_value=_fake_resp(200, _OK_PAYLOAD)) as get:
        cc.fetch_care_data(68)
    assert get.call_args.kwargs["params"] == {}


def test_404_maps_to_case_not_found():
    with patch.object(cc, "CARENOTE_API_KEY", "k" * 32), \
         patch.object(cc.requests, "get", return_value=_fake_resp(404)):
        with pytest.raises(cc.CarenoteCaseNotFound):
            cc.fetch_care_data(999)


def test_401_maps_to_key_error_message():
    with patch.object(cc, "CARENOTE_API_KEY", "k" * 32), \
         patch.object(cc.requests, "get", return_value=_fake_resp(401)):
        with pytest.raises(cc.CarenoteError, match="X-API-Key"):
            cc.fetch_care_data(68)


def test_connection_error_wrapped_as_carenote_error():
    with patch.object(cc, "CARENOTE_API_KEY", "k" * 32), \
         patch.object(cc.requests, "get", side_effect=cc.requests.ConnectionError("no route")):
        with pytest.raises(cc.CarenoteError, match="連線"):
            cc.fetch_care_data(68)


def test_malformed_payload_rejected():
    """上游改了介接格式（data/profile 不見）要立刻炸，不能讓空資料流進 pipeline。"""
    bad = {"caseID": 68, "profile": {}, "data": "not-a-list"}
    with patch.object(cc, "CARENOTE_API_KEY", "k" * 32), \
         patch.object(cc.requests, "get", return_value=_fake_resp(200, bad)):
        with pytest.raises(cc.CarenoteError, match="介接格式"):
            cc.fetch_care_data(68)
