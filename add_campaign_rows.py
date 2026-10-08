#!/usr/bin/env python3
"""
Add campaigns to the Pacing Tracker so the model covers them.

The daily routine matches campaigns to Pacing Tracker rows by exact name and only
writes raw data cells, so a campaign launched (or renamed) on-platform is invisible to
the blended iROAS until it has a row. pacing_tracker_daily.py calls add_rows() to give
new campaigns one automatically; this file is also a CLI for doing it by hand.

What add_rows() does, in order:
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

It skips a name already present, so re-running is safe, and it never edits a formula it
did not create.

Spec entry (JSON list for the CLI):
  {"platform": "Google Ads", "name": "...", "channel_type": "Demand Gen", "daily_budget": 100,
   "replaces": "<optional stale tracker row name>", "notes": "<optional>"}

CLI:
  GSHEET_SA_KEYFILE=... PACING_SHEET_ID=... python3 add_campaign_rows.py spec.json [--dry-run]
"""

import argparse, json, os, re, sys
import gspread
from google.oauth2.service_account import Credentials

TRACKER, ALERTS, SUGG = "Pacing Tracker", "Alerts", "Suggestions Tracker"
START = 3
LAST_COL = 26          # A..Z, the tracker's formula span (columns A..Y are used)

# Tracker columns (1-indexed) that hold data, not formulas.
COL_PLATFORM, COL_NAME, COL_TYPE, COL_MTD, COL_DAILY, COL_L30SP, COL_L30CV = 1, 2, 3, 5, 6, 7, 8
COL_GRACE, COL_NOTES = 19, 20


def infer_channel_type(platform, name):
    """
    Channel type for the incrementality-factor lookup (Config B19:B31, keyed
    "<Google|Microsoft|Meta>|<type>"). Read from the campaign name; anything not
    recognised falls through to the Config default factor, which is the safe direction
    (0.65), and the row's Notes say so.
    """
    n = name.lower()
    if platform == "Meta Ads":
        if "retarget" in n: return "Retargeting"
        if "prospect" in n: return "Prospecting"
        if "advantage" in n or "adv +" in n or "adv+" in n: return "Advantage+"
        return "Prospecting"
    for key, label in (("demand gen", "Demand Gen"), ("pmax", "PMax"), ("performance max", "PMax"),
                       ("shopping", "Shopping"), ("video", "Video"), ("search", "Search")):
        if key in n: return label
    return "Search"


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
    """Last row with anything in `col`, counting formulas that currently display blank.

    Alerts links every row with an IF(...,"") formula, so most of its rows show nothing.
    Reading displayed values would call those rows empty and the append would overwrite
    live links."""
    vals = ws.col_values(col, value_render_option="FORMULA")
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


def add_rows(sh, specs, dry_run=False):
    """Give each spec a tracker row. Returns the names added or repurposed."""
    tr, al, sg = sh.worksheet(TRACKER), sh.worksheet(ALERTS), sh.worksheet(SUGG)

    names = tr.col_values(COL_NAME)
    existing = {n: i + 1 for i, n in enumerate(names) if i + 1 >= START and n}

    repurpose, todo = [], []
    for s_ in specs:
        if s_["name"] in existing:
            print(f"[skip] already in the tracker: {s_['name']}")
        elif s_.get("replaces"):
            if s_["replaces"] not in existing:
                raise ValueError(f"replaces: no tracker row named {s_['replaces']!r}")
            repurpose.append((existing[s_["replaces"]], s_))
        else:
            todo.append(s_)

    last = last_data_row(tr)
    n = len(todo)
    print(f"[plan] tracker data rows {START}-{last}; {len(repurpose)} repurposed, {n} insert(s) at row {last}")
    for row, s_ in repurpose:
        print(f"  repurpose row {row}: {s_['replaces']}  ->  {s_['name']}")
    for s_ in todo:
        print(f"  add: {s_['platform']} | {s_['channel_type']} | ${s_['daily_budget']:,.0f}/day | {s_['name']}")
    if dry_run:
        print("[dry run] nothing written")
        return []

    if repurpose:
        cells = []
        for row, s_ in repurpose:
            cells += row_cells(row, s_)
        tr.update_cells(cells, value_input_option="USER_ENTERED")
    if not n:
        return [s_["name"] for _, s_ in repurpose]

    tid = tr.id
    # Insert inside the range: before the current last row. Rows grow every spanning range.
    reqs = [{"insertDimension": {
        "range": {"sheetId": tid, "dimension": "ROWS", "startIndex": last - 1, "endIndex": last - 1 + n},
        "inheritFromBefore": True}},
        # Copy formulas and formatting from the row above the insertion point.
        {"copyPaste": {
            "source": {"sheetId": tid, "startRowIndex": last - 2, "endRowIndex": last - 1,
                       "startColumnIndex": 0, "endColumnIndex": LAST_COL},
            "destination": {"sheetId": tid, "startRowIndex": last - 1, "endRowIndex": last - 1 + n,
                            "startColumnIndex": 0, "endColumnIndex": LAST_COL},
            "pasteType": "PASTE_NORMAL"}}]
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
    al.update(range_name=f"A{a_first}:E{a_first + n - 1}", values=a_vals, value_input_option="USER_ENTERED")
    sg.update(range_name=f"A{s_first}:J{s_first + n - 1}", values=s_vals, value_input_option="USER_ENTERED")
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
    return [s_["name"] for _, s_ in repurpose] + [s_["name"] for s_ in todo]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sh = gspread.authorize(credentials()).open_by_key(os.environ.get("PACING_SHEET_ID", "REPLACE_WITH_SHEET_ID"))
    try:
        add_rows(sh, json.load(open(args.spec)), dry_run=args.dry_run)
    except ValueError as e:
        sys.exit(str(e))
    if not args.dry_run:
        print("Run the daily routine to populate their spend and conversion value.")


if __name__ == "__main__":
    main()
