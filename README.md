# Antares / AutoTune — Budget Pacing Refresh

Daily refresh for the waterline pacing model. Pulls live spend and conversion value
from Windsor.ai, writes the raw-data cells into the Google Sheet, and posts a
review-gated digest to Slack.

**The Sheet owns the model.** This routine writes raw data only; every decision
(lag-adjusted ROAS, iROAS, bands, suggested budgets, guardrail status, the three
review queues) is recomputed by the Sheet's own formulas.

## Running it

```
/antares-pacing-refresh              # full run: writes to Sheet, posts to Slack
/antares-pacing-refresh --dry-run    # pulls + validates, writes nothing, posts nothing
```

See `.claude/commands/antares-pacing-refresh.md` for the orchestration steps.

## What gets written

| Target | Cells |
|---|---|
| `Pacing Tracker` | **E** MTD spend, **G** L30D spend, **H** L30D conv value (per campaign row) |
| `Pacing Tracker` | **F** daily budget — *Meta rows only*, approximated as L30D ÷ 30 (Meta uses CBO/lifetime budgets) |
| `Config` | **C65** spend-weighted lag gross-up |
| `Config` | **C77** last-run timestamp |
| `Pacing Curve` | **D**(3 + day − 1) today's cumulative MTD spend |

Nothing else is touched.

## Configuration

| Env var | Purpose |
|---|---|
| `PACING_SHEET_ID` | Target Sheet. Currently `1V4NnVrjyiYZEM0-3Pb6fy7ZZX24bCmy_UcS2yijV1rc` |
| `GSHEET_SA_KEYFILE` | **Required.** Path to the service-account JSON |
| `WINDSOR_CACHE` | JSON of pre-fetched Windsor results (how the slash command feeds it) |
| `WINDSOR_API_KEY` | Alternative to the cache — direct REST, for headless/cron runs |
| `SLACK_OUT` | Write the digest here instead of posting (slash command reads it back) |
| `SLACK_BOT_TOKEN` | Alternative to `SLACK_OUT` — post directly via `chat.postMessage` |
| `PACING_SLACK` | Channel. Defaults to `#autotune-pacing` |
| `TIMELAG_DIR` | Where Time Lag CSV exports are dropped |
| `TIMELAG_MIN_MATURITY_DAYS` | Maturity guard, default `28`. See below |
| `PACING_DRY_RUN` | `1` to skip all writes and the Slack post |

With neither `SLACK_OUT` nor `SLACK_BOT_TOKEN` set, the digest prints to stdout and is
**not** posted — a misconfigured run stays silent rather than posting somewhere
unintended.

### Secrets

The service-account key is **not** in this repo and must not be committed
(`.gitignore` covers `*-key.json`). Service account:
`ziggyantares@ziggyadsbot.iam.gserviceaccount.com` — it needs Editor on the Sheet.

## The conversion-lag curve

`Config` holds a 28-day maturation curve (rows 36–63, day index in **B**, % booked in
**C**). Each run computes a *spend-weighted* gross-up from it: weighting each day's
maturity by that day's actual spend, so it self-corrects for spend ramps. Never
hardcode it.

Dropping a fresh Google "Time Lag" CSV into `TIMELAG_DIR` re-derives the curve — but
only if its window has matured. An export whose window ends less than
`TIMELAG_MIN_MATURITY_DAYS` ago has not finished booking its own tail, so a curve
derived from it overstates early maturity and understates the gross-up. Such exports
are skipped with a log line and the existing curve is kept.

`tests/fixtures/timelag_2026-08-05_to_08-11_IMMATURE.csv` is a real example: a 7-day
window that reads ~4pp "more mature" at day 5 than the true July curve, and would have
dropped the gross-up from 1.101x to 1.083x.

## Known gaps

- **Campaigns are matched by exact name.** Rename a campaign on-platform and its row
  silently stops updating. Every run prints a reconciliation block — read it.
- **`Pacing Curve` column D has no history.** The routine writes only today's cell, so
  prior days retain whatever is already there. As of 2026-08-12 rows 3–13 hold
  placeholder data (a flat ~$146/day ramp) that needs a one-time backfill, or the chart
  will read as a flat line with a spike at today.
- **`total_mtd`** sums every campaign Windsor returns, including any with no sheet row.
