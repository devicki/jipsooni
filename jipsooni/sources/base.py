from __future__ import annotations

from abc import ABC, abstractmethod

from jipsooni.models import Notice


class NoticeSource(ABC):
    name: str

    @abstractmethod
    def fetch(self) -> list[Notice]:
        raise NotImplementedError


def enrich_dates_from_detail(notices, settings, get_html, prefix: str):
    """Fill application_start/end_date from each notice's detail page (SH/HUG list pages carry no dates).
    Cached per URL; only notices without an end date are looked up."""
    import dataclasses
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date

    from jipsooni.detail_cache import DetailCache
    from jipsooni.utils import page_text, parse_period, status_from_dates

    if not settings.fetch_details:
        return notices
    cache = DetailCache(settings.detail_cache_path or None)

    def one(n):
        if n.application_end_date or not n.original_url:
            return n
        year = n.announcement_date.year if n.announcement_date else date.today().year

        def fetch():
            s, e = parse_period(page_text(get_html(n.original_url)), default_year=year)
            return {"start": s.isoformat() if s else None, "end": e.isoformat() if e else None}

        info = cache.get(f"{prefix}:{n.original_url}", fetch)
        if not info.get("end"):
            return n
        s = date.fromisoformat(info["start"]) if info.get("start") else None
        e = date.fromisoformat(info["end"])
        return dataclasses.replace(n, application_start_date=s, application_end_date=e,
                                   status=status_from_dates(None, s, e) or n.status)

    with ThreadPoolExecutor(max_workers=4) as pool:
        out = list(pool.map(one, notices))
    cache.save()
    return out
