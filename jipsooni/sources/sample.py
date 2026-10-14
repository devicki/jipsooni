from __future__ import annotations

from datetime import date, timedelta

from jipsooni.models import Notice
from jipsooni.sources.base import NoticeSource


class SampleNoticeSource(NoticeSource):
    name = "샘플"

    def fetch(self) -> list[Notice]:
        today = date.today()
        return [
            Notice(
                source="LH",
                external_id="sample-lh-001",
                title="성남판교 행복주택 예비입주자 모집",
                region="경기 성남시",
                housing_type="행복주택",
                target_group="청년, 신혼부부",
                application_start_date=today + timedelta(days=1),
                application_end_date=today + timedelta(days=7),
                area_text="21A, 36A",
                cost_text="확인 필요",
                status="예정",
                original_url="https://apply.lh.or.kr/",
                raw={"sample": True},
            ),
            Notice(
                source="마이홈",
                external_id="sample-myhome-001",
                title="서울 청년매입임대주택 입주자 모집공고",
                region="서울특별시",
                housing_type="청년매입임대",
                target_group="청년",
                application_start_date=today - timedelta(days=2),
                application_end_date=today + timedelta(days=1),
                area_text="확인 필요",
                cost_text="확인 필요",
                status="접수중",
                original_url="https://www.myhome.go.kr/",
                raw={"sample": True},
            ),
        ]
