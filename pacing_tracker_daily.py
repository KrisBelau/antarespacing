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

import os, csv, glob, datetime as dt
import gspread
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------------- CONFIG
SHEET_ID        = os.environ.get("PACING_SHEET_ID", "REPLACE_WITH_SHEET_ID")
SA_KEYFILE      = os.environ["GSHEET_SA_KEYFILE"]
SLACK_CHANNEL   = os.environ.get("PACING_SLACK", "#autotune-pacing")
TIMELAG_DIR     = os.environ.get("TIMELAG_DIR", "/tmp/timelag")   # drop new exports here

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
def windsor_get(connector, fields, date_from=None, date_to=None, date_preset=None,
                accounts=None, filters=None):
    """
    Replace this body with an MCP call:
      Windsor.ai:get_data(connector=connector, fields=fields, date_from=date_from,
                          date_to=date_to, date_preset=date_preset, accounts=accounts,
                          filters=filters)
    Return the list[dict] Windsor returns. Field names are verified against get_fields:
      google_ads: spend, conversions_value | bing: spend, revenue |
      facebook: spend, action_values_omni_purchase   (NOT conversions_value)
    """
    raise NotImplementedError("Wire to Windsor.ai:get_data")

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
    """Trailing-30d daily spend (Google) -> {date: spend} for the spend-weighted multiplier."""
    start=(TODAY-dt.timedelta(days=29)).isoformat()
    rows=windsor_get("google_ads",["date","spend"],date_from=start,date_to=TODAY.isoformat(),accounts=[GOOGLE_CID])
    return {r["date"]:float(r.get("spend") or 0) for r in rows if r.get("date")}

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

def refresh_curve_from_timelag():
    """Return newest-export curve dict, or None if no export present."""
    files=sorted(glob.glob(os.path.join(TIMELAG_DIR,"*.csv")), key=os.path.getmtime)
    return parse_timelag_curve(files[-1]) if files else None

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

def stamp_lastrun(cfg):
    r=_find_label_row(cfg,"Last routine run")
    if r: cfg.update_cell(r,3,dt.datetime.now().strftime("%Y-%m-%d %H:%M %Z"))

# ================================================================ SLACK
def post_slack(text):
    """Wire to Slack:slack_send_message(channel=SLACK_CHANNEL, text=text)."""
    raise NotImplementedError("Wire to Slack:slack_send_message")

# ================================================================ MAIN
def main():
    creds=Credentials.from_service_account_file(SA_KEYFILE, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc=gspread.authorize(creds); sh=gc.open_by_key(SHEET_ID)
    tracker=sh.worksheet(TAB_TRACKER); cfg=sh.worksheet(TAB_CONFIG)

    data=pull_platform_mtd_and_l30()
    daily_spend=pull_google_daily_spend()

    new_curve=refresh_curve_from_timelag()
    if new_curve:
        write_curve(cfg,new_curve); curve=new_curve
    else:
        curve=read_curve(cfg)
    mult=spend_weighted_multiplier(curve,daily_spend)

    names=tracker.col_values(COL["campaign"])
    row_for={n:i+1 for i,n in enumerate(names) if i+1>=DATA_START_ROW and n}
    cells=[]
    for name,wrow in row_for.items():
        d=data.get(name)
        if not d: continue
        cells.append(gspread.Cell(wrow,COL["mtd"],   round(d["mtd_spend"],2)))
        cells.append(gspread.Cell(wrow,COL["l30sp"], round(d["l30_spend"],2)))
        cells.append(gspread.Cell(wrow,COL["l30cv"], round(d["l30_cv"],2)))
        if d["platform"]=="Meta Ads":
            cells.append(gspread.Cell(wrow,COL["daily"], round(d["l30_spend"]/30,2)))
    if cells: tracker.update_cells(cells, value_input_option="USER_ENTERED")

    write_multiplier(cfg,mult)

    total_mtd=sum(d["mtd_spend"] for d in data.values())
    curve_ws=sh.worksheet(TAB_CURVE)
    curve_ws.update_cell(3+(TODAY.day-1),4,round(total_mtd,2))

    stamp_lastrun(cfg)

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
