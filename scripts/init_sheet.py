import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools import sheets


def ensure_tab(sh, title, headers, clear=False):
    try:
        ws = sh.worksheet(title)
        if clear:
            ws.clear()
    except Exception:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
        print(f"created tab {title}")
    ws.update(range_name="A1", values=[headers], value_input_option="RAW")
    ws.format(f"A1:{chr(ord('A') + len(headers) - 1)}1", {"textFormat": {"bold": True}})
    ws.freeze(rows=1)
    return ws


def main(clear=False):
    sh = sheets.get_spreadsheet()
    ensure_tab(sh, sheets.CONTACTS_TAB, sheets.CONTACT_COLUMNS, clear=clear)
    ensure_tab(sh, sheets.CALLLOG_TAB, sheets.CALLLOG_COLUMNS, clear=clear)
    for ws in sh.worksheets():
        if ws.title in ("Sheet1", "Hoja 1") and not any(ws.get_all_values()):
            sh.del_worksheet(ws)
            print(f"removed empty {ws.title}")
    print(f"Sheet ready: https://docs.google.com/spreadsheets/d/{sh.id}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--clear", action="store_true", help="Clear all rows except headers")
    args = parser.parse_args()
    main(clear=args.clear)
