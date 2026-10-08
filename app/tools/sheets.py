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

CONTACTS_TAB = "Contacts"
CALLLOG_TAB = "CallLog"

CONTACT_COLUMNS = [
    "phone",
    "name",
    "vehicle",
    "first_contact",
    "last_contact",
    "total_interactions",
    "upcoming_appointment",
    "notes",
]

CALLLOG_COLUMNS = [
    "timestamp",
    "channel",
    "session_id",
    "phone",
    "name",
    "intent",
    "summary",
    "outcome",
    "event_ids",
    "needs_followup",
]

_sheet_lock = threading.RLock()
_cached_spreadsheet = None


def _column_letter(column_number: int) -> str:
    return chr(ord("A") + column_number - 1)


def get_spreadsheet():
    global _cached_spreadsheet
    if _cached_spreadsheet is None:
        credentials = get_credentials()
        _cached_spreadsheet = gspread.authorize(credentials).open_by_key(settings.sheet_id)
    return _cached_spreadsheet


def _get_worksheet(title: str):
    return get_spreadsheet().worksheet(title)


def _current_timestamp() -> str:
    return datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M")


def _row_number_from_response(append_response: dict) -> int:
    updated_range = ((append_response or {}).get("updates") or {}).get("updatedRange", "")
    match = re.search(r"![A-Z]+(\d+)", updated_range)
    return int(match.group(1)) if match else 0


def upsert_contact(
    phone: str,
    name: Optional[str] = None,
    vehicle: Optional[str] = None,
    upcoming: Optional[str] = None,
    note: Optional[str] = None,
    increment_interactions: bool = False,
) -> int:
    with _sheet_lock:
        worksheet = _get_worksheet(CONTACTS_TAB)
        existing_phones = worksheet.col_values(1)
        timestamp = _current_timestamp()

        if phone in existing_phones:
            row_idx = existing_phones.index(phone) + 1
            raw_values = worksheet.row_values(row_idx)
            raw_values += [""] * (len(CONTACT_COLUMNS) - len(raw_values))
            record = dict(zip(CONTACT_COLUMNS, raw_values))
        else:
            row_idx = None
            record = {col: "" for col in CONTACT_COLUMNS}
            record["phone"] = phone
            record["first_contact"] = timestamp
            record["total_interactions"] = "0"

        if name:
            record["name"] = name
        if vehicle:
            record["vehicle"] = vehicle
        if upcoming is not None:
            record["upcoming_appointment"] = upcoming
        if note:
            existing_note = record.get("notes", "")
            record["notes"] = f"{existing_note} | {note}" if existing_note else note

        current_interactions = int(record.get("total_interactions") or 0)
        if increment_interactions:
            record["total_interactions"] = current_interactions + 1
        record["last_contact"] = timestamp

        row_data = [record[col] for col in CONTACT_COLUMNS]

        if row_idx:
            range_name = f"A{row_idx}:{_column_letter(len(row_data))}{row_idx}"
            worksheet.update(range_name=range_name, values=[row_data], value_input_option="RAW")
            return row_idx
        else:
            response = worksheet.append_row(row_data, value_input_option="RAW")
            return _row_number_from_response(response)


def log_interaction(
    session_id: str,
    channel: str,
    phone: str,
    name: str,
    intent: str,
    summary: str,
    outcome: str,
    event_ids: str,
    needs_followup: bool,
    row: Optional[int] = None,
) -> int:
    with _sheet_lock:
        worksheet = _get_worksheet(CALLLOG_TAB)
        row_data = [
            _current_timestamp(),
            channel,
            session_id,
            phone,
            name,
            intent,
            summary,
            outcome,
            event_ids,
            "TRUE" if needs_followup else "FALSE",
        ]

        if row:
            range_name = f"A{row}:{_column_letter(len(row_data))}{row}"
            worksheet.update(range_name=range_name, values=[row_data], value_input_option="RAW")
            return row
        else:
            response = worksheet.append_row(row_data, value_input_option="RAW")
            return _row_number_from_response(response)
