# palantir
palantir medical ontology

## realestate — 한국부동산원 R-ONE 데이터 분석 도구

계획: [docs/realestate-analysis-plan.md](docs/realestate-analysis-plan.md)

```bash
pip install -e ".[dev]"
cp .env.example .env          # REB_API_KEY 입력
python -m realestate.cli tables --search 아파트 > tables.csv   # 통계표 목록
python -m realestate.cli items <STATBL_ID>                     # 항목/지역 분류
python -m realestate.cli data <STATBL_ID> --cycle MM --period 202409 > data.csv
pytest
```
