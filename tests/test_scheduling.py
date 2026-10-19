from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app import scheduling as s
from app.phone import format_phone, normalize_phone

TZ = ZoneInfo("America/Los_Angeles")
WED = date(2026, 10, 7)
THU = date(2026, 10, 8)
NOW = datetime(2026, 10, 7, 10, 0, tzinfo=TZ)


def at(d, hh, mm=0):
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=TZ)


THU_BUSY = [(at(THU, 9), at(THU, 12)), (at(THU, 12, 30), at(THU, 13, 30)),
            (at(THU, 14, 30), at(THU, 15, 30)), (at(THU, 15, 30), at(THU, 16, 30))]


@pytest.mark.parametrize("raw,expected", [
    ("15:30", time(15, 30)), ("3:30 pm", time(15, 30)), ("3:30pm", time(15, 30)),
    ("3pm", time(15)), ("9:00 AM", time(9)), ("16:30:00", time(16, 30)),
])
def test_parse_time(raw, expected):
    assert s.parse_time(raw) == expected


def test_parse_time_invalid():
    with pytest.raises(ValueError):
        s.parse_time("invalid-time")


@pytest.mark.parametrize("raw,expected", [
    ("2026-10-08", THU), ("2026/10/08", THU),
])
def test_parse_date(raw, expected):
    assert s.parse_date(raw) == expected


def test_parse_date_invalid():
    with pytest.raises(ValueError):
        s.parse_date("not-a-date")


def test_parse_datetime():
    assert s.parse_datetime("2026-10-08 16:30", TZ) == at(THU, 16, 30)
    assert s.parse_datetime("2026-10-08T16:30", TZ) == at(THU, 16, 30)
    assert s.parse_datetime("2026/10/08 16:30", TZ) == at(THU, 16, 30)


def test_parse_datetime_invalid():
    with pytest.raises(ValueError):
        s.parse_datetime("not-a-datetime", TZ)


def test_spec_scenario_330_full_430_offered():
    ok, reason = s.check_slot(at(THU, 15, 30), 60, THU_BUSY, NOW)
    assert not ok and "booked" in reason
    cands = s.candidate_starts(THU, 60, THU_BUSY, NOW, TZ)
    assert s.nearest_alternatives(at(THU, 15, 30), cands)[0] == at(THU, 16, 30)
    assert s.check_slot(at(THU, 16, 30), 60, THU_BUSY, NOW)[0]


def test_candidates_respect_hours_and_conflicts():
    cands = s.candidate_starts(THU, 60, THU_BUSY, NOW, TZ)
    keys = [s.slot_key(c)[-5:] for c in cands]
    assert keys == ["08:00", "13:30", "16:30", "17:00"]


def test_boundaries():
    assert s.check_slot(at(THU, 13, 30), 60, THU_BUSY, NOW)[0]
    assert not s.check_slot(at(THU, 17, 30), 60, THU_BUSY, NOW)[0]
    assert not s.check_slot(at(THU, 7, 30), 30, THU_BUSY, NOW)[0]
    assert not s.check_slot(at(date(2026, 10, 11), 10), 30, [], NOW)[0]
    assert not s.check_slot(at(WED, 9), 30, [], NOW)[0]
    assert s.candidate_starts(date(2026, 10, 11), 30, [], NOW, TZ) == []


def test_fmt():
    assert s.fmt_label(at(THU, 16, 30)) == "Thursday Oct 8, 4:30 PM"
    assert s.fmt_time(at(THU, 9)) == "9:00 AM"


@pytest.mark.parametrize("raw,expected", [
    ("415-555-0190", "+14155550190"), ("(415) 555 0190", "+14155550190"),
    ("4155550190", "+14155550190"), ("+1 415 555 0190", "+14155550190"),
    ("1-415-555-0190", "+14155550190"),
])
def test_phone(raw, expected):
    assert normalize_phone(raw) == expected
    assert format_phone(normalize_phone(raw)) == "(415) 555-0190"


def test_phone_invalid():
    with pytest.raises(ValueError):
        normalize_phone("555-0190")
