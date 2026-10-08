"""한국부동산원 R-ONE Open API 클라이언트.

서비스
- SttsApiTbl      : 통계표 목록
- SttsApiTblItm   : 통계표 항목/분류
- SttsApiTblData  : 통계 데이터

응답(JSON) 형식
    {"<서비스명>": [
        {"head": [{"list_total_count": N}, {"RESULT": {"CODE": "INFO-000", "MESSAGE": "..."}}]},
        {"row": [{...}, ...]}
    ]}
오류 시에는 최상위에 {"RESULT": {"CODE": "ERROR-xxx", "MESSAGE": "..."}} 만 온다.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

BASE_URL = "https://www.reb.or.kr/r-one/openapi"

CODE_OK = "INFO-000"
CODE_NO_DATA = "INFO-200"

# 주기 코드
CYCLES = {"WK": "주", "MM": "월", "QY": "분기", "HY": "반기", "YY": "년"}


class RoneError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class RoneClient:
    def __init__(
        self,
        api_key: str,
        *,
        page_size: int = 1000,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff: float = 1.0,
        base_url: str = BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ):
        self.api_key = api_key
        self.page_size = page_size
        self.max_retries = max_retries
        self.backoff = backoff
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def __enter__(self) -> RoneClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ---- 공개 API -------------------------------------------------------

    def list_tables(self) -> Iterator[dict[str, Any]]:
        """통계표 목록 (STATBL_ID, STATBL_NM, DTACYCLE_CD ...)."""
        return self._paginate("SttsApiTbl", {})

    def list_items(self, statbl_id: str) -> Iterator[dict[str, Any]]:
        """통계표의 항목/분류 목록 (ITM_ID, ITM_NM, CLS_ID ...)."""
        return self._paginate("SttsApiTblItm", {"STATBL_ID": statbl_id})

    def get_data(
        self,
        statbl_id: str,
        cycle: str,
        *,
        period: str | None = None,
        cls_id: str | None = None,
        itm_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """통계 데이터. period는 기준시점(WRTTIME_IDTFR_ID, 예: 월간 '202409')."""
        if cycle not in CYCLES:
            raise ValueError(f"알 수 없는 주기 코드: {cycle} (가능: {', '.join(CYCLES)})")
        params = {
            "STATBL_ID": statbl_id,
            "DTACYCLE_CD": cycle,
            "WRTTIME_IDTFR_ID": period,
            "CLS_ID": cls_id,
            "ITM_ID": itm_id,
        }
        return self._paginate("SttsApiTblData", {k: v for k, v in params.items() if v})

    # ---- 내부 -----------------------------------------------------------

    def _paginate(self, service: str, params: dict[str, str]) -> Iterator[dict[str, Any]]:
        page = 1
        seen = 0
        while True:
            total, rows = self._fetch_page(service, params, page)
            yield from rows
            seen += len(rows)
            if not rows or seen >= total:
                return
            page += 1

    def _fetch_page(
        self, service: str, params: dict[str, str], page: int
    ) -> tuple[int, list[dict[str, Any]]]:
        query = {
            "KEY": self.api_key,
            "Type": "json",
            "pIndex": page,
            "pSize": self.page_size,
            **params,
        }
        payload = self._get_json(f"/{service}.do", query)
        return parse_response(service, payload)

    def _get_json(self, path: str, query: dict[str, Any]) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._http.get(path, params=query)
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = isinstance(exc, httpx.TransportError) or exc.response.status_code >= 500
                if not retryable or attempt == self.max_retries:
                    raise
                time.sleep(self.backoff * 2**attempt)
        raise AssertionError("unreachable")


def parse_response(service: str, payload: Any) -> tuple[int, list[dict[str, Any]]]:
    """응답 JSON에서 (전체 건수, 행 목록)을 꺼낸다. 오류 코드면 RoneError."""
    if not isinstance(payload, dict):
        raise RoneError("PARSE", f"예상치 못한 응답 형식: {type(payload).__name__}")

    if service not in payload:
        result = payload.get("RESULT", {})
        code = result.get("CODE", "PARSE")
        if code == CODE_NO_DATA:
            return 0, []
        raise RoneError(code, result.get("MESSAGE", f"'{service}' 키가 없는 응답"))

    total = 0
    rows: list[dict[str, Any]] = []
    for block in payload[service]:
        for head in block.get("head", []):
            if "list_total_count" in head:
                total = int(head["list_total_count"])
            if "RESULT" in head:
                code = head["RESULT"].get("CODE", "")
                if code == CODE_NO_DATA:
                    return 0, []
                if code != CODE_OK:
                    raise RoneError(code, head["RESULT"].get("MESSAGE", ""))
        rows.extend(block.get("row", []))
    return total, rows


def to_observation(row: dict[str, Any]) -> dict[str, Any]:
    """SttsApiTblData 행을 분석용 관측값(long format)으로 정규화한다."""
    raw = row.get("DTA_VAL")
    try:
        value = float(raw) if raw not in (None, "", "-") else None
    except (TypeError, ValueError):
        value = None
    return {
        "statbl_id": row.get("STATBL_ID"),
        "cycle": row.get("DTACYCLE_CD"),
        "period": row.get("WRTTIME_IDTFR_ID"),
        "period_desc": row.get("WRTTIME_DESC"),
        "region_code": row.get("CLS_ID"),
        "region_name": row.get("CLS_FULLNM") or row.get("CLS_NM"),
        "item_id": row.get("ITM_ID"),
        "item_name": row.get("ITM_NM"),
        "value": value,
        "unit": row.get("UI_NM"),
    }
