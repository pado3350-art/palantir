from datetime import date
from pathlib import Path

import httpx
import pytest

from realestate.clients.rone import RoneClient
from realestate.etl.ingest import ingest_table, load_targets, next_periods, sync_catalog
from realestate.storage.db import Store
from tests.test_rone import data_row, ok_page


class FakeRone:
    """기준시점별 데이터를 들고 있다가 R-ONE 응답 형식으로 돌려주는 가짜 서버."""

    def __init__(self, data: dict[str, list[dict]]):
        self.data = data
        self.fail_periods: set[str] = set()
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        service = request.url.path.rsplit("/", 1)[-1].removesuffix(".do")
        if service == "SttsApiTbl":
            rows = [
                {"STATBL_ID": "T1", "STATBL_NM": "월간 아파트 매매가격지수", "DTACYCLE_CD": "MM"},
                {"STATBL_ID": "T2", "STATBL_NM": "주간 오피스텔 전세", "DTACYCLE_CD": "WK"},
            ]
            return httpx.Response(200, json=ok_page(service, len(rows), rows))
        period = params.get("WRTTIME_IDTFR_ID")
        if period in self.fail_periods:
            return httpx.Response(200, json={"RESULT": {"CODE": "ERROR-500", "MESSAGE": "서버 오류"}})
        rows = self.data.get(period, []) if period else [r for rs in self.data.values() for r in rs]
        if not rows:
            return httpx.Response(200, json={"RESULT": {"CODE": "INFO-200", "MESSAGE": "데이터 없음"}})
        return httpx.Response(200, json=ok_page(service, len(rows), rows))

    def periods_requested(self):
        return [r.get("WRTTIME_IDTFR_ID") for r in self.requests]


def month_rows(period, value):
    return [data_row("500001", period, value), data_row("500002", period, value)]


@pytest.fixture
def store():
    with Store(":memory:") as s:
        yield s


def make_client(fake):
    return RoneClient("k", transport=httpx.MockTransport(fake), backoff=0)


def test_next_periods():
    assert next_periods("202410", "MM", date(2025, 2, 15)) == ["202411", "202412", "202501", "202502"]
    assert next_periods("202502", "MM", date(2025, 2, 15)) == []
    assert next_periods("2022", "YY", date(2024, 6, 1)) == ["2023", "2024"]
    assert next_periods("20240930", "WK", date(2024, 10, 7)) is None
    assert next_periods("2024-10", "MM", date(2025, 1, 1)) is None


def test_first_run_fetches_everything(store):
    fake = FakeRone({"202407": month_rows("202407", "100"), "202408": month_rows("202408", "101")})
    with make_client(fake) as client:
        r = ingest_table(client, store, "A_2024_00045", "MM", today=date(2024, 8, 20))
    assert r.rows == 4 and not r.errors
    assert fake.periods_requested() == [None]
    assert store.stored_periods("A_2024_00045", "MM") == ["202407", "202408"]
    names = store.conn.execute("SELECT name, full_name FROM region ORDER BY region_code").fetchall()
    assert names[0] == ("서울", "전국>서울")


def test_incremental_refreshes_recent_and_fetches_new(store):
    fake = FakeRone({p: month_rows(p, "100") for p in ["202406", "202407", "202408"]})
    with make_client(fake) as client:
        ingest_table(client, store, "A_2024_00045", "MM", today=date(2024, 8, 20))

        # 202408 수치 수정 + 202409, 202410 신규 공표
        fake.data["202408"] = month_rows("202408", "105.5")
        fake.data["202409"] = month_rows("202409", "106")
        fake.data["202410"] = month_rows("202410", "107")
        fake.requests.clear()
        r = ingest_table(client, store, "A_2024_00045", "MM", refresh_recent=2, today=date(2024, 11, 3))

    assert fake.periods_requested() == ["202407", "202408", "202409", "202410", "202411"]
    assert r.periods == ["202407", "202408", "202409", "202410", "202411"]
    assert store.stored_periods("A_2024_00045", "MM") == ["202406", "202407", "202408", "202409", "202410"]
    assert store.count_observations() == 10  # 5개월 × 2지역, 중복 없음
    value = store.conn.execute(
        "SELECT value FROM observation WHERE period = '202408' AND region_code = '500001'"
    ).fetchone()[0]
    assert value == 105.5


def test_weekly_falls_back_to_full_refetch(store):
    rows = [dict(data_row("500001", "20240930", "99"), DTACYCLE_CD="WK")]
    fake = FakeRone({"20240930": rows})
    with make_client(fake) as client:
        ingest_table(client, store, "W1", "WK")
        ingest_table(client, store, "W1", "WK")
    assert fake.periods_requested() == [None, None]
    assert store.count_observations() == 1


def test_errors_are_logged_and_other_periods_continue(store):
    fake = FakeRone({"202407": month_rows("202407", "100")})
    with make_client(fake) as client:
        ingest_table(client, store, "A_2024_00045", "MM", today=date(2024, 7, 31))
        fake.fail_periods.add("202408")
        fake.data["202409"] = month_rows("202409", "102")
        r = ingest_table(client, store, "A_2024_00045", "MM", refresh_recent=0, today=date(2024, 9, 30))

    assert len(r.errors) == 1 and "202408" in r.errors[0]
    assert r.periods == ["202409"]
    statuses = store.conn.execute("SELECT period, status FROM ingest_log ORDER BY run_at").fetchall()
    assert ("202408", "error") in statuses and ("202409", "ok") in statuses


def test_sync_catalog_and_search(store):
    with make_client(FakeRone({})) as client:
        assert sync_catalog(client, store) == 2
        assert sync_catalog(client, store) == 2
    assert store.search_tables("아파트") == [("T1", "MM", "월간 아파트 매매가격지수")]
    assert len(store.search_tables()) == 2


def test_load_targets(tmp_path):
    path = tmp_path / "tables.toml"
    path.write_text('[[tables]]\nstatbl_id = "X1"\ncycle = "MM"\nname = "테스트"\n', encoding="utf-8")
    [t] = load_targets(path)
    assert (t.statbl_id, t.cycle, t.name) == ("X1", "MM", "테스트")
    assert load_targets(Path("config/tables.toml")) == []
