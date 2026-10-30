#!/usr/bin/env python3
"""
Add campaign rows to the Pacing Tracker - and the two tabs that mirror it row by row.

A new row is not one row. Suggestions Tracker (which the digest's queues read) and Alerts
each point at Pacing Tracker rows by number, so a row added only to Pacing Tracker never
reaches the digest. This adds all three, and inserts inside the existing block so every
$3:$N range in the sheet (headline, frontier, rank columns) expands to include it.

Dry run by default: prints the plan and changes nothing. --apply writes.

    PACING_SHEET_ID=... GSHEET_SA_KEYFILE=... python3 add_tracker_rows.py [--apply]

Edit NEW_ROWS / RENAMES below for the next change. Re-running is safe: rows whose name
is already in column B, and renames already done, are skipped.
"""
import os, re, sys
import gspread

import pacing_tracker_daily as P

ADDED = "2026-10-30"

# (platform, campaign name, channel type, daily budget $, note). Budgets read from Google
# Ads campaign_budget.amount_micros on the date above. MTD/L30D columns start at $0 and
# the routine fills them on its next run - each name is pinned in campaign_ids.json.
NEW_ROWS = [
    ("Google Ads", "Google Ads | ATU | Demand Gen | Remarketing | UK | EN", "Demand Gen", 200.0,
     "Launched 2026-10-29, replacing the paused DNU ATU Remarketing UK campaign (id 24401035329)."),
    ("Google Ads", "Google Ads | AT2026 | Demand Gen | Remarketing | AUS | EN", "Demand Gen", 200.0,
     "Launched 2026-10-29, replacing the paused DNU AT2026 Remarketing AUS campaign (id 24403115748)."),
    ("Google Ads", "Google Ads | AT2026 | Demand Gen | Prospecting | AUS | EN", "Demand Gen", 100.0,
     "Launched 2026-10-30, replacing the paused DNU AT2026 Prospecting AUS campaign (id 24407202164)."),
]

# Tracker rows whose campaign was renamed on-platform: (old name, new name). The ID link
# is unaffected (both names are in campaign_ids.json); this only keeps the label honest.
RENAMES = [
    ("Google Ads | ATA | Video | Views | NA | EN", "Google Ads | ATU | Video | Views | NA | EN"),
]

APPLY = "--apply" in sys.argv
LAST_COL = 25   # Y

def a1(col, row):
    return gspread.utils.rowcol_to_a1(row, col)

def retarget(formula, tracker_row, own_row=None, own_from=None):
    """Point a mirror-tab formula at another Pacing Tracker row (and, for Alerts, its own row)."""
    f = re.sub(r"('Pacing Tracker'!\$?[A-Z]+\$?)\d+", lambda m: f"{m.group(1)}{tracker_row}", formula)
    if own_row is not None:
        f = re.sub(rf"(\$?[A-Z]+){own_from}\b(?!\d)", lambda m: f"{m.group(1)}{own_row}", f)
    return f

def main():
    sh = gspread.authorize(P.sheets_credentials()).open_by_key(P.SHEET_ID)
    tracker = sh.worksheet(P.TAB_TRACKER)
    st = sh.worksheet("Suggestions Tracker")
    alerts = sh.worksheet("Alerts")

    names = tracker.col_values(P.COL["campaign"])
    have = {n for n in names if n}
    todo = [r for r in NEW_ROWS if r[1] not in have]
    renames = [(o, n) for o, n in RENAMES if o in have and n not in have]
    for r in NEW_ROWS:
        if r[1] in have: print(f"[skip] already in tracker: {r[1]}")

    last = P.last_campaign_row(tracker)
    # Template: the nearest Google Ads row - its Incr. Factor (D) is the Config lookup
    # formula. Reddit rows carry a hand-set D and must not be copied.
    plats = tracker.col_values(P.COL["platform"])
    tmpl = next(i for i in range(last, P.DATA_START_ROW - 1, -1) if plats[i-1] == "Google Ads")
    n = len(todo)
    first_new = last                        # new rows go in at `last`, pushing it down
    print(f"[plan] tracker rows {P.DATA_START_ROW}..{last}; insert {n} at row {first_new} "
          f"(inside the block, so $3:${last} ranges become $3:${last+n}); "
          f"template row {tmpl}{' -> ' + str(tmpl+n) if tmpl >= first_new else ''}")
    for k, (plat, name, ctype, budget, note) in enumerate(todo):
        print(f"  + row {first_new+k}: {name}  [{ctype}, ${budget:,.0f}/day]")
    for o, nn in renames:
        print(f"  ~ rename row {names.index(o)+1}: {o}  ->  {nn}")

    # Mirror tabs: one new row each per tracker row, built from their first data row.
    # Alerts is NOT in tracker-row order (its row 50 pointed at tracker row 54 before the
    # 2026-10-30 insert), so rows are appended and own-row refs ($A55) point at themselves.
    st_f = st.get_values(value_render_option="FORMULA")
    st_last = max(i for i, r in enumerate(st_f, 1) if any(str(c).strip() for c in r))
    # First campaign row, by formula: section headers are plain text and must not be copied.
    st_tmpl_row = next(i for i, r in enumerate(st_f, 1) if str(r[0]).startswith("='Pacing Tracker'!"))
    st_tmpl = [str(c) for c in st_f[st_tmpl_row - 1][:9]]
    # By formula, not value: Alerts rows that evaluate to "" read as empty, and appending
    # after the last *visible* row would overwrite live formulas.
    al_f = alerts.get_values(value_render_option="FORMULA")
    al_last = max(i for i, r in enumerate(al_f, 1) if any(str(c).strip() for c in r))
    al_tmpl = alerts.row_values(P.DATA_START_ROW, value_render_option="FORMULA")[:5]
    print(f"  Suggestions Tracker: append rows {st_last+1}..{st_last+n} (pattern from row {st_tmpl_row})")
    print(f"  Alerts: append rows {al_last+1}..{al_last+n} (pattern from row {P.DATA_START_ROW})")

    if not APPLY:
        print("\nDRY RUN - nothing written. Re-run with --apply.")
        return
    if not (todo or renames):
        print("Nothing to do."); return

    if todo:
        sid = tracker.id
        src_row = tmpl + n if tmpl >= first_new else tmpl
        sh.batch_update({"requests": [
            {"insertDimension": {"range": {"sheetId": sid, "dimension": "ROWS",
                                           "startIndex": first_new - 1, "endIndex": first_new - 1 + n},
                                 "inheritFromBefore": True}},
            {"copyPaste": {"source": {"sheetId": sid, "startRowIndex": src_row - 1, "endRowIndex": src_row,
                                      "startColumnIndex": 0, "endColumnIndex": LAST_COL},
                           "destination": {"sheetId": sid, "startRowIndex": first_new - 1,
                                           "endRowIndex": first_new - 1 + n,
                                           "startColumnIndex": 0, "endColumnIndex": LAST_COL},
                           "pasteType": "PASTE_NORMAL"}},
        ]})
        cells = []
        for k, (plat, name, ctype, budget, note) in enumerate(todo):
            r = first_new + k
            cells += [gspread.Cell(r, P.COL["platform"], plat), gspread.Cell(r, P.COL["campaign"], name),
                      gspread.Cell(r, P.COL["ctype"], ctype), gspread.Cell(r, P.COL["mtd"], 0),
                      gspread.Cell(r, P.COL["daily"], budget), gspread.Cell(r, P.COL["l30sp"], 0),
                      gspread.Cell(r, P.COL["l30cv"], 0), gspread.Cell(r, P.COL["grace"], ""),
                      gspread.Cell(r, P.COL["notes"], f"Added {ADDED}. {note}")]
        tracker.update_cells(cells, value_input_option="USER_ENTERED")

        st_rows, al_rows = [], []
        for k in range(n):
            r = first_new + k
            st_rows.append([retarget(c, r) if c.startswith("=") else "" for c in st_tmpl])
            own = al_last + 1 + k
            al_rows.append([retarget(c, r, own, P.DATA_START_ROW) if c.startswith("=") else ""
                            for c in al_tmpl])
        st.update(values=st_rows, range_name=f"A{st_last+1}:{a1(len(st_tmpl), st_last+n)}",
                  value_input_option="USER_ENTERED")
        alerts.update(values=al_rows, range_name=f"A{al_last+1}:{a1(len(al_tmpl), al_last+n)}",
                      value_input_option="USER_ENTERED")

    if renames:
        names = tracker.col_values(P.COL["campaign"])
        tracker.update_cells([gspread.Cell(names.index(o)+1, P.COL["campaign"], nn) for o, nn in renames],
                             value_input_option="RAW")

    # Verify: the new names landed, and the headline ranges grew to cover them.
    names = tracker.col_values(P.COL["campaign"])
    missing = [r[1] for r in todo if r[1] not in names]
    col_a = tracker.col_values(1)
    hl_row = next(i for i, v in enumerate(col_a, 1) if v.startswith("Blended Incremental ROAS"))
    hl = tracker.cell(hl_row, 5, value_render_option="FORMULA").value
    print(f"[done] {n} rows added, {len(renames)} renamed; missing after write: {missing or 'none'}")
    print(f"[check] last campaign row {P.last_campaign_row(tracker)}; headline formula {hl}")
    if f"U{last+n})" not in hl:
        print(f"  [!] headline does not reach row {last+n} - the rows were not inserted inside "
              f"the block; fix the ranges before the next routine run")

if __name__ == "__main__":
    main()
