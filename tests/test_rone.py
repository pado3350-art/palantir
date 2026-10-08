import httpx
import pytest

from realestate.clients.rone import RoneClient, RoneError, parse_response, to_observation


def ok_page(service, total, rows):
    return {
        service: [
            {"head": [{"list_total_count": total}, {"RESULT": {"CODE": "INFO-000", "MESSAGE": "정상"}}]},
            {"row": rows},
        ]
    }


def data_row(cls_id, period, val):
    return {
        "STATBL_ID": "A_2024_00045",
        "DTACYCLE_CD": "MM",
        "WRTTIME_IDTFR_ID": period,
        "WRTTIME_DESC": f"{period[:4]}년 {period[4:]}월",
        "CLS_ID": cls_id,
        "CLS_NM": "서울",
        "CLS_FULLNM": "전국>서울",
        "ITM_ID": "100001",
        "ITM_NM": "지수",
        "DTA_VAL": val,
        "UI_NM": "지수",
    }


def make_client(handler, **kwargs):
    return RoneClient("test-key", transport=httpx.MockTransport(handler), backoff=0, **kwargs)


def test_paginates_until_total():
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        page = int(request.url.params["pIndex"])
        rows = [data_row("500001", f"20240{page}", "100.5")] * (2 if page < 3 else 1)
        return httpx.Response(200, json=ok_page("SttsApiTblData", 5, rows))

    with make_client(handler, page_size=2) as client:
        rows = list(client.get_data("A_2024_00045", "MM"))

    assert len(rows) == 5
    assert [c["pIndex"] for c in calls] == ["1", "2", "3"]
    assert calls[0]["KEY"] == "test-key"
    assert calls[0]["Type"] == "json"
    assert calls[0]["STATBL_ID"] == "A_2024_00045"
    assert calls[0]["DTACYCLE_CD"] == "MM"
    assert "CLS_ID" not in calls[0]


def test_no_data_returns_empty():
    def handler(request):
        return httpx.Response(200, json={"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}})

    with make_client(handler) as client:
        assert list(client.get_data("X", "MM", period="209901")) == []


def test_error_code_raises():
    def handler(request):
        return httpx.Response(200, json={"RESULT": {"CODE": "ERROR-290", "MESSAGE": "인증키가 유효하지 않습니다."}})

    with make_client(handler) as client, pytest.raises(RoneError) as exc:
        list(client.list_tables())
    assert exc.value.code == "ERROR-290"


def test_retries_server_errors():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=ok_page("SttsApiTbl", 1, [{"STATBL_ID": "T1"}]))

    with make_client(handler, max_retries=3) as client:
        assert list(client.list_tables()) == [{"STATBL_ID": "T1"}]
    assert len(attempts) == 3


def test_client_error_not_retried():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(404)

    with make_client(handler) as client, pytest.raises(httpx.HTTPStatusError):
        list(client.list_tables())
    assert len(attempts) == 1


def test_invalid_cycle():
    with make_client(lambda r: httpx.Response(200)) as client, pytest.raises(ValueError):
        client.get_data("X", "DAY")


def test_head_error_in_service_block():
    payload = {
        "SttsApiTbl": [{"head": [{"list_total_count": 0}, {"RESULT": {"CODE": "ERROR-300", "MESSAGE": "필수 값 누락"}}]}]
    }
    with pytest.raises(RoneError):
        parse_response("SttsApiTbl", payload)


def test_to_observation():
    obs = to_observation(data_row("500001", "202409", "101.23"))
    assert obs["region_code"] == "500001"
    assert obs["region_name"] == "전국>서울"
    assert obs["period"] == "202409"
    assert obs["value"] == pytest.approx(101.23)
    assert to_observation(data_row("500001", "202409", "-"))["value"] is None
