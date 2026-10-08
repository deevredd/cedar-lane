import json
from datetime import datetime

from fastapi.testclient import TestClient

from app import agent, main
from app.state import get_state
from app.tools import gcal, registry
from tests.test_scheduling import NOW, THU, THU_BUSY, TZ


def test_check_availability_offers_430(monkeypatch):
    monkeypatch.setattr(gcal, "now", lambda: NOW)
    monkeypatch.setattr(gcal, "_day_busy", lambda d, exclude_id=None: THU_BUSY if d == THU else [])
    res = gcal.check_availability("interior_detail_and_wash", "thursday", "15:30")
    assert res["available"] is False
    assert res["alternatives"][0]["start"] == "2026-10-08 16:30"
    assert gcal.check_availability("interior_detail_and_wash", "thursday", "4:30 pm")["available"] is True
    closed = gcal.check_availability("exterior_wash", "sunday")
    assert closed["available"] is False and closed["next_available"]["weekday"] == "Monday"


def test_book_rejects_conflict(monkeypatch):
    monkeypatch.setattr(gcal, "now", lambda: NOW)
    monkeypatch.setattr(gcal, "_day_busy", lambda d, exclude_id=None: THU_BUSY)
    res = gcal.book_appointment("Sara", "415-555-0190", "interior_detail_and_wash", "2026-10-08 15:30")
    assert res["booked"] is False and res["alternatives"][0]["start"] == "2026-10-08 16:30"


def test_dispatch_filters_and_contains_errors():
    st = get_state("t1")
    assert "error" in registry.dispatch("nope", {}, st)
    assert "error" in registry.dispatch("check_availability", {"service": "boat_wash", "date": "thursday"}, st)


def test_every_spec_has_handler():
    assert {s["name"] for s in registry.TOOL_SPECS} == set(registry.HANDLERS)


def test_agent_loop_runs_tools(monkeypatch):
    replies = iter([
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "check_availability",
             "arguments": json.dumps({"service": "interior_detail_and_wash", "date": "thursday", "time": "15:30"})}}]}}]},
        {"choices": [{"message": {"role": "assistant", "content": "That one's full. 4:30 is open, want that instead?"}}]},
    ])
    seen = []
    monkeypatch.setattr(agent, "_complete", lambda msgs: next(replies))
    monkeypatch.setattr(agent, "dispatch", lambda n, a, st: seen.append((n, a)) or {"available": False})
    out = agent.chat("s-agent", "Is the 3:30 interior detail and wash appointment on Thursday open?")
    assert "4:30" in out["reply"] and seen[0][0] == "check_availability"
    msgs = agent._SESSIONS["s-agent"].messages
    assert msgs[-2]["role"] == "tool" and msgs[-2]["tool_call_id"] == "c1"


def test_vapi_webhook_tool_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "dispatch", lambda n, a, st: calls.append((n, a, st.channel, st.caller_phone)) or {"ok": 1})
    client = TestClient(main.app)
    payload = {"message": {"type": "tool-calls", "call": {"id": "call-1", "customer": {"number": "+14155550190"}},
                           "toolCallList": [{"id": "tc1", "type": "function", "function": {
                               "name": "check_availability", "arguments": {"service": "exterior_wash", "date": "friday"}}}]}}
    r = client.post("/vapi/webhook", json=payload)
    assert r.status_code == 200
    assert r.json() == {"results": [{"toolCallId": "tc1", "result": json.dumps({"ok": 1})}]}
    assert calls == [("check_availability", {"service": "exterior_wash", "date": "friday"}, "voice", "+14155550190")]


def test_vapi_end_of_call_fallback_logs(monkeypatch):
    logged = []
    monkeypatch.setattr(main, "log_interaction_for", lambda st, *a: logged.append((st.session_id, a)))
    client = TestClient(main.app)
    r = client.post("/vapi/webhook", json={"message": {"type": "end-of-call-report", "call": {"id": "call-2"},
                                                       "endedReason": "customer-ended-call",
                                                       "analysis": {"summary": "Asked about hours."}}})
    assert r.status_code == 200 and logged and logged[0][1][3] == "Asked about hours."
