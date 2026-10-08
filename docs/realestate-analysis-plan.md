# 한국부동산원 공공 API 연동 부동산 분석 프로그램 — 개발 계획

## 1. 목표

한국부동산원(REB)이 공개하는 부동산 통계·거래 데이터를 공공 API로 자동 수집하고,
지역·기간·유형별로 가격 동향을 분석·시각화하는 프로그램을 만든다.

핵심 질문 예시
- 우리 지역 아파트 매매/전세 가격지수는 최근 1년간 어떻게 움직였나?
- 전세가율(전세/매매)이 높은 지역은 어디인가? (갭 리스크)
- 수도권 vs 지방, 시·군·구별 상승/하락 순위는?
- 미분양·청약 경쟁률 같은 공급 지표와 가격의 관계는?

## 2. 데이터 소스

| 구분 | 출처 | 주요 데이터 | 비고 |
|---|---|---|---|
| **R-ONE Open API** (주력) | 한국부동산원 부동산통계정보시스템 `www.reb.or.kr/r-one` | 주간/월간 아파트 가격동향(매매·전세 지수, 변동률), 주택가격동향, 전월세전환율, 지역별 거래현황 등 | 회원가입 후 인증키 발급 |
| 공공데이터포털 (보조) | `data.go.kr` — 한국부동산원 제공 API | 청약홈 분양정보·경쟁률, 공동주택 단지정보 등 | 서비스별 활용신청 필요 |
| 국토교통부 실거래가 API (선택) | `data.go.kr` — 아파트 매매/전월세 실거래 | 개별 거래 단위 가격 | 단지 단위 분석 시 결합 |

### R-ONE Open API 구조 (Phase 0에서 실제 응답으로 재확인)

```
GET https://www.reb.or.kr/r-one/openapi/SttsApiTbl.do       # 통계표 목록
GET https://www.reb.or.kr/r-one/openapi/SttsApiTblItm.do    # 통계표 항목/분류
GET https://www.reb.or.kr/r-one/openapi/SttsApiTblData.do   # 통계 데이터 조회
```

공통 파라미터: `KEY`(인증키), `Type=json`, `pIndex`(페이지), `pSize`(페이지 크기)
데이터 조회 파라미터: `STATBL_ID`(통계표 ID), `DTACYCLE_CD`(주기: `WK`/`MM`/`QY`/`YY`),
`WRTTIME_IDTFR_ID`(기준시점), `CLS_ID`(지역 등 분류), `ITM_ID`(항목)

> 통계표 ID는 하드코딩하지 않고 `SttsApiTbl.do` 결과를 카탈로그로 저장한 뒤 설정 파일에서 선택한다.

## 3. 아키텍처

```
┌────────────┐   ┌──────────────┐   ┌───────────┐   ┌──────────────┐
│ API Client │──▶│ ETL/정규화    │──▶│ Storage   │──▶│ 분석 모듈     │
│ (R-ONE,    │   │ (스키마 통일, │   │ (DuckDB   │   │ (지표 계산,   │
│  data.go)  │   │  지역코드 매핑)│   │  /Parquet)│   │  랭킹, 추세)  │
└────────────┘   └──────────────┘   └───────────┘   └──────┬───────┘
       ▲                                                   │
  스케줄러(주 1회/월 1회 증분 수집)              ┌──────────┴─────────┐
                                                 │ CLI · 대시보드 · 리포트 │
                                                 └────────────────────┘
```

### 기술 스택 (제안)
- 언어: Python 3.12
- 수집: `httpx` (재시도·타임아웃), `tenacity`
- 처리: `pandas` / `polars`
- 저장: DuckDB (로컬 분석 DB) + 원본 JSON/Parquet 보관
- 시각화: Streamlit + Plotly (지도는 시·군·구 GeoJSON)
- 스케줄: cron 또는 GitHub Actions
- 설정/비밀: `.env` (`REB_API_KEY`, `DATA_GO_KR_KEY`) — 저장소에 커밋 금지

### 디렉터리 구조
```
realestate/
├── config/
│   ├── settings.py          # 환경변수, 경로
│   └── tables.yaml          # 수집 대상 통계표 ID·주기·항목
├── clients/
│   ├── rone.py              # R-ONE API 클라이언트 (페이지네이션, 에러코드 처리)
│   └── datagokr.py          # 공공데이터포털 클라이언트 (XML/JSON)
├── etl/
│   ├── catalog.py           # 통계표 목록 → catalog 테이블
│   ├── ingest.py            # 원본 수집 + 증분 로직
│   ├── normalize.py         # long format 변환, 지역코드(법정동) 매핑
│   └── regions.py           # 시도/시군구 코드 마스터
├── storage/
│   └── db.py                # DuckDB 스키마, upsert
├── analysis/
│   ├── trends.py            # 누적/전년동기 변동률, 이동평균
│   ├── ranking.py           # 지역별 상승·하락 순위
│   ├── jeonse_ratio.py      # 전세가율, 갭 지표
│   └── signals.py           # 반등/하락 전환, 이상치 탐지
├── app/
│   └── dashboard.py         # Streamlit 대시보드
├── cli.py                   # `python -m realestate.cli fetch|analyze|report`
└── tests/                   # 응답 fixture 기반 단위 테스트
```

## 4. 데이터 모델

```sql
-- 통계표 카탈로그
CREATE TABLE stat_table (
  statbl_id TEXT PRIMARY KEY, name TEXT, cycle TEXT, source TEXT, updated_at TIMESTAMP
);

-- 지역 마스터
CREATE TABLE region (
  region_code TEXT PRIMARY KEY, sido TEXT, sigungu TEXT, level INT, parent_code TEXT
);

-- 통계 관측값 (long format)
CREATE TABLE observation (
  statbl_id TEXT, region_code TEXT, item_id TEXT, item_name TEXT,
  period TEXT,          -- 2026-W40 / 2026-09
  period_date DATE,     -- 정렬·조인용 기준일
  value DOUBLE, unit TEXT,
  fetched_at TIMESTAMP,
  PRIMARY KEY (statbl_id, region_code, item_id, period)
);

-- 수집 이력 (증분·재시도용)
CREATE TABLE ingest_log (
  statbl_id TEXT, period TEXT, status TEXT, row_count INT, error TEXT, run_at TIMESTAMP
);
```

## 5. 분석 기능

| 기능 | 내용 |
|---|---|
| 가격 추이 | 매매·전세 지수 시계열, 주간/월간 변동률, 52주 누적, 전년 동기 대비 |
| 지역 랭킹 | 기간별 상승·하락 Top/Bottom N, 수도권/지방/5대광역시 그룹 비교 |
| 전세가율 | 지역별 전세/매매 비율 추이, 임계치(예: 80%) 초과 지역 경보 |
| 추세 전환 | 연속 하락 후 상승 전환, 이동평균 골든/데드크로스 |
| 공급·수요 | 청약 경쟁률·분양 물량과 가격 변동 상관 분석 |
| 지도 | 시·군·구 단위 단계구분도(choropleth) |
| 리포트 | 주간 요약(Markdown/HTML) 자동 생성 |

## 6. 단계별 일정

| Phase | 기간 | 산출물 | 완료 기준 |
|---|---|---|---|
| **0. 사전 준비** | 1~2일 | R-ONE·data.go.kr 인증키, 대상 통계표 ID 목록, 응답 샘플 fixture | 실제 API 호출 성공, `tables.yaml` 확정 |
| **1. 수집기** | 1주 | `clients/rone.py`, 페이지네이션·재시도·에러코드 처리, 원본 저장 | 대상 통계표 전 기간 백필 완료 |
| **2. ETL·저장** | 1주 | 정규화, 지역코드 매핑, DuckDB upsert, 증분 수집 | 중복 없이 재실행 가능(idempotent) |
| **3. 분석 모듈** | 1~2주 | 추이·랭킹·전세가율·신호 함수 + 테스트 | 단위 테스트 통과, 공표 수치와 샘플 대조 일치 |
| **4. 대시보드/CLI** | 1주 | Streamlit 화면(지역 선택, 기간 필터, 지도), CLI | 주요 질문에 3클릭 이내 답 |
| **5. 자동화·운영** | 3~5일 | 주간 스케줄 수집, 실패 알림, 주간 리포트 | 1개월 무중단 운영 |
| 6. 확장(선택) | — | 실거래가 결합(단지 단위), 가격 예측 모델(ARIMA/Prophet) | — |

## 7. 리스크와 대응

| 리스크 | 대응 |
|---|---|
| 일일 호출 한도·트래픽 제한 | 증분 수집, 요청 간 지연, 원본 캐시, 백필은 야간 분할 실행 |
| 통계표 ID·항목 체계 변경 | 카탈로그 주기적 동기화, 스키마 변경 감지 시 알림 |
| 지역 코드 개편(행정구역 변경) | 코드 이력 테이블, 구→신 코드 매핑 |
| 공표 후 수치 수정 | 최근 N주기 재수집 후 upsert |
| 인증키 노출 | `.env` + `.gitignore`, CI는 Secrets 사용 |
| 이용 조건 | 출처 표기("한국부동산원"), 각 API 이용약관 준수 |

## 8. 바로 다음 할 일

1. R-ONE Open API 회원가입 및 인증키 발급
2. `SttsApiTbl.do`로 통계표 목록 조회 → 주간 아파트 매매/전세 가격지수 통계표 ID 확정
3. 저장소에 Python 프로젝트 골격(`pyproject.toml`, `.env.example`, `.gitignore`) 생성
4. `clients/rone.py` 작성 및 fixture 기반 테스트
