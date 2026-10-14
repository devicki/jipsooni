"""Regression tests from real (sanitized) responses captured 2026-10-14 via the KR proxy."""
import json
from datetime import date
from pathlib import Path

import httpx

from jipsooni.config import Settings
from jipsooni.db import connect, create_alert_if_needed, upsert_notice
from jipsooni.models import Notice
from jipsooni.ranking import _category, _combined_text, normalize_title, rank_alert
from jipsooni.sources.hug import HugNoticeSource
from jipsooni.sources.lh import LhNoticeSource
from jipsooni.sources.myhome import MyHomeNoticeSource
from jipsooni.sources.sh import ShNoticeSource

FX = Path(__file__).parent / "fixtures"


def _settings(**kw):
    base = dict(db_path=":memory:", prefs_path="", data_go_kr_service_key="k", lh_notice_endpoint="https://t/lh",
                lh_lookback_days=1, lh_lookahead_days=1, myhome_notice_endpoint="https://t/myhome",
                myhome_notice_page_url="https://t/mp", applyhome_service_key="k",
                applyhome_remndr_endpoint="https://t/ah", sh_notice_url="https://t/sh/list",
                hug_notice_url="https://t/hug/s070301.jsp", log_level="INFO")
    return Settings(**(base | kw))


def _client(routes):
    def handler(req: httpx.Request):
        for prefix, body in routes.items():
            if str(req.url).startswith(prefix) and "articleId" not in str(req.url):
                return httpx.Response(200, content=body)
        return httpx.Response(200, content=b"<html></html>")
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_lh_live_list_of_dsSch_dsList():
    src = LhNoticeSource(_settings(), client=_client({"https://t/lh": (FX / "lh_live.json").read_bytes()}))
    notices = src.fetch()
    assert len(notices) == 3
    n = next(x for x in notices if "창녕영산" in x.title)
    assert n.region == "경상남도" and n.housing_type == "행복주택"
    assert n.application_end_date == date(2026, 10, 21) and n.status == "공고중"
    assert n.external_id.startswith("2015122300") and n.original_url.startswith("https://apply.lh.or.kr")


def test_myhome_live_dedupes_and_status_from_dates():
    src = MyHomeNoticeSource(_settings(), client=_client({"https://t/myhome": (FX / "myhome_live.json").read_bytes()}))
    notices = src.fetch()
    raw = json.loads((FX / "myhome_live.json").read_text())["response"]["body"]["item"]
    assert len(notices) == len({(r["pblancId"], r["houseSn"]) for r in raw})
    for n in notices:
        assert n.status not in ("일반공고",)  # sttusNm is notice kind, not reception state
        assert n.application_end_date and n.original_url.startswith("https://www.myhome.go.kr")
        assert n.contract_date is None  # przwnerPresnatnDe is 당첨자발표, not 계약일
    fixed = next(n for n in notices if "창녕영산" in n.title)
    assert fixed.status.startswith("정정공고")


def test_sh_live_skips_closed_and_result_notices():
    html = (FX / "sh_live.html").read_bytes()
    src = ShNoticeSource(_settings(), client=_client({"https://t/sh": html}))
    notices = src.fetch()
    assert notices and all("결과" not in n.title for n in notices)
    assert all("마감" not in (n.status or "") for n in notices)
    assert any("신정" in n.title and n.region == "서울특별시 양천구" for n in notices)


def test_hug_live_euc_kr_board():
    body = (FX / "hug_live.html").read_bytes()
    src = HugNoticeSource(_settings(), client=_client({"https://t/hug": body}))
    notices = src.fetch()
    assert notices and all("든든전세" in n.title for n in notices)
    assert all(n.original_url and "articleId=" in n.original_url for n in notices)


def test_lh_and_myhome_same_notice_group_together():
    lh = "[정정공고](정정) 창녕영산 1BL 행복주택 예비입주자 모집 공고 ('26.10.07. 공고)"
    mh = "창녕영산 1BL 행복주택 예비입주자 모집 공고 (‘26.10.07. 공고)"
    assert normalize_title(lh) == normalize_title(mh)


def test_bibunyang_dundun_is_rental_not_ownership():
    row = {"id": 1, "notice_id": 1, "message": "", "title": "[서울지역본부] 26년 2차 비분양전환형 든든전세주택 입주자 모집공고",
           "region": "서울특별시 동대문구", "reason": "새 공고"}
    assert _category(_combined_text(row)) == "move_in"
    assert "분양/매매 비용 확인 필요" not in rank_alert(row).reasons


def test_no_alert_for_already_closed_notice():
    conn = connect(":memory:")
    n = Notice(source="LH", external_id="x", title="t", application_end_date=date(2020, 1, 1))
    upsert_notice(conn, n)
    assert create_alert_if_needed(conn, n, is_new=True, changed=False) is None


def test_kr_proxy_only_for_korean_sources(monkeypatch):
    from jipsooni.sources.applyhome import ApplyHomeRemndrSource
    s = _settings(kr_proxy="http://127.0.0.1:9")
    a = ApplyHomeRemndrSource(s)
    assert a.client._mounts == {} or all(v is None for v in a.client._mounts.values())  # odcloud direct
    assert any(v is not None for v in a.web_client._mounts.values())
    assert any(v is not None for v in LhNoticeSource(s).client._mounts.values())
