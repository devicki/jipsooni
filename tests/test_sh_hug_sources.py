from __future__ import annotations

from datetime import date

from jipsooni.config import Settings
from jipsooni.sources.hug import HugNoticeSource
from jipsooni.sources.sh import ShNoticeSource, _is_recruitment_notice


def _settings() -> Settings:
    return Settings(
        db_path=":memory:", prefs_path="",
        data_go_kr_service_key="key",
        lh_notice_endpoint="https://example.test/lh",
        lh_lookback_days=1,
        lh_lookahead_days=1,
        myhome_notice_endpoint="https://example.test/myhome",
        myhome_notice_page_url="https://example.test/myhome-page",
        applyhome_service_key="key",
        applyhome_remndr_endpoint="https://example.test/applyhome-remndr",
        sh_notice_url="https://housing.seoul.go.kr/site/main/sh/publicLease/list",
        hug_notice_url="https://www.khug.or.kr/jeonse/web/s07/s070301.jsp",
        log_level="INFO",
    )


def test_sh_parse_notice_table() -> None:
    html = """
    <div class="board-list"><table><tbody>
      <tr>
        <td class="td1">1</td>
        <td class="td3">매입임대주택</td>
        <td class="txl td-m">2026년 1차 청년 매입임대주택 입주자 모집공고</td>
        <td class="td4">2026-06-26</td>
        <td class="td-mdisn">2026-11-20</td>
        <td class="td-mdisn">모집중</td>
        <td class="td-mdisn">매입주택공급부</td>
        <td class="td5"><a href="https://www.i-sh.co.kr/view.do?seq=306218">바로가기</a></td>
      </tr>
    </tbody></table></div>
    """
    notices = ShNoticeSource(_settings())._parse(html, "https://housing.seoul.go.kr")

    assert len(notices) == 1
    assert notices[0].source == "SH"
    assert notices[0].region == "서울특별시"
    assert notices[0].housing_type == "매입임대주택"
    assert notices[0].target_group == "청년"
    assert notices[0].announcement_date == date(2026, 6, 26)


def test_sh_skips_result_announcements() -> None:
    html = """
    <div class="board-list"><table><tbody>
      <tr>
        <td class="td1">1</td>
        <td class="td3">장기전세주택</td>
        <td class="txl td-m">제48차 장기전세주택 입주자 모집공고 당첨자 및 예비입주자 발표</td>
        <td class="td4">2026-06-26</td>
        <td class="td-mdisn">2026-11-20</td>
        <td class="td-mdisn">발표</td>
        <td class="td-mdisn">공급부</td>
        <td class="td5"><a href="https://www.i-sh.co.kr/view.do?seq=306219">바로가기</a></td>
      </tr>
    </tbody></table></div>
    """

    notices = ShNoticeSource(_settings())._parse(html, "https://housing.seoul.go.kr")

    assert notices == []


def test_sh_maps_preferred_neighborhood_to_district() -> None:
    html = """
    <div class="board-list"><table><tbody>
      <tr>
        <td class="td1">1</td>
        <td class="td3">도시형생활주택</td>
        <td class="txl td-m">2026년 화곡동 청년 협동조합주택 잔여세대 입주자 모집공고</td>
        <td class="td4">2026-06-24</td>
        <td class="td-mdisn"></td>
        <td class="td-mdisn">모집중</td>
        <td class="td-mdisn">공급부</td>
        <td class="td5"><a href="https://www.i-sh.co.kr/view.do?seq=306065">바로가기</a></td>
      </tr>
    </tbody></table></div>
    """

    notices = ShNoticeSource(_settings())._parse(html, "https://housing.seoul.go.kr")

    assert notices[0].region == "서울특별시 강서구"


def test_hug_parse_notice_board() -> None:
    html = """
    <table><tbody>
      <tr>
        <td class="subject">
          <a href="/jeonse/web/s07/s070301.jsp?mode=S&amp;currentPage=1&amp;articleId=37680">
            든든전세주택 제10차 입주자 모집공고 사전 안내
          </a>
        </td>
        <td class="data">2026-05-22</td>
      </tr>
    </tbody></table>
    """
    source = HugNoticeSource(_settings())
    source._fetch_files = lambda detail_url: []  # type: ignore[method-assign]
    notices = source._parse(html, "https://www.khug.or.kr/jeonse/web/s07/s070301.jsp")

    assert len(notices) == 1
    assert notices[0].source == "HUG"
    assert notices[0].housing_type == "든든전세"
    assert notices[0].external_id == "37680"
    assert notices[0].announcement_date == date(2026, 5, 22)


def test_sh_recruitment_filter_handles_empty_status() -> None:
    # 상태 칸이 비면 clean_text가 None을 준다. 예전엔 여기서 SH 수집 전체가 죽었다.
    assert _is_recruitment_notice("행복주택 입주자모집공고", None) is True
    assert _is_recruitment_notice("2026년 임대주택 안내", None) is False
