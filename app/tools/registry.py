"""Tool schemas (OpenAI function format) + dispatcher. Used by the chat agent AND the Vapi webhook."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

from app import business
from app.phone import normalize_phone
from app.state import SessionState
from app.tools import gcal, sheets

log = logging.getLogger(__name__)

SERVICE_ENUM = list(business.SERVICES)
INTENTS = ["book", "reschedule", "cancel", "pricing", "hours", "prep", "vehicle_question",
           "running_late", "complaint", "quote_request", "other"]

TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "check_availability",
        "description": ("Check whether a service can be booked at a date/time. ALWAYS call this before saying a "
                        "time is open or full. If the time is taken it returns the nearest open alternatives. "
                        "Omit 'time' to list all open start times that day."),
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": SERVICE_ENUM},
                "date": {"type": "string", "description": "YYYY-MM-DD, or 'today', 'tomorrow', or a weekday name like 'thursday'."},
                "time": {"type": "string", "description": "Requested start time, 24h 'HH:MM' (e.g. '15:30')."},
            },
            "required": ["service", "date"],
        },
    },
    {
        "name": "book_appointment",
        "description": ("Book an appointment after the caller has agreed to a specific open time and given their "
                        "name and phone number. Re-checks the slot before booking."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Caller's name."},
                "phone": {"type": "string", "description": "Caller's 10-digit phone number as explicitly stated by the caller. You MUST ask the customer for this if you do not have it. Never guess or invent a number."},
                "service": {"type": "string", "enum": SERVICE_ENUM},
                "start": {"type": "string", "description": "Agreed start time as 'YYYY-MM-DD HH:MM' (24h) from check_availability, or '<day> HH:MM' (e.g. '2026-10-08 16:30' or 'thursday 16:30'). Keep the exact date agreed upon!"},
                "vehicle": {"type": "string", "description": "Vehicle make/model/type if mentioned."},
                "notes": {"type": "string", "description": "Any special requests (pet hair, stains, etc.)."},
            },
            "required": ["name", "phone", "service", "start"],
        },
    },
    {
        "name": "find_appointments",
        "description": "Look up a caller's upcoming appointments by the phone number they booked with. Needed before rescheduling, cancelling or adding a note.",
        "parameters": {
            "type": "object",
            "properties": {"phone": {"type": "string", "description": "10-digit phone number."}},
            "required": ["phone"],
        },
    },
    {
        "name": "reschedule_appointment",
        "description": "Move an existing appointment (event_id from find_appointments) to a new start time. Checks for conflicts; returns alternatives if taken.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "new_start": {"type": "string", "description": "'YYYY-MM-DD HH:MM' (24h)."},
            },
            "required": ["event_id", "new_start"],
        },
    },
    {
        "name": "cancel_appointment",
        "description": "Cancel an existing appointment (event_id from find_appointments) after the caller confirms which one.",
        "parameters": {
            "type": "object",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
        },
    },
    {
        "name": "add_note_to_appointment",
        "description": "Attach a note to an existing appointment, e.g. 'Running about 10 min late' or a special request.",
        "parameters": {
            "type": "object",
            "properties": {"event_id": {"type": "string"}, "note": {"type": "string"}},
            "required": ["event_id", "note"],
        },
    },
    {
        "name": "log_interaction",
        "description": ("Record this conversation in the shop's CRM sheet. Call ONCE when the conversation is wrapping "
                        "up (caller says that's all / goodbye), and immediately when handing something off to a human. "
                        "Calling again updates the same record."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Caller name, or '' if unknown."},
                "phone": {"type": "string", "description": "Caller phone, or '' if unknown."},
                "intent": {"type": "string", "description": f"Comma-separated, from: {', '.join(INTENTS)}"},
                "summary": {"type": "string", "description": "1-2 sentence summary of what came up."},
                "outcome": {"type": "string", "description": "e.g. 'Booked Thu Oct 8 4:30 PM', 'Answered pricing', 'Handed off to owner'."},
                "needs_followup": {"type": "boolean", "description": "True if a human must call back (complaints, billing, quotes, anything unresolved)."},
            },
            "required": ["intent", "summary", "outcome", "needs_followup"],
        },
    },
]


def _track_phone(st: SessionState, phone: str, name: str = "") -> None:
    try:
        st.phone = normalize_phone(phone)
    except ValueError:
        pass
    if name:
        st.name = name


def _book(args: Dict[str, Any], st: SessionState) -> Dict[str, Any]:
    res = gcal.book_appointment(channel=st.channel, **args)
    if res.get("booked"):
        _track_phone(st, args["phone"], args.get("name", ""))
        st.event_ids.append(res["event_id"])
    return res


def _find(args: Dict[str, Any], st: SessionState) -> Dict[str, Any]:
    res = gcal.find_appointments(**args)
    _track_phone(st, args["phone"])
    if res.get("appointments") and not st.name:
        st.name = res["appointments"][0].get("name") or None
    return res


def _event_op(fn: Callable[..., Dict[str, Any]]):
    def run(args: Dict[str, Any], st: SessionState) -> Dict[str, Any]:
        res = fn(**args)
        if args.get("event_id") and args["event_id"] not in st.event_ids:
            st.event_ids.append(args["event_id"])
        return res
    return run


def log_interaction_for(st: SessionState, name: str = "", phone: str = "", intent: str = "other",
                        summary: str = "", outcome: str = "", needs_followup: bool = False) -> Dict[str, Any]:
    raw_phone = phone or st.phone or st.caller_phone or ""
    try:
        e164 = normalize_phone(raw_phone) if raw_phone else ""
    except ValueError:
        e164 = raw_phone
    name = name or st.name or ""
    first = not st.logged
    st.log_row = sheets.log_interaction(
        session_id=st.session_id, channel=st.channel, phone=e164 or "unknown", name=name, intent=intent,
        summary=summary, outcome=outcome, event_ids=", ".join(st.event_ids), needs_followup=bool(needs_followup),
        row=st.log_row,
    )
    if e164.startswith("+"):
        note = f"FOLLOW UP: {summary}" if needs_followup else None
        sheets.upsert_contact(e164, name=name or None, note=note if first or needs_followup else None,
                              increment_interactions=first)
    st.logged = True
    return {"logged": True}


def _log(args: Dict[str, Any], st: SessionState) -> Dict[str, Any]:
    return log_interaction_for(st, **args)


HANDLERS: Dict[str, Callable[[Dict[str, Any], SessionState], Dict[str, Any]]] = {
    "check_availability": lambda a, st: gcal.check_availability(**a),
    "book_appointment": _book,
    "find_appointments": _find,
    "reschedule_appointment": _event_op(gcal.reschedule_appointment),
    "cancel_appointment": _event_op(gcal.cancel_appointment),
    "add_note_to_appointment": _event_op(gcal.add_note_to_appointment),
    "log_interaction": _log,
}


def dispatch(name: str, args: Dict[str, Any], st: SessionState) -> Dict[str, Any]:
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"Unknown tool '{name}'."}
    allowed = set(next(s for s in TOOL_SPECS if s["name"] == name)["parameters"]["properties"])
    clean = {k: v for k, v in (args or {}).items() if k in allowed and v not in (None, "")}
    try:
        return handler(clean, st)
    except (ValueError, TypeError) as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.exception("Tool %s failed", name)
        return {"error": f"Internal error while running {name}: {exc}. Apologize and offer to have someone call back."}


def openai_tools() -> List[Dict[str, Any]]:
    return [{"type": "function", "function": spec} for spec in TOOL_SPECS]
