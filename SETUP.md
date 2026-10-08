# Setup Guide (~20 minutes)

## 0. Install
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 1. Google Cloud service account
1. Go to https://console.cloud.google.com → create a project (e.g. `cedar-lane`).
2. **APIs & Services → Library**: enable **Google Calendar API** and **Google Sheets API**.
3. **APIs & Services → Credentials → Create credentials → Service account**. Name it `receptionist`, skip roles, done.
4. Open the service account → **Keys → Add key → JSON**. Save the file as `service_account.json` in the project root (it's gitignored).
5. Copy the service account email (`receptionist@<project>.iam.gserviceaccount.com`).

## 2. Google Calendar
1. In Google Calendar, **Other calendars → + → Create new calendar**, named "Cedar Lane Detailing". Set its time zone to **Pacific Time**.
2. Calendar **Settings → Share with specific people** → add the service account email with **Make changes to events**.
3. **Integrate calendar → Calendar ID** → paste it into `.env` as `CALENDAR_ID`.

## 3. Google Sheet
1. Create a blank sheet "Cedar Lane CRM" and share it with the service account email as **Editor**.
2. The ID is the part of the URL between `/d/` and `/edit`. Paste it into `.env` as `SHEET_ID`.

## 4. LLM key (free)
- Gemini: https://aistudio.google.com/apikey → put it in `.env` as `LLM_API_KEY`.
- If you hit rate limits: create a Groq key at https://console.groq.com and set
  `LLM_BASE_URL=https://api.groq.com/openai/v1`, `LLM_MODEL=llama-3.3-70b-versatile`.

## 5. Verify, initialize, seed
```bash
python scripts/check_setup.py
python scripts/init_sheet.py
python scripts/seed_calendar.py
```

## 6. Run Phase 1
```bash
uvicorn app.main:app --reload --port 8000
```

## 7. Phase 2 – Vapi
1. Sign up at https://dashboard.vapi.ai (the free credits are enough). Copy your **private API key** into `.env` as `VAPI_API_KEY`.
2. Expose your local server: `brew install ngrok && ngrok config add-authtoken <token> && ngrok http 8000`.
   Put the `https://….ngrok-free.app` URL into `.env` as `PUBLIC_BASE_URL`.
3. `python scripts/vapi_setup.py` → creates the assistant and prints its ID (add it to `.env` as `VAPI_ASSISTANT_ID`).
4. Test with the **Talk to Assistant** button in the dashboard, or **Phone Numbers → Create** (free US Vapi number), assign the assistant, and call it from your phone.
5. Restarting ngrok changes the URL. When that happens, update `PUBLIC_BASE_URL` and re-run `vapi_setup.py`.
