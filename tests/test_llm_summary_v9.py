# 鎖住 llm_summary_v9.py 的安全設計：任何失敗都要回退到原始子句，絕對不能
# 讓LLM改到子句裡的數字/日期/訊號名稱。不打真實API，用unittest.mock模擬
# OpenAI client（跟v2的test_llm_summary.py一樣，不對真實LLM服務發請求）。
from unittest.mock import MagicMock, patch

from src.llm_summary_v9 import polish_clause


def _fake_client(reply_text):
    client = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=reply_text))]
    client.chat.completions.create.return_value = resp
    return client


def test_polish_clause_returns_llm_text_when_validation_passes():
    clause = "11/5–11/9期間，心率上升、血氧下降同時出現異常。"
    with patch("src.llm_summary_v9.OpenAI", return_value=_fake_client(
        "11月5日至9日期間，心率上升與血氧下降同時出現異常。"
    )):
        result = polish_clause(clause)
    assert result["generation_method"] == "llm"
    assert "心率上升" in result["text"]
    assert "血氧下降" in result["text"]


def test_polish_clause_falls_back_when_llm_leaks_foreign_text():
    """白名單驗證應該擋下夾雜英文/其他語言的輸出，重試3次都失敗後
    回退到原始子句——跟v2的白名單機制同一套邏輯。"""
    clause = "11/5–11/9期間，心率上升、血氧下降同時出現異常。"
    with patch("src.llm_summary_v9.OpenAI", return_value=_fake_client(
        "During 11/5-11/9, heart rate rose significantly."
    )):
        result = polish_clause(clause)
    assert result["generation_method"] == "fallback"
    assert result["text"] == clause


def test_polish_clause_falls_back_on_connection_error():
    clause = "11/5–11/9期間，心率上升、血氧下降同時出現異常。"
    with patch("src.llm_summary_v9.OpenAI", side_effect=ConnectionError("no route")):
        result = polish_clause(clause)
    assert result == {"text": clause, "generation_method": "fallback"}


def test_polish_clause_skips_llm_entirely_when_clause_is_empty():
    """完全沒有跨指標關聯(clause=None/空字串)時，不需要打LLM——省一次不必要
    的呼叫，回傳的generation_method明確是fallback。"""
    result = polish_clause(None)
    assert result == {"text": None, "generation_method": "fallback"}
    result2 = polish_clause("")
    assert result2 == {"text": "", "generation_method": "fallback"}


def test_polish_clause_rejects_garbled_signal_name():
    """2026-08-04實測抓到的真bug：LLM把「情緒異常」潤成「的情異常」——每個
    字都是合法繁體中文，_validate()白名單機制抓不到，但這個詞已經不存在了。
    required_terms傳入原始訊號名稱清單後，這種竄改要被擋下、回退到原始子句，
    不能讓亂碼詞流出到使用者看到的報告。"""
    clause = "11/1–11/14期間，心率上升、情緒異常、精神不佳同時出現異常。"
    with patch("src.llm_summary_v9.OpenAI", return_value=_fake_client(
        "在11月1日至14日期間，出現心率的上升、的情異常、精神不佳等異常現象。"
    )):
        result = polish_clause(clause, required_terms=["心率上升", "情緒異常", "精神不佳"])
    assert result["generation_method"] == "fallback"
    assert result["text"] == clause


def test_polish_clause_rejects_signal_name_semantic_substitution():
    """2026-08-04實測抓到的另一種真bug：LLM把「精神不佳」換成語意不同的
    「精神壓力下」——輸出本身語法通順、每個字都合法，_validate()一樣抓不到，
    但這是竄改訊號名稱本身，違反system prompt「不可修改訊號名稱」的要求。"""
    clause = "11/1–11/14期間、心率上升、精神不佳同時出現異常。"
    with patch("src.llm_summary_v9.OpenAI", return_value=_fake_client(
        "11月1日至14日期間，心率上升、精神壓力下同時出現異常。"
    )):
        result = polish_clause(clause, required_terms=["心率上升", "精神不佳"])
    assert result["generation_method"] == "fallback"
    assert result["text"] == clause


def test_polish_clause_accepts_reworded_text_that_keeps_all_signal_names_verbatim():
    """required_terms檢查只要求訊號名稱逐字倖存，不要求整句一字不改——語序/
    連接詞/日期格式可以調整，這是正常的潤飾範圍，不該被新檢查誤擋。"""
    clause = "11/5–11/9期間，心率上升、血氧下降同時出現異常。"
    with patch("src.llm_summary_v9.OpenAI", return_value=_fake_client(
        "11月5日至9日這段期間，同時出現心率上升與血氧下降的情形。"
    )):
        result = polish_clause(clause, required_terms=["心率上升", "血氧下降"])
    assert result["generation_method"] == "llm"
    assert "心率上升" in result["text"]
    assert "血氧下降" in result["text"]
