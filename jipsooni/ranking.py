"""Prefs-driven port of jipsooni ranking. Keyword lists and score weights are the original;
everything that described the original user now comes from prefs (see prefs.py)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from jipsooni.prefs import DEFAULT_PREFS, exclude_child_required

SIDO_ALIASES = {
    "서울": ("서울",), "경기": ("경기",), "인천": ("인천",), "부산": ("부산",), "대구": ("대구",),
    "광주": ("광주광역",  "광주시 "), "대전": ("대전",), "울산": ("울산",), "세종": ("세종",),
    "강원": ("강원",), "충북": ("충북", "충청북도"), "충남": ("충남", "충청남도"), "전북": ("전북", "전라북도"),
    "전남": ("전남", "전라남도"), "경북": ("경북", "경상북도"), "경남": ("경남", "경상남도"), "제주": ("제주",),
}
OWNERSHIP_KEYWORDS = ("공공분양", "분양", "매매", "사전청약", "주택분양")
RENTAL_KEYWORDS = ("임대", "전세", "든든전세", "행복주택", "국민임대", "매입임대")
HIGH_INTENT_MOVE_IN_KEYWORDS = (
    "신혼",
    "전세임대",
    "장기전세",
    "든든전세",
    "든든주택",
    "신혼희망타운",
)
RELAXED_CONDITION_KEYWORDS = (
    "소득·자산 무관",
    "소득 자산 무관",
    "소득 및 자산요건 불문",
    "소득 및 자산요건 충족여부를 불문",
    "소득·자산 기준 없음",
    "자격완화",
    "자격 완화",
    "무순위",
    "잔여세대",
    "미계약",
    "추가모집",
    "추가 모집",
    "선착순",
    "임의공급",
    "계약취소주택",
    "불법행위 재공급",
)
MYHOME_SOURCE_NAMES = ("마이홈", "MyHome")
CHILD_OR_NEWBORN_PRIORITY_KEYWORDS = (
    "신생아",
    "출산",
    "임신",
    "육아 협동조합",
    "육아협동조합",
    "만 7세 미만",
    "자녀를 둔",
    "양육 자녀",
)
SMALL_UNIT_KEYWORDS = (
    "대학생",
    "원룸",
    "공공원룸",
    "희망하우징",
    "공공기숙사",
    "기숙사",
    "도시형생활주택",
    "협동조합주택",
    "청년 협동조합",
    "청년협동조합",
    "두레주택",
)
LOW_VALUE_NOTICE_KEYWORDS = (
    "당첨자",
    "입주대상자 발표",
    "대상자 발표",
    "예비입주자 발표",
    "서류심사",
    "계약안내",
    "계약 안내",
    "선정결과",
    "결과 발표",
    "경쟁률",
)




@dataclass(frozen=True)
class RankedAlert:
    alert_id: int
    notice_id: int
    group_key: str
    category: str
    score: int
    reasons: tuple[str, ...]
    message: str


def _sido_terms(prefs) -> tuple[str, ...]:
    out: list[str] = []
    for s in prefs["regions"].get("sido") or []:
        out.extend(SIDO_ALIASES.get(s, (s,)))
    return tuple(out)


def _tier(text: str, prefs) -> int:
    """1 = 최우선 시군구/동, 2 = 2순위 시군구/동, 0 = none. '...구' names are ambiguous across
    cities (서울/부산 강서구), so they also need a preferred-sido match; 시/동 names don't
    (e.g. '매입임대 모집(인천,부천)' still hits 부천)."""
    r = prefs["regions"]
    sido = _sido_terms(prefs)
    sido_ok = not sido or _contains_any(text, sido)

    def hit(terms) -> bool:
        return any(t and t in text and (sido_ok or not t.endswith("구")) for t in terms)

    if hit(r.get("sigungu") or ()) or hit(r.get("neighborhoods") or ()):
        return 1
    if hit(r.get("secondary_sigungu") or ()) or hit(r.get("secondary_neighborhoods") or ()):
        return 2
    return 0


def _has_tiers(prefs) -> bool:
    r = prefs["regions"]
    return any(r.get(k) for k in ("sigungu", "neighborhoods", "secondary_sigungu", "secondary_neighborhoods"))


def is_newlywed(prefs) -> bool:
    h = prefs["household"]
    return h.get("type") == "newlywed" or bool(h.get("prospective_newlywed"))


def rank_alert(row: dict[str, Any], prefs: dict | None = None, today: date | None = None) -> RankedAlert:
    prefs = prefs or DEFAULT_PREFS
    today = today or date.today()
    score = 0
    reasons: list[str] = []
    text = _combined_text(row)
    sigungu = tuple(prefs["regions"].get("sigungu") or ())
    hoods = tuple(prefs["regions"].get("neighborhoods") or ())
    sido = _sido_terms(prefs)

    end = row.get("application_end_date")
    if isinstance(end, date):
        days_left = (end - today).days
        if days_left == 0:
            score += 120; reasons.append("오늘 마감")
        elif days_left == 1:
            score += 100; reasons.append("내일 마감")
        elif 2 <= days_left <= 3:
            score += 60; reasons.append("3일 이내 마감")
        elif days_left < 0:
            score -= 200; reasons.append("이미 마감 가능성")

    ann = row.get("announcement_date")
    if isinstance(ann, date) and ann == today:
        score += 70; reasons.append("오늘 올라온 공고")
    elif isinstance(ann, date) and (today - ann).days > 180:
        score -= 80; reasons.append("오래된 공고")

    reason = str(row.get("reason") or "")
    if "마감" in reason:
        score += 35
    if "새 공고" in reason:
        score += 15
    if "정정" in reason or "변경" in reason:
        score += 25; reasons.append("정정/변경")

    title = str(row.get("title") or "")
    if _contains_any(title, LOW_VALUE_NOTICE_KEYWORDS):
        score -= 180; reasons.append("후속/결과 공지 가능성")

    # regions: tier1 sigungu/neighborhood (+70), tier2 secondary (+40), else sido (+35 first listed, +15 others)
    tier = _tier(text, prefs)
    if tier == 1:
        score += 70; reasons.append("최우선 지역")
    elif tier == 2:
        score += 40; reasons.append("2순위 지역")
    else:
        matched = [s for s in (prefs["regions"].get("sido") or []) if _contains_any(text, SIDO_ALIASES.get(s, (s,)))]
        if matched:
            first = (prefs["regions"]["sido"] or [None])[0]
            score += 35 if matched[0] == first else 15
            reasons.append(matched[0])
        elif "든든전세" in text and row.get("source") == "HUG":
            reasons.append("지역 확인 필요")
        elif not sido:
            reasons.append("지역 무관")
        else:
            score -= 80; reasons.append("선호 지역 외")

    if "든든전세" in text:
        score += 60; reasons.append("든든전세")
        if row.get("source") == "HUG":
            score += 40; reasons.append("HUG 전용 소스")
    elif "전세" in text:
        score += 35; reasons.append("전세")

    if _contains_any(text, RENTAL_KEYWORDS) and (_contains_any(text, sido[:1]) or (sigungu and _contains_any(text, sigungu))):
        score += 25; reasons.append("선호지역 임대")

    if _contains_any(text, RELAXED_CONDITION_KEYWORDS):
        score += 120; reasons.append("조건완화/무순위 후보")

    if is_newlywed(prefs) and "신혼" in text:
        score += 40; reasons.append("신혼 대상")

    dcap = prefs["budget"].get("deposit_max_krw")
    dep = _parse_deposit_krw(row.get("cost_text"))
    if dcap and dep is not None and _category(text) == "move_in":
        if dep <= Decimal(dcap):
            score += 20; reasons.append(f"보증금 {_eok(dcap)} 이내")
        else:
            score -= 80; reasons.append(f"보증금 {_eok(dcap)} 초과")

    inc = tuple(prefs["housing_types"].get("include") or ())
    hit = [k for k in inc if k in text]
    if hit:
        score += 40; reasons.append("관심 유형 " + "/".join(hit[:2]))

    if _contains_any(text, OWNERSHIP_KEYWORDS):
        score += 15
        cap = prefs["budget"].get("purchase_max_krw")
        price = _parse_price_krw(row.get("cost_text"))
        if price is None or not cap:
            reasons.append("분양/매매 비용 확인 필요")
        elif price <= Decimal(cap):
            score += 60; reasons.append(f"예산 {_eok(cap)} 이내")
        else:
            score -= 80; reasons.append(f"예산 {_eok(cap)} 초과")

    if not reasons:
        reasons.append("조건 확인 필요")
    return RankedAlert(int(row["id"]), int(row["notice_id"]), _group_key(row), _category(text), score,
                       tuple(reasons), str(row["message"]))


def _eok(v) -> str:
    return f"{Decimal(v) / Decimal(100000000):g}억"


def _combined_text(row: dict[str, Any]) -> str:
    text = " ".join(str(row.get(k) or "") for k in ("title", "region", "housing_type", "target_group", "cost_text", "message"))
    # port: '비분양전환형 든든전세' is rental; don't let '분양' make it look like ownership.
    return text.replace("비분양", "비-")


def is_excluded_for_user_profile(row: dict[str, Any], prefs: dict | None = None) -> bool:
    prefs = prefs or DEFAULT_PREFS
    text = _combined_text(row)
    if exclude_child_required(prefs) and _contains_any(text, CHILD_OR_NEWBORN_PRIORITY_KEYWORDS):
        return True
    ex = tuple(prefs["housing_types"].get("exclude") or ())
    return bool(ex) and _contains_any(text, ex)


def is_small_unit(row: dict[str, Any]) -> bool:
    return _contains_any(_combined_text(row), SMALL_UNIT_KEYWORDS)


def in_preferred_regions(row: dict[str, Any], prefs: dict | None = None) -> bool:
    prefs = prefs or DEFAULT_PREFS
    r = prefs["regions"]
    terms = _sido_terms(prefs) + tuple(x for k in ("sigungu", "neighborhoods", "secondary_sigungu", "secondary_neighborhoods") for x in (r.get(k) or ()))
    return not terms or _contains_any(_combined_text(row), terms)


def is_preferred_move_in_row(row: dict[str, Any], prefs: dict | None = None) -> bool:
    prefs = prefs or DEFAULT_PREFS
    text = _combined_text(row)
    if _category(text) != "move_in":
        return True
    if is_excluded_for_user_profile(row, prefs):
        return False
    if _tier(text, prefs):
        return True
    if row.get("source") == "HUG" and "든든전세" in text:
        return True
    inc = tuple(prefs["housing_types"].get("include") or ()) or HIGH_INTENT_MOVE_IN_KEYWORDS
    if in_preferred_regions(row, prefs) and _contains_any(text, inc):
        return True
    # original only kept tier-1 districts; with no sigungu set, fall back to sido match
    return not _has_tiers(prefs) and in_preferred_regions(row, prefs)


def _group_key(row: dict[str, Any]) -> str:
    """Cross-source key: LH and 마이홈 publish the same notice with slightly different titles."""
    return f"{normalize_title(str(row.get('title') or ''))}:{row.get('application_end_date')}"


def normalize_title(title: str) -> str:
    t = re.sub(r"^\s*(\[[^\]]*\]|\([^)]*(수정|정정)[^)]*\)|(수정|정정)\s*[:\-]?)\s*", "", title)
    t = re.sub(r"^\s*(\[[^\]]*\]|\([^)]*(수정|정정)[^)]*\))\s*", "", t)
    t = re.sub(r"[\(\[][^\)\]]*(공고|\d{2}\.\s*\d{1,2}\.)[^\)\]]*[\)\]]", "", t)  # trailing '(26.10.07. 공고)'
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t)


def _contains_any(text: str, needles) -> bool:
    return any(n and n in text for n in needles)


def _category(text: str) -> str:
    if _contains_any(text, RELAXED_CONDITION_KEYWORDS):
        return "relaxed"
    if _contains_any(text, OWNERSHIP_KEYWORDS) and _contains_any(text, RENTAL_KEYWORDS):
        return "move_in"
    if _contains_any(text, OWNERSHIP_KEYWORDS):
        return "ownership"
    return "move_in"


def _parse_price_krw(value: Any) -> Decimal | None:
    if not value:
        return None
    text = str(value).replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*억", text)
    if m:
        return Decimal(m.group(1)) * Decimal("100000000")
    m = re.search(r"(\d+)\s*만원", text)
    if m:
        return Decimal(m.group(1)) * Decimal("10000")
    return None


def _parse_deposit_krw(value: Any) -> Decimal | None:
    """'임대보증금 116,120,000원' / '보증금 1.5억' -> KRW."""
    if not value:
        return None
    m = re.search(r"보증금\s*([\d,\.]+)\s*(억|만원|원)", str(value))
    if not m:
        return None
    n = Decimal(m.group(1).replace(",", ""))
    return n * {"억": Decimal(100000000), "만원": Decimal(10000), "원": Decimal(1)}[m.group(2)]
