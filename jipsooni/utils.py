from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


DATE_PATTERNS = ("%Y-%m-%d", "%Y.%m.%d", "%Y%m%d", "%Y/%m/%d")


def first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def parse_date(value: Any) -> date | None:
    text = clean_text(value)
    if not text:
        return None

    korean_match = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", text)
    if korean_match:
        year, month, day = korean_match.groups()
        return date(int(year), int(month), int(day))

    text = text.replace("년", "-").replace("월", "-").replace("일", "")
    text = re.sub(r"\([^)]*\)", "", text).strip()
    text = text.split(" ")[0]

    for pattern in DATE_PATTERNS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue

    match = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", text)
    if match:
        year, month, day = match.groups()
        return date(int(year), int(month), int(day))
    return None


def extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            nested = extract_items(item)
            if nested:
                return nested
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    candidate_keys = (
        "dsList",
        "data",
        "items",
        "item",
        "list",
        "result",
        "body",
        "response",
    )
    for key in candidate_keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = extract_items(value)
            if nested:
                return nested

    return []


def status_from_dates(raw_status: str | None, start: date | None, end: date | None) -> str | None:
    if raw_status:
        normalized = clean_text(raw_status)
        if normalized:
            return normalized

    today = date.today()
    if start and today < start:
        return "예정"
    if start and end and start <= today <= end:
        return "접수중"
    if end and today > end:
        return "마감"
    return None


_D = r"(?:(\d{4})\s*[.\-/년]\s*)?(\d{1,2})\s*[.\-/월]\s*(\d{1,2})"
_FULL = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
PERIOD_LABELS = ("접수기간", "신청기간", "청약접수", "청약신청", "접수일정", "공고일정", "청약일정", "신청접수",
                 "접수일", "인터넷접수")
_STOP = re.compile(r"■|서류심사|서류제출|대상자 발표|당첨자|발표|계약")


def parse_period(text: str | None, default_year: int | None = None) -> tuple[date | None, date | None]:
    """Application period after a label such as 접수기간/청약신청 일정/공고일정.
    1) full dates (with year) in the block after the label, up to the next section (■, 서류심사, 발표...):
       start = earliest, end = latest (covers 1순위~4순위 / 인터넷·우편 splits);
    2) else 'M.D ~ M.D' with the year from default_year (HUG: '공고일정 : 9.30(수) ~ 10.12(월)').
    '서류접수기간' (document submission after selection) is ignored."""
    if not text:
        return None, None
    hits = sorted((m for label in PERIOD_LABELS for m in re.finditer(re.escape(label), text)),
                  key=lambda m: m.start())
    for m in hits:
            if text[max(0, m.start() - 2): m.start()] == "서류":
                continue
            block = text[m.end(): m.end() + 700]
            stop = _STOP.search(block)
            if stop:
                block = block[: stop.start()]
            full = []
            for y, mo, d in _FULL.findall(block):
                try:
                    full.append(date(int(y), int(mo), int(d)))
                except ValueError:
                    pass
            if len(full) >= 2:
                return min(full), max(full)
            pm = re.search(r"^[^0-9~]{0,12}?" + _D + r"[^~0-9]{0,20}~\s*[^0-9]{0,6}?" + _D, block)
            if not pm:
                if full:
                    return full[0], full[0]
                continue
            y1, m1, d1, y2, m2, d2 = pm.groups()
            year1 = int(y1) if y1 else (int(y2) if y2 else default_year)
            if not year1:
                continue
            year2 = int(y2) if y2 else year1 + (1 if int(m2) < int(m1) else 0)
            try:
                return date(year1, int(m1), int(d1)), date(year2, int(m2), int(d2))
            except ValueError:
                continue
    return None, None


def page_text(html: str) -> str:
    import html as _html
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>|</li>|</tr>", "\n", t, flags=re.I)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[ \t\r\f\v]+", " ", t)
