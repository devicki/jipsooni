"""Regressions for the missed 강서구 든든전세 notice (LH panId 2015122300020843, UPP_AIS_TP_CD=13) and
the follow-up fixes: LH type codes, 마이홈 multi-district regions, target-group filter, SH/HUG dates."""
from __future__ import annotations

import json
from datetime import date, timedelta

from jipsooni.config import Settings
from jipsooni.detail_cache import DetailCache
from jipsooni.models import Notice
from jipsooni.notify import alert_reasons
from jipsooni.prefs import DEFAULT_PREFS, _merge
from jipsooni.ranking import excluded_target_group, is_preferred_move_in_row, rank_alert
from jipsooni.sources.base import enrich_dates_from_detail
from jipsooni.sources.lh import LhNoticeSource, apply_detail, parse_lh_detail
from jipsooni.sources.myhome import MyHomeNoticeSource
from jipsooni.utils import parse_period

TODAY = date.today()


def _settings(**kw) -> Settings:
    base = dict(db_path=":memory:", prefs_path="", data_go_kr_service_key="key",
                lh_notice_endpoint="https://example.test/lh", lh_lookback_days=365, lh_lookahead_days=120,
                myhome_notice_endpoint="https://example.test/myhome", myhome_notice_page_url="",
                applyhome_service_key="key", applyhome_remndr_endpoint="", sh_notice_url="", hug_notice_url="",
                log_level="INFO")
    base.update(kw)
    return Settings(**base)


class _Resp:
    def __init__(self, payload):
        self.payload, self.status_code, self.text = payload, 200, json.dumps(payload)

    def json(self):
        return self.payload


class _LhByCode:
    """Fake LH API: the 든든전세 notice only exists under UPP_AIS_TP_CD=13."""
    def __init__(self):
        self.codes = []

    def get(self, url, params):
        code = params["UPP_AIS_TP_CD"]
        self.codes.append(code)
        rows = {
            "06": [{"ALL_CNT": "2", "PAN_ID": "old", "PAN_NM": "마감된 국민임대", "CNP_CD_NM": "경기도",
                    "CLSG_DT": (TODAY - timedelta(days=30)).strftime("%Y.%m.%d")},
                   {"ALL_CNT": "2", "PAN_ID": "open06", "PAN_NM": "행복주택", "CNP_CD_NM": "서울특별시",
                    "CLSG_DT": (TODAY + timedelta(days=5)).strftime("%Y.%m.%d")}],
            "13": [{"ALL_CNT": "1", "PAN_ID": "2015122300020843", "PAN_NM": "[서울지역본부] 26년 2차 비분양전환형 든든전세주택 입주자 모집공고",
                    "UPP_AIS_TP_NM": "주거복지", "AIS_TP_CD_NM": "매입임대", "CNP_CD_NM": "서울특별시",
                    "PAN_NT_ST_DT": "2026.10.01", "CLSG_DT": (TODAY + timedelta(days=2)).strftime("%Y.%m.%d"),
                    "PAN_SS": "접수중", "DTL_URL": "https://apply.lh.or.kr/x?panId=2015122300020843"}],
        }.get(code, [])
        return _Resp([{"dsSch": []}, {"dsList": rows}])


def test_lh_queries_jugeo_bokji_code_and_keeps_only_open_rows():
    client = _LhByCode()
    src = LhNoticeSource(_settings(lh_upper_type_codes=("05", "06", "13", "39")), client=client)  # type: ignore[arg-type]
    ids = {n.external_id for n in src.fetch()}
    assert set(client.codes) == {"05", "06", "13", "39"}
    assert "2015122300020843" in ids          # 주거복지(13) 든든전세 is now collected
    assert "open06" in ids and "old" not in ids  # selected by open deadline, closed rows dropped


LH_DETAIL = """<div>공급정보 모집지역 : 강동구 ,강서구, 광진구, 금천구, 노원구 모집세대 공급정보 합계 224</div>"""


def test_lh_detail_adds_recruit_districts_to_region():
    info = parse_lh_detail(LH_DETAIL)
    assert info["areas"] == ["강동구", "강서구", "광진구", "금천구", "노원구"]
    n = apply_detail(Notice(source="LH", external_id="1", title="든든전세", region="서울특별시"), info)
    assert n.region == "서울특별시 강동구, 강서구, 광진구, 금천구, 노원구"


def test_myhome_multi_district_notice_keeps_all_districts():
    rows = [{"pblancId": "21370", "houseSn": 0, "pblancNm": "[서울지역본부] 26년 2차 비분양전환형 든든전세주택 입주자 모집공고",
             "brtcNm": "서울특별시", "signguNm": g} for g in ("동대문구", "강서구", "금천구")]
    for ordered in (rows, rows[::-1]):
        src = MyHomeNoticeSource(_settings())
        src._request_api_page = lambda params, o=ordered: {"data": o}  # type: ignore[method-assign]
        (n,) = src._fetch_api()
        assert n.region == "서울특별시 강서구, 금천구, 동대문구"


PREFS = _merge(DEFAULT_PREFS, {"regions": {"sido": ["서울", "경기"], "sigungu": ["강서구"]},
                               "household": {"type": "newlywed", "members": 2}})


def _row(title, region, **kw):
    return {"id": 1, "notice_id": 1, "title": title, "region": region, "message": "", "reason": "새 공고",
            "application_end_date": None, "announcement_date": None, **kw}


def test_gangseo_dundeun_jeonse_ranks_as_top_tier():
    r = _row("[서울지역본부] 26년 2차 비분양전환형 든든전세주택 입주자 모집공고", "서울특별시 강서구, 금천구, 동대문구")
    assert is_preferred_move_in_row(r, PREFS)
    reasons = rank_alert(r, PREFS, TODAY).reasons
    assert "최우선 지역" in reasons and "강서구 포함" in reasons


def test_target_group_filter_from_household():
    assert excluded_target_group(_row("은평구 홀몸어르신 은빛주택 잔여세대 모집", "서울특별시 은평구"), PREFS) == "senior"
    assert excluded_target_group(_row("강동구 청년창업가,예술인주택 잔여세대 모집", "서울특별시 강동구"), PREFS) == "special_occupation"
    assert excluded_target_group(_row("공공기숙사 입주자 모집", "서울특별시"), PREFS) == "student"
    assert excluded_target_group(_row("행복주택 신혼부부 모집", "서울특별시"), PREFS) is None
    # not onboarded -> nothing hidden; explicit allow wins
    assert excluded_target_group(_row("은빛주택 모집", "서울"), DEFAULT_PREFS) is None
    allow = _merge(PREFS, {"filters": {"allow_target_groups": ["senior"]}})
    assert excluded_target_group(_row("은빛주택 모집", "서울"), allow) is None


def test_parse_period_formats_from_sh_and_hug():
    assert parse_period("■ 접수기간 : 2026. 10. 26.(월) ~ 2026.11. 06.(금) 18시까지") == (date(2026, 10, 26), date(2026, 11, 6))
    assert parse_period("ㅁ 공고일정 : 9.30(수) ~ 10.12(월)", 2026) == (date(2026, 9, 30), date(2026, 10, 12))
    assert parse_period("청약접수 : 2026.12.28 ~ 1.3") == (date(2026, 12, 28), date(2027, 1, 3))
    assert parse_period("날짜 없음") == (None, None)


def test_detail_dates_are_filled_and_cached(tmp_path):
    calls = []

    def get_html(url):
        calls.append(url)
        return "<p>■ 접수기간 : 2026.10.08.(목) ~2026.10.22.(목) 15시까지</p>"

    s = _settings(fetch_details=True, detail_cache_path=str(tmp_path / "c.json"))
    n = Notice(source="SH", external_id="1", title="잔여세대", original_url="https://x/1", announcement_date=date(2026, 10, 8))
    (out,) = enrich_dates_from_detail([n], s, get_html, "sh")
    assert (out.application_start_date, out.application_end_date) == (date(2026, 10, 8), date(2026, 10, 22))
    enrich_dates_from_detail([n], s, get_html, "sh")
    assert len(calls) == 1  # second run served from cache


def test_detail_cache_retries_misses_later(tmp_path):
    c = DetailCache(str(tmp_path / "c.json"), ttl_miss_hours=0)
    c.get("k", lambda: {"start": None, "end": None})
    c.get("k", lambda: {"start": None, "end": None})
    assert c.fetches == 2


def test_start_alert_not_shadowed_by_correction_status():
    n = Notice(source="마이홈", external_id="1", title="든든전세", status="정정공고 예정",
               application_start_date=TODAY, application_end_date=TODAY + timedelta(days=2))
    assert alert_reasons(n, is_new=False, changed=False) == ["정정/변경 확인", "접수 시작 임박"]
    assert alert_reasons(n, is_new=True, changed=False) == ["새 공고", "접수 시작 임박"]


def test_parse_period_sh_multi_round_and_ignores_document_submission():
    sh = "■ 청약신청 일정 ( 인터넷 및 방문 ) ○ [1 순위 ] 2026. 9. 14.( 월 ) 10:00 ~ 2026. 9. 15.( 화 ) 17:00 ○ [3·4 순위 ] 2026. 9. 17.( 목 ) 10:00 ~ 17:00 ■ 당첨자"
    assert parse_period(sh) == (date(2026, 9, 14), date(2026, 9, 17))
    lh = "공급일정 접수기간 : 서류제출대상자 발표일 : 2026.10.20 서류접수기간 : 2026.10.21 ~ 2026.10.23"
    assert parse_period(lh) == (None, None)


def test_lh_detail_start_after_end_is_ignored():
    n = Notice(source="LH", external_id="1", title="x", application_end_date=date(2026, 10, 16))
    assert apply_detail(n, {"start": "2026-10-21", "end": None}).application_start_date is None
