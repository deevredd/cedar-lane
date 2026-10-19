from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo
from typing import List, Optional, Sequence, Tuple

from app import business

SLOT_STEP_MIN = 30

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


def parse_date(s: str) -> date:
    raw = (s or "").strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date format '{s}'. Use 'YYYY-MM-DD'.")


def parse_time(s: str) -> time:
    raw = (s or "").strip().lower().replace(".", "")
    try:
        return time.fromisoformat(raw)
    except ValueError:
        pass
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Invalid time format '{s}'. Use 24h 'HH:MM'.")


def parse_datetime(s: str, tz: tzinfo) -> datetime:
    raw = (s or "").strip()
    try:
        dt = datetime.fromisoformat(raw.replace(" ", "T", 1))
        return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=tz)
        except ValueError:
            continue
    raise ValueError(f"Invalid datetime format '{s}'. Use 'YYYY-MM-DD HH:MM'.")
