"""명령줄 도구.

API 직접 조회 (CSV를 표준출력으로):
    python -m realestate.cli items STATBL_ID
    python -m realestate.cli data STATBL_ID --cycle MM [--period 202409] [--cls CLS_ID] [--itm ITM_ID]

DuckDB 저장소 (기본 data/realestate.duckdb, --db로 변경):
    python -m realestate.cli sync-catalog                 # 통계표 목록 저장
    python -m realestate.cli tables [--search 아파트]       # 저장된 통계표 목록 검색
    python -m realestate.cli ingest                       # config/tables.toml 대상 증분 수집
    python -m realestate.cli ingest STATBL_ID --cycle MM  # 특정 통계표만
    python -m realestate.cli ingest ... --full            # 전체 다시 받기
"""

import argparse
import csv
import sys
from collections.abc import Iterable
from pathlib import Path

from realestate.clients.rone import CYCLES, RoneClient, to_observation
from realestate.config import get_api_key
from realestate.etl.ingest import DEFAULT_TARGETS_PATH, Target, ingest_table, load_targets, sync_catalog
from realestate.storage.db import DEFAULT_DB_PATH, Store


def write_csv(rows: Iterable[dict]) -> int:
    writer = None
    count = 0
    for row in rows:
        if writer is None:
            writer = csv.DictWriter(sys.stdout, fieldnames=list(row), extrasaction="ignore")
            writer.writeheader()
        writer.writerow(row)
        count += 1
    return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="realestate", description="한국부동산원 R-ONE 데이터 조회·수집")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB 파일 경로")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sync-catalog", help="통계표 목록을 API에서 받아 저장")

    p_tables = sub.add_parser("tables", help="저장된 통계표 목록 검색")
    p_tables.add_argument("--search", help="통계표명에 포함된 단어")

    p_items = sub.add_parser("items", help="통계표 항목/분류 목록 (API)")
    p_items.add_argument("statbl_id")

    p_data = sub.add_parser("data", help="통계 데이터 조회 (API, 저장 안 함)")
    p_data.add_argument("statbl_id")
    p_data.add_argument("--cycle", required=True, choices=list(CYCLES))
    p_data.add_argument("--period", help="기준시점 (예: 월간 202409)")
    p_data.add_argument("--cls", dest="cls_id", help="분류(지역) ID")
    p_data.add_argument("--itm", dest="itm_id", help="항목 ID")
    p_data.add_argument("--raw", action="store_true", help="정규화하지 않은 원본 필드로 출력")

    p_ingest = sub.add_parser("ingest", help="통계 데이터를 수집해 저장 (증분)")
    p_ingest.add_argument("statbl_id", nargs="?", help="생략하면 --config의 대상 전체")
    p_ingest.add_argument("--cycle", choices=list(CYCLES))
    p_ingest.add_argument("--config", type=Path, default=DEFAULT_TARGETS_PATH)
    p_ingest.add_argument("--full", action="store_true", help="저장된 데이터와 무관하게 전체 수집")
    p_ingest.add_argument("--refresh-recent", type=int, default=3, help="다시 받을 최근 기준시점 수")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "tables":
        with Store(args.db) as store:
            rows = store.search_tables(args.search)
        if not rows:
            print("저장된 통계표가 없습니다. 먼저 sync-catalog를 실행하세요.", file=sys.stderr)
            return
        write_csv({"statbl_id": s, "cycle": c, "name": n} for s, c, n in rows)
        return

    if args.command == "ingest":
        if args.statbl_id:
            if not args.cycle:
                parser.error("통계표 ID를 지정하면 --cycle도 필요합니다.")
            targets = [Target(args.statbl_id, args.cycle)]
        else:
            targets = load_targets(args.config)
            if not targets:
                parser.error(f"{args.config}에 수집 대상이 없습니다.")

    with RoneClient(get_api_key()) as client:
        if args.command == "sync-catalog":
            with Store(args.db) as store:
                print(f"통계표 {sync_catalog(client, store)}건 저장", file=sys.stderr)
        elif args.command == "ingest":
            failed = False
            with Store(args.db) as store:
                for t in targets:
                    r = ingest_table(
                        client, store, t.statbl_id, t.cycle, full=args.full, refresh_recent=args.refresh_recent
                    )
                    scope = f"기준시점 {len(r.periods)}개" if r.periods else "전체"
                    print(f"{t.statbl_id} ({t.cycle}) {t.name}: {r.rows}건 저장, {scope}", file=sys.stderr)
                    for err in r.errors:
                        print(f"  오류 {err}", file=sys.stderr)
                    failed |= bool(r.errors)
            if failed:
                sys.exit(1)
        else:
            if args.command == "items":
                rows = client.list_items(args.statbl_id)
            else:
                rows = client.get_data(
                    args.statbl_id, args.cycle, period=args.period, cls_id=args.cls_id, itm_id=args.itm_id
                )
                if not args.raw:
                    rows = map(to_observation, rows)
            print(f"{write_csv(rows)}건", file=sys.stderr)


if __name__ == "__main__":
    main()
