from __future__ import annotations

from datetime import date

from jipsooni.models import Notice


def should_alert_deadline(notice: Notice, today: date | None = None) -> bool:
    today = today or date.today()
    return notice.application_end_date == today or (
        notice.application_end_date is not None
        and (notice.application_end_date - today).days == 1
    )


def should_alert_start(notice: Notice, today: date | None = None) -> bool:
    today = today or date.today()
    return notice.application_start_date == today or (
        notice.application_start_date is not None
        and (notice.application_start_date - today).days == 1
    )


def alert_reasons(notice: Notice, is_new: bool, changed: bool, today: date | None = None) -> list[str]:
    """All alert reasons that apply now. The primary (classify_alert_reason) can shadow the
    date-based ones: e.g. a '정정공고' or an old notice re-listed with new dates would otherwise
    never get '접수 시작 임박'. Each reason has its own event key, so each fires once."""
    out: list[str] = []
    primary = classify_alert_reason(notice, is_new=is_new, changed=changed)
    if primary:
        out.append(primary)
    if should_alert_start(notice, today) and "접수 시작 임박" not in out:
        out.append("접수 시작 임박")
    if should_alert_deadline(notice, today) and "마감 임박" not in out:
        out.append("마감 임박")
    return out


def classify_alert_reason(notice: Notice, is_new: bool, changed: bool) -> str | None:
    status = notice.status or ""
    if is_new:
        return "새 공고"
    if "정정" in status or changed:
        return "정정/변경 확인"
    if should_alert_start(notice):
        return "접수 시작 임박"
    if should_alert_deadline(notice):
        return "마감 임박"
    return None


def format_notice_message(notice: Notice, reason: str) -> str:
    def value(text: object | None) -> str:
        return str(text) if text not in (None, "") else "확인 필요"

    start = notice.application_start_date.isoformat() if notice.application_start_date else "확인 필요"
    end = notice.application_end_date.isoformat() if notice.application_end_date else "확인 필요"
    judgement = _judgement(notice, reason)

    lines = [
        f"[{notice.source}] {notice.title}",
        f"지역: {value(notice.region)}",
        f"유형/대상: {value(notice.housing_type)} / {value(notice.target_group)}",
        f"접수: {start} ~ {end}",
    ]
    if notice.area_text:
        lines.append(f"상세: {notice.area_text}")
    if notice.cost_text:
        lines.append(f"비용: {notice.cost_text}")
    for file in notice.files[:3]:
        lines.append(f"첨부: {file.label} {file.url}")
    if len(notice.files) > 3:
        lines.append(f"첨부: 외 {len(notice.files) - 3}개")
    lines.extend(
        [
            f"판단: {judgement}",
            f"링크: {value(notice.original_url)}",
        ]
    )
    return "\n".join(lines)


def _judgement(notice: Notice, reason: str) -> str:
    if reason == "마감 임박":
        return "마감이 가까워서 바로 확인 필요"
    if reason == "접수 시작 임박":
        return "접수 시작이 가까워서 일정 확인 필요"
    if "정정" in reason:
        return "공고 내용 변경 가능성이 있어 원문 확인 필요"
    if notice.target_group:
        return f"{notice.target_group} 대상 가능성이 있어 우선 확인"
    if notice.housing_type:
        return f"{notice.housing_type} 공고라 조건 확인 필요"
    return "신규 후보라 조건 확인 필요"
