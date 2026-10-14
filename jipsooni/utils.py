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
