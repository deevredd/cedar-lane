"""Pure date/time + slot logic (no Google calls, fully unit-testable)."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, tzinfo
from typing import List, Optional, Sequence, Tuple

from app import business

SLOT_STEP_MIN = 30
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
Busy = Sequence[Tuple[datetime, datetime]]


def open_hours(d: date) -> Optional[Tuple[time, time]]:
    return business.HOURS.get(d.weekday())


def overlaps(s1: datetime, e1: datetime, s2: datetime, e2: datetime) -> bool:
    return s1 < e2 and s2 < e1


def fmt_time(dt) -> str:
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def fmt_label(dt: datetime) -> str:
    return f"{dt:%A} {dt:%b} {dt.day}, {fmt_time(dt)}"


def slot_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def check_slot(start: datetime, duration_min: int, busy: Busy, now: datetime) -> Tuple[bool, str]:
    end = start + timedelta(minutes=duration_min)
    hours = open_hours(start.date())
    if hours is None:
        return False, f"We're closed on {start:%A}s."
    open_dt = datetime.combine(start.date(), hours[0], tzinfo=start.tzinfo)
    close_dt = datetime.combine(start.date(), hours[1], tzinfo=start.tzinfo)
    if start <= now:
        return False, "That time has already passed."
    if start < open_dt or start >= close_dt:
        return False, f"That's outside business hours ({fmt_time(open_dt)} to {fmt_time(close_dt)})."
    if end > close_dt:
        return False, f"This service takes {duration_min} minutes and would run past closing at {fmt_time(close_dt)}."
    if any(overlaps(start, end, bs, be) for bs, be in busy):
        return False, "That time is already booked."
    return True, ""


def candidate_starts(d: date, duration_min: int, busy: Busy, now: datetime, tz: tzinfo) -> List[datetime]:
    hours = open_hours(d)
    if not hours:
        return []
    cur = datetime.combine(d, hours[0], tzinfo=tz)
    close = datetime.combine(d, hours[1], tzinfo=tz)
    out = []
    while cur + timedelta(minutes=duration_min) <= close:
        if check_slot(cur, duration_min, busy, now)[0]:
            out.append(cur)
        cur += timedelta(minutes=SLOT_STEP_MIN)
    return out


def nearest_alternatives(requested: datetime, candidates: Sequence[datetime], n: int = 3) -> List[datetime]:
    """Closest open slots to the requested time; ties go to the later slot."""
    return sorted(
        candidates,
        key=lambda c: (abs((c - requested).total_seconds()), 0 if c > requested else 1),
    )[:n]


_TIME_RE = re.compile(r"^(\d{1,2})(?::?(\d{2}))?(?::\d{2})?\s*(am|pm|a|p)?$")


def parse_time(s: str) -> time:
    """Accepts '15:30', '3:30 pm', '3:30pm', '3pm', '1530', 'noon'. Bare 1-7 means PM (shop hours)."""
    t = (s or "").strip().lower().replace(".", "").replace("o'clock", "").strip()
    if t in ("noon", "midday"):
        return time(12, 0)
    m = _TIME_RE.match(t)
    if not m:
        raise ValueError(f"Couldn't understand the time '{s}'.")
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap and ap.startswith("p") and h < 12:
        h += 12
    elif ap and ap.startswith("a") and h == 12:
        h = 0
    elif not ap and 1 <= h <= 7:
        h += 12
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"Couldn't understand the time '{s}'.")
    return time(h, mi)


def parse_date(s: str, today: date) -> date:
    """Accepts ISO dates, 'today', 'tomorrow', weekday names ('thursday', 'next friday'), 'Oct 8', '10/8'."""
    t = (s or "").strip().lower().rstrip(".,")
    if t in ("today", "tonight"):
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    if t == "day after tomorrow":
        return today + timedelta(days=2)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", t)
    if m:
        return date.fromisoformat(m.group(1))
    words = t.replace("this ", "").split()
    is_next = bool(words) and words[0] == "next"
    if is_next:
        words = words[1:]
    if len(words) == 1:
        w = words[0]
        for i, name in enumerate(WEEKDAYS):
            if w == name or (len(w) >= 3 and name.startswith(w)):
                delta = (i - today.weekday()) % 7
                if delta == 0 and is_next:
                    delta = 7
                return today + timedelta(days=delta)
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)
    cleaned = re.sub(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+", "", cleaned)
    for fmt in ("%B %d", "%b %d", "%m/%d", "%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            parsed = datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
        if "%Y" not in fmt:
            parsed = parsed.replace(year=today.year)
            if parsed < today:
                parsed = parsed.replace(year=today.year + 1)
        return parsed
    raise ValueError(f"Couldn't understand the date '{s}'. Use YYYY-MM-DD.")


def parse_datetime(s: str, today: date, tz: tzinfo) -> datetime:
    """Accepts 'YYYY-MM-DD HH:MM', ISO, or '<date words> <time>' like 'thursday 4:30pm'."""
    raw = (s or "").strip()
    try:
        dt = datetime.fromisoformat(raw.replace(" ", "T", 1))
        return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
    except ValueError:
        pass
    m = re.match(r"^(.*?)[\s,@]+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)$", raw, re.I)
    if not m:
        raise ValueError(f"Couldn't understand the date/time '{s}'. Use 'YYYY-MM-DD HH:MM'.")
    d = parse_date(re.sub(r"\s+at$", "", m.group(1).strip(), flags=re.I), today)
    return datetime.combine(d, parse_time(m.group(2)), tzinfo=tz)
