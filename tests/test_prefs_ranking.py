import copy
from datetime import date

from jipsooni.db import connect, create_alert_if_needed, pending_rows, upsert_notice
from jipsooni.digest import build_digest
from jipsooni.models import Notice
from jipsooni.prefs import DEFAULT_PREFS
from jipsooni.ranking import rank_alert

T = date(2026, 10, 14)


def row(i, title, region="서울특별시 강서구", cost=None, **kw):
    return {"id": i, "notice_id": i, "reason": "새 공고", "message": title, "source": kw.get("source", "LH"),
            "title": title, "region": region, "housing_type": kw.get("housing_type"), "target_group": None,
            "application_end_date": kw.get("end", date(2026, 10, 30)), "announcement_date": T, "cost_text": cost}


def p(**over):
    x = copy.deepcopy(DEFAULT_PREFS)
    for k, v in over.items():
        x[k].update(v) if isinstance(v, dict) else x.__setitem__(k, v)
    return x


def test_example_profile_with_districts_and_budget():
    example = p(regions={"sido": ["서울", "경기", "인천"], "sigungu": ["강서구", "양천구", "구로구", "동작구", "관악구"]},
             budget={"purchase_max_krw": 800_000_000})
    r = rank_alert(row(1, "마곡 공공분양", cost="최고 7.5억"), example, T)
    assert "최우선 지역" in r.reasons and "예산 8억 이내" in r.reasons and r.category == "ownership"
    r2 = rank_alert(row(2, "공공분양", region="경기도 성남시", cost="9억"), example, T)
    assert "예산 8억 초과" in r2.reasons


def test_busan_user_regions_and_budget():
    pr = p(regions={"sido": ["부산"], "sigungu": ["해운대구"]}, budget={"purchase_max_krw": 400_000_000})
    good = rank_alert(row(1, "해운대 행복주택", region="부산광역시 해운대구"), pr, T)
    bad = rank_alert(row(2, "강서 행복주택"), pr, T)
    assert good.score > bad.score and "선호 지역 외" in bad.reasons


def test_child_exclusion_follows_household():
    rows = [row(1, "신생아 특별공급 행복주택 모집공고")]
    no_kids = p(regions={"sigungu": ["강서구"]})
    kids = p(regions={"sigungu": ["강서구"]}, household={"has_children": True})
    assert not any(build_digest(rows, no_kids, T)[0].values())
    assert build_digest(rows, kids, T)[0]["move_in"]


def test_goals_filter_sections():
    rows = [row(1, "무순위 잔여세대 서울 아파트"), row(2, "강서 행복주택 모집공고")]
    sec, _ = build_digest(rows, p(goals=["relaxed"]), T)
    assert sec["relaxed"] and not sec["move_in"]


def test_sqlite_dedupe_and_change():
    conn = connect(":memory:")
    n = Notice(source="청약홈", external_id="1", title="A 무순위", application_end_date=date(2026, 10, 30))
    assert upsert_notice(conn, n) == (True, False)
    assert create_alert_if_needed(conn, n, is_new=True, changed=False)
    assert upsert_notice(conn, n) == (False, False)
    assert create_alert_if_needed(conn, n, is_new=True, changed=False) is None  # same event_key
    n2 = Notice(source="청약홈", external_id="1", title="A 무순위", application_end_date=date(2026, 10, 31))
    assert upsert_notice(conn, n2) == (False, True)
    assert create_alert_if_needed(conn, n2, is_new=False, changed=True)
    assert len(pending_rows(conn)) == 2
