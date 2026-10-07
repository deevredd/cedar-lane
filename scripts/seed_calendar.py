"""Seed the calendar with existing bookings so conflicts/reschedules can be tested.

Thursday is set up so the spec's demo works: 3:30 PM is taken (and so is 2:30), 4:30 PM is open.
Re-running deletes previously seeded events first. Use --clear to only delete seeded events.
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import business, scheduling  # noqa: E402
from app.config import settings  # noqa: E402
from app.phone import format_phone  # noqa: E402
from app.tools import gcal, sheets  # noqa: E402

# Minimal seed: 2:30 and 3:30 booked so 3:30 is full and 4:30 is the immediate alternative
SEED = [
    ("thursday", "14:30", "interior_detail_and_wash", "Priya Shah", "+14155550113", "Tesla Model Y"),
    ("thursday", "15:30", "interior_detail_and_wash", "Mike Torres", "+14155550114", "Ford F-150"),
]


def clear_seeded():
    now = datetime.now(settings.tz)
    evs = gcal.list_events(now - timedelta(days=7), now + timedelta(days=30), privateExtendedProperty="seeded=true")
    for ev in evs:
        gcal._api().events().delete(calendarId=settings.calendar_id, eventId=ev["id"]).execute()
    print(f"deleted {len(evs)} seeded events")


def seed(with_sheet: bool):
    today = datetime.now(settings.tz).date()
    for day, hhmm, svc_key, name, phone, vehicle in SEED:
        svc = business.SERVICES[svc_key]
        d = scheduling.parse_date(day, today)
        if d == today:  # don't seed into the past; push to next week
            d += timedelta(days=7)
        start = datetime.combine(d, scheduling.parse_time(hhmm), tzinfo=settings.tz)
        end = start + timedelta(minutes=svc.duration_min)
        body = {
            "summary": f"{svc.name} – {name}",
            "description": f"Customer: {name}\nPhone: {format_phone(phone)}\nService: {svc.name} (${svc.price})\nVehicle: {vehicle}\nBooked via: phone (seed)",
            "start": {"dateTime": start.isoformat(), "timeZone": settings.shop_timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": settings.shop_timezone},
            "extendedProperties": {"private": {"phone": phone, "customer_name": name, "service": svc_key,
                                               "vehicle": vehicle, "source": "seed", "seeded": "true"}},
        }
        gcal._api().events().insert(calendarId=settings.calendar_id, body=body).execute()
        print(f"seeded {scheduling.fmt_label(start)}  {svc.name} – {name}")
        if with_sheet:
            sheets.upsert_contact(phone, name=name, vehicle=vehicle,
                                  upcoming=f"{scheduling.fmt_label(start)} ({svc.name})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", action="store_true", help="only delete seeded events")
    ap.add_argument("--no-sheet", action="store_true", help="don't add seeded customers to Contacts")
    a = ap.parse_args()
    clear_seeded()
    if not a.clear:
        seed(with_sheet=not a.no_sheet)
