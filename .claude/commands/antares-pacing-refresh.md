---
description: Refresh the AutoTune (Antares) budget pacing tracker from Windsor.ai and post the digest to Slack
argument-hint: "[--dry-run]"
allowed-tools: Bash, Read, Write, mcp__Windsor_ai__get_data, mcp__Slack__slack_send_message
---

# Antares / AutoTune pacing refresh

Refresh the waterline pacing model from live Windsor.ai data, let the Sheet's own
formulas recompute the decisions, and post the review digest to Slack.

**You do not recompute any decisions.** The Sheet owns the model. This routine only
writes raw data cells and reads back what the Sheet concluded.

If `$ARGUMENTS` contains `--dry-run`, set `PACING_DRY_RUN=1` in step 3 and **skip step 5
entirely** — no Sheet writes, no Slack post.

## Fixed configuration

| | |
|---|---|
| Sheet ID | `1V4NnVrjyiYZEM0-3Pb6fy7ZZX24bCmy_UcS2yijV1rc` |
| Service account | `ziggyantares@ziggyadsbot.iam.gserviceaccount.com` |
| Slack channel | `#autotune-pacing` |
| Google Ads | connector `google_ads`, account `802-485-7603` |
| Microsoft Ads | connector `bing`, `account_name` = `Antares Adwords` |
| Meta Ads | connector `facebook`, `account_name` = `Antares Ads` |

## Step 1 — resolve dates

```bash
python3 -c "import datetime as d;t=d.date.today();print(f'MONTH_START={t.replace(day=1)}');print(f'TODAY={t}');print(f'D30_START={t-d.timedelta(days=29)}')"
```

## Step 2 — pull the seven Windsor datasets

Call `mcp__Windsor_ai__get_data` nine times. Field names below are verified against
`get_fields`; **do not substitute them** — in particular Meta uses
`action_values_omni_purchase`, *not* `conversions_value`.

| Cache key | connector | fields | window |
|---|---|---|---|
| `google_ads:mtd` | `google_ads` | `campaign, spend, conversions_value` | `date_from=MONTH_START`, `date_to=TODAY`, `accounts=["802-485-7603"]` |
| `google_ads:l30` | `google_ads` | `campaign, spend, conversions_value` | `date_preset="last_30d"`, `accounts=["802-485-7603"]` |
| `google_ads:daily` | `google_ads` | `date, spend` | `date_from=D30_START`, `date_to=TODAY`, `accounts=["802-485-7603"]` |
| `bing:mtd` | `bing` | `campaign, spend, revenue` | `date_from=MONTH_START`, `date_to=TODAY`, `filters=[["account_name","eq","Antares Adwords"]]` |
| `bing:l30` | `bing` | `campaign, spend, revenue` | `date_preset="last_30d"`, same filter |
| `bing:daily` | `bing` | `date, spend` | `date_from=D30_START`, `date_to=TODAY`, same filter |
| `facebook:mtd` | `facebook` | `campaign, spend, action_values_omni_purchase` | `date_from=MONTH_START`, `date_to=TODAY`, `filters=[["account_name","eq","Antares Ads"]]` |
| `facebook:l30` | `facebook` | `campaign, spend, action_values_omni_purchase` | `date_preset="last_30d"`, same filter |
| `facebook:daily` | `facebook` | `date, spend` | `date_from=D30_START`, `date_to=TODAY`, same filter |

The three `:daily` pulls feed the month-to-date cumulative series on Pacing Curve.
Google's also feeds the lag gross-up — Google only, because the maturation curve comes
from the Google Time Lag report.

Write all nine result arrays into one JSON file keyed exactly as above:

```json
{ "google_ads:mtd": [ {...}, ... ], "google_ads:l30": [ ... ], ... }
```

Save it to `/tmp/windsor_cache.json`.

**Sanity-check before continuing.** If any of these fail, stop and report rather than
writing partial data to a live client sheet:

- all nine keys present and non-empty
- each `:daily` key has ~30 rows, one per date
- MTD spend totals are plausible (Google MTD has been running ~$45–50k mid-month)

## Step 3 — run the routine

```bash
cd /home/user/antarespacing && \
PACING_SHEET_ID=1V4NnVrjyiYZEM0-3Pb6fy7ZZX24bCmy_UcS2yijV1rc \
GSHEET_SA_KEYFILE=<path to sa-key.json> \
WINDSOR_CACHE=/tmp/windsor_cache.json \
SLACK_OUT=/tmp/pacing_digest.txt \
TIMELAG_DIR=/home/user/antarespacing/timelag \
PACING_SLACK='#autotune-pacing' \
python3 pacing_tracker_daily.py
```

The script writes only these cells, exactly as before:

- `Pacing Tracker` **E** (MTD spend), **G** (L30D spend), **H** (L30D conv value) per campaign
- `Pacing Tracker` **F** (daily budget) for Meta rows only — Meta uses CBO/lifetime, so
  the daily baseline is approximated as L30D ÷ 30
- `Config` C65 — spend-weighted lag gross-up
- `Config` C77 — last-run stamp
- `Pacing Curve` D(3 + day − 1) — today's cumulative MTD spend

Everything downstream (lag-adjusted ROAS, iROAS, bands, suggested budgets, guardrail,
the three queues) is recomputed by the Sheet's own formulas.

## Step 4 — review the run log

The script prints a reconciliation block. **Read it before posting.**

- `[match] N/M sheet rows matched` — campaigns are matched by *exact name*. A campaign
  renamed on-platform silently stops updating; it shows up as `in sheet, no Windsor data`.
- `[timelag] ... skipped` — a Time Lag export was found but its window isn't matured
  (< 28 days). This is correct behavior; the existing curve is kept. Mention it in your
  summary but do not override it.

If more than a couple of rows are unmatched, report that to the user instead of
treating the run as clean.

## Step 5 — post the digest

Skip entirely on `--dry-run`.

Read `/tmp/pacing_digest.txt` and post it verbatim with
`mcp__Slack__slack_send_message` to `#autotune-pacing`. Do not rewrite, re-rank or
summarize the queues — the ordering is computed and the review gating is deliberate.

## Step 6 — report back

Give the user: blended iROAS vs floor, guardrail status, total MTD spend, the lag
gross-up used, queue counts (Cut or Fix / Raise / Reduce), and anything from step 4
that needs attention.
