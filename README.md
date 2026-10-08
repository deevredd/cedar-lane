# Cedar Lane Auto Detailing: AI Receptionist

An AI receptionist that books, reschedules and cancels detailing appointments in **Google Calendar**, logs every caller and conversation to **Google Sheets**, and answers shop questions. Phase 1 is a text chat. Phase 2 is a **Vapi** phone agent that uses the same tools.

```
Chat UI ──/api/chat──► FastAPI ──► agent loop (Gemini, OpenAI-compatible API) ──┐
                                                                                ├─► tool registry ─► Google Calendar
Caller ──► Vapi (Deepgram STT → LLM → TTS) ──tool-calls──► /vapi/webhook ───────┘                 └► Google Sheets
```

The **tools, business rules and prompt live in one place** (`app/tools/registry.py`, `app/business.py`, `app/prompt.py`). Phase 1 runs its own tool-calling loop over them. In Phase 2, Vapi's LLM calls the same tools over a webhook, which keeps voice latency low.

## Quick start
See [SETUP.md](SETUP.md) for accounts and credentials. Then:
```bash
python scripts/check_setup.py && python scripts/init_sheet.py && python scripts/seed_calendar.py
uvicorn app.main:app --port 8000
pytest -q
python scripts/vapi_setup.py
```

## What the agent handles vs. hands off
| Handles | Hands off (logs `needs_followup=TRUE`, promises a callback) |
|---|---|
| Book / reschedule / cancel, with conflict checks and nearest-alternative offers | Charge complaints, refunds, billing disputes |
| Pricing, hours, location, prep, vehicle types, policies | Damage claims, upset callers asking for a manager |
| Running late (notes the event; >15 min → offers to reschedule) | Ceramic coating / custom quotes, fleet deals |
| | Anything not covered by the business info |

## Data model
**Calendar.** One event per appointment, titled `"{Service} – {Name}"`. Phone, service and vehicle are stored in the event's private `extendedProperties`, so a caller's bookings can be looked up by phone. One bay: no overlapping appointments, starts every 30 min, Mon–Sat 8–6. All-day events (e.g. "Closed") block the day.

**Sheet.**
- `Contacts`: one row per caller, keyed by E.164 phone: `phone, name, vehicle, first_contact, last_contact, total_interactions, upcoming_appointment, notes`. `upcoming_appointment` is re-synced from the Calendar after every book/reschedule/cancel.
- `CallLog`: one row per conversation: `timestamp, channel, session_id, phone, name, intent, summary, outcome, event_ids, needs_followup`.

## Correctness decisions
- The model never decides availability. `check_availability` computes it from the Calendar, and `book_appointment` / `reschedule_appointment` **re-check under a lock** right before writing.
- Relative dates ("Thursday", "tomorrow") are resolved **server-side** in the shop's timezone, so the model doesn't do date arithmetic.
- Tool errors come back to the model as `{"error": ...}` so it can recover (e.g. ask for the phone number again) instead of crashing.
- Every conversation gets logged. The model calls `log_interaction` at wrap-up. If it doesn't, **End chat** in Phase 1 forces it, and in Phase 2 the `end-of-call-report` webhook writes the log.

## Voice tuning (Phase 2): `scripts/vapi_setup.py`
| Knob | Value | Why |
|---|---|---|
| Transcriber | Deepgram `nova-3` | fast, accurate streaming STT |
| LLM | `gpt-4o-mini`, temp 0.3, max 200 tokens | low time-to-first-token |
| Voice | Vapi `Elliot` (configurable: ElevenLabs flash / Cartesia) | low-latency TTS |
| `startSpeakingPlan` | `waitSeconds 0.4` + LiveKit smart endpointing | replies fast without cutting people off mid-phone-number |
| `stopSpeakingPlan` | `numWords 2, voiceSeconds 0.2` | natural barge-in |
| Tool `request-start` messages | "Let me check…" | covers Calendar latency the way a human would |
| Background sound | office | sounds like a real front desk |
| Prompt | 1–2 sentence replies, spoken times, caller ID awareness | conversational |

The per-turn latency breakdown is in the Vapi dashboard (Call Logs).

## Layout
```
app/business.py        services, prices, hours, policies
app/scheduling.py      pure slot/date logic (unit-tested)
app/tools/gcal.py      Calendar tools
app/tools/sheets.py    Contacts + CallLog
app/tools/registry.py  tool schemas + dispatcher (shared by chat + voice)
app/prompt.py          system prompt (chat + voice variants)
app/agent.py           Phase 1 tool-calling loop
app/main.py            FastAPI: chat API, UI, Vapi webhook
scripts/               setup check, sheet init, calendar seed, Vapi config
```
