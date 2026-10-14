from datetime import date
from jipsooni.prefs import DEFAULT_PREFS, _merge
from jipsooni.ranking import rank_alert, is_preferred_move_in_row

P = _merge(DEFAULT_PREFS, {"regions": {"sido": ["서울", "경기"], "sigungu": ["강서구"], "neighborhoods": ["마곡"],
       "secondary_sigungu": ["금천구", "김포"], "secondary_neighborhoods": ["개봉"]},
       "household": {"type": "newlywed"}, "budget": {"deposit_max_krw": 350000000}})

def row(title, region, cost=None):
    return {"id": 1, "notice_id": 1, "title": title, "region": region, "message": "", "reason": "새 공고",
            "cost_text": cost, "application_end_date": None, "announcement_date": None}

def test_tier1_beats_tier2_beats_sido():
    t1 = rank_alert(row("행복주택 모집", "서울특별시 강서구"), P, date(2026, 10, 14)).score
    t2 = rank_alert(row("행복주택 모집", "서울특별시 금천구"), P, date(2026, 10, 14)).score
    t0 = rank_alert(row("행복주택 모집", "서울특별시 노원구"), P, date(2026, 10, 14)).score
    assert t1 > t2 > t0

def test_busan_gangseo_is_not_tier1():
    r = rank_alert(row("행복주택 모집", "부산광역시 강서구"), P, date(2026, 10, 14))
    assert "최우선 지역" not in r.reasons

def test_secondary_passes_move_in_filter():
    assert is_preferred_move_in_row(row("매입임대 모집", "경기도 김포시"), P)
    assert not is_preferred_move_in_row(row("매입임대 모집", "경기도 연천군"), P)

def test_newlywed_boost_and_deposit_cap():
    ok = rank_alert(row("신혼희망타운 행복주택", "경기도 하남시", "임대보증금 116,120,000원"), P, date(2026, 10, 14))
    over = rank_alert(row("행복주택", "경기도 하남시", "임대보증금 400,000,000원"), P, date(2026, 10, 14))
    assert "신혼 대상" in ok.reasons and "보증금 3.5억 이내" in ok.reasons
    assert "보증금 3.5억 초과" in over.reasons
