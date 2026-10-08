"""R-ONE → DuckDB 수집.

증분 규칙
- 저장된 데이터가 없거나 full=True면 기준시점 없이 전체를 받는다.
- 이미 있으면 최근 `refresh_recent`개 기준시점(공표 후 수정 반영)과
  그 이후 기준시점(월간·연간만 계산 가능)을 기준시점별로 받는다.
- 기준시점 형식을 계산할 수 없는 주기(주간 등)는 전체를 다시 받는다. upsert라 중복은 없다.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from realestate.clients.rone import RoneClient, RoneError, to_observation
from realestate.storage.db import Store

DEFAULT_TARGETS_PATH = Path("config/tables.toml")


@dataclass
class Target:
    statbl_id: str
    cycle: str
    name: str = ""


@dataclass
class IngestResult:
    statbl_id: str
    cycle: str
    rows: int = 0
    periods: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def load_targets(path: Path = DEFAULT_TARGETS_PATH) -> list[Target]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return [Target(t["statbl_id"], t["cycle"], t.get("name", "")) for t in data.get("tables", [])]


def sync_catalog(client: RoneClient, store: Store) -> int:
    return store.upsert_tables(client.list_tables())


def next_periods(last: str, cycle: str, today: date) -> list[str] | None:
    """last 다음부터 today가 속한 기간까지의 기준시점. 계산할 수 없으면 None."""
    if cycle == "MM" and len(last) == 6 and last.isdigit():
        year, month = int(last[:4]), int(last[4:])
        out = []
        while True:
            month += 1
            if month > 12:
                year, month = year + 1, 1
            if (year, month) > (today.year, today.month):
                return out
            out.append(f"{year:04d}{month:02d}")
    if cycle == "YY" and len(last) == 4 and last.isdigit():
        return [str(y) for y in range(int(last) + 1, today.year + 1)]
    return None


def ingest_table(
    client: RoneClient,
    store: Store,
    statbl_id: str,
    cycle: str,
    *,
    full: bool = False,
    refresh_recent: int = 3,
    today: date | None = None,
) -> IngestResult:
    result = IngestResult(statbl_id, cycle)
    stored = store.stored_periods(statbl_id, cycle)

    upcoming = None if full or not stored else next_periods(stored[-1], cycle, today or date.today())
    if upcoming is None:
        _fetch(client, store, result, None)
        return result

    recent = stored[-refresh_recent:] if refresh_recent > 0 else []
    for period in recent + upcoming:
        _fetch(client, store, result, period)
    return result


def _fetch(client: RoneClient, store: Store, result: IngestResult, period: str | None) -> None:
    try:
        rows = client.get_data(result.statbl_id, result.cycle, period=period)
        count = store.upsert_observations(map(to_observation, rows))
    except RoneError as exc:
        store.log_ingest(result.statbl_id, result.cycle, period, "error", 0, str(exc))
        result.errors.append(f"{period or '전체'}: {exc}")
        return
    store.log_ingest(result.statbl_id, result.cycle, period, "ok", count)
    result.rows += count
    if period:
        result.periods.append(period)
