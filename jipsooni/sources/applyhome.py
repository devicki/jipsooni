from __future__ import annotations

import dataclasses
import re
from datetime import date, timedelta
from math import ceil
from typing import Any

from bs4 import BeautifulSoup
import httpx

from jipsooni.config import Settings, kr_client
from jipsooni.models import Notice
from jipsooni.sources.base import NoticeSource
from jipsooni.utils import clean_text, first_present, parse_date, status_from_dates


REGION_FILTERS = ("서울", "경기", "인천")
# getRemndrLttotPblancDetail request docs: 04 무순위, 06 불법행위 재공급.
HOUSE_SECTION_CODES = ("04", "06")
# The remndr list page also emits 임의공급 as data-hsecd=11 (verified 2026-11-12).
# That code is not a documented remndr API filter; 임의공급 is getOPTLttotPblancDetail.
WEB_LIST_SECTION_CODES = ("04", "06", "11")
OPTIONAL_SUPPLY_SECTION_CODE = "11"
# getAPTLttotPblancDetail: 01 APT, 09 민간사전청약, 10 신혼희망타운.
REGULAR_APT_SECTION_CODE = "01"
# RENT_SECD 0 분양주택, 1 분양전환 가능임대. Only sale units are 일반분양.
SALE_RENT_SECTION_CODE = "0"
# Bound the documented announcement filter, then drop rows whose reception already ended.
SUPPLY_ANNOUNCEMENT_LOOKBACK_DAYS = 365
LIST_PAGE_URL = "https://www.applyhome.co.kr/ai/aia/selectAPTRemndrLttotPblancListView.do"
APT_DETAIL_PAGE = "https://www.applyhome.co.kr/ai/aia/selectAPTLttotPblancDetail.do"
REMNDR_DETAIL_PAGE = "https://www.applyhome.co.kr/ai/aia/selectAPTRemndrLttotPblancDetailView.do"
# Reception window on getAPTLttotPblancDetail (YYYY-MM-DD).
_APT_START_KEYS = (
    "RCEPT_BGNDE",
    "SPSPLY_RCEPT_BGNDE",
    "GNRL_RNK1_CRSPAREA_RCPTDE",
    "GNRL_RNK1_ETC_GG_RCPTDE",
    "GNRL_RNK1_ETC_AREA_RCPTDE",
    "GNRL_RNK2_CRSPAREA_RCPTDE",
    "GNRL_RNK2_ETC_GG_RCPTDE",
    "GNRL_RNK2_ETC_AREA_RCPTDE",
)
_APT_END_KEYS = (
    "RCEPT_ENDDE",
    "SPSPLY_RCEPT_ENDDE",
    "GNRL_RNK1_CRSPAREA_ENDDE",
    "GNRL_RNK1_ETC_GG_ENDDE",
    "GNRL_RNK1_ETC_AREA_ENDDE",
    "GNRL_RNK2_CRSPAREA_ENDDE",
    "GNRL_RNK2_ETC_GG_ENDDE",
    "GNRL_RNK2_ETC_AREA_ENDDE",
)
# getOPTLttotPblancDetail. Announcement filters are YYYYMMDD; these response fields accept both.
_OPT_START_KEYS = ("SUBSCRPT_RCEPT_BGNDE", "SPSPLY_RCEPT_BGNDE", "GNRL_RCEPT_BGNDE")
_OPT_END_KEYS = ("SUBSCRPT_RCEPT_ENDDE", "SPSPLY_RCEPT_ENDDE", "GNRL_RCEPT_ENDDE")


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
        # 일반분양 / 임의공급 use other operations. A failure there must not drop 04/06.
        self._collect_regular_sale(unique)
        self._collect_optional_supply(unique)
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

    def _collect_regular_sale(self, unique: dict[str, Notice]) -> None:
        endpoint = self.settings.applyhome_apt_endpoint
        if not endpoint:
            return
        try:
            since = (self.today - timedelta(days=SUPPLY_ANNOUNCEMENT_LOOKBACK_DAYS)).isoformat()
            for region in self.region_filters:
                for row in self._fetch_pages(
                    {
                        "cond[HSSPLY_ADRES::LIKE]": region,
                        "cond[HOUSE_SECD::EQ]": REGULAR_APT_SECTION_CODE,
                        "cond[RCRIT_PBLANC_DE::GTE]": since,
                    },
                    endpoint=endpoint,
                ):
                    notice = self._normalize_regular_sale(row)
                    if notice:
                        unique.setdefault(notice.canonical_key, notice)
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"regular sale skipped: {type(exc).__name__}")

    def _collect_optional_supply(self, unique: dict[str, Notice]) -> None:
        endpoint = self.settings.applyhome_opt_endpoint
        if not endpoint:
            return
        try:
            # Swagger: 임의공급 모집공고일 filters are YYYYMMDD, unlike APT's YYYY-MM-DD.
            since = (self.today - timedelta(days=SUPPLY_ANNOUNCEMENT_LOOKBACK_DAYS)).strftime("%Y%m%d")
            for region in self.region_filters:
                for row in self._fetch_pages(
                    {
                        "cond[HSSPLY_ADRES::LIKE]": region,
                        "cond[RCRIT_PBLANC_DE::GTE]": since,
                    },
                    endpoint=endpoint,
                ):
                    notice = self._normalize_optional_supply(row)
                    if notice:
                        unique.setdefault(notice.canonical_key, notice)
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"optional supply skipped: {type(exc).__name__}")

    def _fetch_pages(
        self, extra_params: dict[str, str], endpoint: str | None = None
    ) -> list[dict[str, Any]]:
        page_size = 100
        rows: list[dict[str, Any]] = []
        page = 1
        total_count: int | None = None
        while True:
            payload = self._request_page(
                page=page, page_size=page_size, extra_params=extra_params, endpoint=endpoint
            )
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
        self,
        page: int,
        page_size: int,
        extra_params: dict[str, str] | None = None,
        endpoint: str | None = None,
    ) -> dict[str, Any]:
        response = self.client.get(
            endpoint or self.settings.applyhome_remndr_endpoint,
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
            if house_section_code not in WEB_LIST_SECTION_CODES:
                continue
            application_end = _period_end_date(cells[5])
            if application_end and application_end < self.today:
                continue
            house_manage_no = row.get("data-hmno") or ""
            pblanc_no = row.get("data-pbno") or house_manage_no
            if house_section_code == OPTIONAL_SUPPLY_SECTION_CODE:
                notice = self._fetch_optional_supply_web_notice(house_manage_no, pblanc_no, cells)
            else:
                notice = self._fetch_detail_notice(house_manage_no, pblanc_no=pblanc_no)
            if notice and (notice.application_end_date is None or notice.application_end_date >= self.today):
                notices.append(notice)
        return notices

    def _fetch_optional_supply_web_notice(
        self, house_manage_no: str, pblanc_no: str, cells: list[str]
    ) -> Notice | None:
        notice: Notice | None = None
        try:
            notice = self._fetch_detail_notice(
                house_manage_no, pblanc_no=pblanc_no, housing_type="임의공급"
            )
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"optional supply detail skipped: {type(exc).__name__}")
        if notice:
            return notice
        return self._notice_from_optional_list_row(house_manage_no, pblanc_no, cells)

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

    def _normalize_regular_sale(self, row: dict[str, Any]) -> Notice | None:
        if not _is_regular_sale(row):
            return None
        address = clean_text(first_present(row, "HSSPLY_ADRES", "hssplyAdres"))
        if not _address_in_regions(address, self.region_filters):
            return None
        start, end = _date_span(row, _APT_START_KEYS, _APT_END_KEYS)
        if end is None or end < self.today:
            return None
        house_manage_no, pblanc_no = _house_ids(row)
        if not house_manage_no:
            return None
        title = clean_text(first_present(row, "HOUSE_NM", "houseNm")) or "주택명 확인 필요"
        contract_start = parse_date(first_present(row, "CNTRCT_CNCLS_BGNDE"))
        contract_end = parse_date(first_present(row, "CNTRCT_CNCLS_ENDDE"))
        detail_kind = clean_text(first_present(row, "HOUSE_DTL_SECD_NM"))
        area = _detail_text(row, contract_start, contract_end)
        if detail_kind:
            area = f"{detail_kind} / {area}" if area else detail_kind
        notice = Notice(
            source=self.name,
            external_id=f"{house_manage_no}-{pblanc_no}",
            title=f"{title} 일반분양 입주자모집공고",
            region=_region_from_address(address),
            housing_type="일반분양",
            target_group="무주택 세대주 확인 필요",
            application_start_date=start,
            application_end_date=end,
            announcement_date=parse_date(first_present(row, "RCRIT_PBLANC_DE")),
            contract_date=contract_start,
            area_text=area,
            status=status_from_dates(None, start, end),
            original_url=_applyhome_detail_url(APT_DETAIL_PAGE, house_manage_no, pblanc_no),
            raw=row,
        )
        return self._with_top_price(notice, house_manage_no, pblanc_no, self.settings.applyhome_apt_model_endpoint)

    def _normalize_optional_supply(self, row: dict[str, Any]) -> Notice | None:
        address = clean_text(first_present(row, "HSSPLY_ADRES", "hssplyAdres"))
        if not _address_in_regions(address, self.region_filters):
            return None
        start, end = _date_span(row, _OPT_START_KEYS, _OPT_END_KEYS)
        if end is None or end < self.today:
            return None
        house_manage_no, pblanc_no = _house_ids(row)
        if not house_manage_no:
            return None
        title = clean_text(first_present(row, "HOUSE_NM", "houseNm")) or "주택명 확인 필요"
        contract_start = parse_date(first_present(row, "CNTRCT_CNCLS_BGNDE"))
        contract_end = parse_date(first_present(row, "CNTRCT_CNCLS_ENDDE"))
        label = clean_text(first_present(row, "HOUSE_SECD_NM")) or "임의공급"
        if "임의공급" not in label:
            label = "임의공급"
        notice = Notice(
            source=self.name,
            external_id=f"{house_manage_no}-{pblanc_no}",
            title=f"{title} {label} 입주자모집공고",
            region=_region_from_address(address),
            housing_type=label,
            target_group="무주택 세대주 확인 필요",
            application_start_date=start,
            application_end_date=end,
            announcement_date=parse_date(first_present(row, "RCRIT_PBLANC_DE")),
            contract_date=contract_start,
            area_text=_detail_text(row, contract_start, contract_end),
            status=status_from_dates(None, start, end),
            original_url=_applyhome_detail_url(REMNDR_DETAIL_PAGE, house_manage_no, pblanc_no),
            raw=row,
        )
        return self._with_top_price(notice, house_manage_no, pblanc_no, self.settings.applyhome_opt_model_endpoint)

    def _with_top_price(
        self, notice: Notice, house_manage_no: str, pblanc_no: str, endpoint: str
    ) -> Notice:
        if not endpoint:
            return notice
        try:
            payload = self._request_page(
                page=1,
                page_size=100,
                extra_params={
                    "cond[HOUSE_MANAGE_NO::EQ]": house_manage_no,
                    "cond[PBLANC_NO::EQ]": pblanc_no,
                },
                endpoint=endpoint,
            )
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"supply price skipped: {type(exc).__name__}")
            return notice
        amounts = [
            amount
            for item in (payload.get("data") or [])
            if isinstance(item, dict)
            for amount in (_manwon(item.get("LTTOT_TOP_AMOUNT")),)
            if amount is not None
        ]
        if not amounts:
            return notice
        return dataclasses.replace(notice, cost_text=f"최고 공급금액 {max(amounts):,}만원")

    def _notice_from_optional_list_row(
        self, house_manage_no: str, pblanc_no: str, cells: list[str]
    ) -> Notice | None:
        if len(cells) < 7 or not house_manage_no:
            return None
        start, end = _period_bounds(cells[5])
        if end is None or end < self.today:
            return None
        name = cells[2] or "주택명 확인 필요"
        area_parts = []
        if cells[6]:
            area_parts.append(f"당첨자발표 {cells[6]}")
        if cells[3]:
            area_parts.append(f"사업주체 {cells[3]}")
        return Notice(
            source=self.name,
            external_id=f"{house_manage_no}-{pblanc_no}",
            title=f"{name} 임의공급 입주자모집공고",
            region=cells[0] or None,
            housing_type="임의공급",
            target_group="무주택 세대주 확인 필요",
            application_start_date=start,
            application_end_date=end,
            announcement_date=parse_date(cells[4]),
            area_text=" / ".join(area_parts) if area_parts else None,
            status=status_from_dates(None, start, end),
            original_url=_applyhome_detail_url(REMNDR_DETAIL_PAGE, house_manage_no, pblanc_no),
            raw={"house_manage_no": house_manage_no, "pblanc_no": pblanc_no, "list_cells": cells},
        )

    def _fetch_detail_notice(
        self,
        house_manage_no: str,
        pblanc_no: str | None = None,
        housing_type: str | None = None,
    ) -> Notice | None:
        pblanc_no = pblanc_no or house_manage_no
        detail_url = _applyhome_detail_url(REMNDR_DETAIL_PAGE, house_manage_no, pblanc_no)
        response = self.web_client.get(detail_url)
        if response.status_code >= 400:
            raise RuntimeError(
                f"ApplyHome detail request failed status={response.status_code} "
                f"body={response.text[:200]}"
            )
        return self._normalize_detail_html(
            house_manage_no, pblanc_no, detail_url, response.text, housing_type=housing_type
        )

    def _normalize_detail_html(
        self,
        house_manage_no: str,
        pblanc_no: str,
        detail_url: str,
        html: str,
        housing_type: str | None = None,
    ) -> Notice | None:
        soup = BeautifulSoup(html, "html.parser")
        text = clean_text(soup.get_text(" ", strip=True))
        if housing_type == "임의공급":
            if not text or (house_manage_no not in text and house_manage_no not in html):
                return None
        elif not text or house_manage_no not in text:
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

        label = housing_type or "불법행위 재공급"
        app_start = parse_date(special_start or general_start)
        app_end = parse_date(general_start or special_start)
        if label == "임의공급":
            period = re.search(r"청약접수\s+(\d{4}-\d{2}-\d{2})\s*[~～\-]\s*(\d{4}-\d{2}-\d{2})", text)
            if period:
                app_start = parse_date(period.group(1))
                app_end = parse_date(period.group(2))

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
            title=f"{title} {label} 입주자모집공고",
            region=_region_from_address(address),
            housing_type=label,
            target_group="무주택 세대주 확인 필요",
            application_start_date=app_start,
            application_end_date=app_end,
            announcement_date=parse_date(announcement),
            contract_date=parse_date(contract_start),
            area_text=" / ".join(area_parts) if area_parts else None,
            cost_text=_max_price_text(text),
            status=status_from_dates(None, app_start, app_end),
            original_url=detail_url,
            raw={
                "house_manage_no": house_manage_no,
                "pblanc_no": pblanc_no,
                "detail_url": detail_url,
                "detail_text": text,
            },
        )


def _applyhome_detail_url(page: str, house_manage_no: str, pblanc_no: str) -> str:
    return f"{page}?houseManageNo={house_manage_no}&pblancNo={pblanc_no}"


def _house_ids(row: dict[str, Any]) -> tuple[str | None, str | None]:
    house_manage_no = clean_text(first_present(row, "HOUSE_MANAGE_NO"))
    pblanc_no = clean_text(first_present(row, "PBLANC_NO")) or house_manage_no
    return house_manage_no, pblanc_no


def _address_in_regions(address: str | None, regions: tuple[str, ...]) -> bool:
    text = address or ""
    return any(region and region in text for region in regions)


def _is_regular_sale(row: dict[str, Any]) -> bool:
    section = clean_text(first_present(row, "HOUSE_SECD"))
    if section and section != REGULAR_APT_SECTION_CODE:
        return False
    rent = clean_text(first_present(row, "RENT_SECD"))
    rent_name = clean_text(first_present(row, "RENT_SECD_NM")) or ""
    if rent == "1" or "임대" in rent_name:
        return False
    if rent and rent != SALE_RENT_SECTION_CODE and rent_name != "분양주택":
        return False
    return True


def _date_span(
    row: dict[str, Any], start_keys: tuple[str, ...], end_keys: tuple[str, ...]
) -> tuple[date | None, date | None]:
    starts = [parsed for key in start_keys if (parsed := parse_date(row.get(key)))]
    ends = [parsed for key in end_keys if (parsed := parse_date(row.get(key)))]
    return (min(starts) if starts else None, max(ends) if ends else None)


def _manwon(value: Any) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    digits = text.replace(",", "")
    if not digits.isdigit():
        return None
    return int(digits)


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


def _period_bounds(text: str | None) -> tuple[date | None, date | None]:
    if not text:
        return None, None
    dates = [parse_date(match) for match in re.findall(r"\d{4}-\d{2}-\d{2}", text)]
    dates = [item for item in dates if item]
    if not dates:
        return None, None
    return dates[0], dates[-1]


def _max_price_text(text: str) -> str | None:
    prices = [
        int(value.replace(",", ""))
        for value in re.findall(r"\b\d{2,3}\.\d{4}[A-Z]?\s+(\d{1,3},\d{3})", text)
    ]
    # 임의공급 detail tables put the household count between the type code and the price.
    prices.extend(
        int(value.replace(",", ""))
        for value in re.findall(r"\b\d{2,3}\.\d{4}[A-Z]?\s+\d+\s+(\d{1,3},\d{3})", text)
    )
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
