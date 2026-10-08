"""Google Calendar integrations: availability checking, booking, and appointment management.

This module communicates with the Google Calendar API:
1. 'check_availability': Queries open slots and finds nearest alternatives if full.
2. 'book_appointment': Books single-bay detailing slots with atomic locks to prevent race conditions.
3. 'find_appointments': Looks up upcoming bookings by customer's phone number.
4. 'reschedule_appointment': Validates new time and updates the calendar event.
5. 'cancel_appointment': Removes an appointment from the calendar.
6. 'add_note_to_appointment': Appends customer notes (e.g. running late).
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from googleapiclient.discovery import build

from app import business, scheduling
from app.config import settings
from app.google_auth import get_credentials
from app.phone import format_phone, normalize_phone
from app.tools import sheets

log = logging.getLogger(__name__)

# Thread-local storage for Google Calendar API service instance
_thread_local = threading.local()

# Concurrency lock: ensures two callers cannot book the exact same slot simultaneously
_booking_lock = threading.Lock()


def _get_calendar_service():
    """Return an authenticated Google Calendar API client for the current thread."""
    if getattr(_thread_local, "api", None) is None:
        credentials = get_credentials()
        _thread_local.api = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    return _thread_local.api


def now() -> datetime:
    """Return the current datetime in the shop's local timezone (Pacific Time)."""
    return datetime.now(settings.tz)


def _parse_iso_datetime(iso_string: str) -> datetime:
    """Parse ISO datetime string from Google Calendar and convert to shop timezone."""
    return datetime.fromisoformat(iso_string.replace("Z", "+00:00")).astimezone(settings.tz)


def event_bounds(event: Dict[str, Any]) -> Tuple[datetime, datetime]:
    """Extract (start_dt, end_dt) from a Google Calendar event object."""
    start_info = event["start"]
    end_info = event["end"]

    # Normal timed appointment
    if "dateTime" in start_info:
        return _parse_iso_datetime(start_info["dateTime"]), _parse_iso_datetime(end_info["dateTime"])

    # All-day event (e.g. "Shop Closed" or holiday) blocks the full 24 hours
    shop_tz = settings.tz
    start_dt = datetime.combine(date.fromisoformat(start_info["date"]), time(0), tzinfo=shop_tz)
    end_dt = datetime.combine(date.fromisoformat(end_info["date"]), time(0), tzinfo=shop_tz)
    return (start_dt, end_dt)


def list_events(start_dt: datetime, end_dt: datetime, **extra_query_params) -> List[Dict[str, Any]]:
    """Fetch calendar events within a datetime range, handling pagination automatically."""
    calendar_service = _get_calendar_service()
    all_events: List[Dict[str, Any]] = []
    page_token = None

    while True:
        response = calendar_service.events().list(
            calendarId=settings.calendar_id,
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
            pageToken=page_token,
            **extra_query_params,
        ).execute()

        for item in response.get("items", []):
            is_cancelled = item.get("status") == "cancelled"
            is_transparent = item.get("transparency") == "transparent"  # Free/available time
            if not is_cancelled and not is_transparent:
                all_events.append(item)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return all_events


def _day_busy(target_date: date, exclude_id: Optional[str] = None) -> List[Tuple[datetime, datetime]]:
    """Return list of (start, end) time ranges already booked on target_date."""
    day_start = datetime.combine(target_date, time(0), tzinfo=settings.tz)
    day_end = day_start + timedelta(days=1)
    events = list_events(day_start, day_end)
    return [event_bounds(ev) for ev in events if ev.get("id") != exclude_id]


def _format_slot_dict(dt: datetime) -> Dict[str, str]:
    """Format datetime into standard slot dict with machine key and human label."""
    return {
        "start": scheduling.slot_key(dt),
        "label": scheduling.fmt_label(dt),
    }


def _find_next_available_day(
    from_date: date,
    duration_min: int,
    lookahead_days: int = 10,
    exclude_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Scan upcoming days until an open day with valid slots is found."""
    for day_offset in range(lookahead_days):
        check_date = from_date + timedelta(days=day_offset)
        # Skip days the shop is closed
        if not scheduling.open_hours(check_date):
            continue

        busy_times = _day_busy(check_date, exclude_id=exclude_id)
        open_slots = scheduling.candidate_starts(check_date, duration_min, busy_times, now(), settings.tz)
        if open_slots:
            return {
                "date": check_date.isoformat(),
                "weekday": check_date.strftime("%A"),
                "slots": [_format_slot_dict(slot) for slot in open_slots[:6]],
            }
    return None


def _build_alternatives_dict(
    requested_dt: datetime,
    duration_min: int,
    busy_times: List[Tuple[datetime, datetime]],
    exclude_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Find nearby alternative slots on the same day, or suggest the next open day."""
    all_open_today = scheduling.candidate_starts(requested_dt.date(), duration_min, busy_times, now(), settings.tz)
    nearby_slots = scheduling.nearest_alternatives(requested_dt, all_open_today, n=3)

    result: Dict[str, Any] = {
        "alternatives": [_format_slot_dict(slot) for slot in nearby_slots]
    }

    # If the entire day is booked solid, find the next open day
    if not nearby_slots:
        next_day = requested_dt.date() + timedelta(days=1)
        result["next_available"] = _find_next_available_day(next_day, duration_min, exclude_id=exclude_id)

    return result


# -----------------------------------------------------------------------------
# Tool Handlers
# -----------------------------------------------------------------------------

def check_availability(service: str, date: str, time: Optional[str] = None) -> Dict[str, Any]:
    """Check if a service can be booked on a given date and time.

    If the requested slot is full, returns the closest open alternative slots.
    If 'time' is omitted, returns all open start times for that entire date.
    """
    service_info = business.get_service(service)
    today = now().date()
    target_date = scheduling.parse_date(date, today)

    base_result: Dict[str, Any] = {
        "service": service_info.name,
        "duration_minutes": service_info.duration_min,
        "date": target_date.isoformat(),
        "weekday": target_date.strftime("%A"),
    }

    # 1. Check if date is in the past
    if target_date < today:
        base_result["available"] = False
        base_result["reason"] = "That date is in the past."
        return base_result

    # 2. Check if shop is open on this day of the week
    if not scheduling.open_hours(target_date):
        base_result["available"] = False
        base_result["reason"] = f"We're closed on {target_date:%A}s."
        base_result["next_available"] = _find_next_available_day(
            target_date + timedelta(days=1), service_info.duration_min
        )
        return base_result

    busy_times = _day_busy(target_date)

    # 3. If a specific time was requested, check that exact slot
    if time:
        requested_time = scheduling.parse_time(time)
        requested_dt = datetime.combine(target_date, requested_time, tzinfo=settings.tz)
        is_open, rejection_reason = scheduling.check_slot(
            requested_dt, service_info.duration_min, busy_times, now()
        )

        base_result["requested"] = _format_slot_dict(requested_dt)
        base_result["available"] = is_open

        if not is_open:
            base_result["reason"] = rejection_reason
            alternatives_info = _build_alternatives_dict(requested_dt, service_info.duration_min, busy_times)
            base_result.update(alternatives_info)

        return base_result

    # 4. If no time was specified, return all open slots on that day
    open_slots = scheduling.candidate_starts(
        target_date, service_info.duration_min, busy_times, now(), settings.tz
    )
    base_result["open_slots"] = [scheduling.slot_key(slot) for slot in open_slots]

    if not open_slots:
        base_result["next_available"] = _find_next_available_day(
            target_date + timedelta(days=1), service_info.duration_min
        )

    return base_result


def book_appointment(
    name: str,
    phone: str,
    service: str,
    start: str,
    vehicle: Optional[str] = None,
    notes: Optional[str] = None,
    channel: str = "chat",
) -> Dict[str, Any]:
    """Book a new appointment on Google Calendar and sync customer into Sheets CRM."""
    service_info = business.get_service(service)
    e164_phone = normalize_phone(phone)
    start_dt = scheduling.parse_datetime(start, now().date(), settings.tz)

    with _booking_lock:
        # Atomic check-then-insert under lock to prevent double booking
        busy_times = _day_busy(start_dt.date())
        is_open, rejection_reason = scheduling.check_slot(
            start_dt, service_info.duration_min, busy_times, now()
        )

        if not is_open:
            conflict_result = {
                "booked": False,
                "reason": rejection_reason,
            }
            conflict_result.update(_build_alternatives_dict(start_dt, service_info.duration_min, busy_times))
            return conflict_result

        end_dt = start_dt + timedelta(minutes=service_info.duration_min)

        event_body = {
            "summary": f"{service_info.name} – {name}",
            "description": (
                f"Customer: {name}\n"
                f"Phone: {format_phone(e164_phone)}\n"
                f"Service: {service_info.name} (${service_info.price})\n"
                f"Vehicle: {vehicle or '-'}\n"
                f"Notes: {notes or '-'}\n"
                f"Booked via: AI receptionist ({channel})"
            ),
            "start": {"dateTime": start_dt.isoformat(), "timeZone": settings.shop_timezone},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": settings.shop_timezone},
            "extendedProperties": {
                "private": {
                    "phone": e164_phone,
                    "customer_name": name,
                    "service": service_info.key,
                    "vehicle": vehicle or "",
                    "source": channel,
                }
            },
        }

        created_event = _get_calendar_service().events().insert(
            calendarId=settings.calendar_id, body=event_body
        ).execute()

    booking_confirmation = {
        "booked": True,
        "event_id": created_event["id"],
        "service": service_info.name,
        "price": service_info.price,
        "when": scheduling.fmt_label(start_dt),
        "name": name,
        "phone": format_phone(e164_phone),
    }

    # Keep Google Sheets CRM updated with the new appointment
    booking_confirmation.update(sync_contact(e164_phone, name=name, vehicle=vehicle))
    return booking_confirmation


def _summarize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Convert raw Google Calendar event into a concise appointment summary."""
    start_dt, end_dt = event_bounds(event)
    private_props = (event.get("extendedProperties") or {}).get("private") or {}
    duration_mins = int((end_dt - start_dt).total_seconds() // 60)

    return {
        "event_id": event["id"],
        "title": event.get("summary", ""),
        "name": private_props.get("customer_name", ""),
        "service": private_props.get("service", ""),
        "start": scheduling.slot_key(start_dt),
        "when": scheduling.fmt_label(start_dt),
        "duration_minutes": duration_mins,
    }


def _upcoming_appointments_for(e164_phone: str) -> List[Dict[str, Any]]:
    """Look up all upcoming appointments matching the customer's phone number."""
    events = list_events(
        now(),
        now() + timedelta(days=120),
        privateExtendedProperty=f"phone={e164_phone}",
    )
    return [_summarize_event(ev) for ev in events]


def find_appointments(phone: str) -> Dict[str, Any]:
    """Search for existing upcoming appointments by customer phone number."""
    e164_phone = normalize_phone(phone)
    appointments = _upcoming_appointments_for(e164_phone)

    result = {
        "phone": format_phone(e164_phone),
        "appointments": appointments,
    }
    if not appointments:
        result["note"] = "No upcoming appointments found for that number."

    return result


def _fetch_event(event_id: str) -> Dict[str, Any]:
    """Fetch single event by its Google Calendar ID."""
    return _get_calendar_service().events().get(
        calendarId=settings.calendar_id, eventId=event_id
    ).execute()


def reschedule_appointment(event_id: str, new_start: str) -> Dict[str, Any]:
    """Move an existing appointment to a new date and time."""
    existing_event = _fetch_event(event_id)
    old_start, old_end = event_bounds(existing_event)
    duration_mins = int((old_end - old_start).total_seconds() // 60)

    target_dt = scheduling.parse_datetime(new_start, now().date(), settings.tz)

    with _booking_lock:
        busy_times = _day_busy(target_dt.date(), exclude_id=event_id)
        is_open, rejection_reason = scheduling.check_slot(
            target_dt, duration_mins, busy_times, now()
        )

        if not is_open:
            conflict_result = {
                "rescheduled": False,
                "reason": rejection_reason,
            }
            conflict_result.update(
                _build_alternatives_dict(target_dt, duration_mins, busy_times, exclude_id=event_id)
            )
            return conflict_result

        new_end_dt = target_dt + timedelta(minutes=duration_mins)

        _get_calendar_service().events().patch(
            calendarId=settings.calendar_id,
            eventId=event_id,
            body={
                "start": {"dateTime": target_dt.isoformat(), "timeZone": settings.shop_timezone},
                "end": {"dateTime": new_end_dt.isoformat(), "timeZone": settings.shop_timezone},
            },
        ).execute()

    reschedule_confirmation = {
        "rescheduled": True,
        "event_id": event_id,
        "from": scheduling.fmt_label(old_start),
        "to": scheduling.fmt_label(target_dt),
        "title": existing_event.get("summary", ""),
    }

    # Sync Sheets CRM with updated upcoming date
    phone = ((existing_event.get("extendedProperties") or {}).get("private") or {}).get("phone")
    if phone:
        reschedule_confirmation.update(sync_contact(phone))

    return reschedule_confirmation


def cancel_appointment(event_id: str) -> Dict[str, Any]:
    """Delete an appointment from Google Calendar."""
    existing_event = _fetch_event(event_id)
    start_dt, _ = event_bounds(existing_event)

    _get_calendar_service().events().delete(
        calendarId=settings.calendar_id, eventId=event_id
    ).execute()

    cancellation_result = {
        "cancelled": True,
        "event_id": event_id,
        "was": scheduling.fmt_label(start_dt),
        "title": existing_event.get("summary", ""),
    }

    # Sync Sheets CRM
    phone = ((existing_event.get("extendedProperties") or {}).get("private") or {}).get("phone")
    if phone:
        cancellation_result.update(sync_contact(phone))

    return cancellation_result


def add_note_to_appointment(event_id: str, note: str) -> Dict[str, Any]:
    """Append a customer note (e.g. 'Running 15 mins late') to the appointment description."""
    existing_event = _fetch_event(event_id)
    timestamp = now().strftime("%b %d %I:%M %p")
    updated_description = (existing_event.get("description") or "") + f"\n[{timestamp}] {note}"

    _get_calendar_service().events().patch(
        calendarId=settings.calendar_id,
        eventId=event_id,
        body={"description": updated_description},
    ).execute()

    return {
        "noted": True,
        "event_id": event_id,
        "note": note,
    }


def sync_contact(e164_phone: str, name: Optional[str] = None, vehicle: Optional[str] = None) -> Dict[str, Any]:
    """Update upcoming appointment in Google Sheets Contacts tab asynchronously in the background.

    Running in a background thread eliminates 3-4 seconds of blocking latency so the customer
    receives an instant booking confirmation.
    """
    def _run_background_sync():
        try:
            upcoming_appointments = _upcoming_appointments_for(e164_phone)
            formatted_appointments = "; ".join(
                f"{appt['when']} ({appt['title'].split(' – ')[0]})"
                for appt in upcoming_appointments
            )
            sheets.upsert_contact(
                e164_phone,
                name=name,
                vehicle=vehicle,
                upcoming=formatted_appointments,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Background contact sheet sync failed: %s", exc)

    threading.Thread(target=_run_background_sync, daemon=True).start()
    return {}
