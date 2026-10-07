"""Environment-driven settings."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Settings:
    def __init__(self) -> None:
        # LLM (any OpenAI-compatible endpoint; default = Gemini free tier)
        self.llm_base_url = os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
        self.llm_api_key = os.getenv("LLM_API_KEY", "")
        self.llm_model = os.getenv("LLM_MODEL", "gemini-2.5-flash")

        # Google
        sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
        self.google_sa_file = str((ROOT / sa_file) if not os.path.isabs(sa_file) else Path(sa_file))
        self.google_sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        self.calendar_id = os.getenv("CALENDAR_ID", "")
        self.sheet_id = os.getenv("SHEET_ID", "")
        self.shop_timezone = os.getenv("SHOP_TIMEZONE", "America/Los_Angeles")

        # Vapi
        self.vapi_api_key = os.getenv("VAPI_API_KEY", "")
        self.vapi_assistant_id = os.getenv("VAPI_ASSISTANT_ID", "")
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        self.vapi_webhook_secret = os.getenv("VAPI_WEBHOOK_SECRET", "")
        self.vapi_model_provider = os.getenv("VAPI_MODEL_PROVIDER", "openai")
        self.vapi_model = os.getenv("VAPI_MODEL", "gpt-4o-mini")
        self.vapi_voice_provider = os.getenv("VAPI_VOICE_PROVIDER", "vapi")
        self.vapi_voice_id = os.getenv("VAPI_VOICE_ID", "Elliot")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.shop_timezone)


settings = Settings()
