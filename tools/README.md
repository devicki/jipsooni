# tools

`build_adjacency.py` regenerates `jipsooni/data/adjacency_kr.json` (전국 시군구 인접표).

    curl -LO https://raw.githubusercontent.com/vuski/admdongkor/master/ver20260701/HangJeongDong_ver20260701.geojson
    pip install shapely
    python tools/build_adjacency.py HangJeongDong_ver20260701.geojson

Source: 통계청 SGIS 행정동 경계 via vuski/admdongkor (공공누리 제1유형 / CC BY 4.0).
Attribution required: "출처: 통계청 SGIS, github.com/vuski/admdongkor".
