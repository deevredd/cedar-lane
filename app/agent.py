from __future__ import annotations

import json
import logging
import re
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

MAX_TOOL_HOPS = 8


@dataclass
class ChatSession:
    state: SessionState
    messages: List[Dict[str, Any]] = field(default_factory=list)


_ACTIVE_SESSIONS: Dict[str, ChatSession] = {}
_SESSIONS = _ACTIVE_SESSIONS


def get_or_create_session(session_id: str) -> ChatSession:
    session = _ACTIVE_SESSIONS.get(session_id)
    if session is None:
        session = ChatSession(
            state=get_state(session_id, channel="chat"),
            messages=[{"role": "system", "content": build_prompt("chat")}],
        )
        _ACTIVE_SESSIONS[session_id] = session
    return session


def _trim_history_if_needed(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(messages) <= 10:
        return messages

    system_message = messages[0]

    target_idx = None
    for idx in range(len(messages) - 8, 0, -1):
        if messages[idx].get("role") == "user":
            target_idx = idx
            break

    if target_idx is not None:
        return [system_message] + messages[target_idx:]

    return messages


_HTTP_CLIENT = httpx.Client(
    timeout=httpx.Timeout(45.0, connect=10.0),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
)


def _call_llm(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    endpoint_url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": _trim_history_if_needed(messages),
        "tools": openai_tools(),
        "tool_choice": "auto",
        "temperature": 0.1,
        "max_tokens": 120,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}

    response = None
    for attempt in range(5):
        response = _HTTP_CLIENT.post(endpoint_url, json=payload, headers=headers)
        if response.status_code in (429, 500, 502, 503, 504):
            wait_seconds = (2 ** attempt) * 2
            log.warning("LLM %s, retrying in %ds...", response.status_code, wait_seconds)
            time.sleep(wait_seconds)
            continue
        break

    if response.status_code >= 400:
        raise RuntimeError(f"LLM API error ({response.status_code}): {response.text[:500]}")

    return response.json()


_complete = _call_llm


NUMBER_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def _extract_user_provided_digits(messages: List[Dict[str, Any]]) -> str:
    user_text = " ".join(
        msg.get("content", "").lower()
        for msg in messages
        if msg.get("role") == "user"
    )

    for word, digit in NUMBER_WORDS.items():
        user_text = re.sub(rf"\b{word}\b", digit, user_text)

    return "".join(re.findall(r"\d", user_text))


def _run_conversation_turn(session: ChatSession) -> Dict[str, Any]:
    execution_trace: List[Dict[str, Any]] = []

    for _ in range(MAX_TOOL_HOPS):
        response_json = _complete(session.messages)
        assistant_message = response_json["choices"][0]["message"]
        tool_calls = assistant_message.get("tool_calls") or []

        for call in tool_calls:
            call["id"] = call.get("id") or f"call_{uuid.uuid4().hex[:12]}"

        clean_assistant_message = {k: v for k, v in assistant_message.items() if v is not None}
        clean_assistant_message["role"] = "assistant"
        session.messages.append(clean_assistant_message)

        if not tool_calls:
            final_text = (assistant_message.get("content") or "").strip()
            if not final_text and execution_trace and any(t["tool"] == "log_interaction" for t in execution_trace):
                final_text = "You're all set! Thanks for reaching out to Cedar Lane Auto Detailing, have a great day!"
            return {"reply": final_text, "tools": execution_trace}

        for call in tool_calls:
            tool_name = call["function"]["name"]
            try:
                tool_args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                tool_args = {}

            if tool_name == "book_appointment":
                phone_arg_digits = "".join(re.findall(r"\d", str(tool_args.get("phone", ""))))[-10:]
                actual_user_digits = _extract_user_provided_digits(session.messages)

                if not phone_arg_digits or phone_arg_digits not in actual_user_digits:
                    blocked_result = {
                        "booked": False,
                        "error": "The customer has not provided their phone number yet. Ask the customer: 'What is your phone number?'",
                    }
                    log.warning("Blocked booking with unverified phone number: %s", tool_args.get("phone"))
                    execution_trace.append({"tool": tool_name, "args": tool_args, "result": blocked_result})
                    session.messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(blocked_result),
                    })
                    continue

            try:
                tool_result = dispatch(tool_name, tool_args, session.state)
            except Exception as exc:
                log.exception("Tool execution error in %s: %s", tool_name, exc)
                tool_result = {"error": f"Failed to execute {tool_name}: {exc}"}

            log.info("Tool executed: %s(%s) -> %s", tool_name, tool_args, tool_result)
            execution_trace.append({"tool": tool_name, "args": tool_args, "result": tool_result})

            session.messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(tool_result),
            })

    return {
        "reply": "Sorry, I had trouble processing that. Could you repeat what you'd like to do?",
        "tools": execution_trace,
    }


def chat(session_id: str, text: str) -> Dict[str, Any]:
    session = get_or_create_session(session_id)
    session.messages.append({"role": "user", "content": text})
    return _run_conversation_turn(session)


def end_chat(session_id: str) -> Dict[str, Any]:
    session = _ACTIVE_SESSIONS.pop(session_id, None)
    if session is None or len(session.messages) <= 1 or session.state.logged:
        return {"reply": "", "tools": []}

    output: Dict[str, Any] = {"reply": "", "tools": []}
    try:
        session.messages.append({
            "role": "user",
            "content": (
                "[SYSTEM NOTE: the caller has left the chat. Call log_interaction now for this whole conversation. "
                "Do not reply to the caller.]"
            ),
        })
        output = _run_conversation_turn(session)
    except Exception as exc:
        log.warning("Graceful recovery during end_chat logging: %s", exc)

    return output
