from __future__ import annotations

import re


def normalize_phone(raw: str) -> str:
    s = (raw or "").strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if s.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    raise ValueError(f"'{raw}' doesn't look like a valid phone number (need 10 digits).")


def format_phone(e164: str) -> str:
    d = re.sub(r"\D", "", e164 or "")
    if len(d) == 11 and d.startswith("1"):
        return f"({d[1:4]}) {d[4:7]}-{d[7:]}"
    return e164
