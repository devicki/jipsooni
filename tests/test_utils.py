from __future__ import annotations

from datetime import date

from jipsooni.utils import extract_items, parse_date, status_from_dates


def test_parse_date_handles_common_korean_api_formats() -> None:
    assert parse_date("2026.07.06") == date(2026, 7, 6)
    assert parse_date("20260706") == date(2026, 7, 6)
    assert parse_date("2026년 7월 6일") == date(2026, 7, 6)


def test_extract_items_handles_nested_data_go_kr_shapes() -> None:
    payload = {"response": {"body": {"items": [{"a": 1}, {"a": 2}]}}}

    assert extract_items(payload) == [{"a": 1}, {"a": 2}]


def test_extract_items_handles_lh_list_shape() -> None:
    payload = [{"dsSch": [{"PAGE": "1"}]}, {"dsList": [{"PAN_ID": "1"}]}]

    assert extract_items(payload) == [{"PAN_ID": "1"}]


def test_status_from_dates_marks_upcoming() -> None:
    assert status_from_dates(None, date(2999, 1, 1), None) == "예정"
