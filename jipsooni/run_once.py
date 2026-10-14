from __future__ import annotations

import argparse
import dataclasses
import os
import json
import logging
import re
from datetime import datetime, timezone

from jipsooni.config import Settings
from jipsooni.db import connect, create_alert_if_needed, record_collector_run, upsert_notice
from jipsooni.kr_proxy import ensure_forwarder
from jipsooni.prefs import load_prefs
from jipsooni.sources import (ApplyHomeRemndrSource, HugNoticeSource, LhNoticeSource, MyHomeNoticeSource,
                              SampleNoticeSource, ShNoticeSource)

ALL = ("lh", "myhome", "sh", "hug", "applyhome")


def build_sources(settings: Settings, names, prefs):
    sel = set(names)
    if "all" in sel:
        sel = set(prefs.get("sources") or ALL)
    out = []
    if "sample" in sel: out.append(SampleNoticeSource())
    if "lh" in sel: out.append(LhNoticeSource(settings))
    if "myhome" in sel: out.append(MyHomeNoticeSource(settings))
    if "sh" in sel: out.append(ShNoticeSource(settings))
    if "hug" in sel: out.append(HugNoticeSource(settings))
    if "applyhome" in sel:
        out.append(ApplyHomeRemndrSource(settings, region_filters=tuple(prefs["regions"].get("sido") or ()) or None))
    return out


def safe_error(exc: Exception, s: Settings) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    for secret in (s.data_go_kr_service_key, s.applyhome_service_key):
        if secret:
            msg = msg.replace(secret, "[REDACTED]")
    msg = re.sub(r"(?i)(service[_-]?key|api[_-]?key|authorization|token)=([^&\s]+)", r"\1=[REDACTED]", msg)
    return msg[:500]


def hint(msg: str, s: Settings, name: str) -> str | None:
    if "Timeout" in msg or "ConnectError" in msg:
        if s.kr_proxy:
            return "network error via KR proxy (proxy down or site slow); check forwarder/tailnet"
        return "network unreachable (likely foreign-IP block); set network.kr_proxy or run from a Korean-IP machine"
    if not s.data_go_kr_service_key:
        return "DATA_GO_KR_SERVICE_KEY not set"
    if "SERVICE_KEY_IS_NOT_REGISTERED" in msg or "403" in msg or "-4" in msg:
        return f"key not approved for {name}; apply on data.go.kr"
    return None


def run(settings: Settings, sources, dry_run=False) -> dict:
    report = {"sources": [], "fetched": 0, "inserted": 0, "changed": 0, "alerts": 0}
    batches = []
    for src in sources:
        st = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry = {"source": src.name, "status": "success", "fetched": 0, "error": None, "hint": None, "warnings": []}
        try:
            notices = src.fetch()
            entry["fetched"] = len(notices)
            batches.extend(notices)
        except Exception as exc:  # noqa: BLE001
            entry.update(status="failed", error=safe_error(exc, settings))
            entry["hint"] = hint(entry["error"], settings, src.name)
        entry["warnings"] = list(getattr(src, "warnings", []))
        entry["started_at"], entry["finished_at"] = st, datetime.now(timezone.utc).isoformat(timespec="seconds")
        report["sources"].append(entry)
    report["fetched"] = len(batches)
    if dry_run:
        report["sample"] = [n.title for n in batches[:5]]
        return report
    conn = connect(settings.db_path)
    for e in report["sources"]:
        record_collector_run(conn, source=e["source"], status=e["status"], fetched_count=e["fetched"],
                             error_message=e["error"], warnings=e["warnings"], started_at=e["started_at"], finished_at=e["finished_at"])
    for n in batches:
        new, changed = upsert_notice(conn, n)
        report["inserted"] += new; report["changed"] += changed
        report["alerts"] += create_alert_if_needed(conn, n, is_new=new, changed=changed) is not None
    conn.commit()
    report["pending_alerts"] = conn.execute("select count(*) from alerts where sent_at is null").fetchone()[0]
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Collect notices once; JSON report to stdout")
    ap.add_argument("--source", action="append", default=[], choices=["all", *ALL, "sample"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prefs")
    a = ap.parse_args()
    s = Settings.from_env()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.WARNING))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    prefs = load_prefs(a.prefs or s.prefs_path)
    net = prefs.get("network") or {}
    if not s.kr_proxy and net.get("kr_proxy"):
        s = dataclasses.replace(s, kr_proxy=net["kr_proxy"])
    upstream = os.getenv("JIPSOONI_KR_PROXY_UPSTREAM") or net.get("kr_proxy_upstream", "")
    proxy_state = ensure_forwarder(s.kr_proxy, upstream) if s.kr_proxy else "none"
    rep = run(s, build_sources(s, a.source or ["all"], prefs), dry_run=a.dry_run)
    rep["kr_proxy"] = {"configured": bool(s.kr_proxy), "forwarder": proxy_state}
    rep["failed_sources"] = [e["source"] for e in rep["sources"] if e["status"] == "failed"]
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    raise SystemExit(2 if rep["failed_sources"] and not rep["fetched"] else 0)


if __name__ == "__main__":
    main()
