from __future__ import annotations

import json
from functools import lru_cache

from google.oauth2 import service_account

from app.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
]


@lru_cache(maxsize=1)
def get_credentials():
    if settings.google_sa_json:
        return service_account.Credentials.from_service_account_info(json.loads(settings.google_sa_json), scopes=SCOPES)
    return service_account.Credentials.from_service_account_file(settings.google_sa_file, scopes=SCOPES)
