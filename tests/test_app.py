import json
from datetime import datetime
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app import agent, main
from app.state import get_state
from app.tools import gcal, registry
from tests.test_scheduling import NOW, THU, THU_BUSY, TZ


def test_check_availability_offers_430(monkeypatch):
    monkeypatch.setattr(gcal, "now", lambda: NOW)
    monkeypatch.setattr(gcal, "_day_busy", lambda d, exclude_id=None: THU_BUSY if d == THU else [])
    res = gcal.check_availability("interior_detail_and_wash", "2026-10-08", "15:30")
    assert res["available"] is False
    assert res["alternatives"][0]["start"] == "2026-10-08 16:30"
    assert gcal.check_availability("interior_detail_and_wash", "2026-10-08", "4:30 pm")["available"] is True
    closed = gcal.check_availability("exterior_wash", "2026-10-11")
    assert closed["available"] is False and closed["next_available"]["weekday"] == "Monday"


def test_book_rejects_conflict(monkeypatch):
    monkeypatch.setattr(gcal, "now", lambda: NOW)
    monkeypatch.setattr(gcal, "list_events", lambda *a, **k: [])
    monkeypatch.setattr(gcal, "_day_busy", lambda d, exclude_id=None: THU_BUSY)
    res = gcal.book_appointment("Sara", "415-555-0190", "interior_detail_and_wash", "2026-10-08 15:30")
    assert res["booked"] is False and res["alternatives"][0]["start"] == "2026-10-08 16:30"


def test_book_idempotent_retry_prevents_duplicate(monkeypatch):
    mock_existing_event = {
        "id": "existing_ev_123",
        "start": {"dateTime": "2026-10-08T15:30:00-07:00"},
        "end": {"dateTime": "2026-10-08T16:30:00-07:00"},
        "summary": "Interior Detail + Wash – Sara",
        "status": "confirmed",
    }
    monkeypatch.setattr(gcal, "now", lambda: NOW)
    monkeypatch.setattr(gcal, "list_events", lambda *a, **k: [mock_existing_event])
    res = gcal.book_appointment("Sara", "415-555-0190", "interior_detail_and_wash", "2026-10-08 15:30")
    assert res["booked"] is True
    assert res["event_id"] == "existing_ev_123"


def test_dispatch_filters_and_contains_errors():
    st = get_state("t1")
    assert "error" in registry.dispatch("nope", {}, st)
    assert "error" in registry.dispatch("check_availability", {"service": "boat_wash", "date": "2026-10-08"}, st)


def test_every_spec_has_handler():
    assert {s["name"] for s in registry.TOOL_SPECS} == set(registry.HANDLERS)


def test_agent_loop_runs_tools(monkeypatch):
    replies = iter([
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "check_availability",
             "arguments": json.dumps({"service": "interior_detail_and_wash", "date": "2026-10-08", "time": "15:30"})}}]}}]},
        {"choices": [{"message": {"role": "assistant", "content": "That one's full. 4:30 is open, want that instead?"}}]},
    ])
    seen = []
    monkeypatch.setattr(agent, "_complete", lambda msgs: next(replies))
    monkeypatch.setattr(agent, "dispatch", lambda n, a, st: seen.append((n, a)) or {"available": False})
    out = agent.chat("s-agent", "Is the 3:30 interior detail and wash appointment on Thursday open?")
    assert "4:30" in out["reply"] and seen[0][0] == "check_availability"
    msgs = agent._SESSIONS["s-agent"].messages
    assert msgs[-2]["role"] == "tool" and msgs[-2]["tool_call_id"] == "c1"


def test_post_booking_vehicle_addition(monkeypatch):
    replies = iter([
        {"choices": [{"message": {"role": "assistant", "content": "Got it, added your Tesla to the appointment notes.",
                                  "tool_calls": [
            {"id": "c2", "type": "function", "function": {"name": "add_note_to_appointment",
             "arguments": json.dumps({"event_id": "ev_123", "note": "Vehicle: Tesla"})}}]}}]},
        {"choices": [{"message": {"role": "assistant", "content": "Anything else you need today?"}}]},
    ])
    seen = []
    session = agent.get_or_create_session("s-post-book")
    session.state.event_ids.append("ev_123")
    monkeypatch.setattr(agent, "_complete", lambda msgs: next(replies))
    monkeypatch.setattr(agent, "dispatch", lambda n, a, st: seen.append((n, a)) or {"noted": True})
    out = agent.chat("s-post-book", "tesla")
    assert seen[0][0] == "add_note_to_appointment"
    assert seen[0][1]["note"] == "Vehicle: Tesla"
    assert seen[0][1]["event_id"] == "ev_123"


def test_reschedule_preserves_original_booking_on_conflict(monkeypatch):
    mock_event = {
        "id": "ev_target",
        "start": {"dateTime": "2026-10-08T10:00:00-07:00"},
        "end": {"dateTime": "2026-10-08T11:00:00-07:00"},
        "summary": "Interior Detail – Alex",
    }
    monkeypatch.setattr(gcal, "now", lambda: NOW)
    monkeypatch.setattr(gcal, "_fetch_event", lambda eid: mock_event)
    monkeypatch.setattr(gcal, "_day_busy", lambda d, exclude_id=None: [(gcal.now(), gcal.now())])
    monkeypatch.setattr(gcal.scheduling, "check_slot", lambda *a: (False, "Slot taken"))

    res = gcal.reschedule_appointment("ev_target", "2026-10-08 14:00")
    assert res["rescheduled"] is False
    assert "Slot taken" in res["reason"]


def test_add_note_to_appointment_preserves_description(monkeypatch):
    existing_desc = "Customer: Eve\nPhone: (415) 555-0190"
    mock_event = {"id": "ev_note_test", "description": existing_desc}
    patched_body = {}

    mock_service = MagicMock()
    mock_service.events().get().execute.return_value = mock_event
    mock_service.events().patch().execute.side_effect = lambda **kwargs: patched_body.update(kwargs)

    monkeypatch.setattr(gcal, "_get_calendar_service", lambda: mock_service)
    res = gcal.add_note_to_appointment("ev_note_test", "Vehicle: Tesla Model 3")
    assert res["noted"] is True
    assert res["event_id"] == "ev_note_test"


def test_cancel_appointment_deletes_event(monkeypatch):
    deleted_events = []
    mock_event = {
        "id": "ev_cancel_test",
        "start": {"dateTime": "2026-10-08T10:00:00-07:00"},
        "end": {"dateTime": "2026-10-08T11:00:00-07:00"},
    }
    mock_service = MagicMock()
    mock_service.events().get().execute.return_value = mock_event
    mock_service.events().delete().execute.side_effect = lambda **kwargs: deleted_events.append(kwargs)

    monkeypatch.setattr(gcal, "_get_calendar_service", lambda: mock_service)
    monkeypatch.setattr(gcal, "invalidate_calendar_cache", lambda: None)
    res = gcal.cancel_appointment("ev_cancel_test")
    assert res["cancelled"] is True
    assert res["event_id"] == "ev_cancel_test"


def test_find_appointments_normalizes_phone(monkeypatch):
    monkeypatch.setattr(gcal, "_upcoming_appointments_for", lambda phone: [{"id": "ev_1", "name": "Eve"}])
    res = gcal.find_appointments("415 555 0190")
    assert res["phone"] == "(415) 555-0190"
    assert len(res["appointments"]) == 1


def test_dispatch_missing_required_arguments():
    st = get_state("t_missing")
    res = registry.dispatch("book_appointment", {"name": "Eve"}, st)
    assert "error" in res


def test_vapi_webhook_tool_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "dispatch", lambda n, a, st: calls.append((n, a, st.channel, st.caller_phone)) or {"ok": 1})
    client = TestClient(main.app)
    payload = {"message": {"type": "tool-calls", "call": {"id": "call-1", "customer": {"number": "+14155550190"}},
                           "toolCallList": [{"id": "tc1", "type": "function", "function": {
                               "name": "check_availability", "arguments": {"service": "exterior_wash", "date": "2026-10-09"}}}]}}
    r = client.post("/vapi/webhook", json=payload)
    assert r.status_code == 200
    assert r.json() == {"results": [{"toolCallId": "tc1", "result": json.dumps({"ok": 1})}]}
    assert calls == [("check_availability", {"service": "exterior_wash", "date": "2026-10-09"}, "voice", "+14155550190")]


def test_vapi_end_of_call_fallback_logs(monkeypatch):
    logged = []
    monkeypatch.setattr(main, "log_interaction_for", lambda st, *a: logged.append((st.session_id, a)))
    client = TestClient(main.app)
    r = client.post("/vapi/webhook", json={"message": {"type": "end-of-call-report", "call": {"id": "call-2"},
                                                       "endedReason": "customer-ended-call",
                                                       "analysis": {"summary": "Asked about hours."}}})
    assert r.status_code == 200 and logged and logged[0][1][3] == "Asked about hours."
