import sys, os, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pacing_tracker_daily as P

def series(start, values):
    return {(start + dt.timedelta(days=i)).isoformat(): v for i, v in enumerate(values)}

# Mid-month step change, the case the run rate is for. 30-day month, run on the 16th.
# $1,000/day for days 1-9, budget cut to $500/day from the 10th, today partial at $100.
# The 7-day window is days 9-15: one $1,000 day and six $500 days.
today = dt.date(2026, 11, 16)
daily = series(dt.date(2026, 10, 1), [800.0] * 31)            # October, for the lookback
daily.update(series(dt.date(2026, 11, 1), [1000.0] * 9 + [500.0] * 6 + [100.0]))
p = P.run_rate_projection(daily, today=today, days=7)
rate = (1000 + 6 * 500) / 7
assert p["thru_yesterday"] == 12000.0, p["thru_yesterday"]
assert abs(p["rate"] - rate) < 1e-9, p["rate"]
assert p["days_left"] == 15 and p["days_in_month"] == 30
assert abs(p["eom"] - (12000 + rate * 15)) < 1e-6, p["eom"]
assert p["straight_line"] == 24000.0                          # 12,000 / 15 days x 30
assert p["window"] == (dt.date(2026, 11, 9), dt.date(2026, 11, 15))
print(f"step change    : run rate ${p['eom']:,.0f} vs straight line ${p['straight_line']:,.0f}")

# Today's partial day is in neither the anchor nor the rate.
daily2 = dict(daily); daily2[today.isoformat()] = 4000.0
assert P.run_rate_projection(daily2, today=today, days=7)["eom"] == p["eom"]
print("partial today  : excluded")

# Path: blank before yesterday, actual through yesterday as the anchor so the line joins
# the actual curve, then linear at the rate to month end.
assert all(p["path"][n] == "" for n in range(1, 15))
assert p["path"][15] == 12000.0
assert abs(p["path"][16] - (12000 + rate)) < 0.01
assert abs(p["path"][30] - p["eom"]) < 0.01
assert len(p["path"]) == 30
print("path           : anchored on day 15, ends at EOM")

# Day 1: nothing booked this month yet, so the rate comes from last month's closing week
# and there is no straight line to compare with.
p1 = P.run_rate_projection(daily, today=dt.date(2026, 11, 1), days=7)
assert p1["thru_yesterday"] == 0.0 and p1["straight_line"] is None
assert p1["rate"] == 800.0 and p1["eom"] == 800.0 * 30
assert p1["path"][1] == 800.0 and p1["path"][30] == 24000.0
print("day 1          : last month's rate, no straight line")

# Last day of the month: one day left, today.
pl = P.run_rate_projection(series(dt.date(2026, 10, 1), [800.0] * 31),
                           today=dt.date(2026, 10, 31), days=7)
assert pl["days_left"] == 1 and pl["eom"] == 800.0 * 31
print("last day       : one day left")

# A date absent from every platform is reported, and counted as $0 rather than skipped,
# so the rate visibly reads low instead of quietly averaging over fewer days.
gap = dict(daily); del gap["2026-11-12"]
pg = P.run_rate_projection(gap, today=today, days=7)
assert pg["missing"] == ["2026-11-12"]
assert abs(pg["rate"] - (rate - 500 / 7)) < 1e-9
print("missing day    : reported, counted $0")

# Window length follows the argument.
p14 = P.run_rate_projection(daily, today=today, days=14)
assert p14["window"] == (dt.date(2026, 11, 2), dt.date(2026, 11, 15))
assert abs(p14["rate"] - (8 * 1000 + 6 * 500) / 14) < 1e-9

# ---- per-campaign inputs for Pacing Tracker Projected EOM (col Q)
# Campaign 1: $300/day for the 7 complete days, $40 so far today. Campaign 2: only spent
# today. A row outside the window (8 days ago) must not count. No id: skipped.
rows = [{"date": (today - dt.timedelta(days=k)).isoformat(), "campaign_id": "1", "spend": 300.0}
        for k in range(1, 8)]
rows += [{"date": today.isoformat(), "campaign_id": "1", "spend": 40.0},
         {"date": today.isoformat(), "campaign_id": "2", "spend": 15.0},
         {"date": (today - dt.timedelta(days=8)).isoformat(), "campaign_id": "1", "spend": 9999.0},
         {"date": today.isoformat(), "campaign_id": None, "spend": 5.0}]
rb = P.recent_by_campaign(rows, today=today, days=7)
assert rb == {"1": (40.0, 300.0), "2": (15.0, 0.0)}, rb
print("recent         : today split out, window bounded")

recent = {f"google_ads:{k}": v for k, v in rb.items()}
inp = P.run_rate_inputs([("Row 1", "google_ads:1", 4540.0),     # 15 days x $300 + $40 today
                         ("Row 2", "google_ads:2", 15.0),
                         ("Row 3", "google_ads:3", 10.0),       # in MTD, not in recent: no spend lately
                         ("Row 4", None, 0.0),                  # zeroed row, no campaign ID
                         ("Row 5", "facebook:9", 800.0),        # platform whose recent pull failed
                         ("Row 6", "google_ads:2", 10.0)],      # MTD below today's spend: floored at 0
                        recent, ["facebook"])
assert inp == {"Row 1": (4500.0, 300.0), "Row 2": (0.0, 0.0), "Row 3": (10.0, 0.0),
               "Row 4": (0.0, 0.0), "Row 6": (0.0, 0.0)}, inp
print("inputs         : thru yesterday = MTD - today; failed platform left out")
print("PASS")
