from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from jipsooni.models import Notice
from jipsooni.notify import alert_reasons, format_notice_message

SCHEMA = """
create table if not exists notices (
  id integer primary key, canonical_key text not null unique, source text not null,
  external_id text, title text not null, region text, housing_type text, target_group text,
  application_start_date text, application_end_date text, announcement_date text, contract_date text,
  area_text text, cost_text text, status text, original_url text, content_hash text not null,
  raw text not null default '{}', first_seen_at text, last_seen_at text, updated_at text);
create index if not exists notices_end_idx on notices(application_end_date);
create table if not exists notice_files (
  id integer primary key, notice_id integer not null references notices(id) on delete cascade,
  label text not null, url text not null, unique(notice_id, url));
create table if not exists notice_events (
  id integer primary key, notice_id integer not null references notices(id) on delete cascade,
  event_type text not null, event_key text not null unique, created_at text);
create table if not exists alerts (
  id integer primary key, notice_id integer not null references notices(id) on delete cascade,
  reason text not null, event_key text not null, channel text not null default 'chat',
  message text not null, sent_at text, created_at text, unique(event_key, channel));
create table if not exists collector_runs (
  id integer primary key, source text not null, status text not null, fetched_count integer,
  error_message text, warnings text, started_at text, finished_at text);
"""
DATE_COLS = ("application_start_date", "application_end_date", "announcement_date", "contract_date")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys=on")
    conn.executescript(SCHEMA)
    return conn


def _d(v):
    return v.isoformat() if isinstance(v, date) else v


def upsert_notice(conn: sqlite3.Connection, n: Notice) -> tuple[bool, bool]:
    row = conn.execute("select id, content_hash from notices where canonical_key=?", (n.canonical_key,)).fetchone()
    vals = dict(
        canonical_key=n.canonical_key, source=n.source, external_id=n.external_id, title=n.title,
        region=n.region, housing_type=n.housing_type, target_group=n.target_group,
        application_start_date=_d(n.application_start_date), application_end_date=_d(n.application_end_date),
        announcement_date=_d(n.announcement_date), contract_date=_d(n.contract_date), area_text=n.area_text,
        cost_text=n.cost_text, status=n.status, original_url=n.original_url, content_hash=n.content_hash,
        raw=json.dumps(n.raw, ensure_ascii=False, default=str), now=now(),
    )
    if row is None:
        conn.execute(
            """insert into notices (canonical_key, source, external_id, title, region, housing_type, target_group,
            application_start_date, application_end_date, announcement_date, contract_date, area_text, cost_text,
            status, original_url, content_hash, raw, first_seen_at, last_seen_at, updated_at) values
            (:canonical_key,:source,:external_id,:title,:region,:housing_type,:target_group,:application_start_date,
            :application_end_date,:announcement_date,:contract_date,:area_text,:cost_text,:status,:original_url,
            :content_hash,:raw,:now,:now,:now)""", vals)
        _replace_files(conn, n)
        return True, False
    changed = row["content_hash"] != n.content_hash
    conn.execute(
        """update notices set title=:title, region=:region, housing_type=:housing_type, target_group=:target_group,
        application_start_date=:application_start_date, application_end_date=:application_end_date,
        announcement_date=:announcement_date, contract_date=:contract_date, area_text=:area_text,
        cost_text=:cost_text, status=:status, original_url=:original_url, content_hash=:content_hash, raw=:raw,
        last_seen_at=:now, updated_at=:now where canonical_key=:canonical_key""", vals)
    if changed:
        _replace_files(conn, n)
    return False, changed


def _replace_files(conn, n: Notice) -> None:
    nid = conn.execute("select id from notices where canonical_key=?", (n.canonical_key,)).fetchone()["id"]
    conn.execute("delete from notice_files where notice_id=?", (nid,))
    for f in n.files:
        conn.execute("insert or ignore into notice_files (notice_id,label,url) values (?,?,?)", (nid, f.label, f.url))


def create_alert_if_needed(conn, n: Notice, *, is_new: bool, changed: bool) -> str | None:
    """Returns the first newly created alert message (or None). Creates one alert per applicable reason."""
    # port: LH/마이홈 APIs return already-closed notices; store them but never alert.
    if n.application_end_date and n.application_end_date < date.today():
        return None
    nid = conn.execute("select id from notices where canonical_key=?", (n.canonical_key,)).fetchone()["id"]
    first = None
    for reason in alert_reasons(n, is_new=is_new, changed=changed):
        message = format_notice_message(n, reason)
        if reason in ("접수 시작 임박", "마감 임박"):
            # date events key on the date itself, so a re-listing with new dates fires again
            d = n.application_start_date if reason == "접수 시작 임박" else n.application_end_date
            event_key = f"{n.canonical_key}:{reason}:{d}"
        else:
            event_key = f"{n.canonical_key}:{reason}:{n.content_hash[:12]}"
        conn.execute("insert or ignore into notice_events (notice_id,event_type,event_key,created_at) values (?,?,?,?)",
                     (nid, reason, event_key, now()))
        cur = conn.execute("insert or ignore into alerts (notice_id,reason,event_key,message,created_at) values (?,?,?,?,?)",
                           (nid, reason, event_key, message, now()))
        if cur.rowcount and first is None:
            first = message
    return first


PENDING_ALERT_QUERY = """
select a.id, a.notice_id, a.reason, a.message, n.source, n.title, n.region, n.housing_type, n.target_group,
  n.application_start_date, n.application_end_date, n.announcement_date, n.cost_text, n.original_url
from alerts a join notices n on n.id=a.notice_id where a.sent_at is null order by a.created_at asc, a.id asc
"""


def pending_rows(conn) -> list[dict]:
    rows = []
    for r in conn.execute(PENDING_ALERT_QUERY).fetchall():
        d = dict(r)
        for c in DATE_COLS:
            if d.get(c):
                d[c] = date.fromisoformat(d[c])
        rows.append(d)
    return rows


def mark_alerts_sent(conn, ids: list[int]) -> None:
    conn.executemany("update alerts set sent_at=? where id=?", [(now(), i) for i in ids])


def record_collector_run(conn, **kw) -> None:
    conn.execute(
        "insert into collector_runs (source,status,fetched_count,error_message,warnings,started_at,finished_at) values (?,?,?,?,?,?,?)",
        (kw["source"], kw["status"], kw["fetched_count"], kw["error_message"], json.dumps(kw.get("warnings") or [], ensure_ascii=False), kw["started_at"], kw["finished_at"]))
