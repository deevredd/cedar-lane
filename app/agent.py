"""Phase 1 agent: tool-calling loop over any OpenAI-compatible chat API (Gemini by default)."""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

import httpx

from app.config import settings
from app.prompt import build_prompt
from app.state import SessionState, get_state
from app.tools.registry import dispatch, openai_tools

log = logging.getLogger(__name__)
MAX_HOPS = 8


@dataclass
class ChatSession:
    state: SessionState
    messages: List[Dict[str, Any]] = field(default_factory=list)


_SESSIONS: Dict[str, ChatSession] = {}


def _session(session_id: str) -> ChatSession:
    s = _SESSIONS.get(session_id)
    if s is None:
        s = _SESSIONS[session_id] = ChatSession(
            state=get_state(session_id, channel="chat"),
            messages=[{"role": "system", "content": build_prompt("chat")}],
        )
    return s


def _complete(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    payload = {"model": settings.llm_model, "messages": messages, "tools": openai_tools(),
               "tool_choice": "auto", "temperature": 0.2}
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    resp = None
    for attempt in range(5):
        resp = httpx.post(url, json=payload, headers=headers, timeout=90)
        if resp.status_code in (429, 500, 502, 503, 504):
            wait = 2 ** attempt * 2
            log.warning("LLM %s, retrying in %ss: %s", resp.status_code, wait, resp.text[:200])
            time.sleep(wait)
            continue
        break
    if resp.status_code >= 400:
        raise RuntimeError(f"LLM error {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def _run(sess: ChatSession) -> Dict[str, Any]:
    trace: List[Dict[str, Any]] = []
    for _ in range(MAX_HOPS):
        msg = _complete(sess.messages)["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            tc["id"] = tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
        clean = {k: v for k, v in msg.items() if v is not None}
        clean["role"] = "assistant"
        sess.messages.append(clean)
        if not tool_calls:
            text = (msg.get("content") or "").strip()
            if not text and trace and any(t["tool"] == "log_interaction" for t in trace):
                text = "You're all set! Thanks for reaching out to Cedar Lane Auto Detailing, have a great day!"
            return {"reply": text, "tools": trace}
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = dispatch(name, args, sess.state)
            log.info("tool %s(%s) -> %s", name, args, result)
            trace.append({"tool": name, "args": args, "result": result})
            sess.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)})
    return {"reply": "Sorry, I got tangled up there. Could you say that again?", "tools": trace}


def chat(session_id: str, text: str) -> Dict[str, Any]:
    sess = _session(session_id)
    sess.messages.append({"role": "user", "content": text})
    return _run(sess)


def end_chat(session_id: str) -> Dict[str, Any]:
    """Caller closed the chat. Make sure the conversation is logged to the Sheet."""
    sess = _SESSIONS.get(session_id)
    if sess is None or len(sess.messages) <= 1:
        return {"reply": "", "tools": []}
    out: Dict[str, Any] = {"reply": "", "tools": []}
    if not sess.state.logged:
        sess.messages.append({"role": "user", "content": (
            "[SYSTEM NOTE: the caller has left the chat. Call log_interaction now for this whole conversation. "
            "Do not reply to the caller.]")})
        out = _run(sess)
    _SESSIONS.pop(session_id, None)
    return out
