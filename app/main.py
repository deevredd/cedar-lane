from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import agent
from app.config import settings
from app.state import drop_state, get_state
from app.tools.registry import dispatch, log_interaction_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="Cedar Lane AI Receptionist")
STATIC = Path(__file__).parent / "static"


class ChatIn(BaseModel):
    session_id: str
    message: str = ""


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/chat")
def api_chat(body: ChatIn):
    try:
        return agent.chat(body.session_id, body.message)
    except Exception as exc:
        log.exception("chat failed")
        raise HTTPException(500, str(exc))


@app.post("/api/end")
def api_end(body: ChatIn):
    try:
        return agent.end_chat(body.session_id)
    except Exception as exc:
        log.exception("end failed")
        raise HTTPException(500, str(exc))



def _parse_args(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


@app.post("/vapi/webhook")
async def vapi_webhook(request: Request, x_vapi_secret: Optional[str] = Header(default=None)):
    if settings.vapi_webhook_secret and x_vapi_secret != settings.vapi_webhook_secret:
        raise HTTPException(401, "bad secret")
    body = await request.json()
    msg = body.get("message") or {}
    mtype = msg.get("type")
    call = msg.get("call") or {}
    call_id = call.get("id") or "vapi-unknown"
    caller = (call.get("customer") or {}).get("number")
    st = get_state(call_id, channel="voice", caller_phone=caller)

    if mtype == "tool-calls":
        results = []
        for tc in msg.get("toolCallList") or []:
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name")
            args = _parse_args(fn.get("arguments", tc.get("arguments")))
            res = await run_in_threadpool(dispatch, name, args, st)
            log.info("vapi tool %s(%s) -> %s", name, args, res)
            results.append({"toolCallId": tc.get("id"), "result": json.dumps(res)})
        return {"results": results}

    if mtype == "end-of-call-report":
        if not st.logged:
            analysis = msg.get("analysis") or {}
            summary = analysis.get("summary") or msg.get("summary") or "Call ended (no summary)."
            outcome = f"Ended: {msg.get('endedReason', 'unknown')}"
            if st.event_ids:
                outcome = f"Appointment change(s) made; {outcome}"
            await run_in_threadpool(log_interaction_for, st, "", "", "other", summary, outcome, False)
        drop_state(call_id)
        return {}

    return {}
