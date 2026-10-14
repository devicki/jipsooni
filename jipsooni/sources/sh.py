from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import httpx
from bs4 import BeautifulSoup

from jipsooni.config import Settings, kr_client
from jipsooni.models import Notice
from jipsooni.sources.base import NoticeSource, enrich_dates_from_detail
from jipsooni.utils import clean_text, parse_date, status_from_dates


class ShNoticeSource(NoticeSource):
    name = "SH"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or kr_client(settings, follow_redirects=True)

    def fetch(self) -> list[Notice]:
        first_html = self._fetch_page(1)
        notices = self._parse(first_html, self.settings.sh_notice_url)
        last_page = self._last_page(first_html)
        for page in range(2, last_page + 1):
            notices.extend(self._parse(self._fetch_page(page), self.settings.sh_notice_url))

        unique: dict[str, Notice] = {}
        for notice in notices:
            unique.setdefault(notice.canonical_key, notice)
        return enrich_dates_from_detail(list(unique.values()), self.settings, self._get_html, "sh")

    def _get_html(self, url: str) -> str:
        r = self.client.get(url)
        r.raise_for_status()
        return r.text

    def _fetch_page(self, page: int) -> str:
        response = self.client.get(_with_page(self.settings.sh_notice_url, page))
        if response.status_code >= 400:
            raise RuntimeError(
                f"SH page request failed status={response.status_code} body={response.text[:200]}"
            )
        return response.text

    def _parse(self, html: str, base_url: str) -> list[Notice]:
        soup = BeautifulSoup(html, "html.parser")
        notices: list[Notice] = []
        for row in soup.select(".board-list tbody tr"):
            cells = row.select("td")
            if len(cells) < 8:
                continue
            housing_type = clean_text(cells[1].get_text(" ", strip=True))
            title = clean_text(cells[2].get_text(" ", strip=True))
            if not title:
                continue
            announced = parse_date(cells[3].get_text(" ", strip=True))
            winner_date = parse_date(cells[4].get_text(" ", strip=True))
            raw_status = clean_text(cells[5].get_text(" ", strip=True))
            if not _is_recruitment_notice(title, raw_status):
                continue
            department = clean_text(cells[6].get_text(" ", strip=True))
            link = cells[7].select_one("a[href]")
            original_url = urljoin(base_url, link["href"]) if link else base_url
            external_id = _external_id(original_url) or title

            notices.append(
                Notice(
                    source=self.name,
                    external_id=external_id,
                    title=title,
                    region=_region_hint(title),
                    housing_type=housing_type,
                    target_group=_target_group(title),
                    announcement_date=announced,
                    contract_date=winner_date,
                    area_text=_detail_text(announced, winner_date, department),
                    status=status_from_dates(raw_status, None, None),
                    original_url=original_url,
                    raw={
                        "housing_type": housing_type,
                        "title": title,
                        "announced": str(announced) if announced else None,
                        "winner_date": str(winner_date) if winner_date else None,
                        "status": raw_status,
                        "department": department,
                        "url": original_url,
                    },
                )
            )

        unique: dict[str, Notice] = {}
        for notice in notices:
            unique.setdefault(notice.canonical_key, notice)
        return list(unique.values())

    def _last_page(self, html: str) -> int:
        soup = BeautifulSoup(html, "html.parser")
        pages = [1]
        for link in soup.select(".paging a[href]"):
            href = link.get("href", "")
            qs = parse_qs(urlparse(href).query)
            if "cp" in qs:
                try:
                    pages.append(int(qs["cp"][0]))
                except (TypeError, ValueError):
                    continue
        return max(pages)


def _detail_text(announced: object | None, winner_date: object | None, department: str | None) -> str | None:
    parts = []
    if announced:
        parts.append(f"공고일 {announced}")
    if winner_date:
        parts.append(f"당첨자발표 {winner_date}")
    if department:
        parts.append(f"담당부서 {department}")
    return " / ".join(parts) if parts else None


def _external_id(url: str) -> str | None:
    marker = "seq="
    if marker not in url:
        return None
    return url.split(marker, 1)[1].split("&", 1)[0]


def _with_page(url: str, page: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["cp"] = [str(page)]
    query.setdefault("supplyType", ["publicLease"])
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _target_group(title: str) -> str | None:
    targets = []
    for keyword in ("청년", "신혼", "신생아", "고령자", "일반"):
        if keyword in title:
            targets.append(keyword)
    return ", ".join(targets) if targets else None


SEOUL_GU = ("종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구",
            "노원구", "은평구", "서대문구", "마포구", "양천구", "강서구", "구로구", "금천구", "영등포구",
            "동작구", "관악구", "서초구", "강남구", "송파구", "강동구")


def _region_hint(title: str) -> str:
    # 제목에 자치구명이 있으면 그대로 사용 (25개 구 전체). 없으면 아래 동 이름 보조표로 추정.
    for gu in sorted(SEOUL_GU, key=len, reverse=True):
        if gu in title or (len(gu) > 2 and gu[:-1] + " " in title):
            return f"서울특별시 {gu}"
    # 보조표: 일부 동 이름 → 자치구 (부분 목록, 필요 시 확장)
    district_by_neighborhood = {
        "화곡": "강서구",
        "마곡": "강서구",
        "등촌": "강서구",
        "가양": "강서구",
        "방화": "강서구",
        "목동": "양천구",
        "신월": "양천구",
        "신정": "양천구",
        "구로": "구로구",
        "고척": "구로구",
        "개봉": "구로구",
        "오류": "구로구",
        "가리봉": "구로구",
        "사당": "동작구",
        "상도": "동작구",
        "흑석": "동작구",
        "노량진": "동작구",
        "대방": "동작구",
        "신대방": "동작구",
        "신림": "관악구",
        "봉천": "관악구",
        "관악": "관악구",
    }
    for keyword, district in district_by_neighborhood.items():
        if keyword in title:
            return f"서울특별시 {district}"
    return "서울특별시"


def _is_recruitment_notice(title: str, status: str | None) -> bool:
    excluded = (
        "당첨자",
        "입주대상자 발표",
        "대상자 발표",
        "예비입주자 발표",
        "서류심사",
        "계약안내",
        "계약 안내",
        "선정결과",
        "결과 발표",
        "경쟁률",
        "접수 결과",
        "접수결과",
        "일정 연기",
        "연기 안내",
    )
    if any(keyword in title for keyword in excluded):
        return False
    # port: the SH list keeps years of closed notices; they have no dates, so drop by status.
    if status and any(word in status for word in ("마감", "완료", "종료")):
        return False

    recruitment_keywords = (
        "모집공고",
        "입주자모집",
        "입주자 모집",
        "잔여세대",
        "공가",
        "예비입주자 모집",
    )
    if any(keyword in title for keyword in recruitment_keywords):
        return True
    return "모집" in (status or "")
