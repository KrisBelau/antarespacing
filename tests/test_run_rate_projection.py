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
print("PASS")
