# palantir
palantir medical ontology

## realestate — 한국부동산원 R-ONE 데이터 분석 도구

계획: [docs/realestate-analysis-plan.md](docs/realestate-analysis-plan.md)

```bash
pip install -e ".[dev]"
cp .env.example .env                       # REB_API_KEY 입력

# 1) 통계표 목록 저장 후 검색
python -m realestate.cli sync-catalog
python -m realestate.cli tables --search 아파트

# 2) config/tables.toml에 수집 대상 STATBL_ID·주기 입력 후 수집 (data/realestate.duckdb)
python -m realestate.cli ingest            # 증분: 최근 3개 기준시점 재수집 + 새 기준시점
python -m realestate.cli ingest --full     # 전체 재수집
python -m realestate.cli ingest <STATBL_ID> --cycle MM

# API 직접 조회 (저장 안 함, CSV 출력)
python -m realestate.cli items <STATBL_ID>
python -m realestate.cli data <STATBL_ID> --cycle MM --period 202409 > data.csv

pytest
```
