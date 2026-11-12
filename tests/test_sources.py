from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path

from jipsooni.config import Settings
from jipsooni.digest import build_digest
from jipsooni.prefs import DEFAULT_PREFS
from jipsooni.sources.applyhome import ApplyHomeRemndrSource
from jipsooni.sources.myhome import MyHomeNoticeSource
from jipsooni.sources.lh import LhNoticeSource

FX = Path(__file__).parent / "fixtures"


def _settings(applyhome_extra_house_manage_nos: tuple[str, ...] = ()) -> Settings:
    return Settings(
        db_path=":memory:", prefs_path="",
        data_go_kr_service_key="key",
        lh_notice_endpoint="https://example.test/lh",
        lh_lookback_days=1,
        lh_lookahead_days=1,
        myhome_notice_endpoint="",
        myhome_notice_page_url="https://example.test/myhome",
        applyhome_service_key="key",
        applyhome_remndr_endpoint="https://example.test/applyhome-remndr",
        sh_notice_url="https://example.test/sh",
        hug_notice_url="https://example.test/hug",
        log_level="INFO",
        applyhome_extra_house_manage_nos=applyhome_extra_house_manage_nos,
        lh_upper_type_codes=("06",),
    )


def test_lh_normalize_maps_core_fields() -> None:
    source = LhNoticeSource(_settings())
    notice = source._normalize(
        {
            "PAN_ID": "0001",
            "PAN_NM": "행복주택 모집공고",
            "CNP_CD_NM": "서울특별시",
            "AIS_TP_CD_NM": "행복주택",
            "PAN_SS": "공고중",
            "PAN_NT_ST_DT": "2026.07.01",
            "CLSG_DT": "2026.07.10",
            "DTL_URL": "https://example.test/detail",
        }
    )

    assert notice.source == "LH"
    assert notice.external_id == "0001"
    assert notice.title == "행복주택 모집공고"
    assert notice.region == "서울특별시"
    assert notice.application_end_date == date(2026, 7, 10)
    assert notice.original_url == "https://example.test/detail"


class _FakeResponse:
    def __init__(self, payload: object, text: str = "") -> None:
        self.status_code = 200
        self.text = text
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _FakeClient:
    def __init__(self) -> None:
        self.pages: list[str] = []

    def get(self, url: str, params: dict[str, str]) -> _FakeResponse:
        self.pages.append(params["PAGE"])
        page = int(params["PAGE"])
        start = (page - 1) * 100
        rows = [
            {
                "ALL_CNT": "101",
                "PAN_ID": f"id-{i}",
                "PAN_NM": f"공고 {i}",
                "CLSG_DT": "2099.07.10",
            }
            for i in range(start, min(start + 100, 101))
        ]
        return _FakeResponse([{"dsSch": [{"PAGE": str(page)}]}, {"dsList": rows}])


class _FakeApplyHomeClient:
    def __init__(self) -> None:
        self.params: list[dict[str, str]] = []
        self.urls: list[str] = []
        self.list_html = "<html><body><table><tbody></tbody></table></body></html>"

    def get(self, url: str, params: dict[str, str] | None = None) -> _FakeResponse:
        self.urls.append(url)
        if params is None:
            if "selectAPTRemndrLttotPblancListView" in url:
                return _FakeResponse({}, text=self.list_html)
            return _FakeResponse({}, text=_applyhome_detail_html())

        self.params.append(params)
        if params.get("cond[HSSPLY_ADRES::LIKE]") == "서울" and params.get("cond[HOUSE_SECD::EQ]") == "04":
            return _FakeResponse(
                {
                    "page": 1,
                    "perPage": 100,
                    "totalCount": 1,
                    "data": [
                        {
                            "HOUSE_MANAGE_NO": "2026910166",
                            "PBLANC_NO": "2026910166",
                            "HOUSE_NM": "대방역 여의도 더로드캐슬(4차)",
                            "HOUSE_SECD_NM": "무순위",
                            "HSSPLY_ADRES": "서울특별시 영등포구 신길동 449-11",
                            "CNTRCT_CNCLS_ENDDE": "2026-07-27",
                            "HMPG_ADRES": "https://www.example.test",
                        }
                    ],
                }
            )
        return _FakeResponse({"page": 1, "perPage": 100, "totalCount": 0, "data": []})


def test_lh_fetch_paginates_until_total_count() -> None:
    client = _FakeClient()
    source = LhNoticeSource(_settings(), client=client)  # type: ignore[arg-type]

    notices = source.fetch()

    assert len(notices) == 101
    assert notices[0].external_id == "id-0"
    assert notices[-1].external_id == "id-100"
    assert client.pages == ["1", "2"]


def test_myhome_normalize_maps_core_fields_and_costs() -> None:
    source = MyHomeNoticeSource(_settings())
    notice = source._normalize_api(
        {
            "pblancId": "20715",
            "houseSn": 1,
            "pblancNm": "수원시 수원매탄6 국민임대 예비입주자 모집공고",
            "sttusNm": "일반공고",
            "rcritPblancDe": "20260706",
            "beginDe": "20260722",
            "endDe": "20260724",
            "brtcNm": "경기도",
            "signguNm": "수원시 영통구",
            "suplyTyNm": "국민임대",
            "hsmpNm": "수원매탄6",
            "rentGtn": 20923000,
            "mtRntchrg": 123560,
            "pcUrl": "https://www.myhome.go.kr/detail",
        }
    )

    assert notice.source == "마이홈"
    assert notice.external_id == "20715-1"
    assert notice.region == "경기도 수원시 영통구"
    assert notice.housing_type == "국민임대"
    assert notice.application_end_date == date(2026, 7, 24)
    assert notice.area_text == "단지 수원매탄6"
    assert notice.cost_text == "임대보증금 20,923,000원 / 월임대료 123,560원"


def test_myhome_same_key_rows_pick_same_notice_regardless_of_api_order() -> None:
    # 실사례: 26-4차 인천본부 든든전세 21186-0이 시군구 행 3개로 와서 실행마다 가짜 정정 알림이 떴다.
    rows = [
        {"pblancId": "21186", "houseSn": 0, "pblancNm": "26-4차 인천본부 든든전세주택 입주자 모집공고", "brtcNm": sido, "signguNm": signgu}
        for sido, signgu in (("인천광역시", "서해구"), ("경기도", "부천시"), ("인천광역시", "계양구"))
    ]
    picked = set()
    for ordered in (rows, rows[::-1], rows[1:] + rows[:1]):
        source = MyHomeNoticeSource(_settings())
        source._request_api_page = lambda params, ordered=ordered: {"data": ordered}  # type: ignore[method-assign]
        notices = source._fetch_api()
        assert len(notices) == 1
        picked.add(notices[0].content_hash)

    assert len(picked) == 1


def test_applyhome_remndr_normalize_maps_core_fields() -> None:
    source = ApplyHomeRemndrSource(_settings())
    notice = source._normalize(
        {
            "HOUSE_MANAGE_NO": "2026910166",
            "PBLANC_NO": "2026910166",
            "HOUSE_NM": "대방역 여의도 더로드캐슬(4차)",
            "HOUSE_SECD_NM": "무순위",
            "HSSPLY_ADRES": "서울특별시 영등포구 신길동 449-11",
            "CNTRCT_CNCLS_BGNDE": "2026-07-24",
            "CNTRCT_CNCLS_ENDDE": "2026-07-27",
            "PRZWNER_PRESNATN_DE": "2026-07-20",
            "TOT_SUPLY_HSHLDCO": 19,
            "MVN_PREARNGE_YM": "202607",
            "BSNS_MBY_NM": "대방건설",
            "MDHS_TELNO": "1555-0807",
            "HMPG_ADRES": "https://www.example.test",
        }
    )

    assert notice.source == "청약홈"
    assert notice.external_id == "2026910166-2026910166"
    assert notice.title == "대방역 여의도 더로드캐슬(4차) 무순위 입주자모집공고"
    assert notice.region == "서울특별시 영등포구"
    assert notice.housing_type == "무순위"
    assert notice.application_end_date == date(2026, 7, 27)
    assert notice.area_text == (
        "공급세대 19세대 / 당첨자발표 2026-07-20 / 계약 2026-07-24 ~ 2026-07-27 / "
        "입주예정 2026-07 / 사업주체 대방건설 / 문의 1555-0807"
    )
    assert notice.original_url == "https://www.example.test"


def test_applyhome_fetch_filters_to_seoul_gyeonggi_incheon_remndr_types() -> None:
    client = _FakeApplyHomeClient()
    source = ApplyHomeRemndrSource(
        _settings(), client=client, today=date(2026, 7, 6)  # type: ignore[arg-type]
    )

    notices = source.fetch()

    assert len(notices) == 1
    assert notices[0].region == "서울특별시 영등포구"
    remndr = [params for params in client.params if params.get("cond[HOUSE_SECD::EQ]") in {"04", "06"}]
    assert {
        (params["cond[HSSPLY_ADRES::LIKE]"], params["cond[HOUSE_SECD::EQ]"])
        for params in remndr
    } == {
        ("서울", "04"),
        ("서울", "06"),
        ("경기", "04"),
        ("경기", "06"),
        ("인천", "04"),
        ("인천", "06"),
    }
    apt = [params for params in client.params if params.get("cond[HOUSE_SECD::EQ]") == "01"]
    assert {params["cond[HSSPLY_ADRES::LIKE]"] for params in apt} == {"서울", "경기", "인천"}
    assert {params["cond[RCRIT_PBLANC_DE::GTE]"] for params in apt} == {"2025-07-06"}
    optional = [
        params for params in client.params
        if "cond[RCRIT_PBLANC_DE::GTE]" in params and "cond[HOUSE_SECD::EQ]" not in params
    ]
    assert {params["cond[HSSPLY_ADRES::LIKE]"] for params in optional} == {"서울", "경기", "인천"}
    assert {params["cond[RCRIT_PBLANC_DE::GTE]"] for params in optional} == {"20250706"}
    assert any(url.endswith("/getAPTLttotPblancDetail") for url in client.urls)
    assert any(url.endswith("/getOPTLttotPblancDetail") for url in client.urls)


def test_applyhome_fetch_includes_extra_detail_house_manage_numbers() -> None:
    client = _FakeApplyHomeClient()
    source = ApplyHomeRemndrSource(
        _settings(applyhome_extra_house_manage_nos=("2026930023",)),
        client=client,  # type: ignore[arg-type]
        today=date(2026, 7, 6),
    )

    notices = source.fetch()

    extra = [notice for notice in notices if notice.external_id == "2026930023-2026930023"]
    assert len(extra) == 1
    assert extra[0].title == "루원시티 SK 리더스뷰 불법행위 재공급 입주자모집공고"
    assert extra[0].region == "인천광역시 서구"
    assert extra[0].housing_type == "불법행위 재공급"
    assert extra[0].application_start_date == date(2026, 7, 13)
    assert extra[0].application_end_date == date(2026, 7, 14)
    assert extra[0].announcement_date == date(2026, 7, 8)
    assert extra[0].area_text == "공급규모 9세대 / 당첨자발표 2026-07-20 / 문의 032-860-2401"
    assert extra[0].cost_text == "최고 공급금액 50,025만원"


def test_applyhome_fetch_uses_web_list_fallback_for_missing_api_rows() -> None:
    client = _FakeApplyHomeClient()
    client.list_html = """
    <html><body><table><tbody>
    <tr data-pbno="2026930023" data-hmno="2026930023" data-hsecd="06" data-honm="루원시티 SK 리더스뷰">
      <td>인천</td><td>불법행위 재공급</td><td>루원시티 SK 리더스뷰</td><td>신한자산신탁</td>
      <td>2026-07-08</td><td>2026-07-13 ~ 2026-07-14</td><td>2026-07-20</td><td>경쟁률</td>
    </tr>
    <tr data-pbno="2026910173" data-hmno="2026910173" data-hsecd="04" data-honm="힐스테이트 앞산 센트럴">
      <td>대구</td><td>무순위(사후)</td><td>힐스테이트 앞산 센트럴</td><td>사업주체</td>
      <td>2026-07-08</td><td>2026-07-13 ~ 2026-07-13</td><td>2026-07-16</td><td>경쟁률</td>
    </tr>
    </tbody></table></body></html>
    """
    source = ApplyHomeRemndrSource(
        _settings(), client=client, today=date(2026, 7, 6)  # type: ignore[arg-type]
    )

    notices = source.fetch()

    assert any(notice.external_id == "2026930023-2026930023" for notice in notices)
    assert not any(notice.external_id == "2026910173-2026910173" for notice in notices)


def _applyhome_detail_html() -> str:
    return """
    <html><body>
    입주자모집공고 주요정보 입주자모집공고 주요정보 루원시티 SK 리더스뷰
    공급위치 인천광역시 서해구 가정로 437(루원시티 SK 리더스뷰)
    공급규모 9세대
    문의처 ☎ 032-860-2401
    모집공고일 2026-07-08
    청약접수 구분 접수기간 접수장소 특별공급 2026-07-13 인터넷 일반공급 2026-07-14 인터넷
    당첨자 발표일 2026-07-20 계약일 2026-07-30 ~ 2026-07-30
    공급금액(최고가 기준) 075.8201 38,405 2026.08 084.8254A 43,740 084.6013D 42,720
    084.6419E 43,155 100.9470A 50,025
    2026930023(01)
    </body></html>
    """


class _SupplyClient:
    """Odcloud stand-in for getAPTLttotPblancDetail and getOPTLttotPblancDetail."""

    def __init__(self, list_html: str = "<html><body><table><tbody></tbody></table></body></html>") -> None:
        self.params: list[dict[str, str]] = []
        self.urls: list[str] = []
        self.list_html = list_html
        self.apt = json.loads((FX / "applyhome_apt.json").read_text(encoding="utf-8"))
        self.opt = json.loads((FX / "applyhome_opt.json").read_text(encoding="utf-8"))
        self.apt_model = json.loads((FX / "applyhome_apt_model.json").read_text(encoding="utf-8"))
        self.opt_model = json.loads((FX / "applyhome_opt_model.json").read_text(encoding="utf-8"))
        self.opt_detail = (FX / "applyhome_opt_detail_2026940247.html").read_text(encoding="utf-8")
        self.detail_overrides: dict[str, str] = {}

    def get(self, url: str, params: dict[str, str] | None = None) -> _FakeResponse:
        self.urls.append(url)
        if params is None:
            if "selectAPTRemndrLttotPblancListView" in url:
                return _FakeResponse({}, text=self.list_html)
            for house_no, html in self.detail_overrides.items():
                if house_no in url:
                    return _FakeResponse({}, text=html)
            if "2026940247" in url:
                return _FakeResponse({}, text=self.opt_detail)
            return _FakeResponse({}, text=_applyhome_detail_html())
        self.params.append(params)
        house = params.get("cond[HOUSE_MANAGE_NO::EQ]")
        if url.endswith("/getAPTLttotPblancMdl"):
            return _FakeResponse(self.apt_model if house == "2026000535" else {"data": []})
        if url.endswith("/getOPTLttotPblancMdl"):
            return _FakeResponse(self.opt_model if house == "2026940247" else {"data": []})
        if url.endswith("/getAPTLttotPblancDetail"):
            return _FakeResponse(self.apt)
        if url.endswith("/getOPTLttotPblancDetail"):
            return _FakeResponse(self.opt)
        return _FakeResponse({"page": 1, "perPage": 100, "totalCount": 0, "data": []})


def test_applyhome_ingests_regular_sale_and_optional_supply() -> None:
    client = _SupplyClient()
    source = ApplyHomeRemndrSource(_settings(), client=client, today=date(2026, 11, 12))  # type: ignore[arg-type]

    notices = {notice.external_id: notice for notice in source.fetch()}

    sale = notices["2026000535-2026000535"]
    assert sale.housing_type == "일반분양"
    assert sale.title == "중앙하이츠 구성 플랫폼시티 일반분양 입주자모집공고"
    assert sale.region == "경기도 용인시"
    assert sale.application_start_date == date(2026, 11, 9)
    assert sale.application_end_date == date(2026, 11, 12)
    assert sale.announcement_date == date(2026, 10, 29)
    assert sale.contract_date == date(2026, 11, 30)
    assert sale.cost_text == "최고 공급금액 85,210만원"
    assert sale.original_url == (
        "https://www.applyhome.co.kr/ai/aia/selectAPTLttotPblancDetail.do"
        "?houseManageNo=2026000535&pblancNo=2026000535"
    )
    assert sale.area_text and "민영" in sale.area_text and "공급세대 76세대" in sale.area_text

    optional = notices["2026940247-2026940247"]
    assert optional.housing_type == "임의공급"
    assert "임의공급" in optional.title
    assert optional.region == "경기도 수원시"
    assert optional.application_start_date == date(2026, 11, 12)
    assert optional.application_end_date == date(2026, 11, 13)
    assert optional.announcement_date == date(2026, 11, 10)
    assert optional.cost_text == "최고 공급금액 102,866만원"
    assert optional.original_url == (
        "https://www.applyhome.co.kr/ai/aia/selectAPTRemndrLttotPblancDetailView.do"
        "?houseManageNo=2026940247&pblancNo=2026940247"
    )

    iso = notices["2026940252-2026940252"]
    assert iso.housing_type == "임의공급"
    assert iso.region == "서울특별시 양천구"
    assert iso.application_end_date == date(2026, 11, 12)
    assert iso.cost_text is None

    dropped = {
        "2026000999-2026000999",  # reception already ended
        "2026000888-2026000888",  # 분양전환 가능임대
        "2026820015-2026820015",  # 신혼희망타운 (HOUSE_SECD 10)
        "2026000777-2026000777",  # 부산
        "2026940001-2026940001",
        "2026940099-2026940099",
    }
    assert dropped.isdisjoint(notices)


def test_applyhome_supply_api_error_keeps_remndr_rows() -> None:
    class _BoomApt(_FakeApplyHomeClient):
        def get(self, url: str, params: dict[str, str] | None = None) -> _FakeResponse:
            if url.endswith("/getAPTLttotPblancDetail"):
                raise RuntimeError("nope serviceKey=super-secret-value")
            return super().get(url, params)

    client = _BoomApt()
    source = ApplyHomeRemndrSource(_settings(), client=client, today=date(2026, 7, 6))  # type: ignore[arg-type]
    notices = source.fetch()

    assert any(notice.external_id == "2026910166-2026910166" for notice in notices)
    warning = " ".join(source.warnings)
    assert "regular sale skipped: RuntimeError" in warning
    assert "super-secret-value" not in warning
    assert "serviceKey" not in warning


def test_applyhome_web_list_keeps_optional_supply_and_remndr_codes() -> None:
    client = _SupplyClient(list_html="""
    <html><body><table><tbody>
    <tr data-pbno="2026930023" data-hmno="2026930023" data-hsecd="06" data-honm="루원시티 SK 리더스뷰">
      <td>인천</td><td>불법행위 재공급</td><td>루원시티 SK 리더스뷰</td><td>신한자산신탁</td>
      <td>2026-07-08</td><td>2026-07-13 ~ 2026-07-14</td><td>2026-07-20</td><td>경쟁률</td>
    </tr>
    <tr data-pbno="2026940247" data-hmno="2026940247" data-hsecd="11" data-honm="수원이목지구 대방 디에트르 더 리체">
      <td>경기</td><td>임의공급</td><td>수원이목지구 대방 디에트르 더 리체Ⅰ(A4BL)</td><td>디비이엔씨주식회사</td>
      <td>2026-11-10</td><td>2026-11-12 ~ 2026-11-13</td><td>2026-11-18</td><td>경쟁률</td>
    </tr>
    <tr data-pbno="2026940099" data-hmno="2026940099" data-hsecd="11" data-honm="해운대">
      <td>부산</td><td>임의공급</td><td>해운대 임의공급</td><td>사업주체</td>
      <td>2026-11-10</td><td>2026-11-12 ~ 2026-11-20</td><td>2026-11-25</td><td>경쟁률</td>
    </tr>
    </tbody></table></body></html>
    """)
    client.apt = {"page": 1, "perPage": 100, "totalCount": 0, "data": []}
    client.opt = {"page": 1, "perPage": 100, "totalCount": 0, "data": []}
    source = ApplyHomeRemndrSource(_settings(), client=client, today=date(2026, 7, 6))  # type: ignore[arg-type]

    notices = {notice.external_id: notice for notice in source.fetch()}

    resupply = notices["2026930023-2026930023"]
    assert resupply.housing_type == "불법행위 재공급"
    assert "불법행위 재공급" in resupply.title

    optional = notices["2026940247-2026940247"]
    assert optional.housing_type == "임의공급"
    assert optional.region == "경기도 수원시"
    assert optional.application_start_date == date(2026, 11, 12)
    assert optional.application_end_date == date(2026, 11, 13)
    assert optional.cost_text == "최고 공급금액 102,866만원"
    assert "2026940099-2026940099" not in notices


def test_applyhome_optional_web_row_falls_back_to_list_cells() -> None:
    client = _SupplyClient(list_html="""
    <html><body><table><tbody>
    <tr data-pbno="2026940251" data-hmno="2026940251" data-hsecd="11" data-honm="해링턴플레이스 노원 센트럴">
      <td>서울</td><td>임의공급</td><td>해링턴플레이스 노원 센트럴(4차)</td><td>사업주체</td>
      <td>2026-11-10</td><td>2026-11-12 ~ 2026-11-13</td><td>2026-11-18</td><td>경쟁률</td>
    </tr>
    </tbody></table></body></html>
    """)
    client.apt = {"data": []}
    client.opt = {"data": []}
    client.detail_overrides["2026940251"] = "<html><body>빈 페이지</body></html>"
    source = ApplyHomeRemndrSource(_settings(), client=client, today=date(2026, 11, 12))  # type: ignore[arg-type]

    notices = source.fetch()
    optional = next(notice for notice in notices if notice.external_id == "2026940251-2026940251")
    assert optional.housing_type == "임의공급"
    assert optional.title == "해링턴플레이스 노원 센트럴(4차) 임의공급 입주자모집공고"
    assert optional.region == "서울"
    assert optional.application_start_date == date(2026, 11, 12)
    assert optional.application_end_date == date(2026, 11, 13)
    assert optional.original_url.endswith("houseManageNo=2026940251&pblancNo=2026940251")


def _digest_row(i: int, notice, reason: str = "새 공고") -> dict:
    return {
        "id": i,
        "notice_id": i,
        "reason": reason,
        "message": notice.title,
        "source": notice.source,
        "title": notice.title,
        "region": notice.region,
        "housing_type": notice.housing_type,
        "target_group": notice.target_group,
        "application_end_date": notice.application_end_date,
        "announcement_date": notice.announcement_date,
        "cost_text": notice.cost_text,
    }


def test_regular_sale_and_optional_supply_reach_digest_without_lowering_floors() -> None:
    client = _SupplyClient()
    source = ApplyHomeRemndrSource(_settings(), client=client, today=date(2026, 11, 12))  # type: ignore[arg-type]
    found = {notice.external_id: notice for notice in source.fetch()}
    sale = found["2026000535-2026000535"]
    optional = found["2026940247-2026940247"]
    rows = [_digest_row(1, sale), _digest_row(2, optional)]
    # Far-region ownership is still dropped by the existing region rule.
    rows.append({
        "id": 3, "notice_id": 3, "reason": "새 공고", "message": "해운대 일반분양",
        "source": "청약홈", "title": "해운대 일반분양 입주자모집공고", "region": "부산광역시 해운대구",
        "housing_type": "일반분양", "application_end_date": date(2026, 11, 13),
        "announcement_date": date(2026, 11, 1), "cost_text": None,
    })
    sections, discard = build_digest(rows, deepcopy(DEFAULT_PREFS), date(2026, 11, 12))

    assert [item["title"] for item in sections["ownership"]] == [sale.title]
    assert sections["ownership"][0]["score"] >= DEFAULT_PREFS["digest"]["ownership_min_score"]
    assert sections["ownership"][0]["cost_text"] == "최고 공급금액 85,210만원"
    assert "분양/매매 비용 확인 필요" in sections["ownership"][0]["reasons"]
    assert [item["title"] for item in sections["relaxed"]] == [optional.title]
    assert sections["relaxed"][0]["score"] >= DEFAULT_PREFS["digest"]["relaxed_min_score"]
    assert 3 in discard

    priced = deepcopy(DEFAULT_PREFS)
    priced["budget"]["purchase_max_krw"] = 900_000_000
    owned, _ = build_digest([_digest_row(1, sale)], priced, date(2026, 11, 12))
    assert "예산 9억 이내" in owned["ownership"][0]["reasons"]

    # Non-urgent 일반분양 clears the floor, and the section cap still bounds the digest.
    filler = []
    for i in range(8):
        filler.append({
            "id": 10 + i, "notice_id": 10 + i, "reason": "새 공고", "message": f"단지{i} 일반분양",
            "source": "청약홈", "title": f"단지{i} 일반분양 입주자모집공고", "region": "경기도 성남시",
            "housing_type": "일반분양", "application_end_date": date(2026, 12, 20),
            "announcement_date": date(2026, 11, 1), "cost_text": None,
        })
    capped, _ = build_digest(filler, deepcopy(DEFAULT_PREFS), date(2026, 11, 12))
    assert len(capped["ownership"]) == DEFAULT_PREFS["digest"]["per_section_limit"]
    assert capped["ownership"][0]["score"] >= DEFAULT_PREFS["digest"]["ownership_min_score"]
