"""명령줄 도구.

    python -m realestate.cli tables [--search 아파트]
    python -m realestate.cli items STATBL_ID
    python -m realestate.cli data STATBL_ID --cycle MM [--period 202409] [--cls CLS_ID] [--itm ITM_ID]

결과는 CSV로 표준출력에 쓴다 (`> out.csv`로 저장).
"""

import argparse
import csv
import sys
from collections.abc import Iterable

from realestate.clients.rone import CYCLES, RoneClient, to_observation
from realestate.config import get_api_key


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
    parser = argparse.ArgumentParser(prog="realestate", description="한국부동산원 R-ONE 데이터 조회")
    sub = parser.add_subparsers(dest="command", required=True)

    p_tables = sub.add_parser("tables", help="통계표 목록")
    p_tables.add_argument("--search", help="통계표명에 포함된 단어로 필터")

    p_items = sub.add_parser("items", help="통계표 항목/분류 목록")
    p_items.add_argument("statbl_id")

    p_data = sub.add_parser("data", help="통계 데이터 조회")
    p_data.add_argument("statbl_id")
    p_data.add_argument("--cycle", required=True, choices=list(CYCLES))
    p_data.add_argument("--period", help="기준시점 (예: 월간 202409)")
    p_data.add_argument("--cls", dest="cls_id", help="분류(지역) ID")
    p_data.add_argument("--itm", dest="itm_id", help="항목 ID")
    p_data.add_argument("--raw", action="store_true", help="정규화하지 않은 원본 필드로 출력")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    with RoneClient(get_api_key()) as client:
        if args.command == "tables":
            rows = client.list_tables()
            if args.search:
                rows = (r for r in rows if args.search in r.get("STATBL_NM", ""))
        elif args.command == "items":
            rows = client.list_items(args.statbl_id)
        else:
            rows = client.get_data(
                args.statbl_id, args.cycle, period=args.period, cls_id=args.cls_id, itm_id=args.itm_id
            )
            if not args.raw:
                rows = map(to_observation, rows)
        count = write_csv(rows)
    print(f"{count}건", file=sys.stderr)


if __name__ == "__main__":
    main()
