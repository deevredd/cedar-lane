from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, tzinfo
from typing import List, Optional, Sequence, Tuple

from app import business

SLOT_STEP_MIN = 30
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

BusyTimes = Sequence[Tuple[datetime, datetime]]


def open_hours(d: date) -> Optional[Tuple[time, time]]:
    return business.HOURS.get(d.weekday())


def overlaps(start_1: datetime, end_1: datetime, start_2: datetime, end_2: datetime) -> bool:
    return start_1 < end_2 and start_2 < end_1


def fmt_time(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    am_pm = "AM" if dt.hour < 12 else "PM"
    return f"{hour}:{dt.minute:02d} {am_pm}"


def fmt_label(dt: datetime) -> str:
    return f"{dt:%A} {dt:%b} {dt.day}, {fmt_time(dt)}"


def slot_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def check_slot(
    start: datetime,
    duration_min: int,
    busy: BusyTimes,
    now: datetime,
) -> Tuple[bool, str]:
    end = start + timedelta(minutes=duration_min)
    hours = open_hours(start.date())

    if hours is None:
        return False, f"We're closed on {start:%A}s."

    open_time, close_time = hours
    open_dt = datetime.combine(start.date(), open_time, tzinfo=start.tzinfo)
    close_dt = datetime.combine(start.date(), close_time, tzinfo=start.tzinfo)

    if start <= now:
        return False, "That time has already passed."

    if start < open_dt or start >= close_dt:
        return False, f"That's outside business hours ({fmt_time(open_dt)} to {fmt_time(close_dt)})."

    if end > close_dt:
        return False, f"This service takes {duration_min} minutes and would run past closing at {fmt_time(close_dt)}."

    for busy_start, busy_end in busy:
        if overlaps(start, end, busy_start, busy_end):
            return False, "That time is already booked."

    return True, ""


def candidate_starts(
    d: date,
    duration_min: int,
    busy: BusyTimes,
    now: datetime,
    tz: tzinfo,
) -> List[datetime]:
    hours = open_hours(d)
    if not hours:
        return []

    open_time, close_time = hours
    current_slot = datetime.combine(d, open_time, tzinfo=tz)
    closing_time = datetime.combine(d, close_time, tzinfo=tz)

    available_slots: List[datetime] = []

    while current_slot + timedelta(minutes=duration_min) <= closing_time:
        is_open, _ = check_slot(current_slot, duration_min, busy, now)
        if is_open:
            available_slots.append(current_slot)
        current_slot += timedelta(minutes=SLOT_STEP_MIN)

    return available_slots


def nearest_alternatives(
    requested: datetime,
    candidates: Sequence[datetime],
    n: int = 3,
) -> List[datetime]:
    def distance_from_requested(candidate: datetime):
        time_difference_seconds = abs((candidate - requested).total_seconds())
        is_earlier_penalty = 0 if candidate > requested else 1
        return (time_difference_seconds, is_earlier_penalty)

    sorted_candidates = sorted(candidates, key=distance_from_requested)
    return sorted_candidates[:n]


_TIME_REGEX = re.compile(r"^(\d{1,2})(?::?(\d{2}))?(?::\d{2})?\s*(am|pm|a|p)?$")


def parse_time(s: str) -> time:
    clean = (s or "").strip().lower().replace(".", "").replace("o'clock", "").strip()

    if clean in ("noon", "midday"):
        return time(12, 0)

    match = _TIME_REGEX.match(clean)
    if not match:
        raise ValueError(f"Couldn't understand the time '{s}'.")

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    am_pm = match.group(3)

    if am_pm and am_pm.startswith("p") and hour < 12:
        hour += 12
    elif am_pm and am_pm.startswith("a") and hour == 12:
        hour = 0
    elif not am_pm and 1 <= hour <= 7:
        hour += 12

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Couldn't understand the time '{s}'.")

    return time(hour, minute)


def parse_date(s: str, today: date) -> date:
    text = (s or "").strip().lower().rstrip(".,")

    if text in ("today", "tonight"):
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text == "day after tomorrow":
        return today + timedelta(days=2)

    iso_match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if iso_match:
        return date.fromisoformat(iso_match.group(1))

    words = text.replace("this ", "").split()
    is_next = bool(words) and words[0] == "next"
    if is_next:
        words = words[1:]

    if len(words) == 1:
        weekday_word = words[0]
        for index, weekday_name in enumerate(WEEKDAYS):
            if weekday_word == weekday_name or (len(weekday_word) >= 3 and weekday_name.startswith(weekday_word)):
                day_offset = (index - today.weekday()) % 7
                if day_offset == 0 and is_next:
                    day_offset = 7
                return today + timedelta(days=day_offset)

    cleaned_date = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)
    cleaned_date = re.sub(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+", "", cleaned_date)

    supported_formats = (
        "%B %d", "%b %d", "%m/%d",
        "%B %d %Y", "%b %d %Y", "%m/%d/%Y",
        "%B %d, %Y", "%b %d, %Y"
    )
    for fmt in supported_formats:
        try:
            parsed = datetime.strptime(cleaned_date, fmt).date()
        except ValueError:
            continue

        if "%Y" not in fmt:
            parsed = parsed.replace(year=today.year)
            if parsed < today:
                parsed = parsed.replace(year=today.year + 1)
        return parsed

    raise ValueError(f"Couldn't understand the date '{s}'. Use YYYY-MM-DD.")


def parse_datetime(s: str, today: date, tz: tzinfo) -> datetime:
    raw = (s or "").strip()

    try:
        dt = datetime.fromisoformat(raw.replace(" ", "T", 1))
        return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
    except ValueError:
        pass

    match = re.match(r"^(.*?)[\s,@]+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)$", raw, re.IGNORECASE)
    if not match:
        raise ValueError(f"Couldn't understand the date/time '{s}'. Use 'YYYY-MM-DD HH:MM'.")

    date_part_str = re.sub(r"\s+at$", "", match.group(1).strip(), flags=re.IGNORECASE)
    time_part_str = match.group(2)

    parsed_date = parse_date(date_part_str, today)
    parsed_time = parse_time(time_part_str)

    return datetime.combine(parsed_date, parsed_time, tzinfo=tz)
