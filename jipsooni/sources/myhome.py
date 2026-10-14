from __future__ import annotations

from math import ceil
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from jipsooni.config import Settings, kr_client
from jipsooni.models import Notice
from jipsooni.sources.base import NoticeSource
from jipsooni.utils import clean_text, extract_items, first_present, parse_date, status_from_dates


class MyHomeNoticeSource(NoticeSource):
    name = "마이홈"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or kr_client(settings, follow_redirects=True)

    def fetch(self) -> list[Notice]:
        if self.settings.myhome_notice_endpoint and self.settings.data_go_kr_service_key:
            return self._fetch_api()
        return self._fetch_page()

    def _fetch_api(self) -> list[Notice]:
        base_params = {
            "serviceKey": self.settings.data_go_kr_service_key,
            "numOfRows": "100",
        }
        rows: list[dict[str, Any]] = []
        total_count: int | None = None
        page_size = int(base_params["numOfRows"])
        page = 1

        while True:
            payload = self._request_api_page(base_params | {"pageNo": str(page)})
            items = extract_items(payload)
            if not items:
                break

            rows.extend(items)
            if total_count is None:
                total_count = self._total_count(payload, items)

            if total_count is not None and page >= ceil(total_count / page_size):
                break
            if total_count is None and len(items) < page_size:
                break
            page += 1

        unique: dict[str, Notice] = {}
        regions: dict[str, set[str]] = {}
        for row in rows:
            notice = self._normalize_api(row)
            regions.setdefault(notice.canonical_key, set()).add(notice.region or "")
            current = unique.get(notice.canonical_key)
            # 같은 pblancId-houseSn에 시군구 행이 여러 개 온다. API 순서와 무관하게 같은 행을 고른다.
            if current is None or notice.content_hash < current.content_hash:
                unique[notice.canonical_key] = notice
        # port fix: keep every 시군구 of a multi-district notice (e.g. 든든전세 서울 11개 구), not just one,
        # otherwise 강서구 is lost when the picked row is 동대문구.
        import dataclasses
        out = []
        for key, notice in unique.items():
            merged = _merge_regions(regions[key])
            out.append(dataclasses.replace(notice, region=merged) if merged != notice.region else notice)
        return out

    def _request_api_page(self, params: dict[str, str]) -> Any:
        response = self.client.get(self.settings.myhome_notice_endpoint, params=params)
        if response.status_code >= 400:
            raise RuntimeError(
                f"MyHome API request failed status={response.status_code} "
                f"body={response.text[:200]}"
            )
        return response.json()

    def _total_count(self, payload: Any, items: list[dict[str, Any]]) -> int | None:
        if isinstance(payload, dict):
            body = payload.get("response", {}).get("body", {})
            value = body.get("totalCount")
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        for item in items:
            value = first_present(item, "totalCount", "ALL_CNT")
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        return None

    def _fetch_page(self) -> list[Notice]:
        response = self.client.get(self.settings.myhome_notice_page_url)
        if response.status_code >= 400:
            raise RuntimeError(
                f"MyHome page request failed status={response.status_code} "
                f"body={response.text[:200]}"
            )
        soup = BeautifulSoup(response.text, "html.parser")
        notices: list[Notice] = []

        for link in soup.select("a[href]"):
            title = clean_text(link.get_text(" ", strip=True))
            href = link.get("href")
            if not title or not href:
                continue
            if len(title) < 12:
                continue
            if not any(keyword in title for keyword in ("주택", "임대", "공고", "입주자", "행복")):
                continue

            original_url = urljoin(self.settings.myhome_notice_page_url, href)
            notices.append(
                Notice(
                    source=self.name,
                    external_id=original_url,
                    title=title,
                    original_url=original_url,
                    status="확인 필요",
                    raw={"title": title, "href": href, "scraper": "myhome_page"},
                )
            )

        unique: dict[str, Notice] = {}
        for notice in notices:
            unique.setdefault(notice.canonical_key, notice)
        return list(unique.values())

    def _normalize_api(self, row: dict[str, Any]) -> Notice:
        title = clean_text(
            first_present(row, "pblancNm", "PBLANC_NM", "suplyNm", "HOUSE_NM", "title")
        ) or "제목 확인 필요"
        external_id = clean_text(
            "-".join(
                str(part)
                for part in (
                    first_present(row, "pblancId", "PBLANC_ID", "houseManageNo", "HOUSE_MANAGE_NO"),
                    first_present(row, "houseSn", "HOUSE_SN"),
                )
                if part not in (None, "")
            )
        )
        app_start = parse_date(
            first_present(row, "beginDe", "rceptBgnde", "RCEPT_BGNDE", "applicationStartDate")
        )
        app_end = parse_date(
            first_present(row, "endDe", "rceptEndde", "RCEPT_ENDDE", "applicationEndDate")
        )
        raw_status = clean_text(
            first_present(row, "sttusNm", "status", "PBLANC_STTUS", "pblancSttus")
        )
        region = " ".join(
            part
            for part in (
                clean_text(first_present(row, "brtcNm", "brtcCodeNm", "SIDO_NM", "region")),
                clean_text(first_present(row, "signguNm", "signguCodeNm", "SIGUNGU_NM")),
            )
            if part
        ) or None

        return Notice(
            source=self.name,
            external_id=external_id,
            title=title,
            region=region,
            housing_type=clean_text(
                first_present(row, "suplyTyNm", "houseTy", "HOUSE_TY", "housingType")
            ),
            target_group=clean_text(first_present(row, "suplyTrget", "SUPLY_TRGET", "targetGroup")),
            application_start_date=app_start,
            application_end_date=app_end,
            announcement_date=parse_date(
                first_present(row, "rcritPblancDe", "pblancDe", "PBLANC_DE", "noticeDate")
            ),
            contract_date=parse_date(first_present(row, "cntrctBgnde", "contractDate")),  # port: przwnerPresnatnDe is 당첨자발표, see area_text
            area_text=_detail_text(row),
            cost_text=_format_cost_text(row),
            # port: sttusNm is the notice kind (일반공고/정정공고), not reception state.
            status=_myhome_status(raw_status, app_start, app_end),
            original_url=clean_text(first_present(row, "pcUrl", "url", "URL", "pblancUrl", "PBLANC_URL")),
            raw=row,
        )


def _format_cost_text(row: dict[str, Any]) -> str | None:
    rent_deposit = _money(first_present(row, "rentGtn", "RENT_GTN"))
    monthly_rent = _money(first_present(row, "mtRntchrg", "MT_RNTCHRG"))
    entry = _money(first_present(row, "enty", "ENTY"))
    parts = []
    if rent_deposit:
        parts.append(f"임대보증금 {rent_deposit}")
    if monthly_rent:
        parts.append(f"월임대료 {monthly_rent}")
    if entry:
        parts.append(f"계약금 {entry}")
    return " / ".join(parts) if parts else clean_text(first_present(row, "costText"))


def _detail_text(row: dict[str, Any]) -> str | None:
    parts = []
    complex_name = clean_text(first_present(row, "hsmpNm"))
    if complex_name:
        parts.append(f"단지 {complex_name}")

    address = clean_text(first_present(row, "fullAdres"))
    if address:
        parts.append(f"주소 {address}")

    house_type = clean_text(first_present(row, "houseTyNm"))
    if house_type:
        parts.append(f"주택유형 {house_type}")

    total_supply = first_present(row, "sumSuplyCo", "suplyHoCo")
    if total_supply not in (None, ""):
        parts.append(f"공급 {total_supply}호")

    winner_date = parse_date(first_present(row, "przwnerPresnatnDe"))
    if winner_date:
        parts.append(f"당첨자발표 {winner_date.isoformat()}")

    supplier = clean_text(first_present(row, "suplyInsttNm"))
    if supplier:
        parts.append(f"공급기관 {supplier}")

    reference = clean_text(first_present(row, "refrnc"))
    if reference:
        parts.append(f"문의 {reference}")

    return " / ".join(parts) if parts else clean_text(first_present(row, "houseArea", "HOUSE_AREA", "areaText"))


def _money(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return f"{amount:,}원"


def _myhome_status(kind: str | None, start, end) -> str | None:
    state = status_from_dates(None, start, end)
    if kind and "정정" in kind:
        return f"정정공고 {state}" if state else "정정공고"
    return state or kind


def _merge_regions(regions: set[str]) -> str | None:
    """{'서울특별시 강서구', '서울특별시 동대문구'} -> '서울특별시 강서구, 동대문구' (sorted, stable)."""
    by_sido: dict[str, list[str]] = {}
    for r in sorted(x for x in regions if x):
        sido, _, rest = r.partition(" ")
        lst = by_sido.setdefault(sido, [])
        if rest and rest not in lst:
            lst.append(rest)
    parts = [(f"{sido} {', '.join(rest)}" if rest else sido) for sido, rest in by_sido.items()]
    return " / ".join(parts) or None
