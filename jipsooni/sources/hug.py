from __future__ import annotations

from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from jipsooni.config import Settings, kr_client
from jipsooni.models import Notice, NoticeFile
from jipsooni.sources.base import NoticeSource, enrich_dates_from_detail
from jipsooni.utils import clean_text, parse_date


class HugNoticeSource(NoticeSource):
    name = "HUG"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or kr_client(settings, follow_redirects=True)

    def fetch(self) -> list[Notice]:
        response = self.client.get(self.settings.hug_notice_url)
        if response.status_code >= 400:
            raise RuntimeError(
                f"HUG page request failed status={response.status_code} body={response.text[:200]}"
            )
        response.encoding = "euc-kr"
        notices = self._parse(response.text, self.settings.hug_notice_url)
        return enrich_dates_from_detail(notices, self.settings, self._get_html, "hug")

    def _get_html(self, url: str) -> str:
        r = self.client.get(url)
        r.raise_for_status()
        r.encoding = "euc-kr"
        return r.text

    def _parse(self, html: str, base_url: str) -> list[Notice]:
        soup = BeautifulSoup(html, "html.parser")
        notices: list[Notice] = []
        for row in soup.select("tr"):
            link = row.select_one("td.subject a[href]")
            if not link:
                continue
            title = clean_text(link.get_text(" ", strip=True))
            if not title or "든든전세" not in title:
                continue
            if not _is_recruit_notice(title):
                continue
            date_cell = row.select_one("td.data")
            announced = parse_date(date_cell.get_text(" ", strip=True) if date_cell else None)
            detail_url = urljoin(base_url, link["href"].strip())
            article_id = _article_id(detail_url) or title
            files = self._fetch_files(detail_url)

            notices.append(
                Notice(
                    source=self.name,
                    external_id=article_id,
                    title=title,
                    region=_region_hint(title),
                    housing_type="든든전세",
                    target_group="무주택세대구성원",
                    announcement_date=announced,
                    area_text=_detail_text(announced, files),
                    status="공지",
                    original_url=detail_url,
                    files=tuple(files),
                    raw={
                        "title": title,
                        "announcement_date": str(announced) if announced else None,
                        "url": detail_url,
                        "files": [file.__dict__ for file in files],
                    },
                )
            )

        unique: dict[str, Notice] = {}
        for notice in notices:
            unique.setdefault(notice.canonical_key, notice)
        return list(unique.values())

    def _fetch_files(self, detail_url: str) -> list[NoticeFile]:
        try:
            response = self.client.get(detail_url)
            if response.status_code >= 400:
                return []
            response.encoding = "euc-kr"
        except Exception:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        files = []
        for link in soup.select("a[href]"):
            label = clean_text(link.get_text(" ", strip=True)) or "첨부파일"
            href = link.get("href", "")
            if not href:
                continue
            if ".pdf" not in href.lower() and "download" not in href.lower():
                continue
            files.append(NoticeFile(label=label, url=urljoin(detail_url, href)))
        return files


def _article_id(url: str) -> str | None:
    marker = "articleId="
    if marker not in url:
        return None
    return url.split(marker, 1)[1].split("&", 1)[0].strip()


def _region_hint(title: str) -> str | None:
    regions = [region for region in ("서울", "경기", "인천", "부산") if region in title]
    return ", ".join(regions) if regions else None


def _is_recruit_notice(title: str) -> bool:
    if any(keyword in title for keyword in ("매뉴얼", "홍보", "갱신", "재계약", "경쟁률")):
        return False
    return "모집공고" in title or "사전 안내" in title or "입주자 모집" in title


def _detail_text(announced: object | None, files: list[NoticeFile]) -> str | None:
    parts = []
    if announced:
        parts.append(f"공고일 {announced}")
    if files:
        parts.append(f"첨부 {len(files)}개")
    return " / ".join(parts) if parts else None
