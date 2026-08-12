#!/usr/bin/env python3
"""
AutoTune / Antares - Pacing & Incremental-ROAS Tracker - DAILY ROUTINE
======================================================================
Objective the sheet encodes: MAXIMIZE SPEND while blended incremental ROAS >= floor (1.25).
Budget is a guardrail, not the target.

What this routine does each morning:
  1. Pull MTD + L30D spend/conv-value per campaign from Windsor.ai for all 3 platforms
       - google_ads   (account 802-485-7603)  conv field: conversions_value
       - bing         (Antares Adwords)        conv field: revenue
       - facebook     (Antares Ads)            conv field: action_values_omni_purchase
  2. Pull the trailing-30d DAILY spend series (Google) to compute the SPEND-WEIGHTED
     conversion-lag gross-up multiplier from the maturation curve in the sheet.
  3. Write per-campaign raw data into the Pacing Tracker tab (cols C,F,G,H). The sheet's
     own formulas recompute lag-adjusted ROAS, iROAS, bands, suggested budgets, headline.
  4. Write the spend-weighted multiplier into Config so lag adjustment stays current.
  5. Snapshot today's cumulative spend into Pacing Curve.
  6. (Optional) If a fresh Google "Time Lag" CSV export is dropped in TIMELAG_DIR,
     re-derive the maturation curve and overwrite the curve cells in Config.
  7. Post a Slack digest: actual blended iROAS vs floor, guardrail status, potential upside,
     and the three review queues (Cut or Fix / Raise / Reduce). All suggestions are review-gated.

Confidence notes (read before trusting output):
  - Lag multiplier is SPEND-WEIGHTED: it weights each day's maturity by that day's actual
    spend, so it self-corrects for spend ramps. Recompute daily; never hardcode.
  - Maturation curve is account-level (one blended curve for all channels). If channel-level
    lag differs materially, pull separate Time Lag reports per channel and extend the curve.
  - Meta uses lifetime/CBO budgets; the daily-budget baseline is approximated as L30D spend/30.
  - The 12+ day Time Lag bucket is spread across days 12-27 with a documented decay assumption;
    it lands in the already-mature region so the L30D multiplier is insensitive to it.
  - Use a FULLY MATURE Time Lag window to derive the curve. A recent window (e.g. last 7 days)
    understates its own tail because those conversions have not finished booking yet.
"""

import os, csv, glob, json, re, datetime as dt
import gspread
import requests
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------------- CONFIG
SHEET_ID        = os.environ.get("PACING_SHEET_ID", "REPLACE_WITH_SHEET_ID")
SA_KEYFILE      = os.environ["GSHEET_SA_KEYFILE"]
SLACK_CHANNEL   = os.environ.get("PACING_SLACK", "#autotune-pacing")
TIMELAG_DIR     = os.environ.get("TIMELAG_DIR", "/tmp/timelag")   # drop new exports here
DRY_RUN         = os.environ.get("PACING_DRY_RUN", "").lower() in ("1", "true", "yes")

GOOGLE_CID   = "802-485-7603"
BING_ACCOUNT = "Antares Adwords"     # account_name filter value
META_ACCOUNT = "Antares Ads"

TAB_TRACKER="Pacing Tracker"; TAB_CURVE="Pacing Curve"; TAB_CONFIG="Config"
DATA_START_ROW=3

# Column map on Pacing Tracker (1-indexed). Routine writes only C(mtd), F(daily, Meta only), G(l30sp), H(l30cv).
COL=dict(platform=1,campaign=2,ctype=3,factor=4,mtd=5,daily=6,l30sp=7,l30cv=8,
         l30roas=9,iroas=10,ivsf=11,band=12,sugg=13,dbud=14,exp=15,pace=16,proj=17,
         action=18,grace=19,notes=20)

TODAY = dt.date.today()

# ================================================================ WINDSOR
def windsor_cache_key(connector, fields, date_preset=None):
    """
    Stable name for one Windsor pull. The seven calls this routine makes are fully
    distinguished by connector + window, so the slash command can populate the cache
    without reconstructing argument tuples:
        google_ads:mtd  google_ads:l30  google_ads:daily
        bing:mtd        bing:l30
        facebook:mtd    facebook:l30
    """
    if date_preset:            window = "l30"
    elif "date" in fields:     window = "daily"
    else:                      window = "mtd"
    return f"{connector}:{window}"

def windsor_get(connector, fields, date_from=None, date_to=None, date_preset=None,
                accounts=None, filters=None):
    """
    Resolve one Windsor pull. Two sources, in priority order:

    1. WINDSOR_CACHE - a JSON file {cache_key: [row, ...]} written by the caller.
       This is how /antares-pacing-refresh runs it: the agent makes the
       Windsor.ai:get_data MCP calls and drops the results here. MCP tools are only
       callable from an agent session, not from a bare python process.
    2. WINDSOR_API_KEY - direct REST against the Windsor API, so the same file can
       run headless under cron with no agent in the loop.

    Field names are verified live against get_fields (2026-08-12):
      google_ads: spend, conversions_value | bing: spend, revenue |
      facebook: spend, action_values_omni_purchase   (NOT conversions_value)
    """
    key = windsor_cache_key(connector, fields, date_preset)

    cache_path = os.environ.get("WINDSOR_CACHE")
    if cache_path and os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)
        if key in cache:
            return cache[key]
        raise KeyError(f"WINDSOR_CACHE {cache_path} has no entry {key!r}; "
                       f"present: {sorted(cache)}")

    api_key = os.environ.get("WINDSOR_API_KEY")
    if not api_key:
        raise RuntimeError(
            f"No data source for {key}. Set WINDSOR_CACHE (agent-populated) or "
            f"WINDSOR_API_KEY (headless REST).")

    params = {"api_key": api_key, "fields": ",".join(fields), "_renderer": "json"}
    if date_preset: params["date_preset"] = date_preset
    if date_from:   params["date_from"] = date_from
    if date_to:     params["date_to"] = date_to
    if accounts:    params["accounts"] = ",".join(accounts)
    if filters:     params["filters"] = json.dumps(filters)
    r = requests.get(f"https://connectors.windsor.ai/{connector}", params=params, timeout=120)
    r.raise_for_status()
    return r.json().get("data", [])

def pull_platform_mtd_and_l30():
    """Return {campaign: {platform, mtd_spend, mtd_cv, l30_spend, l30_cv}} across all 3 platforms."""
    m = TODAY.replace(day=1).isoformat(); t = TODAY.isoformat()
    out = {}
    def merge(rows, plat, spend_f, cv_f, window):
        for r in rows:
            name=r.get("campaign")
            if not name: continue
            d=out.setdefault(name, {"platform":plat,"mtd_spend":0,"mtd_cv":0,"l30_spend":0,"l30_cv":0})
            d["platform"]=plat
            d[f"{window}_spend"]=float(r.get(spend_f) or 0)
            d[f"{window}_cv"]=float(r.get(cv_f) or 0)
    merge(windsor_get("google_ads",["campaign","spend","conversions_value"],date_from=m,date_to=t,accounts=[GOOGLE_CID]),
          "Google Ads","spend","conversions_value","mtd")
    merge(windsor_get("google_ads",["campaign","spend","conversions_value"],date_preset="last_30d",accounts=[GOOGLE_CID]),
          "Google Ads","spend","conversions_value","l30")
    merge(windsor_get("bing",["campaign","spend","revenue"],date_from=m,date_to=t,filters=[["account_name","eq",BING_ACCOUNT]]),
          "Microsoft Ads","spend","revenue","mtd")
    merge(windsor_get("bing",["campaign","spend","revenue"],date_preset="last_30d",filters=[["account_name","eq",BING_ACCOUNT]]),
          "Microsoft Ads","spend","revenue","l30")
    merge(windsor_get("facebook",["campaign","spend","action_values_omni_purchase"],date_from=m,date_to=t,filters=[["account_name","eq",META_ACCOUNT]]),
          "Meta Ads","spend","action_values_omni_purchase","mtd")
    merge(windsor_get("facebook",["campaign","spend","action_values_omni_purchase"],date_preset="last_30d",filters=[["account_name","eq",META_ACCOUNT]]),
          "Meta Ads","spend","action_values_omni_purchase","l30")
    return out

def pull_google_daily_spend():
    """Trailing-30d daily spend (Google) -> {date: spend} for the spend-weighted multiplier.

    Google only, deliberately: the maturation curve is derived from the Google Time Lag
    report, so the gross-up it feeds is a Google-shaped correction.
    """
    start=(TODAY-dt.timedelta(days=29)).isoformat()
    rows=windsor_get("google_ads",["date","spend"],date_from=start,date_to=TODAY.isoformat(),accounts=[GOOGLE_CID])
    return {r["date"]:float(r.get("spend") or 0) for r in rows if r.get("date")}

def pull_daily_spend_all_platforms():
    """
    Trailing-30d daily spend summed across all three platforms -> {date: spend}.

    Feeds the month-to-date cumulative series on Pacing Curve. Separate from
    pull_google_daily_spend() because that one is Google-only on purpose (see above),
    whereas the pacing curve tracks total account spend against the budget guardrail.
    """
    start=(TODAY-dt.timedelta(days=29)).isoformat(); end=TODAY.isoformat()
    totals={}
    def add(rows):
        for r in rows:
            d=r.get("date")
            if d: totals[d]=totals.get(d,0.0)+float(r.get("spend") or 0)
    add(windsor_get("google_ads",["date","spend"],date_from=start,date_to=end,accounts=[GOOGLE_CID]))
    add(windsor_get("bing",["date","spend"],date_from=start,date_to=end,filters=[["account_name","eq",BING_ACCOUNT]]))
    add(windsor_get("facebook",["date","spend"],date_from=start,date_to=end,filters=[["account_name","eq",META_ACCOUNT]]))
    return totals

# ================================================================ LAG MATH
def spend_weighted_multiplier(curve, daily_spend):
    """
    gross-up = sum(spend) / sum(spend * maturity(age)).
    Weighting each day's maturity by that day's spend self-corrects for spend ramps:
    when recent (under-matured) days carry more spend, the undercount - and the gross-up - rises.
    """
    def mat(age): return 1.0 if (age>=28 or age<0) else curve.get(age,1.0)
    num=sum(daily_spend.values()); den=0.0
    for d,sp in daily_spend.items():
        age=(TODAY-dt.date.fromisoformat(d)).days
        den+=sp*mat(age)
    return (num/den) if den else 1.0

# ================================================================ CURVE (from Time Lag export)
def parse_timelag_curve(path):
    """Derive cumulative maturation curve {day:frac} from a Google Time Lag CSV (by conv value)."""
    rows=[]
    with open(path) as f:
        started=False
        for line in f:
            line=line.rstrip("\n")
            if line.startswith("Hours to conversion"): started=True; continue
            if not started: continue
            p=list(csv.reader([line]))[0]
            if len(p)<3: continue
            try: rows.append((p[0].strip(), float(p[2])))
            except ValueError: continue
    if not rows: return None
    def day_of(b):
        if b.startswith("<1"): return 0
        if b.startswith("12+"): return 12
        return int(b.split()[0])
    by_day={}
    for b,v in rows: by_day[day_of(b)]=by_day.get(day_of(b),0)+v
    tail=by_day.pop(12,0.0)
    w=[0.85**i for i in range(16)]; ws_=sum(w)          # decay spread over days 12..27
    for i in range(16): by_day[12+i]=by_day.get(12+i,0)+tail*w[i]/ws_
    daily={d:by_day.get(d,0.0) for d in range(28)}
    grand=sum(daily.values()) or 1.0
    cum=0.0; curve={}
    for d in range(28):
        cum+=daily[d]; curve[d]=round(cum/grand,4)
    return curve

MIN_MATURITY_DAYS = int(os.environ.get("TIMELAG_MIN_MATURITY_DAYS", "28"))

def timelag_window_end(path):
    """End date of a Time Lag export, from its '# Date Range: ... - Mon D, YYYY' header."""
    with open(path) as f:
        head = f.read(2048)
    mm = re.search(r"#\s*Date Range:\s*.+?-\s*([A-Z][a-z]{2} \d{1,2}, \d{4})", head)
    if not mm:
        return None
    try:
        return dt.datetime.strptime(mm.group(1), "%b %d, %Y").date()
    except ValueError:
        return None

def refresh_curve_from_timelag():
    """
    Return newest-export curve dict, or None if no usable export is present.

    Guard: an export whose window ends less than MIN_MATURITY_DAYS ago has not
    finished booking its own tail, so deriving a curve from it OVERSTATES early
    maturity and understates the gross-up. Skipping is the safe failure: the
    routine falls back to the existing Config curve rather than corrupting it.
    Set TIMELAG_MIN_MATURITY_DAYS=0 to override.
    """
    files = sorted(glob.glob(os.path.join(TIMELAG_DIR, "*.csv")), key=os.path.getmtime)
    for path in reversed(files):
        end = timelag_window_end(path)
        if end is None:
            print(f"[timelag] {os.path.basename(path)}: no parseable '# Date Range' header - skipped")
            continue
        age = (TODAY - end).days
        if age < MIN_MATURITY_DAYS:
            print(f"[timelag] {os.path.basename(path)}: window ends {end} ({age}d ago), "
                  f"needs {MIN_MATURITY_DAYS}d to mature - skipped, keeping existing curve")
            continue
        print(f"[timelag] {os.path.basename(path)}: window ends {end} ({age}d ago) - refreshing curve")
        return parse_timelag_curve(path)
    return None

# ================================================================ SHEET I/O
def _find_label_row(ws, needle):
    for i,v in enumerate(ws.col_values(2)):
        if v and needle.lower() in v.lower(): return i+1
    return None

def read_curve(cfg):
    """Read the 28-day curve from Config (day index in col B, % booked in col C)."""
    b=cfg.col_values(2); c=cfg.col_values(3); curve={}
    for a,val in zip(b,c):
        try: day=int(a)
        except (TypeError,ValueError): continue
        if 0<=day<=27 and val not in (None,""):
            try:
                x=float(str(val).strip().rstrip("%"))
                curve[day]=x/100.0 if x>1.5 else x
            except ValueError: pass
    return curve

def write_curve(cfg, curve):
    hdr=None
    for i,v in enumerate(cfg.col_values(3)):
        if v and "% Booked" in str(v): hdr=i+1; break
    if not hdr: return
    cfg.update_cells([gspread.Cell(hdr+1+d,3,curve[d]) for d in range(28)],
                     value_input_option="USER_ENTERED")

def write_multiplier(cfg, mult):
    r=_find_label_row(cfg,"gross-up")
    if r: cfg.update_cell(r,3,round(mult,4))

def mtd_series_cells(daily_all):
    """
    Cumulative month-to-date spend for day 1..today -> Pacing Curve col D cells.

    Rewriting the whole series each run rather than only today's cell makes the column
    self-healing: a missed run, a late-restating platform, or a backfill all correct
    themselves on the next refresh. Row for day N is N+2 (day 1 -> row 3).

    Returns (cells, cumulative_total, days_missing) where days_missing lists MTD dates
    absent from the feed - those contribute 0 and would flat-line the curve, so the
    caller surfaces them rather than writing a silently wrong series.
    """
    month_start=TODAY.replace(day=1)
    cum=0.0; cells=[]; missing=[]
    for n in range(1, TODAY.day+1):
        d=(month_start+dt.timedelta(days=n-1)).isoformat()
        if d not in daily_all: missing.append(d)
        cum+=daily_all.get(d,0.0)
        cells.append(gspread.Cell(n+2,4,round(cum,2)))
    return cells, cum, missing

def stamp_lastrun(cfg):
    r=_find_label_row(cfg,"Last routine run")
    if r: cfg.update_cell(r,3,dt.datetime.now().strftime("%Y-%m-%d %H:%M %Z"))

# ================================================================ SLACK
def post_slack(text):
    """
    Emit the digest. Two sinks, in priority order:

    1. SLACK_OUT - write the rendered text to this path and stop. This is how
       /antares-pacing-refresh runs it: the agent reads the file and posts via
       Slack:slack_send_message(channel=SLACK_CHANNEL, text=...).
    2. SLACK_BOT_TOKEN - post directly via chat.postMessage, so the same file can
       run headless under cron.

    With neither set, the digest goes to stdout and nothing is posted - deliberate,
    so a misconfigured run is silent rather than posting somewhere unintended.
    """
    out_path = os.environ.get("SLACK_OUT")
    if out_path:
        with open(out_path, "w") as f:
            f.write(text)
        print(f"[digest written to {out_path} for agent to post to {SLACK_CHANNEL}]")
        return

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("[no SLACK_OUT or SLACK_BOT_TOKEN set - digest not posted]\n")
        print(text)
        return

    r = requests.post("https://slack.com/api/chat.postMessage",
                      headers={"Authorization": f"Bearer {token}"},
                      json={"channel": SLACK_CHANNEL, "text": text}, timeout=30)
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Slack post failed: {body.get('error')}")

# ================================================================ MAIN
def main():
    creds=Credentials.from_service_account_file(SA_KEYFILE, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc=gspread.authorize(creds); sh=gc.open_by_key(SHEET_ID)
    tracker=sh.worksheet(TAB_TRACKER); cfg=sh.worksheet(TAB_CONFIG)

    data=pull_platform_mtd_and_l30()
    daily_spend=pull_google_daily_spend()      # Google only -> lag gross-up
    daily_all=pull_daily_spend_all_platforms() # all three  -> pacing curve series

    new_curve=refresh_curve_from_timelag()
    if new_curve:
        if not DRY_RUN: write_curve(cfg,new_curve)
        curve=new_curve
    else:
        curve=read_curve(cfg)
    mult=spend_weighted_multiplier(curve,daily_spend)

    names=tracker.col_values(COL["campaign"])
    row_for={n:i+1 for i,n in enumerate(names) if i+1>=DATA_START_ROW and n}
    cells=[]
    matched=[]
    for name,wrow in row_for.items():
        d=data.get(name)
        if not d: continue
        matched.append(name)
        cells.append(gspread.Cell(wrow,COL["mtd"],   round(d["mtd_spend"],2)))
        cells.append(gspread.Cell(wrow,COL["l30sp"], round(d["l30_spend"],2)))
        cells.append(gspread.Cell(wrow,COL["l30cv"], round(d["l30_cv"],2)))
        if d["platform"]=="Meta Ads":
            cells.append(gspread.Cell(wrow,COL["daily"], round(d["l30_spend"]/30,2)))

    # Reconciliation: a campaign renamed on-platform silently stops updating, because
    # rows are matched by exact name. Surface both sides of the mismatch every run.
    unmatched_sheet=[n for n in row_for if n not in data]
    unmatched_feed =[n for n in data if n not in row_for]
    print(f"[match] {len(matched)}/{len(row_for)} sheet rows matched to Windsor campaigns")
    for n in unmatched_sheet: print(f"  [!] in sheet, no Windsor data : {n}")
    for n in unmatched_feed:  print(f"  [!] in Windsor, no sheet row  : {n}")

    if cells and not DRY_RUN:
        tracker.update_cells(cells, value_input_option="USER_ENTERED")
    print(f"[write] {len(cells)} cells -> Pacing Tracker" + ("  (DRY RUN, not sent)" if DRY_RUN else ""))

    if not DRY_RUN: write_multiplier(cfg,mult)

    total_mtd=sum(d["mtd_spend"] for d in data.values())
    curve_ws=sh.worksheet(TAB_CURVE)

    # Rewrite the whole month-to-date cumulative series, not just today's cell.
    series_cells, series_total, missing_days = mtd_series_cells(daily_all)
    for d in missing_days:
        print(f"  [!] no daily spend for {d} - counted as $0 in the pacing curve")

    # Cross-check: the daily series and the per-campaign MTD pull are independent reads
    # of the same quantity, so they should broadly agree.
    #
    # They will not agree exactly. Two known reasons, neither a fault in this routine:
    #   - date-level totals run slightly above the sum of campaign rows (spend not
    #     attributable to a currently-reported campaign). Measured at ~1% on 2026-08-12.
    #   - the current day is still accruing, and the two pulls happen seconds apart.
    # So ~1% is expected. The threshold is set at 3% to mean "structurally wrong"
    # (a platform dropped out, a filter stopped matching) rather than "normal noise" -
    # the drift is printed every run either way, so the number stays visible.
    drift = abs(series_total - total_mtd)
    pct = (drift/total_mtd*100) if total_mtd else 0.0
    flag = "" if pct <= 3.0 else "   <== investigate before trusting either figure"
    print(f"[check] MTD from daily series ${series_total:,.0f} vs from campaigns "
          f"${total_mtd:,.0f}  (drift ${drift:,.0f}, {pct:.1f}%){flag}")

    if not DRY_RUN:
        curve_ws.update_cells(series_cells, value_input_option="USER_ENTERED")
    print(f"[write] {len(series_cells)} cumulative cells -> Pacing Curve D3:D{len(series_cells)+2}"
          + ("  (DRY RUN, not sent)" if DRY_RUN else ""))

    if not DRY_RUN: stamp_lastrun(cfg)

    # ---- digest: headline metrics + the three review queues ----
    def hl(needle):
        col_a = tracker.col_values(1)
        for i, v in enumerate(col_a):
            if v and needle.lower() in v.lower():
                return tracker.cell(i + 1, 5).value
        return None

    # Read the grouped Suggestions Tracker: action in col C, campaign col B,
    # current col E, suggested col F. Section-header rows have no action value.
    st_ws = sh.worksheet("Suggestions Tracker")
    st_rows = st_ws.get_all_values()
    from collections import defaultdict
    queues = defaultdict(list)  # action -> list of (campaign, cur, sug)
    for row in st_rows[2:]:
        if len(row) < 6:
            continue
        action = (row[2] or "").strip()
        if action not in ("Cut or Fix", "Raise", "Reduce"):
            continue
        camp = row[1]
        def _num(x):
            try: return float(str(x).replace("$", "").replace(",", ""))
            except (ValueError, AttributeError): return 0.0
        queues[action].append((camp, _num(row[4]), _num(row[5])))

    def queue_block(action, header, top=5):
        items = queues.get(action, [])
        if not items:
            return [f"{header}: none this refresh"]
        # rank by absolute daily $ change, biggest first
        items.sort(key=lambda t: -abs(t[2] - t[1]))
        out = [f"{header}: {len(items)} campaigns"]
        for camp, cur, sug in items[:top]:
            delta = sug - cur
            out.append(f"   \u2022 {camp[:44]}  ${cur:,.0f} \u2192 ${sug:,.0f} ({delta:+,.0f})")
        if len(items) > top:
            out.append(f"   \u2026 +{len(items) - top} more (see Suggestions Tracker)")
        return out

    lines = [
        *([":test_tube: *DRY RUN — nothing was written to the Sheet, do not post this.*"] if DRY_RUN else []),
        f":bar_chart: *AutoTune Pacing refreshed* ({TODAY:%b %d})",
        f"Total MTD spend: ${total_mtd:,.0f}",
        f"Blended incremental ROAS (actuals): {hl('Blended Incremental ROAS')}  (floor {hl('Target iROAS Floor')})",
        f"Guardrail: {hl('GUARDRAIL STATUS')}",
        f"Potential upside/day (raises, not in blend): {hl('Potential upside')}",
        f"Lag gross-up (spend-weighted): x{mult:.3f}" + ("  [curve refreshed]" if new_curve else ""),
        "",
        ":warning: *All suggestions require review before applying.*",
    ]
    lines += queue_block("Cut or Fix", ":mag: *Cut or Fix* (below 1.0x — diagnose tracking/LP/approvals/audience first)")
    lines += queue_block("Raise", ":arrow_up: *Raise if volume available* (ceiling lift; credit only if spend lands)")
    lines += queue_block("Reduce", ":arrow_down: *Reduce* (below waterline; right-size down)")
    post_slack("\n".join(lines))

if __name__ == "__main__":
    main()
