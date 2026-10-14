from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any

from jipsooni.config import Settings
from jipsooni.db import connect, mark_alerts_sent, pending_rows
from jipsooni.prefs import DEFAULT_PREFS, load_prefs
from jipsooni.ranking import (RankedAlert, in_preferred_regions, is_excluded_for_user_profile,
                              is_preferred_move_in_row, is_small_unit, rank_alert)

CATEGORY_LABELS = {"move_in": "입주 안정 후보", "relaxed": "조건완화 후보", "ownership": "내집마련 후보"}


def should_discard(row, ranked: RankedAlert, prefs, today: date) -> bool:
    end = row.get("application_end_date")
    if isinstance(end, date) and end < today:
        return True
    if ranked.category not in (prefs.get("goals") or CATEGORY_LABELS):
        return True
    if is_excluded_for_user_profile(row, prefs):
        return True
    # port fix: ownership used to skip region rules entirely (인천 공공분양 for a 서울·경기 user)
    if ranked.category in ("relaxed", "ownership") and prefs["filters"].get("drop_outside_regions", True) \
            and not in_preferred_regions(row, prefs):
        return True
    if prefs["filters"].get("exclude_small_units") and is_small_unit(row):
        return True
    if ranked.category == "move_in" and not is_preferred_move_in_row(row, prefs):
        return True
    return False


def build_digest(rows: list[dict[str, Any]], prefs=None, today: date | None = None, limit: int | None = None):
    prefs = prefs or DEFAULT_PREFS
    today = today or date.today()
    limit = limit or prefs["digest"]["per_section_limit"]
    mins = {"move_in": None, "relaxed": prefs["digest"]["relaxed_min_score"], "ownership": prefs["digest"]["ownership_min_score"]}
    groups: dict[str, list[int]] = {}
    best: dict[str, tuple[RankedAlert, dict]] = {}
    discard: list[int] = []
    for row in rows:
        r = rank_alert(row, prefs, today)
        if should_discard(row, r, prefs, today):
            discard.append(r.alert_id); continue
        groups.setdefault(r.group_key, []).append(r.alert_id)
        if r.group_key not in best or r.score > best[r.group_key][0].score:
            best[r.group_key] = (r, row)
    ordered = sorted(best.values(), key=lambda x: x[0].score, reverse=True)
    sections = {}
    for cat in ("move_in", "relaxed", "ownership"):
        items = [x for x in ordered if x[0].category == cat and (mins[cat] is None or x[0].score >= mins[cat])][:limit]
        sections[cat] = [{
            "alert_ids": groups[r.group_key], "score": r.score, "reasons": list(r.reasons),
            "source": row.get("source"), "title": row.get("title"), "region": row.get("region"),
            "housing_type": row.get("housing_type"), "reason": row.get("reason"),
            "application_start_date": _iso(row.get("application_start_date")),
            "application_end_date": _iso(row.get("application_end_date")),
            "cost_text": row.get("cost_text"), "url": row.get("original_url"), "message": r.message,
        } for r, row in items]
    return sections, discard


def _iso(v):
    return v.isoformat() if isinstance(v, date) else v


def main() -> None:
    ap = argparse.ArgumentParser(description="Pick pending alerts per prefs; JSON to stdout")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--mark-sent", action="store_true")
    ap.add_argument("--prefs")
    a = ap.parse_args()
    s = Settings.from_env()
    prefs = load_prefs(a.prefs or s.prefs_path)
    conn = connect(s.db_path)
    rows = pending_rows(conn)
    sections, discard = build_digest(rows, prefs, limit=a.limit)
    out = {"pending": len(rows), "discarded": len(discard),
           "sections": {CATEGORY_LABELS[k]: v for k, v in sections.items()}, "marked_sent": False}
    if a.mark_sent:
        mark_alerts_sent(conn, discard + [i for v in sections.values() for it in v for i in it["alert_ids"]])
        conn.commit(); out["marked_sent"] = True
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
