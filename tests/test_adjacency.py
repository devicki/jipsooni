from datetime import date

from jipsooni.digest import build_digest
from jipsooni.prefs import DEFAULT_PREFS, _merge
from jipsooni.ranking import _adjacency, adjacent_match, in_preferred_regions, rank_alert

P = _merge(DEFAULT_PREFS, {"regions": {"sido": ["서울", "경기"], "sigungu": ["강서구"],
           "secondary_sigungu": ["금천구", "부천", "김포"]}, "household": {"type": "newlywed"}})
T = date(2026, 10, 14)


def row(title, region, **kw):
    return {"id": kw.pop("id", 1), "notice_id": 1, "title": title, "region": region, "message": "",
            "reason": "새 공고", "application_end_date": date(2026, 10, 27), "announcement_date": None, **kw}


def test_adjacency_is_symmetric_and_covers_capital_area():
    adj = _adjacency()
    for a, ns in adj.items():
        for b in ns:
            assert a in adj[b], (a, b)
    assert sum(k.startswith("서울 ") for k in adj) == 25
    assert {"인천 계양구", "인천 부평구", "서울 강서구", "서울 구로구", "서울 양천구", "경기 시흥시", "경기 광명시"} <= set(adj["경기 부천시"])
    assert {"인천 서해구", "인천 계양구", "서울 강서구", "경기 고양시"} <= set(adj["경기 김포시"])


def test_incheon_gyeyang_is_adjacent_to_bucheon_gimpo_gangseo():
    r = row("인천계양 A17블록 신혼희망타운(공공분양) 입주자모집공고", "인천광역시")
    vias = adjacent_match(r["title"] + " " + r["region"], P)
    assert set(vias) == {"강서구", "부천", "김포"}
    ranked = rank_alert(r, P, T)
    assert any(x.endswith(" 인접") for x in ranked.reasons) and "선호 지역 외" not in ranked.reasons
    assert in_preferred_regions(r, P)


def test_adjacent_tier_is_below_secondary():
    sec = rank_alert(row("행복주택", "경기도 부천시"), P, T).score
    adj = rank_alert(row("행복주택", "경기도 시흥시"), P, T).score
    far = rank_alert(row("행복주택", "경기도 연천군"), P, T).score
    assert sec > adj > far


def test_namyangju_is_not_yangju_and_option_off():
    p = _merge(P, {"regions": {"secondary_sigungu": ["의정부시"]}})
    assert "의정부시" not in adjacent_match("경기도 남양주시 행복주택", _merge(p, {"regions": {"secondary_sigungu": ["동두천시"]}}))
    off = _merge(P, {"regions": {"include_adjacent": False}})
    assert adjacent_match("인천계양 공공분양 인천광역시", off) == ()


def test_ownership_section_applies_region_rules():
    rows = [row("부산 에코델타 공공분양 입주자모집", "부산광역시", id=1),
            row("인천계양 A17블록 신혼희망타운(공공분양) 입주자모집공고", "인천광역시", id=2),
            row("남양주왕숙 공공분양 입주자모집", "경기도 남양주시", id=3)]
    sections, discard = build_digest(rows, P, T)
    titles = [i["title"] for i in sections["ownership"]]
    assert 1 in discard and not any("부산" in t for t in titles)
    assert any("인천계양" in t for t in titles) and any("남양주" in t for t in titles)
