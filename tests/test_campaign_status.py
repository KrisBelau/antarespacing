import sys, os, json, tempfile, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pacing_tracker_daily as P

NOW = dt.datetime(2026, 11, 9, 18, 0, tzinfo=dt.timezone.utc)

# Row shapes captured live from Windsor on 2026-11-09 (undated last_90d pulls).
g = [
    {"campaign_id": "21150877849", "campaign_status": "ENABLED"},
    {"campaign_id": "24286409891", "campaign_status": "PAUSED"},     # video, paused 2026-11-04
    {"campaign_id": "22846172334", "campaign_status": "PAUSED"},     # DNU row
    {"campaign_id": "99", "campaign_status": "REMOVED"},
]
assert P.statuses_from_rows("google_ads", g, NOW) == {
    "21150877849": "Active", "24286409891": "Paused", "22846172334": "Paused", "99": "Removed"}

# Microsoft: title case, and BudgetPaused is a running campaign that ran out of budget today.
b = [
    {"campaign_id": "568379530", "campaign_status": "Paused"},
    {"campaign_id": "1", "campaign_status": "Active"},
    {"campaign_id": "2", "campaign_status": "BudgetPaused"},
    {"campaign_id": "3", "campaign_status": "BudgetAndManualPaused"},
    {"campaign_id": "4", "campaign_status": "Deleted"},
    {"campaign_id": "5", "campaign_status": "Suspended"},
]
assert P.statuses_from_rows("bing", b, NOW) == {
    "568379530": "Paused", "1": "Active", "2": "Budget-limited", "3": "Paused",
    "4": "Removed", "5": "Suspended"}

# Meta: a lifetime-budget campaign past its stop time still reads ACTIVE.
m = [
    {"campaign_id": "120248524895380655", "campaign_effective_status": "ACTIVE",
     "campaign_stop_time": "2026-11-30T23:00:00-0800"},              # still running on Nov 9
    {"campaign_id": "120252346306870655", "campaign_effective_status": "ACTIVE",
     "campaign_stop_time": None},
    {"campaign_id": "ENDED", "campaign_effective_status": "ACTIVE",
     "campaign_stop_time": "2026-11-01T23:00:00-0700"},
    {"campaign_id": "ISSUES", "campaign_effective_status": "WITH_ISSUES", "campaign_stop_time": None},
    {"campaign_id": "ARCH", "campaign_effective_status": "ARCHIVED", "campaign_stop_time": None},
]
assert P.statuses_from_rows("facebook", m, NOW) == {
    "120248524895380655": "Active", "120252346306870655": "Active", "ENDED": "Ended",
    "ISSUES": "With issues", "ARCH": "Removed"}
# ...and reads Ended once the stop time passes.
after = dt.datetime(2026, 12, 1, 12, 0, tzinfo=dt.timezone.utc)
assert P.statuses_from_rows("facebook", m[:1], after) == {"120248524895380655": "Ended"}

r = [{"campaign_id": "2598089559800834900", "campaign_effective_status": "ACTIVE"},
     {"campaign_id": "X", "campaign_effective_status": "PAUSED"}]
assert P.statuses_from_rows("reddit", r, NOW) == {"2598089559800834900": "Active", "X": "Paused"}

# One campaign under two statuses: no date to pick the newer, so it says so, and the
# label is not one that takes the row out of the queues.
two = P.statuses_from_rows("google_ads", [{"campaign_id": "7", "campaign_status": "ENABLED"},
                                          {"campaign_id": "7", "campaign_status": "PAUSED"}], NOW)
assert two == {"7": "Unclear (Active/Paused)"} and two["7"] not in P.STATUS_OFF
print("labels: ok")

# Which rows get written.
row_for = {"Video": 10, "Search": 11, "Bing row": 12, "New, no delivery": 13,
           "Unmapped": 14, "Dupe": 15, "Unmapped Meta": 16}
platform_of = {"Video": "Google Ads", "Search": "Google Ads", "Bing row": "Microsoft Ads",
               "New, no delivery": "Google Ads", "Unmapped": "Google Ads", "Dupe": "Google Ads",
               "Unmapped Meta": "Meta Ads"}
resolved = {"Video": "google_ads:24286409891", "Search": "google_ads:21150877849",
            "Bing row": "bing:568379530", "New, no delivery": "google_ads:555",
            "Dupe": "google_ads:21150877849"}
statuses = {"google_ads:24286409891": "Paused", "google_ads:21150877849": "Active",
            "bing:568379530": "Paused"}

out = P.status_updates(row_for, platform_of, resolved, statuses, failed=[], skip={"Dupe"})
assert out == {"Video": "Paused", "Search": "Active",
               # Microsoft's spend pull is empty, but its status is still known and written.
               "Bing row": "Paused",
               "New, no delivery": P.STATUS_NO_DELIVERY,
               "Unmapped": P.STATUS_NO_ID, "Unmapped Meta": P.STATUS_NO_ID}

# A failed platform pull leaves that platform's rows alone, mapped or not.
out = P.status_updates(row_for, platform_of, resolved, statuses, failed=["google_ads"], skip={"Dupe"})
assert out == {"Bing row": "Paused", "Unmapped Meta": P.STATUS_NO_ID}
print("row updates: ok")

# Cache keys: undated, but must not collide with :l30 (also date_preset) or :mtd.
for c in P.STATUS_FIELDS:
    assert P.windsor_cache_key(c, P.STATUS_FIELDS[c], date_preset=P.STATUS_PRESET,
                               window="status") == f"{c}:status"
    assert "date" not in P.STATUS_FIELDS[c]

# A cache without :status keys (old procedure) degrades to "left as is", not a crash.
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump({}, fh)
os.environ["WINDSOR_CACHE"] = fh.name
out, failed = P.pull_campaign_status()
assert out == {} and failed == ["google_ads", "bing", "facebook", "reddit"]

# Cache with the four keys resolves to "connector:id".
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump({"google_ads:status": g, "bing:status": b[:1], "facebook:status": [],
               "reddit:status": r[:1]}, fh)
os.environ["WINDSOR_CACHE"] = fh.name
out, failed = P.pull_campaign_status()
assert failed == []
assert out["google_ads:24286409891"] == "Paused" and out["bing:568379530"] == "Paused"
assert out["reddit:2598089559800834900"] == "Active"
assert not any(k.startswith("facebook:") for k in out)
print("pull + cache: ok")
