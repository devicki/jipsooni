from __future__ import annotations

from datetime import date, timedelta
from math import ceil
from typing import Any

import httpx

from jipsooni.config import Settings, kr_client
from jipsooni.models import Notice
from jipsooni.sources.base import NoticeSource
from jipsooni.utils import clean_text, extract_items, first_present, parse_date, status_from_dates


class LhNoticeSource(NoticeSource):
    name = "LH"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or kr_client(settings)

    def fetch(self) -> list[Notice]:
        if not self.settings.data_go_kr_service_key:
            return []

        today = date.today()
        start = today - timedelta(days=self.settings.lh_lookback_days)
        end = today + timedelta(days=self.settings.lh_lookahead_days)
        base_params = {
            "ServiceKey": self.settings.data_go_kr_service_key,
            "PG_SZ": "100",
            "PAN_NT_ST_DT": start.strftime("%Y.%m.%d"),
            "CLSG_DT": end.strftime("%Y.%m.%d"),
            "UPP_AIS_TP_CD": "06",
        }

        rows: list[dict[str, Any]] = []
        total_count: int | None = None
        page_size = int(base_params["PG_SZ"])
        page = 1

        while True:
            payload = self._request_page(base_params | {"PAGE": str(page)})
            items = extract_items(payload)
            if not items:
                break

            rows.extend(items)
            if total_count is None:
                total_count = self._total_count(items)

            if total_count is not None and page >= ceil(total_count / page_size):
                break
            if total_count is None and len(items) < page_size:
                break
            page += 1

        unique: dict[str, Notice] = {}
        for row in rows:
            notice = self._normalize(row)
            unique.setdefault(notice.canonical_key, notice)
        return list(unique.values())

    def _request_page(self, params: dict[str, str]) -> Any:
        response = self.client.get(self.settings.lh_notice_endpoint, params=params)
        if response.status_code >= 400:
            raise RuntimeError(
                f"LH API request failed status={response.status_code} "
                f"body={response.text[:200]}"
            )
        return response.json()

    def _total_count(self, items: list[dict[str, Any]]) -> int | None:
        for item in items:
            value = first_present(item, "ALL_CNT", "allCnt", "totalCount")
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        return None

    def _normalize(self, row: dict[str, Any]) -> Notice:
        title = clean_text(first_present(row, "PAN_NM", "panNm", "title")) or "제목 확인 필요"
        external_id = clean_text(
            first_present(row, "PAN_ID", "panId", "ANN_ID", "RNUM", "rnum")
        )
        app_start = parse_date(
            first_present(row, "RQS_ST_DT", "SPLY_ST_DT", "RCPT_ST_DT", "applicationStartDate")
        )
        app_end = parse_date(
            first_present(row, "CLSG_DT", "RQS_ED_DT", "RCPT_ED_DT", "applicationEndDate")
        )
        raw_status = clean_text(first_present(row, "PAN_SS", "PAN_SS_NM", "status"))

        return Notice(
            source=self.name,
            external_id=external_id,
            title=title,
            region=clean_text(first_present(row, "CNP_CD_NM", "ARA_NM", "region")),
            housing_type=clean_text(
                first_present(row, "AIS_TP_CD_NM", "UPP_AIS_TP_NM", "housingType")
            ),
            target_group=clean_text(first_present(row, "SPLY_TRGT_NM", "targetGroup")),
            application_start_date=app_start,
            application_end_date=app_end,
            announcement_date=parse_date(first_present(row, "PAN_NT_ST_DT", "noticeDate")),
            contract_date=parse_date(first_present(row, "CTRT_ST_DT", "contractDate")),
            area_text=_detail_text(row),
            cost_text=clean_text(
                first_present(row, "RFE", "LS_GMY", "SPL_XPC_AMT", "costText")
            ),
            status=status_from_dates(raw_status, app_start, app_end),
            original_url=clean_text(first_present(row, "DTL_URL", "detailUrl", "url")),
            raw=row,
        )


def _detail_text(row: dict[str, Any]) -> str | None:
    parts = []
    notice_date = parse_date(first_present(row, "PAN_NT_ST_DT", "noticeDate"))
    if notice_date:
        parts.append(f"공고일 {notice_date.isoformat()}")

    upper_type = clean_text(first_present(row, "UPP_AIS_TP_NM"))
    detail_type = clean_text(first_present(row, "AIS_TP_CD_NM"))
    if upper_type and detail_type and upper_type != detail_type:
        parts.append(f"분류 {upper_type}/{detail_type}")

    supply_info = clean_text(first_present(row, "SPL_INF_TP_NM", "SPL_INF_TP_CD"))
    if supply_info:
        parts.append(f"공급정보 {supply_info}")

    area = clean_text(first_present(row, "AR", "HS_TY_NM", "areaText"))
    if area:
        parts.append(f"면적/형 {area}")

    return " / ".join(parts) if parts else None
