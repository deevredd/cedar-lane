import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import business, scheduling
from app.config import settings
from app.phone import format_phone
from app.tools import gcal, sheets

SEED = [
    ("thursday", "09:00", "full_detail", "Dana Whitfield", "+14155550111", "2019 Subaru Outback"),
    ("thursday", "12:30", "interior_detail", "Chris Ono", "+14155550112", "Honda Civic"),
    ("thursday", "14:30", "interior_detail_and_wash", "Priya Shah", "+14155550113", "Tesla Model Y"),
    ("thursday", "15:30", "interior_detail_and_wash", "Mike Torres", "+14155550114", "Ford F-150"),
    ("friday", "10:00", "exterior_wash", "Jordan Lee", "+14155550123", "Mazda CX-5"),
    ("friday", "13:00", "full_detail", "Alex Kim", "+14155550124", "BMW X5"),
    ("saturday", "09:00", "interior_detail_and_wash", "Sam Rivera", "+14155550125", "Toyota Sienna"),
]


def clear_seeded(all_events: bool = False):
    now = datetime.now(settings.tz)
    start_window = now - timedelta(days=7)
    end_window = now + timedelta(days=30)
    if all_events:
        evs = gcal.list_events(start_window, end_window)
    else:
        evs = gcal.list_events(start_window, end_window, privateExtendedProperty="seeded=true")
    for ev in evs:
        gcal._get_calendar_service().events().delete(calendarId=settings.calendar_id, eventId=ev["id"]).execute()
    print(f"deleted {len(evs)} events")


def seed(with_sheet: bool):
    today = datetime.now(settings.tz).date()
    weekday_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
    for day, hhmm, svc_key, name, phone, vehicle in SEED:
        svc = business.SERVICES[svc_key]
        day_offset = (weekday_map[day.lower()] - today.weekday()) % 7
        d = today + timedelta(days=day_offset if day_offset > 0 else 7)
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
        gcal._get_calendar_service().events().insert(calendarId=settings.calendar_id, body=body).execute()
        print(f"seeded {scheduling.fmt_label(start)}  {svc.name} – {name}")
        if with_sheet:
            sheets.upsert_contact(phone, name=name, vehicle=vehicle,
                                  upcoming=f"{scheduling.fmt_label(start)} ({svc.name})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", action="store_true", help="only delete seeded events")
    ap.add_argument("--clear-all", action="store_true", help="delete all events in test window")
    ap.add_argument("--no-sheet", action="store_true", help="don't add seeded customers to Contacts")
    a = ap.parse_args()
    clear_seeded(all_events=a.clear_all)
    if not a.clear and not a.clear_all:
        seed(with_sheet=not a.no_sheet)
