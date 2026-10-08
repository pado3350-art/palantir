"""DuckDB 저장소. 모든 쓰기는 upsert라서 같은 데이터를 다시 넣어도 중복이 생기지 않는다."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

DEFAULT_DB_PATH = Path("data/realestate.duckdb")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stat_table (
    statbl_id  TEXT NOT NULL,
    cycle      TEXT NOT NULL,
    name       TEXT,
    raw        JSON,
    updated_at TIMESTAMP,
    PRIMARY KEY (statbl_id, cycle)
);

CREATE TABLE IF NOT EXISTS region (
    region_code TEXT PRIMARY KEY,
    name        TEXT,
    full_name   TEXT
);

CREATE TABLE IF NOT EXISTS observation (
    statbl_id   TEXT NOT NULL,
    cycle       TEXT NOT NULL,
    region_code TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    period      TEXT NOT NULL,
    period_desc TEXT,
    item_name   TEXT,
    value       DOUBLE,
    unit        TEXT,
    fetched_at  TIMESTAMP,
    PRIMARY KEY (statbl_id, cycle, region_code, item_id, period)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    statbl_id TEXT,
    cycle     TEXT,
    period    TEXT,
    status    TEXT,
    row_count INTEGER,
    error     TEXT,
    run_at    TIMESTAMP
);
"""


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(path))
        self.conn.execute(SCHEMA)

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # ---- 쓰기 -----------------------------------------------------------

    def upsert_tables(self, rows: Iterable[dict[str, Any]]) -> int:
        now = datetime.now()
        records = [
            (r["STATBL_ID"], r.get("DTACYCLE_CD") or "", r.get("STATBL_NM"), json.dumps(r, ensure_ascii=False), now)
            for r in rows
            if r.get("STATBL_ID")
        ]
        if records:
            self.conn.executemany("INSERT OR REPLACE INTO stat_table VALUES (?, ?, ?, ?, ?)", records)
        return len(records)

    def upsert_observations(self, observations: Iterable[dict[str, Any]]) -> int:
        """`rone.to_observation` 결과를 저장하고, 지역 마스터도 함께 갱신한다."""
        now = datetime.now()
        obs_records = []
        regions: dict[str, tuple[str, str | None, str | None]] = {}
        for o in observations:
            region_code = o.get("region_code") or ""
            obs_records.append(
                (
                    o["statbl_id"],
                    o.get("cycle") or "",
                    region_code,
                    o.get("item_id") or "",
                    o["period"],
                    o.get("period_desc"),
                    o.get("item_name"),
                    o.get("value"),
                    o.get("unit"),
                    now,
                )
            )
            if region_code:
                regions[region_code] = (region_code, o.get("region_short_name"), o.get("region_name"))
        if obs_records:
            self.conn.executemany(
                "INSERT OR REPLACE INTO observation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", obs_records
            )
        if regions:
            self.conn.executemany("INSERT OR REPLACE INTO region VALUES (?, ?, ?)", list(regions.values()))
        return len(obs_records)

    def log_ingest(
        self, statbl_id: str, cycle: str, period: str | None, status: str, row_count: int, error: str | None = None
    ) -> None:
        self.conn.execute(
            "INSERT INTO ingest_log VALUES (?, ?, ?, ?, ?, ?, ?)",
            (statbl_id, cycle, period, status, row_count, error, datetime.now()),
        )

    # ---- 읽기 -----------------------------------------------------------

    def stored_periods(self, statbl_id: str, cycle: str) -> list[str]:
        """저장된 기준시점을 오름차순으로."""
        rows = self.conn.execute(
            "SELECT DISTINCT period FROM observation WHERE statbl_id = ? AND cycle = ? ORDER BY period",
            (statbl_id, cycle),
        ).fetchall()
        return [r[0] for r in rows]

    def search_tables(self, keyword: str | None = None) -> list[tuple[str, str, str]]:
        sql = "SELECT statbl_id, cycle, name FROM stat_table"
        params: tuple = ()
        if keyword:
            sql += " WHERE name LIKE ?"
            params = (f"%{keyword}%",)
        return self.conn.execute(sql + " ORDER BY statbl_id, cycle", params).fetchall()

    def count_observations(self, statbl_id: str | None = None) -> int:
        if statbl_id:
            return self.conn.execute(
                "SELECT count(*) FROM observation WHERE statbl_id = ?", (statbl_id,)
            ).fetchone()[0]
        return self.conn.execute("SELECT count(*) FROM observation").fetchone()[0]
