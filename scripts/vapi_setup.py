import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app import business
from app.config import settings
from app.prompt import build_prompt
from app.tools.registry import TOOL_SPECS

API = "https://api.vapi.ai"
NAME = "Cedar Lane Receptionist"

FILLERS = {
    "check_availability": "Let me check…",
    "find_appointments": "One sec, pulling that up…",
    "reschedule_appointment": "Okay, moving that now…",
    "book_appointment": "",
    "cancel_appointment": "",
    "add_note_to_appointment": "",
    "log_interaction": "",
}


def assistant_config():
    server = {"url": f"{settings.public_base_url}/vapi/webhook", "timeoutSeconds": 20}
    if settings.vapi_webhook_secret:
        server["headers"] = {"x-vapi-secret": settings.vapi_webhook_secret}
    tools = []
    for spec in TOOL_SPECS:
        tool = {"type": "function", "function": spec, "server": server}
        if FILLERS.get(spec["name"]):
            tool["messages"] = [{"type": "request-start", "content": FILLERS[spec["name"]]}]
        tools.append(tool)
    tools.append({"type": "endCall"})

    return {
        "name": NAME,
        "firstMessage": f"Hi, thanks for calling {business.SHOP_NAME}, this is {business.AGENT_NAME}. How can I help?",
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": settings.vapi_model_provider,
            "model": settings.vapi_model,
            "temperature": 0.3,
            "maxTokens": 200,
            "messages": [{"role": "system", "content": build_prompt("voice")}],
            "tools": tools,
        },
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "en"},
        "voice": {"provider": settings.vapi_voice_provider, "voiceId": settings.vapi_voice_id},
        "startSpeakingPlan": {
            "waitSeconds": 0.4,
            "smartEndpointingPlan": {"provider": "livekit"},
        },
        "stopSpeakingPlan": {"numWords": 2, "voiceSeconds": 0.2, "backoffSeconds": 1},
        "backgroundSound": "office",
        "silenceTimeoutSeconds": 20,
        "maxDurationSeconds": 600,
        "endCallMessage": "Thanks for calling, bye!",
        "server": server,
        "serverMessages": ["end-of-call-report", "tool-calls"],
    }


def main():
    if not settings.vapi_api_key or not settings.public_base_url:
        sys.exit("Set VAPI_API_KEY and PUBLIC_BASE_URL in .env first.")
    h = {"Authorization": f"Bearer {settings.vapi_api_key}"}
    cfg = assistant_config()
    aid = settings.vapi_assistant_id
    if not aid:
        existing = httpx.get(f"{API}/assistant", headers=h, params={"limit": 100}, timeout=30).json()
        aid = next((a["id"] for a in existing if a.get("name") == NAME), "")
    if aid:
        r = httpx.patch(f"{API}/assistant/{aid}", headers=h, json=cfg, timeout=30)
    else:
        r = httpx.post(f"{API}/assistant", headers=h, json=cfg, timeout=30)
    if r.status_code >= 400:
        print(json.dumps(cfg, indent=2)[:2000])
        sys.exit(f"Vapi error {r.status_code}: {r.text}")
    a = r.json()
    print(f"{'Updated' if aid else 'Created'} assistant {a['id']}")
    print(f"Webhook: {cfg['server']['url']}")
    print(f"Dashboard: https://dashboard.vapi.ai/assistants/{a['id']}")
    if not settings.vapi_assistant_id:
        print(f"Tip: add VAPI_ASSISTANT_ID={a['id']} to .env")


if __name__ == "__main__":
    main()
