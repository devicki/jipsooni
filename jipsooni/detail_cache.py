"""Small JSON cache for detail-page lookups (SH/HUG/LH), so each run doesn't refetch every page.
A hit with dates is kept for `ttl_found_days`; a miss (no dates found / fetch error) is retried after
`ttl_miss_hours`."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

_LOCK = threading.Lock()


class DetailCache:
    def __init__(self, path: str | None, ttl_found_days: int = 30, ttl_miss_hours: int = 12) -> None:
        self.path = path
        self.ttl_found = timedelta(days=ttl_found_days)
        self.ttl_miss = timedelta(hours=ttl_miss_hours)
        self.data: dict[str, Any] = {}
        self.fetches = 0
        if path and Path(path).exists():
            try:
                self.data = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def get(self, key: str, fetch: Callable[[], dict[str, Any] | None]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with _LOCK:
            hit = self.data.get(key)
        if hit:
            age = now - datetime.fromisoformat(hit["fetched_at"])
            if age < (self.ttl_found if hit.get("found") else self.ttl_miss):
                return hit["value"]
        try:
            value = fetch() or {}
        except Exception:  # network/parse errors: remember as a miss, don't fail the source
            value = {}
        self.fetches += 1
        found = any(v for v in value.values())
        with _LOCK:
            self.data[key] = {"fetched_at": now.isoformat(), "found": found, "value": value}
        return value

    def save(self) -> None:
        if not self.path:
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{self.path}.tmp"
        with _LOCK:
            Path(tmp).write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
