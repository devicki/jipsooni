"""Build jipsooni/data/adjacency_kr.json: 시군구 adjacency for all of Korea.

Source: vuski/admdongkor HangJeongDong_ver20260701.geojson (행정동 경계, 통계청 SGIS 기반,
공공누리 제1유형 / CC BY 4.0, https://github.com/vuski/admdongkor). 행정동 polygons are dissolved
into 시군구 (일반구 such as 부천시 오정구 or 수원시 장안구 are merged into the city), then two areas
are adjacent if their boundaries come within BUFFER degrees (~50 m; covers slivers in the source).
MANUAL_LINKS adds bridge/causeway links across sea where polygons don't touch.

usage: python tools/build_adjacency.py /path/HangJeongDong_ver20260701.geojson
needs: shapely (not a runtime dependency)
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

BUFFER = 0.0005
SIDO_SHORT = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
    "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기",
    "강원특별자치도": "강원", "강원도": "강원", "충청북도": "충북", "충청남도": "충남",
    "전북특별자치도": "전북", "전라북도": "전북", "전라남도": "전남", "경상북도": "경북", "경상남도": "경남",
    "제주특별자치도": "제주",
}
# sea links by bridge/causeway that polygon contact may miss (checked against the result; kept if absent)
MANUAL_LINKS = [
    ("전남 목포시", "전남 신안군"),      # 천사대교 계열·압해대교 (목포–압해도)
    ("전남 여수시", "경남 남해군"),      # 여수–남해 해저터널 (공사 중) -> 해상 인접
    ("경남 거제시", "부산 강서구"),      # 거가대교
    ("인천 영종구", "인천 연수구"),      # 인천대교
    ("인천 영종구", "인천 서해구"),      # 영종대교
    ("인천 강화군", "경기 김포시"),      # 강화대교·초지대교
    ("인천 옹진군", "인천 영종구"),      # 영흥·신도 등 연륙/인접 해역
    ("전남 완도군", "전남 해남군"),      # 완도대교
    ("전남 진도군", "전남 해남군"),      # 진도대교
    ("경남 통영시", "경남 거제시"),      # 거제대교
    ("경남 남해군", "경남 하동군"),      # 남해대교·노량대교
    ("경남 사천시", "경남 남해군"),      # 삼천포대교
    ("충남 보령시", "충남 태안군"),      # 보령해저터널·원산안면대교
    ("경북 울릉군", "경북 울릉군"),      # placeholder (island, no neighbours)
]


GWANGJU_GU = {"광산구", "남구", "동구", "북구", "서구"}


def sido_short(sidonm: str, sggnm: str) -> str:
    # 2026-07 전남광주통합특별시: keep the old 시도 labels (notices still say 광주/전남); 5 자치구 = 광주
    if sidonm == "전남광주통합특별시":
        return "광주" if sggnm in GWANGJU_GU else "전남"
    return SIDO_SHORT[sidonm]


def city_of(sggnm: str) -> str:
    m = re.match(r"^(\S+?시)\s*(\S+구)$", sggnm)
    return m.group(1) if m else sggnm


def main(src: str) -> None:
    feats = json.loads(Path(src).read_text(encoding="utf-8"))["features"]
    parts = defaultdict(list)
    for f in feats:
        p = f["properties"]
        sgg = p.get("sggnm") or ""
        key = f"{sido_short(p['sidonm'], sgg)} {city_of(sgg)}" if sgg else sido_short(p["sidonm"], sgg)
        parts[key].append(shape(f["geometry"]).buffer(0))
    keys = sorted(parts)
    geoms = [unary_union(parts[k]).buffer(BUFFER) for k in keys]
    tree = STRtree(geoms)
    adj = defaultdict(set)
    for i, g in enumerate(geoms):
        for j in tree.query(g, predicate="intersects"):
            if j != i:
                adj[keys[i]].add(keys[int(j)])
    added = []
    for a, b in MANUAL_LINKS:
        if a != b and a in parts and b in parts and b not in adj[a]:
            adj[a].add(b); adj[b].add(a); added.append(f"{a}–{b}")
    out = {
        "_source": "vuski/admdongkor HangJeongDong_ver20260701.geojson (통계청 SGIS 행정동 경계), "
                   "공공누리 제1유형 / CC BY 4.0. 출처: 통계청 SGIS, github.com/vuski/admdongkor",
        "_method": f"행정동 -> 시군구 dissolve (일반구는 시로 병합), buffer {BUFFER}° intersects; "
                   "manual bridge links: " + ", ".join(added),
        "_generator": "tools/build_adjacency.py",
        "adjacency": {k: sorted(adj[k]) for k in keys},
    }
    dst = Path(__file__).resolve().parent.parent / "jipsooni" / "data" / "adjacency_kr.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(keys)} areas, {sum(map(len, adj.values()))//2} pairs, manual added {added} -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1])
