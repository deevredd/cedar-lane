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


# Seeded Thursday: 9-12, 12:30-13:30, 14:30-15:30, 15:30-16:30
THU_BUSY = [(at(THU, 9), at(THU, 12)), (at(THU, 12, 30), at(THU, 13, 30)),
            (at(THU, 14, 30), at(THU, 15, 30)), (at(THU, 15, 30), at(THU, 16, 30))]


@pytest.mark.parametrize("raw,expected", [
    ("15:30", time(15, 30)), ("3:30 pm", time(15, 30)), ("3:30pm", time(15, 30)), ("3pm", time(15)),
    ("3:30", time(15, 30)), ("10", time(10)), ("1530", time(15, 30)), ("noon", time(12)),
    ("12am", time(0)), ("9:00 AM", time(9)), ("4:30 p.m.", time(16, 30)), ("16:30:00", time(16, 30)),
])
def test_parse_time(raw, expected):
    assert s.parse_time(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("thursday", THU), ("Thursday", THU), ("thu", THU), ("next thursday", THU), ("tomorrow", THU),
    ("2026-10-08", THU), ("today", WED), ("wednesday", WED), ("next wednesday", date(2026, 10, 14)),
    ("October 8", THU), ("Oct 8th", THU), ("10/8", THU), ("Thursday, October 8", THU),
    ("monday", date(2026, 10, 12)), ("Sep 1", date(2027, 9, 1)),
])
def test_parse_date(raw, expected):
    assert s.parse_date(raw, WED) == expected


def test_parse_datetime():
    assert s.parse_datetime("2026-10-08 16:30", WED, TZ) == at(THU, 16, 30)
    assert s.parse_datetime("2026-10-08T16:30", WED, TZ) == at(THU, 16, 30)
    assert s.parse_datetime("thursday 4:30pm", WED, TZ) == at(THU, 16, 30)
    assert s.parse_datetime("Thursday at 4:30 PM", WED, TZ) == at(THU, 16, 30)


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
    assert s.check_slot(at(THU, 13, 30), 60, THU_BUSY, NOW)[0]  # back-to-back ok
    assert not s.check_slot(at(THU, 17, 30), 60, THU_BUSY, NOW)[0]  # runs past close
    assert not s.check_slot(at(THU, 7, 30), 30, THU_BUSY, NOW)[0]  # before open
    assert not s.check_slot(at(date(2026, 10, 11), 10), 30, [], NOW)[0]  # Sunday
    assert not s.check_slot(at(WED, 9), 30, [], NOW)[0]  # past
    assert s.candidate_starts(date(2026, 10, 11), 30, [], NOW, TZ) == []


def test_fmt():
    assert s.fmt_label(at(THU, 16, 30)) == "Thursday Oct 8, 4:30 PM"
    assert s.fmt_time(at(THU, 9)) == "9:00 AM"


@pytest.mark.parametrize("raw", ["415-555-0190", "(415) 555 0190", "4155550190", "+1 415 555 0190", "1-415-555-0190"])
def test_phone(raw):
    assert normalize_phone(raw) == "+14155550190"
    assert format_phone(normalize_phone(raw)) == "(415) 555-0190"


def test_phone_invalid():
    with pytest.raises(ValueError):
        normalize_phone("555-0190")
