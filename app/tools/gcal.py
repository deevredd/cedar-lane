"""Google Calendar tools: availability, book, find, reschedule, cancel, notes."""
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

_local = threading.local()
_write_lock = threading.Lock()  # serialize check-then-write so two bookings can't take the same slot


def _api():
    if getattr(_local, "api", None) is None:
        _local.api = build("calendar", "v3", credentials=get_credentials(), cache_discovery=False)
    return _local.api


def now() -> datetime:
    return datetime.now(settings.tz)


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(settings.tz)


def event_bounds(ev: Dict[str, Any]) -> Tuple[datetime, datetime]:
    s, e = ev["start"], ev["end"]
    if "dateTime" in s:
        return _parse_dt(s["dateTime"]), _parse_dt(e["dateTime"])
    tz = settings.tz  # all-day event (e.g. "Closed") blocks the whole day
    return (datetime.combine(date.fromisoformat(s["date"]), time(0), tzinfo=tz),
            datetime.combine(date.fromisoformat(e["date"]), time(0), tzinfo=tz))


def list_events(start: datetime, end: datetime, **extra) -> List[Dict[str, Any]]:
    items, token = [], None
    while True:
        resp = _api().events().list(
            calendarId=settings.calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=250, pageToken=token, **extra,
        ).execute()
        items += [e for e in resp.get("items", [])
                  if e.get("status") != "cancelled" and e.get("transparency") != "transparent"]
        token = resp.get("nextPageToken")
        if not token:
            return items


def _day_busy(d: date, exclude_id: Optional[str] = None) -> List[Tuple[datetime, datetime]]:
    start = datetime.combine(d, time(0), tzinfo=settings.tz)
    return [event_bounds(e) for e in list_events(start, start + timedelta(days=1)) if e["id"] != exclude_id]


def _slot(dt: datetime) -> Dict[str, str]:
    return {"start": scheduling.slot_key(dt), "label": scheduling.fmt_label(dt)}


def _next_available(from_day: date, duration: int, days: int = 10, exclude_id: Optional[str] = None):
    for i in range(days):
        d = from_day + timedelta(days=i)
        if not scheduling.open_hours(d):
            continue
        cands = scheduling.candidate_starts(d, duration, _day_busy(d, exclude_id), now(), settings.tz)
        if cands:
            return {"date": d.isoformat(), "weekday": d.strftime("%A"), "slots": [_slot(c) for c in cands[:6]]}
    return None


def _alternatives(req: datetime, duration: int, busy, exclude_id: Optional[str] = None) -> Dict[str, Any]:
    cands = scheduling.candidate_starts(req.date(), duration, busy, now(), settings.tz)
    alts = scheduling.nearest_alternatives(req, cands)
    out: Dict[str, Any] = {"alternatives": [_slot(a) for a in alts]}
    if not alts:
        out["next_available"] = _next_available(req.date() + timedelta(days=1), duration, exclude_id=exclude_id)
    return out


# ---------------------------------------------------------------- tools

def check_availability(service: str, date: str, time: Optional[str] = None) -> Dict[str, Any]:
    svc = business.get_service(service)
    today = now().date()
    d = scheduling.parse_date(date, today)
    res: Dict[str, Any] = {"service": svc.name, "duration_minutes": svc.duration_min,
                           "date": d.isoformat(), "weekday": d.strftime("%A")}
    if d < today:
        return {**res, "available": False, "reason": "That date is in the past."}
    if not scheduling.open_hours(d):
        return {**res, "available": False, "reason": f"We're closed on {d:%A}s.",
                "next_available": _next_available(d + timedelta(days=1), svc.duration_min)}
    busy = _day_busy(d)
    if time:
        req = datetime.combine(d, scheduling.parse_time(time), tzinfo=settings.tz)
        ok, reason = scheduling.check_slot(req, svc.duration_min, busy, now())
        res.update(requested=_slot(req), available=ok)
        if not ok:
            res["reason"] = reason
            res.update(_alternatives(req, svc.duration_min, busy))
        return res
    cands = scheduling.candidate_starts(d, svc.duration_min, busy, now(), settings.tz)
    res["open_slots"] = [scheduling.slot_key(c) for c in cands]
    if not cands:
        res["next_available"] = _next_available(d + timedelta(days=1), svc.duration_min)
    return res


def book_appointment(name: str, phone: str, service: str, start: str, vehicle: Optional[str] = None,
                     notes: Optional[str] = None, channel: str = "chat") -> Dict[str, Any]:
    svc = business.get_service(service)
    e164 = normalize_phone(phone)
    dt = scheduling.parse_datetime(start, now().date(), settings.tz)
    with _write_lock:
        busy = _day_busy(dt.date())
        ok, reason = scheduling.check_slot(dt, svc.duration_min, busy, now())
        if not ok:
            return {"booked": False, "reason": reason, **_alternatives(dt, svc.duration_min, busy)}
        end = dt + timedelta(minutes=svc.duration_min)
        body = {
            "summary": f"{svc.name} – {name}",
            "description": (f"Customer: {name}\nPhone: {format_phone(e164)}\nService: {svc.name} (${svc.price})\n"
                            f"Vehicle: {vehicle or '-'}\nNotes: {notes or '-'}\nBooked via: AI receptionist ({channel})"),
            "start": {"dateTime": dt.isoformat(), "timeZone": settings.shop_timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": settings.shop_timezone},
            "extendedProperties": {"private": {"phone": e164, "customer_name": name, "service": svc.key,
                                               "vehicle": vehicle or "", "source": channel}},
        }
        ev = _api().events().insert(calendarId=settings.calendar_id, body=body).execute()
    res = {"booked": True, "event_id": ev["id"], "service": svc.name, "price": svc.price,
           "when": scheduling.fmt_label(dt), "name": name, "phone": format_phone(e164)}
    res.update(sync_contact(e164, name=name, vehicle=vehicle))
    return res


def _summarize(ev: Dict[str, Any]) -> Dict[str, Any]:
    s, e = event_bounds(ev)
    p = (ev.get("extendedProperties") or {}).get("private") or {}
    return {"event_id": ev["id"], "title": ev.get("summary", ""), "name": p.get("customer_name", ""),
            "service": p.get("service", ""), "start": scheduling.slot_key(s), "when": scheduling.fmt_label(s),
            "duration_minutes": int((e - s).total_seconds() // 60)}


def _upcoming_for(e164: str) -> List[Dict[str, Any]]:
    evs = list_events(now(), now() + timedelta(days=120), privateExtendedProperty=f"phone={e164}")
    return [_summarize(e) for e in evs]


def find_appointments(phone: str) -> Dict[str, Any]:
    e164 = normalize_phone(phone)
    appts = _upcoming_for(e164)
    return {"phone": format_phone(e164), "appointments": appts,
            **({} if appts else {"note": "No upcoming appointments found for that number."})}


def _get_event(event_id: str) -> Dict[str, Any]:
    return _api().events().get(calendarId=settings.calendar_id, eventId=event_id).execute()


def reschedule_appointment(event_id: str, new_start: str) -> Dict[str, Any]:
    ev = _get_event(event_id)
    old_s, old_e = event_bounds(ev)
    duration = int((old_e - old_s).total_seconds() // 60)
    dt = scheduling.parse_datetime(new_start, now().date(), settings.tz)
    with _write_lock:
        busy = _day_busy(dt.date(), exclude_id=event_id)
        ok, reason = scheduling.check_slot(dt, duration, busy, now())
        if not ok:
            return {"rescheduled": False, "reason": reason, **_alternatives(dt, duration, busy, exclude_id=event_id)}
        end = dt + timedelta(minutes=duration)
        _api().events().patch(calendarId=settings.calendar_id, eventId=event_id, body={
            "start": {"dateTime": dt.isoformat(), "timeZone": settings.shop_timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": settings.shop_timezone},
        }).execute()
    res = {"rescheduled": True, "event_id": event_id, "from": scheduling.fmt_label(old_s),
           "to": scheduling.fmt_label(dt), "title": ev.get("summary", "")}
    phone = ((ev.get("extendedProperties") or {}).get("private") or {}).get("phone")
    if phone:
        res.update(sync_contact(phone))
    return res


def cancel_appointment(event_id: str) -> Dict[str, Any]:
    ev = _get_event(event_id)
    s, _ = event_bounds(ev)
    _api().events().delete(calendarId=settings.calendar_id, eventId=event_id).execute()
    res = {"cancelled": True, "event_id": event_id, "was": scheduling.fmt_label(s), "title": ev.get("summary", "")}
    phone = ((ev.get("extendedProperties") or {}).get("private") or {}).get("phone")
    if phone:
        res.update(sync_contact(phone))
    return res


def add_note_to_appointment(event_id: str, note: str) -> Dict[str, Any]:
    ev = _get_event(event_id)
    stamp = now().strftime("%b %d %I:%M %p")
    desc = (ev.get("description") or "") + f"\n[{stamp}] {note}"
    _api().events().patch(calendarId=settings.calendar_id, eventId=event_id, body={"description": desc}).execute()
    return {"noted": True, "event_id": event_id, "note": note}


def sync_contact(e164: str, name: Optional[str] = None, vehicle: Optional[str] = None) -> Dict[str, Any]:
    """Keep the Contacts row's upcoming_appointment in sync with the Calendar. Never fails the booking."""
    try:
        upcoming = "; ".join(f"{a['when']} ({a['title'].split(' – ')[0]})" for a in _upcoming_for(e164))
        sheets.upsert_contact(e164, name=name, vehicle=vehicle, upcoming=upcoming)
        return {}
    except Exception as exc:  # noqa: BLE001
        log.exception("Contact sync failed")
        return {"warning": f"Contact sheet update failed: {exc}"}
