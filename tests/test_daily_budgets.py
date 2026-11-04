import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pacing_tracker_daily as P

# Row shapes captured live from Windsor on 2026-11-04.

# Google: one row per campaign per date. A budget change must take the LATEST date's value.
g = [
    {"date": "2026-11-02", "campaign_id": "24286409891", "budget_amount": 700},
    {"date": "2026-11-03", "campaign_id": "24286409891", "budget_amount": 450},
    {"date": "2026-11-03", "campaign_id": "21150877849", "budget_amount": 1900},
    {"date": "2026-11-03", "campaign_id": "21150877849", "budget_amount": 1900},  # repeat, not summed
]
assert P.daily_budgets_from_rows("google_ads", g) == {"24286409891": 450.0, "21150877849": 1900.0}

# Bing: Windsor repeats the same row many times; never sum.
b = [{"date": "2026-11-03", "campaign_id": "568379530", "budget_amount": 200}] * 32
assert P.daily_budgets_from_rows("bing", b) == {"568379530": 200.0}

# Meta: CBO daily budget in cents; lifetime-only campaign omitted so the caller falls back.
m = [
    {"date": "2026-11-02", "campaign_id": "LIFETIME", "adset_id": "a0",
     "campaign_daily_budget": None, "adset_daily_budget": 0},
    {"date": "2026-11-04", "campaign_id": "120252346306870655", "adset_id": "a1",
     "campaign_daily_budget": 50000, "adset_daily_budget": None},
    {"date": "2026-11-04", "campaign_id": "120252346830380655", "adset_id": "a2",
     "campaign_daily_budget": 70000, "adset_daily_budget": None},
    # ABO campaign: two ad sets, each repeated
    {"date": "2026-11-04", "campaign_id": "ABO", "adset_id": "x", "campaign_daily_budget": None, "adset_daily_budget": 2500},
    {"date": "2026-11-04", "campaign_id": "ABO", "adset_id": "x", "campaign_daily_budget": None, "adset_daily_budget": 2500},
    {"date": "2026-11-04", "campaign_id": "ABO", "adset_id": "y", "campaign_daily_budget": None, "adset_daily_budget": 1500},
]
assert P.daily_budgets_from_rows("facebook", m) == {
    "120252346306870655": 500.0, "120252346830380655": 700.0, "ABO": 40.0}

# Reddit: sum DAILY_SPEND ad groups per campaign; lifetime ad groups are not a daily budget.
r = [
    {"date": "2026-11-04", "campaign_id": "C", "campaign_is_campaign_budget_optimization": False,
     "ad_group_id": "g1", "ad_group_goal_type": "DAILY_SPEND", "ad_group_budget": 160},
    {"date": "2026-11-04", "campaign_id": "C", "campaign_is_campaign_budget_optimization": False,
     "ad_group_id": "g2", "ad_group_goal_type": "DAILY_SPEND", "ad_group_budget": 120},
    {"date": "2026-11-03", "campaign_id": "C", "campaign_is_campaign_budget_optimization": False,
     "ad_group_id": "g1", "ad_group_goal_type": "DAILY_SPEND", "ad_group_budget": 999},  # older date
    {"date": "2026-11-04", "campaign_id": "L", "campaign_is_campaign_budget_optimization": False,
     "ad_group_id": "g3", "ad_group_goal_type": "LIFETIME_SPEND", "ad_group_budget": 5000},
    {"date": "2026-11-04", "campaign_id": "CBO", "campaign_is_campaign_budget_optimization": True,
     "campaign_goal_type": "DAILY_SPEND", "campaign_goal_value": 75_000_000,
     "ad_group_id": "g4", "ad_group_goal_type": None, "ad_group_budget": None},
]
assert P.daily_budgets_from_rows("reddit", r) == {"C": 280.0, "CBO": 75.0}

# Cache key: the budget pull carries `date` but must not collide with :daily.
assert P.windsor_cache_key("google_ads", P.BUDGET_FIELDS["google_ads"], window="budget") == "google_ads:budget"
assert P.windsor_cache_key("google_ads", ["date", "spend"]) == "google_ads:daily"

# A cache without :budget keys (old procedure) degrades to "left as is", not a crash.
import json, tempfile
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump({}, fh)
os.environ["WINDSOR_CACHE"] = fh.name
out, failed = P.pull_daily_budgets()
assert out == {} and failed == ["google_ads", "bing", "facebook", "reddit"]
print("ok")
