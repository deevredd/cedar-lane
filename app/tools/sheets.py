"""Google Sheets: Contacts (one row per caller) and CallLog (one row per conversation)."""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from typing import Optional

import gspread

from app.config import settings
from app.google_auth import get_credentials

log = logging.getLogger(__name__)

CONTACTS = "Contacts"
CALLLOG = "CallLog"
CONTACT_HEADERS = ["phone", "name", "vehicle", "first_contact", "last_contact",
                   "total_interactions", "upcoming_appointment", "notes"]
CALLLOG_HEADERS = ["timestamp", "channel", "session_id", "phone", "name", "intent",
                   "summary", "outcome", "event_ids", "needs_followup"]

_lock = threading.RLock()
_spreadsheet = None


def _col(n: int) -> str:
    return chr(ord("A") + n - 1)


def spreadsheet():
    global _spreadsheet
    if _spreadsheet is None:
        _spreadsheet = gspread.authorize(get_credentials()).open_by_key(settings.sheet_id)
    return _spreadsheet


def _ws(title: str):
    return spreadsheet().worksheet(title)


def _now() -> str:
    return datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M")


def upsert_contact(phone: str, name: Optional[str] = None, vehicle: Optional[str] = None,
                   upcoming: Optional[str] = None, note: Optional[str] = None,
                   increment_interactions: bool = False) -> int:
    """Create or update the caller's row (keyed by E.164 phone). Returns the row number."""
    with _lock:
        ws = _ws(CONTACTS)
        phones = ws.col_values(1)
        ts = _now()
        if phone in phones:
            row = phones.index(phone) + 1
            values = ws.row_values(row)
            values += [""] * (len(CONTACT_HEADERS) - len(values))
            rec = dict(zip(CONTACT_HEADERS, values))
        else:
            row = None
            rec = {h: "" for h in CONTACT_HEADERS}
            rec.update(phone=phone, first_contact=ts, total_interactions="0")
        if name:
            rec["name"] = name
        if vehicle:
            rec["vehicle"] = vehicle
        if upcoming is not None:
            rec["upcoming_appointment"] = upcoming
        if note:
            rec["notes"] = f"{rec['notes']} | {note}" if rec["notes"] else note
        count = int(rec["total_interactions"] or 0)
        rec["total_interactions"] = count + 1 if increment_interactions else count
        rec["last_contact"] = ts
        out = [rec[h] for h in CONTACT_HEADERS]
        if row:
            ws.update(range_name=f"A{row}:{_col(len(out))}{row}", values=[out], value_input_option="RAW")
            return row
        resp = ws.append_row(out, value_input_option="RAW")
        return _row_from_append(resp)


def log_interaction(session_id: str, channel: str, phone: str, name: str, intent: str, summary: str,
                    outcome: str, event_ids: str, needs_followup: bool, row: Optional[int] = None) -> int:
    """Append a CallLog row (or overwrite this conversation's existing row). Returns the row number."""
    with _lock:
        ws = _ws(CALLLOG)
        out = [_now(), channel, session_id, phone, name, intent, summary, outcome, event_ids,
               "TRUE" if needs_followup else "FALSE"]
        if row:
            ws.update(range_name=f"A{row}:{_col(len(out))}{row}", values=[out], value_input_option="RAW")
            return row
        return _row_from_append(ws.append_row(out, value_input_option="RAW"))


def _row_from_append(resp) -> int:
    rng = ((resp or {}).get("updates") or {}).get("updatedRange", "")
    m = re.search(r"![A-Z]+(\d+)", rng)
    return int(m.group(1)) if m else 0
