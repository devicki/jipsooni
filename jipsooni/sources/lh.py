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
        # port: the original asked only UPP_AIS_TP_CD=06 (임대). 든든전세/매입임대 live under 13 (주거복지),
        # 공공분양 under 05, 신혼희망타운 under 39 -> query each, then keep still-open rows only.
        rows: list[dict[str, Any]] = []
        for code in self.settings.lh_upper_type_codes or ("06",):
            rows.extend(self._fetch_code(code, start, end))

        keep_from = today - timedelta(days=self.settings.lh_closed_grace_days)
        unique: dict[str, Notice] = {}
        for row in rows:
            notice = self._normalize(row)
            if notice.application_end_date and notice.application_end_date < keep_from:
                continue  # selection is by open deadline, not by posting date
            unique.setdefault(notice.canonical_key, notice)
        notices = list(unique.values())
        if self.settings.fetch_details:
            notices = self._enrich(notices, today)
        return notices

    def _fetch_code(self, code: str, start: date, end: date) -> list[dict[str, Any]]:
        base_params = {
            "ServiceKey": self.settings.data_go_kr_service_key,
            "PG_SZ": "100",
            "PAN_NT_ST_DT": start.strftime("%Y.%m.%d"),
            "CLSG_DT": end.strftime("%Y.%m.%d"),
            "UPP_AIS_TP_CD": code,
        }
        rows: list[dict[str, Any]] = []
        total_count: int | None = None
        page_size = int(base_params["PG_SZ"])
        page = 1
        while True:
            items = extract_items(self._request_page(base_params | {"PAGE": str(page)}))
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
        return rows

    def _enrich(self, notices: list[Notice], today: date) -> list[Notice]:
        """LH list rows only say '서울특별시'. The detail page lists 모집지역 (e.g. 강서구) and sometimes
        접수기간; read it for still-open notices, cached."""
        from concurrent.futures import ThreadPoolExecutor
        from jipsooni.detail_cache import DetailCache

        cache = DetailCache(self.settings.detail_cache_path or None)

        def one(n: Notice) -> Notice:
            if not n.original_url or (n.application_end_date and n.application_end_date < today):
                return n
            info = cache.get(f"lh:{n.external_id}", lambda: parse_lh_detail(self._get_text(n.original_url)))
            return apply_detail(n, info)

        with ThreadPoolExecutor(max_workers=6) as pool:
            out = list(pool.map(one, notices))
        cache.save()
        return out

    def _get_text(self, url: str) -> str:
        r = self.client.get(url)
        r.raise_for_status()
        return r.text

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


def parse_lh_detail(html: str) -> dict[str, Any]:
    from jipsooni.utils import page_text, parse_period
    import re
    text = page_text(html)
    m = re.search(r"모집지역\s*:\s*([^\n]{1,300}?)\s*(?:모집세대|공급정보|\n)", text)
    areas = []
    if m:
        areas = [a.strip() for a in re.split(r"[,，/]", m.group(1)) if a.strip()][:40]
    s, e = parse_period(text)
    return {"areas": areas, "start": s.isoformat() if s else None, "end": e.isoformat() if e else None}


def apply_detail(n: Notice, info: dict[str, Any]) -> Notice:
    import dataclasses
    changes: dict[str, Any] = {}
    areas = info.get("areas") or []
    if areas:
        base = n.region or ""
        changes["region"] = (base + " " + ", ".join(areas)).strip()
    start = date.fromisoformat(info["start"]) if info.get("start") else None
    end = n.application_end_date or (date.fromisoformat(info["end"]) if info.get("end") else None)
    if start and not n.application_start_date and end and timedelta(0) <= end - start <= timedelta(days=60):
        changes["application_start_date"] = start
    if info.get("end") and not n.application_end_date:
        changes["application_end_date"] = date.fromisoformat(info["end"])
    return dataclasses.replace(n, **changes) if changes else n
