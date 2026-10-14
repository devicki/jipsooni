from __future__ import annotations

import re
from datetime import date
from math import ceil
from typing import Any

from bs4 import BeautifulSoup
import httpx

from jipsooni.config import Settings, kr_client
from jipsooni.models import Notice
from jipsooni.sources.base import NoticeSource
from jipsooni.utils import clean_text, first_present, parse_date, status_from_dates


REGION_FILTERS = ("서울", "경기", "인천")
HOUSE_SECTION_CODES = ("04", "06")  # 04: 무순위, 06: 불법행위 재공급
LIST_PAGE_URL = "https://www.applyhome.co.kr/ai/aia/selectAPTRemndrLttotPblancListView.do"


class ApplyHomeRemndrSource(NoticeSource):
    name = "청약홈"

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        today: date | None = None,
        region_filters: tuple[str, ...] | None = None,
    ) -> None:
        self.region_filters = tuple(region_filters or REGION_FILTERS)
        self.warnings: list[str] = []
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.http_timeout)  # odcloud API: direct
        self.web_client = client or kr_client(settings, follow_redirects=True)  # applyhome web: KR proxy
        self.today = today or date.today()

    def fetch(self) -> list[Notice]:
        if not self.settings.applyhome_service_key:
            return []

        rows: list[dict[str, Any]] = []
        for region in self.region_filters:
            for house_section_code in HOUSE_SECTION_CODES:
                rows.extend(
                    self._fetch_pages(
                        {
                            "cond[HSSPLY_ADRES::LIKE]": region,
                            "cond[HOUSE_SECD::EQ]": house_section_code,
                            "cond[SUBSCRPT_RCEPT_ENDDE::GTE]": self.today.isoformat(),
                        }
                    )
                )

        unique: dict[str, Notice] = {}
        for row in rows:
            notice = self._normalize(row)
            if notice.application_end_date and notice.application_end_date < self.today:
                continue
            unique.setdefault(notice.canonical_key, notice)
        # port: the web list is only a supplement; on foreign-IP hosts it times out.
        try:
            for notice in self._fetch_web_list_detail_notices():
                unique.setdefault(notice.canonical_key, notice)
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"web list fallback skipped: {type(exc).__name__}")
        for house_manage_no in self.settings.applyhome_extra_house_manage_nos:
            notice = self._fetch_detail_notice(house_manage_no)
            if notice and (notice.application_end_date is None or notice.application_end_date >= self.today):
                unique.setdefault(notice.canonical_key, notice)
        return list(unique.values())

    def _fetch_pages(self, extra_params: dict[str, str]) -> list[dict[str, Any]]:
        page_size = 100
        rows: list[dict[str, Any]] = []
        page = 1
        total_count: int | None = None
        while True:
            payload = self._request_page(page=page, page_size=page_size, extra_params=extra_params)
            items = payload.get("data") or []
            if not isinstance(items, list) or not items:
                break

            rows.extend(items)
            if total_count is None:
                total_count = _as_int(payload.get("totalCount"))

            if total_count is not None and page >= ceil(total_count / page_size):
                break
            if total_count is None and len(items) < page_size:
                break
            page += 1
        return rows

    def _request_page(
        self, page: int, page_size: int, extra_params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        response = self.client.get(
            self.settings.applyhome_remndr_endpoint,
            params={
                "page": str(page),
                "perPage": str(page_size),
                "serviceKey": self.settings.applyhome_service_key,
            }
            | (extra_params or {}),
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"ApplyHome API request failed status={response.status_code} "
                f"body={response.text[:200]}"
            )
        payload = response.json()
        if payload.get("code") is not None:
            raise RuntimeError(f"ApplyHome API error code={payload.get('code')} msg={payload.get('msg')}")
        return payload

    def _fetch_web_list_detail_notices(self) -> list[Notice]:
        response = self.web_client.get(LIST_PAGE_URL)
        if response.status_code >= 400:
            raise RuntimeError(
                f"ApplyHome list page request failed status={response.status_code} "
                f"body={response.text[:200]}"
            )
        soup = BeautifulSoup(response.text, "html.parser")
        notices: list[Notice] = []
        for row in soup.select("tr[data-hmno][data-pbno][data-hsecd]"):
            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
            if len(cells) < 7:
                continue
            region = cells[0] or ""
            house_section_code = row.get("data-hsecd") or ""
            if not any(region_filter in region for region_filter in self.region_filters):
                continue
            if house_section_code not in HOUSE_SECTION_CODES:
                continue
            application_end = _period_end_date(cells[5])
            if application_end and application_end < self.today:
                continue
            house_manage_no = row.get("data-hmno") or ""
            pblanc_no = row.get("data-pbno") or house_manage_no
            notice = self._fetch_detail_notice(house_manage_no, pblanc_no=pblanc_no)
            if notice:
                notices.append(notice)
        return notices

    def _normalize(self, row: dict[str, Any]) -> Notice:
        title = clean_text(first_present(row, "HOUSE_NM", "houseNm")) or "주택명 확인 필요"
        housing_type = clean_text(first_present(row, "HOUSE_SECD_NM", "houseSecdNm"))
        region = _region_from_address(clean_text(first_present(row, "HSSPLY_ADRES", "hssplyAdres")))
        app_start = parse_date(first_present(row, "SUBSCRPT_RCEPT_BGNDE", "RCEPT_BGNDE"))
        app_end = parse_date(first_present(row, "SUBSCRPT_RCEPT_ENDDE", "RCEPT_ENDDE"))
        contract_start = parse_date(first_present(row, "CNTRCT_CNCLS_BGNDE"))
        contract_end = parse_date(first_present(row, "CNTRCT_CNCLS_ENDDE"))
        external_id = clean_text(
            "-".join(
                str(part)
                for part in (
                    first_present(row, "HOUSE_MANAGE_NO"),
                    first_present(row, "PBLANC_NO"),
                )
                if part not in (None, "")
            )
        )
        original_url = clean_text(first_present(row, "PBLANC_URL", "HMPG_ADRES"))

        return Notice(
            source=self.name,
            external_id=external_id,
            title=f"{title} {housing_type or '잔여세대'} 입주자모집공고",
            region=region,
            housing_type=housing_type or "무순위/잔여세대",
            target_group="무주택 세대주 확인 필요",
            application_start_date=app_start,
            application_end_date=app_end or contract_end,
            announcement_date=parse_date(first_present(row, "RCRIT_PBLANC_DE", "PBLANC_DE")),
            contract_date=contract_start,
            area_text=_detail_text(row, contract_start, contract_end),
            cost_text=clean_text(first_present(row, "NSPRC_NM")),
            status=status_from_dates(None, app_start, app_end or contract_end),
            original_url=original_url,
            raw=row,
        )

    def _fetch_detail_notice(self, house_manage_no: str, pblanc_no: str | None = None) -> Notice | None:
        pblanc_no = pblanc_no or house_manage_no
        detail_url = (
            "https://www.applyhome.co.kr/ai/aia/selectAPTRemndrLttotPblancDetailView.do?"
            f"houseManageNo={house_manage_no}&pblancNo={pblanc_no}"
        )
        response = self.web_client.get(detail_url)
        if response.status_code >= 400:
            raise RuntimeError(
                f"ApplyHome detail request failed status={response.status_code} "
                f"body={response.text[:200]}"
            )
        return self._normalize_detail_html(house_manage_no, pblanc_no, detail_url, response.text)

    def _normalize_detail_html(
        self, house_manage_no: str, pblanc_no: str, detail_url: str, html: str
    ) -> Notice | None:
        soup = BeautifulSoup(html, "html.parser")
        text = clean_text(soup.get_text(" ", strip=True))
        if not text or house_manage_no not in text:
            return None

        title = _match_text(text, r"(?:입주자모집공고\s+주요정보\s+)+(.+?)\s+공급위치")
        address = _match_text(text, r"공급위치\s+(.+?)\s+공급규모")
        supply = _match_text(text, r"공급규모\s+(\d+\s*세대)")
        announcement = _match_text(text, r"모집공고일\s+(\d{4}-\d{2}-\d{2})")
        special_start = _match_text(text, r"특별공급\s+(\d{4}-\d{2}-\d{2})")
        general_start = _match_text(text, r"일반공급\s+(\d{4}-\d{2}-\d{2})")
        winner_date = _match_text(text, r"당첨자 발표일\s+(\d{4}-\d{2}-\d{2})")
        contract_start = _match_text(text, r"계약일\s+(\d{4}-\d{2}-\d{2})")
        phone = _match_text(text, r"문의처\s+☎\s*([0-9-]+)")

        if not title:
            return None

        area_parts = []
        if supply:
            area_parts.append(f"공급규모 {supply.replace(' ', '')}")
        if winner_date:
            area_parts.append(f"당첨자발표 {winner_date}")
        if phone:
            area_parts.append(f"문의 {phone}")

        return Notice(
            source=self.name,
            external_id=f"{house_manage_no}-{pblanc_no}",
            title=f"{title} 불법행위 재공급 입주자모집공고",
            region=_region_from_address(address),
            housing_type="불법행위 재공급",
            target_group="무주택 세대주 확인 필요",
            application_start_date=parse_date(special_start or general_start),
            application_end_date=parse_date(general_start or special_start),
            announcement_date=parse_date(announcement),
            contract_date=parse_date(contract_start),
            area_text=" / ".join(area_parts) if area_parts else None,
            cost_text=_max_price_text(text),
            status=status_from_dates(
                None,
                parse_date(special_start or general_start),
                parse_date(general_start or special_start),
            ),
            original_url=detail_url,
            raw={
                "house_manage_no": house_manage_no,
                "pblanc_no": pblanc_no,
                "detail_url": detail_url,
                "detail_text": text,
            },
        )


def _region_from_address(address: str | None) -> str | None:
    if not address:
        return None
    parts = address.split()
    region = " ".join(parts[:2]) if len(parts) >= 2 else address
    if region == "인천광역시 서해구":
        return "인천광역시 서구"
    return region


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _match_text(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    if not match:
        return None
    return clean_text(match.group(1))


def _period_end_date(text: str | None) -> date | None:
    if not text:
        return None
    matches = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    if not matches:
        return None
    return parse_date(matches[-1])


def _max_price_text(text: str) -> str | None:
    prices = [
        int(value.replace(",", ""))
        for value in re.findall(r"\b\d{2,3}\.\d{4}[A-Z]?\s+(\d{1,3},\d{3})", text)
    ]
    if not prices:
        return None
    return f"최고 공급금액 {max(prices):,}만원"


def _detail_text(row: dict[str, Any], contract_start: date | None, contract_end: date | None) -> str | None:
    parts: list[str] = []
    total_supply = first_present(row, "TOT_SUPLY_HSHLDCO")
    if total_supply not in (None, ""):
        parts.append(f"공급세대 {total_supply}세대")

    winner_date = parse_date(first_present(row, "PRZWNER_PRESNATN_DE"))
    if winner_date:
        parts.append(f"당첨자발표 {winner_date.isoformat()}")

    if contract_start or contract_end:
        start = contract_start.isoformat() if contract_start else "확인 필요"
        end = contract_end.isoformat() if contract_end else "확인 필요"
        parts.append(f"계약 {start} ~ {end}")

    move_in = _move_in_text(clean_text(first_present(row, "MVN_PREARNGE_YM")))
    if move_in:
        parts.append(f"입주예정 {move_in}")

    business_owner = clean_text(first_present(row, "BSNS_MBY_NM"))
    if business_owner:
        parts.append(f"사업주체 {business_owner}")

    phone = clean_text(first_present(row, "MDHS_TELNO"))
    if phone:
        parts.append(f"문의 {phone}")

    return " / ".join(parts) if parts else None


def _move_in_text(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) == 6:
        return f"{digits[:4]}-{digits[4:]}"
    return value
