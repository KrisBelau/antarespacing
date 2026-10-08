#!/usr/bin/env python3
"""
Add campaigns to the Pacing Tracker so the model covers them.

The daily routine matches campaigns to Pacing Tracker rows by exact name and only
writes raw data cells, so a campaign launched (or renamed) on-platform is invisible to
the blended iROAS until someone gives it a row. The routine flags these every run
(see the coverage block in pacing_tracker_daily.py). This script is the on-demand fix.

What it does, in order:
  1. Repurpose stale rows. A spec entry with "replaces": "<old name>" takes over that
     tracker row in place (a campaign renamed or ended on-platform). Its formulas and
     its links on Alerts and Suggestions Tracker are untouched, so nothing is inserted
     or deleted.
  2. Insert one tracker row per new campaign INSIDE the data range (before the last
     data row), so every range in the workbook that spans the data grows with it: the
     headline block, iROAS Frontier, the Pacing Curve projection, the sorted prototype.
     Inserting after the last row would leave those ranges one row short.
  3. Copy the formulas and formatting of the row above into each new row, then set
     platform, name, channel type, daily budget and zero the raw data cells (the daily
     routine fills them on its next run).
  4. Append a matching row to Alerts and to Suggestions Tracker. Both are linked to
     tracker rows one by one, so a tracker row with no link there never reaches the
     digest queues.

It refuses to run on a name already present, so re-running is safe. It never edits a
formula it did not create. Use --dry-run to print the plan.

Spec file (JSON list):
  [{"platform": "Google Ads", "name": "...", "channel_type": "Demand Gen", "daily_budget": 100,
    "replaces": "<optional stale tracker row name>", "notes": "<optional>"}]

Usage:
  GSHEET_SA_KEYFILE=... PACING_SHEET_ID=... python3 add_campaign_rows.py spec.json [--dry-run]
"""

import argparse, json, os, re, sys
import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = os.environ.get("PACING_SHEET_ID", "REPLACE_WITH_SHEET_ID")
TRACKER, ALERTS, SUGG = "Pacing Tracker", "Alerts", "Suggestions Tracker"
START = 3
LAST_COL = 26          # A..Z, the tracker's formula span (columns A..Y are used)

# Tracker columns (1-indexed) that hold data, not formulas.
COL_PLATFORM, COL_NAME, COL_TYPE, COL_MTD, COL_DAILY, COL_L30SP, COL_L30CV = 1, 2, 3, 5, 6, 7, 8
COL_GRACE, COL_NOTES = 19, 20


def credentials():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if os.environ.get("GSHEET_SA_KEY_JSON"):
        return Credentials.from_service_account_info(json.loads(os.environ["GSHEET_SA_KEY_JSON"]), scopes=scopes)
    if os.environ.get("GSHEET_SA_KEYFILE"):
        return Credentials.from_service_account_file(os.environ["GSHEET_SA_KEYFILE"], scopes=scopes)
    sys.exit("No credentials: set GSHEET_SA_KEY_JSON or GSHEET_SA_KEYFILE.")


def last_data_row(tracker):
    """Last row of the contiguous campaign block starting at START (names are in col B)."""
    names = tracker.col_values(COL_NAME)
    row = START - 1
    for i in range(START - 1, len(names)):
        if not names[i]:
            break
        row = i + 1
    return row


def last_filled_row(ws, col=1):
    vals = ws.col_values(col)
    return max((i + 1 for i, v in enumerate(vals) if v), default=0)


def row_cells(row, s):
    """The data cells of one tracker row. Raw spend and value start at 0; the routine fills them."""
    return [gspread.Cell(row, COL_PLATFORM, s["platform"]), gspread.Cell(row, COL_NAME, s["name"]),
            gspread.Cell(row, COL_TYPE, s["channel_type"]), gspread.Cell(row, COL_MTD, 0),
            gspread.Cell(row, COL_DAILY, s["daily_budget"]), gspread.Cell(row, COL_L30SP, 0),
            gspread.Cell(row, COL_L30CV, 0), gspread.Cell(row, COL_GRACE, ""),
            gspread.Cell(row, COL_NOTES, s.get("notes", ""))]


def relink(template, old_row, new_row):
    """Rewrite one template formula that points at tracker row `old_row` to `new_row`."""
    return re.sub(rf"(!\$?[A-Z]+\$?){old_row}\b", rf"\g<1>{new_row}", template)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    specs = json.load(open(args.spec))
    sh = gspread.authorize(credentials()).open_by_key(SHEET_ID)
    tr, al, sg = sh.worksheet(TRACKER), sh.worksheet(ALERTS), sh.worksheet(SUGG)

    names = tr.col_values(COL_NAME)
    existing = {n: i + 1 for i, n in enumerate(names) if i + 1 >= START and n}

    taken = set(existing)
    repurpose, todo = [], []
    for s_ in specs:
        if s_["name"] in taken:
            print(f"[skip] already in the tracker: {s_['name']}")
        elif s_.get("replaces"):
            if s_["replaces"] not in existing:
                sys.exit(f"replaces: no tracker row named {s_['replaces']!r}")
            repurpose.append((existing[s_["replaces"]], s_))
        else:
            todo.append(s_)

    last = last_data_row(tr)
    n = len(todo)
    print(f"[plan] tracker data rows {START}-{last}; {len(repurpose)} repurposed, {n} insert(s) at row {last}")
    for row, s_ in repurpose:
        print(f"  repurpose row {row}: {s_['replaces']}  ->  {s_['name']}")
    for s in todo:
        print(f"  add: {s['platform']} | {s['channel_type']} | ${s['daily_budget']}/day | {s['name']}")
    if args.dry_run:
        print("[dry run] nothing written")
        return

    cells = []
    for row, s_ in repurpose:
        cells += row_cells(row, s_)
    if repurpose:
        tr.update_cells(cells, value_input_option="USER_ENTERED")
    if not n:
        return

    tid = tr.id
    # Insert inside the range: before the current last row. Rows grow every spanning range.
    reqs = [{"insertDimension": {
        "range": {"sheetId": tid, "dimension": "ROWS", "startIndex": last - 1, "endIndex": last - 1 + n},
        "inheritFromBefore": True}}]
    # Copy formulas and formatting from the row above the insertion point.
    reqs.append({"copyPaste": {
        "source": {"sheetId": tid, "startRowIndex": last - 2, "endRowIndex": last - 1,
                   "startColumnIndex": 0, "endColumnIndex": LAST_COL},
        "destination": {"sheetId": tid, "startRowIndex": last - 1, "endRowIndex": last - 1 + n,
                        "startColumnIndex": 0, "endColumnIndex": LAST_COL},
        "pasteType": "PASTE_NORMAL"}})
    sh.batch_update({"requests": reqs})

    new_rows = list(range(last, last + n))
    cells = []
    for row, s_ in zip(new_rows, todo):
        cells += row_cells(row, s_)
    tr.update_cells(cells, value_input_option="USER_ENTERED")

    # Alerts and Suggestions Tracker link to tracker rows one by one. Append new links.
    a_tpl = al.get_values(f"A{START}:E{START}", value_render_option="FORMULA")[0]
    a_first = last_filled_row(al) + 1
    s_first = last_filled_row(sg) + 1
    s_src = s_first - 1
    s_tpl_row = sg.get_values(f"A{s_src}:J{s_src}", value_render_option="FORMULA")[0]
    s_old = int(re.search(r"!A(\d+)", s_tpl_row[0]).group(1))

    a_vals, s_vals = [], []
    for k, trow in enumerate(new_rows):
        ar = a_first + k
        row = [relink(c, START, trow) for c in a_tpl]
        row = [re.sub(rf"\$A{START}\b", f"$A{ar}", c) for c in row]
        a_vals.append(row)
        s_vals.append([relink(c, s_old, trow) if isinstance(c, str) else c for c in s_tpl_row])
    al.update(f"A{a_first}:E{a_first + n - 1}", a_vals, value_input_option="USER_ENTERED")
    sg.update(f"A{s_first}:J{s_first + n - 1}", s_vals, value_input_option="USER_ENTERED")
    sh.batch_update({"requests": [
        {"copyPaste": {"source": {"sheetId": al.id, "startRowIndex": a_first - 2, "endRowIndex": a_first - 1,
                                  "startColumnIndex": 0, "endColumnIndex": 5},
                       "destination": {"sheetId": al.id, "startRowIndex": a_first - 1, "endRowIndex": a_first - 1 + n,
                                       "startColumnIndex": 0, "endColumnIndex": 5},
                       "pasteType": "PASTE_FORMAT"}},
        {"copyPaste": {"source": {"sheetId": sg.id, "startRowIndex": s_src - 1, "endRowIndex": s_src,
                                  "startColumnIndex": 0, "endColumnIndex": 10},
                       "destination": {"sheetId": sg.id, "startRowIndex": s_first - 1, "endRowIndex": s_first - 1 + n,
                                       "startColumnIndex": 0, "endColumnIndex": 10},
                       "pasteType": "PASTE_FORMAT"}}]})
    print(f"[done] tracker rows {new_rows[0]}-{new_rows[-1]}; Alerts rows {a_first}-{a_first + n - 1}; "
          f"Suggestions Tracker rows {s_first}-{s_first + n - 1}")
    print("Run the daily routine to populate their spend and conversion value.")


if __name__ == "__main__":
    main()
