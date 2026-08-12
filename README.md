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
| `Pacing Curve` | **D3:D**(today+2) full month-to-date cumulative spend series, rewritten each run |

Nothing else is touched.

## Configuration

| Env var | Purpose |
|---|---|
| `PACING_SHEET_ID` | Target Sheet. Currently `1V4NnVrjyiYZEM0-3Pb6fy7ZZX24bCmy_UcS2yijV1rc` |
| `GSHEET_SA_KEYFILE` | Path to the service-account JSON |
| `GSHEET_SA_KEY_JSON` | The service-account JSON inline, for secret managers. Takes precedence over the path. One of the two is required |
| `WINDSOR_CACHE` | JSON of pre-fetched Windsor results (how the slash command feeds it) |
| `WINDSOR_API_KEY` | Alternative to the cache — direct REST, for headless/cron runs |
| `SLACK_OUT` | Write the digest here instead of posting (slash command reads it back) |
| `SLACK_BOT_TOKEN` | Alternative to `SLACK_OUT` — post directly via `chat.postMessage` |
| `PACING_SLACK` | Channel. Defaults to `#antares-pacing` |
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

## Scheduling

Routine `trig_012PJ6qGrvVKeJwu3D2LioXV` — "Antares pacing refresh — daily 7am PT",
cron `0 14 * * *` (UTC), fresh session per fire. **Currently disabled**, see below.

**Daylight saving.** Cron runs in UTC and does not follow DST. `0 14 * * *` is 07:00
Pacific during PDT. When PST resumes (2026-11-01) it becomes 06:00 Pacific — change to
`0 15 * * *` then, and back again in March.

**Before enabling**, the fired session needs credentials it does not currently have.
Trigger-fired sessions run **without MCP connector tools**, so the slash-command flow
(Windsor MCP -> cache -> Slack MCP) cannot run there. Two ways to fix:

1. *Headless / REST (recommended).* Set three environment secrets and the routine needs
   no MCP at all:
   - `WINDSOR_API_KEY` — `windsor_get` falls back to the Windsor REST API
   - `SLACK_BOT_TOKEN` — `post_slack` uses `chat.postMessage` directly
   - `GSHEET_SA_KEY_JSON` — the service-account key inline (no file on disk)

   Then drop `WINDSOR_CACHE` and `SLACK_OUT` from the invocation; the same file runs
   end to end unattended.

2. *Recreate the Routine from the claude.ai Routines UI*, attaching the Windsor, Slack
   and Google Drive connectors. The stored prompt already handles fetching the key
   from Drive. Faster to set up, but depends on connector auth staying valid.

## Sheet changes made outside this repo

**2026-08-12 — blended iROAS omitted the conversion-lag gross-up.** `Pacing Tracker`
rows 50 (Blended Incremental ROAS) and 60 (GUARDRAIL STATUS) computed
`SUMPRODUCT(H,D)/SUM(G)`, which drops `Config!$C$65`. Every per-campaign figure
includes it — `I = H*C65/G`, `J = I*D`, and the waterline helper `U = H*C65*D` — so the
headline blend was understated by exactly the gross-up factor while the queues were
correct.

It only became visible once live data landed: at the previous stale 1.31x the blend
cleared the 1.25x floor either way, but fresh data read 1.15x, which crosses below the
floor *only* because of the missing multiplier. The guardrail would have told the team
to cut spend when the model's own per-campaign basis showed headroom.

Both cells now read `SUM(U3:U47)/SUM(G3:G47)`, reusing the helper column that already
applies the gross-up so the two paths cannot drift apart again. Blend went 1.15x ->
1.27x, guardrail flipped to AT/ABOVE TARGET, queue counts unchanged (20 / 19 / 6).

## Known gaps

- **Campaigns are matched by exact name.** Rename a campaign on-platform and its row
  silently stops updating. Every run prints a reconciliation block — read it.
- **Two MTD figures that don't quite agree.** The pacing curve is built from date-level
  daily spend; the tracker rows come from a campaign-level pull. Date-level totals run
  ~1% higher, because they include spend not attributable to a currently-reported
  campaign row. Each run prints both and the drift; it only flags above 3%, which would
  mean something structural (a platform dropped out, a filter stopped matching) rather
  than this known gap. Measured 2026-08-12: $61,181 vs $60,551.
- **`total_mtd`** sums every campaign Windsor returns, including any with no sheet row.
