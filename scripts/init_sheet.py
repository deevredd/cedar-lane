"""Create the Contacts and CallLog tabs with headers. Safe to re-run."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools import sheets  # noqa: E402


def ensure_tab(sh, title, headers):
    try:
        ws = sh.worksheet(title)
    except Exception:  # gspread.WorksheetNotFound
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
        print(f"created tab {title}")
    ws.update(range_name="A1", values=[headers], value_input_option="RAW")
    ws.format(f"A1:{chr(ord('A') + len(headers) - 1)}1", {"textFormat": {"bold": True}})
    ws.freeze(rows=1)
    return ws


def main():
    sh = sheets.spreadsheet()
    ensure_tab(sh, sheets.CONTACTS, sheets.CONTACT_HEADERS)
    ensure_tab(sh, sheets.CALLLOG, sheets.CALLLOG_HEADERS)
    for ws in sh.worksheets():
        if ws.title in ("Sheet1", "Hoja 1") and not any(ws.get_all_values()):
            sh.del_worksheet(ws)
            print(f"removed empty {ws.title}")
    print(f"Sheet ready: https://docs.google.com/spreadsheets/d/{sh.id}")


if __name__ == "__main__":
    main()
