from __future__ import annotations

from datetime import date

from jipsooni.config import Settings
from jipsooni.sources.applyhome import ApplyHomeRemndrSource
from jipsooni.sources.myhome import MyHomeNoticeSource
from jipsooni.sources.lh import LhNoticeSource


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
        if params["cond[HSSPLY_ADRES::LIKE]"] == "서울" and params["cond[HOUSE_SECD::EQ]"] == "04":
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
    assert {
        (params["cond[HSSPLY_ADRES::LIKE]"], params["cond[HOUSE_SECD::EQ]"])
        for params in client.params
    } == {
        ("서울", "04"),
        ("서울", "06"),
        ("경기", "04"),
        ("경기", "06"),
        ("인천", "04"),
        ("인천", "06"),
    }


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
