# Cedar Lane Auto Detailing: AI Receptionist

An autonomous agentic receptionist that books, reschedules, and cancels auto detailing appointments in **Google Calendar**, logs every caller and conversation to **Google Sheets**, and answers customer questions. Phase 1 provides an interactive web chat. Phase 2 extends the exact same agent brain to a live **Vapi** voice assistant for phone calls.

```
Chat UI ──/api/chat──► FastAPI ──► Open Skinny Harness (LLM) ────┐
                                                                 ├─► Composable Tool Registry ─► Google Calendar
Caller  ──► Vapi (Deepgram STT → LLM → TTS) ──► /vapi/webhook ───┘                             └► Google Sheets
```

---

## Architecture: The Open Skinny Harness

This system uses an agentic architecture centered on an **open skinny harness**:

1. **LLM — Reasoning & Dynamic Orchestration**:
   - Extracts unstructured entities (names, phone numbers, vehicle models) from natural, casual dialogue.
   - Resolves temporal references (e.g. *"this Friday morning"*) into structured ISO contracts (`YYYY-MM-DD`, `HH:MM`) using real-world situational grounding.
   - Dynamically composes atomic tools (`check_availability`, `book_appointment`, `add_note_to_appointment`, `reschedule_appointment`, `cancel_appointment`) without rigid procedural state machines.
   - Handles multi-intent conversational turns (e.g. booking an appointment, adding a vehicle, asking about duration, and rescheduling in one fluid conversation).

2. **Python — Deterministic Execution & Safety Invariants**:
   - Strictly validates structured parameters (ISO-8601 datetimes, E.164 phone normalization, canonical service enums).
   - Enforces operating hours (8:00 AM – 6:00 PM, closed Sunday) and service durations.
   - Prevents double-booking via thread-safe mutex locking (`_booking_lock`).
   - Guarantees transactional rescheduling: validates new slot availability before patching, ensuring existing appointments are never dropped on conflict.
   - Prevents duplicate bookings from network retries via idempotent booking guards.

---

## Quick Start
See [SETUP.md](SETUP.md) for accounts and credentials. Then:
```bash
python scripts/check_setup.py && python scripts/init_sheet.py && python scripts/seed_calendar.py
uvicorn app.main:app --port 8000
pytest -q
python scripts/vapi_setup.py
```

---

## What the Agent Handles vs. Hands Off
| Handles Autonomously | Hands Off to Owner (`needs_followup=TRUE`) |
|---|---|
| Availability checks & booking with nearest-alternative suggestions | Billing disputes, refunds, and charge complaints |
| Atomic rescheduling & cancellations | Vehicle damage claims or upset callers requesting a manager |
| Adding vehicle details & special notes to existing appointments | Custom ceramic coating quotes & commercial fleet deals |
| Shop policies, hours, pricing, vehicle prep, and duration Q&A | Any request outside shop policy boundaries |
| Late arrival estimates (notes appointment; >15 min offers reschedule) | |

---

## Data Model & Grounding
- **Google Calendar**: Single-bay reservation model. Events are titled `"{Service} – {Name}"` with customer details stored in private `extendedProperties`.
- **Google Sheets (CRM)**:
  - `Contacts`: Tracks customer history (`phone, name, vehicle, first_contact, last_contact, total_interactions, upcoming_appointment, notes`).
  - `CallLog`: Records every conversation with timestamp, channel (`chat` vs `voice`), intent, summary, outcome, event IDs, and follow-up flags.

---

## Voice Tuning (Phase 2): `scripts/vapi_setup.py`
| Setting | Value | Purpose |
|---|---|---|
| Transcriber | Deepgram `nova-3` | Low-latency streaming speech-to-text |
| Voice Model | Vapi `Elliot` | Natural conversational phone timbre |
| Smart Endpointing | LiveKit | Natural conversational turn-taking and barge-in |
| Tool Webhooks | `/vapi/webhook` | Executes the shared tool registry with sub-second execution |
| Shared Brain | `app/prompt.py` | Shares the same declarative policies and shop facts with Phase 1 |

---

## Test Suite
62 automated unit tests verify deterministic safety:
```bash
pytest
# 62 passed in 0.44s
```
- Multi-turn out-of-order entity extraction & post-booking note updates.
- Concurrency locks & idempotent retry protection.
- Atomic reschedule preservation under conflict.
- Slot duration calculation & operating hour boundary checks.

---

## Repository Layout
```
app/business.py        Services, pricing, shop hours, vehicle surcharges
app/scheduling.py      Deterministic slot math, interval checks, ISO parsing
app/tools/gcal.py      Google Calendar tools, concurrency lock, idempotent retries
app/tools/sheets.py    Google Sheets CRM (Contacts + CallLog)
app/tools/registry.py  Composable tool schemas + dispatcher (shared by chat + voice)
app/prompt.py          Declarative prompt & temporal grounding
app/agent.py           Open skinny agent execution loop
app/main.py            FastAPI: chat API, web UI, Vapi webhook
scripts/               Setup verification, sheet init, calendar seed, Vapi setup
tests/                 62 unit and boundary tests
```
