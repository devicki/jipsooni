from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class NoticeFile:
    label: str
    url: str


@dataclass(frozen=True)
class Notice:
    source: str
    external_id: str | None
    title: str
    region: str | None = None
    housing_type: str | None = None
    target_group: str | None = None
    application_start_date: date | None = None
    application_end_date: date | None = None
    announcement_date: date | None = None
    contract_date: date | None = None
    area_text: str | None = None
    cost_text: str | None = None
    status: str | None = None
    original_url: str | None = None
    files: tuple[NoticeFile, ...] = field(default_factory=tuple)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def canonical_key(self) -> str:
        if self.source and self.external_id:
            return f"{self.source}:{self.external_id}"

        basis = {
            "source": self.source,
            "title": self.title,
            "region": self.region,
            "application_end_date": self.application_end_date.isoformat()
            if self.application_end_date
            else None,
            "original_url": self.original_url,
        }
        encoded = json.dumps(basis, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
        return f"{self.source}:hash:{digest}"

    @property
    def content_hash(self) -> str:
        payload = {
            "title": self.title,
            "region": self.region,
            "housing_type": self.housing_type,
            "target_group": self.target_group,
            "application_start_date": self.application_start_date.isoformat()
            if self.application_start_date
            else None,
            "application_end_date": self.application_end_date.isoformat()
            if self.application_end_date
            else None,
            "announcement_date": self.announcement_date.isoformat()
            if self.announcement_date
            else None,
            "contract_date": self.contract_date.isoformat()
            if self.contract_date
            else None,
            "area_text": self.area_text,
            "cost_text": self.cost_text,
            "status": self.status,
            "original_url": self.original_url,
            "files": [file.__dict__ for file in self.files],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
