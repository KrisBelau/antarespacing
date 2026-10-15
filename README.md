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
| `Pacing Curve` | **D3:D**(last day of month) cumulative through today; later days blanked |
| `iROAS Daily` | whole tab, rewritten each run |

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
| `PACING_SLACK` | Channel. Defaults to `#antares-pacing-internal` |
| `TIMELAG_DIR` | Where Time Lag CSV exports are dropped |
| `TIMELAG_MIN_MATURITY_DAYS` | Maturity guard, default `28`. See below |
| `PACING_DRY_RUN` | `1` to skip all writes and the Slack post |
| `PACING_ALLOW_MONTH_MISMATCH` | `1` to bypass the month-rollover guard. See below |

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

**Nothing is scheduled yet.** See [ROUTINE.md](ROUTINE.md) — it has the settings, the
paste-ready prompt, and why the Routine must be created from the claude.ai Routines UI
rather than from a Claude session (session-created Routines carry no MCP connectors, so
the Windsor pull and Slack post would both fail).

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

**2026-08-12 — Pacing Curve projection referenced the wrong cell.** All 31 formulas in
`Pacing Curve` column E extrapolated `'Pacing Tracker'!$E$55`, which is
`SUMIFS(F,L,"Cut or Fix")` — $1,817/day of cut-candidate spend. The intended source is
`$E$58`, `SUM(E3:E47)` = total MTD spend, three rows down. The projection line therefore
read $4,695 at day 31 instead of ~$156k, making the chart's whole projected trajectory
meaningless.

All 31 cells now reference `$E$58`. Projection runs from today's actual to $156,425 at
month end against the $140,000 guardrail — 12% over, which is the point of the line.

Note the projection anchors on `E58` (campaign-level sum, $60,551) while Actual
Cumulative comes from the date-level daily series ($61,181), so the two differ by ~1%
where they meet at today. That is the known aggregation gap documented under Known gaps,
not a further error.

**2026-09-01 — rolled the sheet to September and rebuilt August.** `Config!C11` was a
hardcoded `=DATE(2026,8,1)` while the routine derives its window from `TODAY`, so the
Sep 1 run wrote September data into August's rows. C11 is now
`=EOMONTH(TODAY(),-1)+1`, which self-rolls; `C12/C14/C15` and the whole Pacing Curve
(dates, guardrail line, day-31 blanking) already derived from it, so nothing else needed
touching. `Pacing Curve D3:D33` was cleared for the new month.

August is archived as static values in **`Pacing Curve (Aug 2026)`**, rebuilt from
Windsor daily spend rather than copied: the live tab was understated by \$7,122 — Aug 1's
spend — on every day from the 2nd onward, because of the trailing-30d window bug. Real
August total was **\$154,495 against the \$140,000 guardrail (10.4% over)**, not the
\$140,837 the sheet displayed.

## Digest queue ordering

Cut or Fix and Reduce rank by absolute daily $ change: the story there is how much money
is moving. **Raise ranks by iROAS**, deliberately.

Ranking raises by dollars surfaces the largest budgets, which are systematically the
*least* efficient of the raises — a campaign is small precisely because it has not been
scaled yet, so its dollar delta stays small while its efficiency is the best in the
account. On 2026-08-26 the five raises shown by a dollar sort ran 1.61x–2.84x while
9.04x, 4.89x and 4.84x sat unseen in the "+14 more" line. That is the opposite of what
the waterline model is trying to do.

iROAS is joined from `Pacing Tracker` col J by campaign name; the Suggestions Tracker
does not carry it. A queue row with no matching tracker row sorts last and is reported
in the run log — distinct from a genuine `0.00x`, which is a real value and common in
Cut or Fix.

## Month rollover

`Config!C11` (reporting month) is a hardcoded `=DATE(y,m,1)` set by hand, but the
routine derives its MTD window and its Pacing Curve row index from `TODAY`. When those
disagree everything lands in the wrong place: MTD columns fill with the new month's
partial spend while days-elapsed, month-fraction and the curve's date labels still
describe the old month, and day N of the new month overwrites day N of the old.

The routine now refuses to run on a mismatch. To roll over:

1. `Config!C11` -> the new month's 1st (or make it dynamic: `=EOMONTH(TODAY(),-1)+1`)
2. `Pacing Curve` column B date labels -> the new month
3. Archive or clear `Pacing Curve` column D, which still holds last month's cumulative

`PACING_ALLOW_MONTH_MISMATCH=1` overrides the guard; only use it for a run you have
reasoned about.

## Why the curve showed future actuals (fixed 2026-10-15)

`mtd_series_cells` wrote days 1..today and left the rest of the column alone, so last
month's cumulative values sat in the future rows reading as "actuals" far above the
projection, until the new month's days overwrote them one at a time. A shorter previous
month never overwrote its own tail at all: September ended at day 30, so going into
October days 16-30 still held September while days 1-15 were October — a $53,834 jump
between day 15 and day 16.

The routine now writes every day of the month each run, blanking those after today, so
the column cannot carry anything stale.

## Row bounds are discovered, not hardcoded

The tracker grew from 45 campaigns (ending row 47) to 48 (row 50). The sheet's own
formulas were extended to `3:50`; the routine still read `3:47`, so three campaigns were
silently missing from the blended iROAS, the projection and the Raise ranking — the
newest campaigns, the ones most likely to be under watch. `last_campaign_row()` now
finds the end of the data and every run prints the range it used.

## iROAS Daily

A per-day series for telling whether a change actually moved anything: spend, projected
conversion value, and iROAS at three horizons — that day, month-to-date, and rolling 30
days. Covers **last month plus this month to date**, so a change has a before to be
compared against. `iROAS (MTD)` restarts on the 1st; `iROAS (rolling 30d)` runs
continuously across the boundary. Rewritten whole each run so the series stays consistent
as recent days mature, rather than freezing each day at its first and least mature reading.

Showing two months means the daily window reaches back further — to the 1st of last month
minus 29 days, so the rolling-30 column is complete on day one rather than ramping up from
nothing. `spend_weighted_multiplier` therefore enforces its own 30-day bound rather than
trusting the window: every day past 27 is fully matured and would add equally to numerator
and denominator, dragging the gross-up toward 1.0 and silently deflating every iROAS figure
downstream.

**Each day is grossed up by its own age-based maturity**, not by the spend-weighted
multiplier. That multiplier describes an aggregate L30D window; applied to a single day
it would under-gross yesterday (~75% booked) and over-gross a day from three weeks ago
(~98% booked). On a daily series that is the difference between seeing a change land and
seeing the booking curve. Verified against a synthetic flat-1.50x series with simulated
partial booking: every day reads 1.50, including today at 67.3% booked, where the raw
number would read 1.01.

**The incrementality factor is a blend, not per-campaign.** Account-level daily data has
no campaign breakdown, so the routine applies the L30D conv-value-weighted mean of the
per-campaign factors, recomputed each run. It moves when the channel mix moves — compare
levels within a stable mix, and treat a step change in mix as a break in the series
rather than a result. Per-campaign daily accuracy would need a campaign x date pull,
roughly 2,000 rows a day.

**Today's row is a partial day** and is flagged as such. The maturity curve corrects
booking lag, not an unfinished day: spend and conversions are both incomplete, and
conversions lag spend within the day, so today reads low for reasons unrelated to
performance. On 2026-10-15 it read 0.52x on \$1,705 of spend against a ~\$4,700 daily
norm, while MTD sat at 1.07x.

### The two digest windows

The Slack digest carries four ROAS figures on three windows, so each states its window:

| Line | Window | Source |
|---|---|---|
| Blended incremental ROAS (L30D, uncorrected) | L30D | Pacing Tracker cols G/H |
| Projected Blended incremental ROAS (L30D) | L30D | the above x `Config!C65` |
| Projected iROAS, month to date | 1st to today | iROAS Daily, last MTD value |
| Projected iROAS, last 7 complete days | ends yesterday | `window_iroas` |

The two iROAS lines come off the same basis as the tab, so Slack and the Sheet tie out:
per-day age-based maturity gross-up, blended incrementality factor, spend-weighted.

**Spend-weighted, not a mean of the daily ratios.** `window_iroas` is
`SUM(projected conv value) / SUM(spend)`. A mean of ratios would weight a $200 day the
same as a $7,000 one, so a single thin day with a lucky conversion could move it several
points while changing nothing about the account.

**The 7-day window ends yesterday.** Today is a partial day that reads low for reasons
about the clock, not performance. It is 1/7 of a weekly window against 1/30 of a monthly
one, so including it would drag the weekly number by roughly four times as much. MTD does
include today, so it reconciles with the MTD spend line above it; by mid-month that drag
is small and shrinking.

**Read the 7-day number for mix changes before reading it for performance.** It is the
most sensitive figure in the digest and it moves for two reasons that are not efficiency:

- *Mix.* The incrementality factor is a single L30D-weighted blend. A week whose channel
  mix differs from the trailing month is scored with the wrong factor. On 2026-10-15 the
  line read 1.30x against 1.07x MTD; Oct 8-14 vs Oct 1-7 was +27% booked, but the video
  views campaign was 19.2% of Oct 1-7 spend at $0 conversion value and 1.0% of Oct 8-14.
  Ex-video the same comparison is **+4%**. Nearly all of the lift was that campaign
  ending.
- *Maturity.* A 7-day window sits entirely in the steep part of the curve. That same run
  grossed Oct 8-14 up by 23% against 13% for Oct 1-7, so the weekly number leans hardest
  on the least certain part of the curve.

**A channel pausing or launching is a break in the series,** not a result: it moves the
blended factor. Bing was paused around 2026-10-06, so comparisons across that date are
not like for like.

### The charts

**Two charts, one per month**, stacked down the right of the data and rebuilt each run:
last month complete, this month to date. Each plots iROAS by day, iROAS MTD and the 1.25x
floor on the **left** axis and **daily** spend on the **right**.

The second axis is not decoration — spend runs in thousands against iROAS around 1.0–1.5,
so on a shared axis the iROAS lines collapse into the baseline and the chart shows nothing.
Daily spend rather than MTD: the cumulative line only ever climbs, so it reads as a ramp
whatever happens underneath it. Daily spend shows the days a change actually landed on.

The floor line is there so "are we above target" is readable at a glance rather than by
comparing numbers.

Three API constraints shape how the tab is laid out. They are not obvious and are easy to
reintroduce:

- **Each chart's range must be contiguous and carry its own header.** The API rejects a
  series stitched from a distant header row plus a far-off data block. That is why each
  month is written as its own labelled section with its own repeated header row, rather
  than as one long table with a single header.
- **An anchor cell past the last column is rejected outright.** The charts anchor at
  column 12, so the grid is resized to 26 columns before they are added.
- **A bare month label gets parsed as a date.** Under `USER_ENTERED`, Sheets coerces
  `"Sep 2026"` into `2026-09-01` and renders it as a date, silently replacing the section
  heading. Labels are written as `September 2026 (complete)` /
  `October 2026 (to date)`; the parenthetical is what keeps them text.

Charts are deleted and re-added each run: `ws.clear()` wipes values but leaves embedded
objects, so without that they stack up two per day.

Recent days carry the widest error bars by construction. One day is not a result.

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
