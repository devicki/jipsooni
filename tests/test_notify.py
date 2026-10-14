from __future__ import annotations

from datetime import date, timedelta

from jipsooni.models import Notice, NoticeFile
from jipsooni.notify import classify_alert_reason, format_notice_message


def test_new_notice_alert_comes_before_deadline_reason() -> None:
    notice = Notice(
        source="LH",
        external_id="1",
        title="테스트 행복주택 공고",
        application_end_date=date.today() + timedelta(days=1),
    )

    assert classify_alert_reason(notice, is_new=True, changed=False) == "새 공고"


def test_deadline_alert_for_existing_notice() -> None:
    notice = Notice(
        source="LH",
        external_id="1",
        title="테스트 행복주택 공고",
        application_end_date=date.today() + timedelta(days=1),
    )

    assert classify_alert_reason(notice, is_new=False, changed=False) == "마감 임박"


def test_format_notice_message_uses_required_shape() -> None:
    notice = Notice(
        source="마이홈",
        external_id="a",
        title="서울 청년매입임대주택 입주자 모집공고",
        region="서울특별시",
        housing_type="청년매입임대",
        target_group="청년",
        area_text="단지 테스트 / 공급 10호",
        original_url="https://example.test/notice",
        files=(NoticeFile(label="공고문.pdf", url="https://example.test/file.pdf"),),
    )

    message = format_notice_message(notice, "새 공고")

    assert "[마이홈] 서울 청년매입임대주택 입주자 모집공고" in message
    assert "지역: 서울특별시" in message
    assert "유형/대상: 청년매입임대 / 청년" in message
    assert "상세: 단지 테스트 / 공급 10호" in message
    assert "첨부: 공고문.pdf https://example.test/file.pdf" in message
    assert "링크: https://example.test/notice" in message
