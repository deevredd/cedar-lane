"""Verify credentials and access: Calendar, Sheet, LLM. Run this first after filling in .env."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import settings


def check(label, fn):
    try:
        print(f"[ OK ] {label}: {fn()}")
        return True
    except Exception as exc:
        print(f"[FAIL] {label}: {exc}")
        return False


def cal():
    from app.tools.gcal import _api
    c = _api().calendars().get(calendarId=settings.calendar_id).execute()
    return f"calendar '{c.get('summary')}' (tz {c.get('timeZone')})"


def sheet():
    from app.tools.sheets import spreadsheet
    sh = spreadsheet()
    return f"sheet '{sh.title}', tabs={[w.title for w in sh.worksheets()]}"


def llm():
    r = httpx.post(settings.llm_base_url.rstrip("/") + "/chat/completions",
                   headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                   json={"model": settings.llm_model, "messages": [{"role": "user", "content": "Say OK"}]},
                   timeout=60)
    r.raise_for_status()
    return f"{settings.llm_model}: {r.json()['choices'][0]['message']['content']!r}"


if __name__ == "__main__":
    ok = all([check("Google Calendar", cal), check("Google Sheet", sheet), check("LLM", llm)])
    sys.exit(0 if ok else 1)
