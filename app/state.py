"""Per-conversation state (chat session id or Vapi call id)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SessionState:
    session_id: str
    channel: str = "chat"            # "chat" | "voice"
    caller_phone: Optional[str] = None  # caller ID (voice only)
    phone: Optional[str] = None      # phone collected / used during the conversation
    name: Optional[str] = None
    event_ids: List[str] = field(default_factory=list)
    intents: List[str] = field(default_factory=list)
    logged: bool = False
    log_row: Optional[int] = None


_STATES: Dict[str, SessionState] = {}
_LOCK = threading.Lock()


def get_state(session_id: str, channel: str = "chat", caller_phone: Optional[str] = None) -> SessionState:
    with _LOCK:
        st = _STATES.get(session_id)
        if st is None:
            st = _STATES[session_id] = SessionState(session_id=session_id, channel=channel, caller_phone=caller_phone)
        elif caller_phone and not st.caller_phone:
            st.caller_phone = caller_phone
        return st


def drop_state(session_id: str) -> None:
    with _LOCK:
        _STATES.pop(session_id, None)
