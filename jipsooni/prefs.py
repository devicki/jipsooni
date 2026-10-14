"""Per-user preferences (replaces jipsooni's hardcoded profile). Stored as JSON."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DEFAULT_PREFS: dict[str, Any] = {
    "version": 1,
    "household": {
        "type": None,            # single | newlywed | couple | family | senior
        "members": None,
        "is_homeless": None,     # 무주택 세대구성원 여부
        "is_householder": None,  # 세대주 여부
        "has_children": False,
        "children_count": 0,
        "has_newborn_or_pregnant": False,
        "age": None,
        "marriage_years": None,
    },
    "finance": {
        "monthly_income_krw": None,
        "total_assets_krw": None,
        "car_value_krw": None,
    },
    "subscription_account": {"has": None, "years": None, "deposit_count": None, "balance_krw": None},
    "goals": ["move_in", "relaxed", "ownership"],
    "budget": {
        "purchase_max_krw": None,   # 매매/분양 상한
        "deposit_max_krw": None,    # 전세/보증금 상한
        "monthly_rent_max_krw": None,
    },
    "regions": {
        "sido": ["서울", "경기", "인천"],   # 광역 단위 (청약홈 API 필터에도 사용)
        "sigungu": [],                      # 최우선 시군구, 예: ["강서구", "양천구"]
        "neighborhoods": [],                # 동/생활권 키워드, 예: ["마곡", "목동"]
    },
    "housing_types": {
        "include": [],   # 가산점 키워드 예: ["행복주택", "든든전세", "공공분양"]
        "exclude": [],   # 제외 키워드
    },
    "filters": {
        "exclude_child_required": None,   # None => !has_children 로 자동 결정
        "exclude_small_units": False,      # 원룸/기숙사/대학생 등 제외 (가족·신혼)
        "drop_outside_regions": True,
    },
    "digest": {"per_section_limit": 5, "relaxed_min_score": 60, "ownership_min_score": 40},
    "sources": ["lh", "myhome", "sh", "hug", "applyhome"],
    "network": {
        # HTTP proxy with Korean egress, used only for LH/마이홈/SH/HUG/청약홈 web. "" = direct.
        "kr_proxy": "",
        # optional: pproxy remote chain; if set, `python -m jipsooni proxy` / collect auto-starts
        # a local forwarder on kr_proxy's host:port, e.g. "socks5://127.0.0.1:1055__http://<host>:8888"
        "kr_proxy_upstream": "",
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_prefs(path: str | None) -> dict[str, Any]:
    if path and Path(path).exists():
        return _merge(DEFAULT_PREFS, json.loads(Path(path).read_text(encoding="utf-8")))
    return copy.deepcopy(DEFAULT_PREFS)


def save_prefs(path: str, prefs: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")


def exclude_child_required(prefs: dict[str, Any]) -> bool:
    flag = prefs["filters"].get("exclude_child_required")
    if flag is None:
        h = prefs["household"]
        return not (h.get("has_children") or h.get("has_newborn_or_pregnant"))
    return bool(flag)
