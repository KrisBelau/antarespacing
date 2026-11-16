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
| `Pacing Tracker` | **E** MTD spend, **G** L30D spend, **H** L30D conv value (per campaign row; $0 when the campaign has no Windsor data) |
| `Pacing Tracker` | **F** daily budget — read from each platform's configured budget (Google/Microsoft `budget_amount`, Meta campaign or ad-set daily budget, Reddit daily ad-group or campaign budget), latest of the last three days. No daily budget to read: Meta/Reddit fall back to L30D ÷ 30, Google/Microsoft are left as is |
| `Pacing Tracker` | **MTD thru Yesterday ($)** and **Run Rate, 7d ($/day)** (found by header; **AA**/**AB**): per-campaign inputs to Projected EOM (Q). See [Per-campaign Projected EOM](#per-campaign-projected-eom) |
| `Pacing Tracker` | **Platform Status** (column found by header; created at **Z** on first run) — each campaign's on-platform status, every row, every run. See [Platform Status](#platform-status) |
| `Config` | **C65** spend-weighted lag gross-up |
| `Config` | **C77** last-run timestamp |
| `Pacing Curve` | **D3:D33** cumulative through today; later days, and days past the month's end, blanked |
| `Pacing Curve` | **Projected, 7-day run rate ($)** (column found by header; created at **F** on first run) — projected cumulative spend from yesterday to month end. See [Month-end spend projection](#month-end-spend-projection) |
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
| `PACING_COVERAGE_MIN` | Share of MTD spend that must land on a tracker row before the digest warns. Default `0.98` |
| `PACING_RUN_RATE_DAYS` | Complete days averaged for the month-end spend projection. Default `7`, range 1–29 |

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

**2026-11-16 — projections moved to the run rate and off the partial day.** Applied by
script with the service account; every formula was checked against the expected old
text before it was replaced. To roll back, put the old formulas below back.

- `Pacing Curve` **E3:E33** (header renamed **Projected, straight line ($)**). Was, for
  day *n*: `=IF(n>=Config!$C$12*Config!$C$15,'Pacing Tracker'!$E$70/(Config!$C$12*Config!$C$15)*n,NA())`,
  which divided campaign-level MTD, today's partial day included, by days elapsed
  including today. Now
  `=IF(AND(Config!$C$14>1,n>=Config!$C$14-1,n<=Config!$C$12),INDEX($D$3:$D$33,Config!$C$14-1)/(Config!$C$14-1)*n,NA())`:
  actual cumulative through yesterday from column D, over complete days, so it starts on
  the actual line. Blank on day 1, where there is no complete day.
- `Pacing Curve` **F**: new, **Projected, 7-day run rate ($)**, written by the routine.
  Added to the chart as a solid orange series; the straight line stays dotted.
- `Pacing Curve` **D33** held October 31's cumulative ($213,396) as a stray point at day 31,
  because the routine only wrote rows up to the month's last day. Blanked, and the
  routine now writes all 31 rows.
- `Pacing Tracker` **Q3:Q59** (Projected EOM). Was `=IFERROR(E{r}/Config!$C$15,0)`. Now
  `=IF(OR(ISBLANK($AA{r}),ISBLANK($AB{r})),IFERROR(E{r}/Config!$C$15,0),$AA{r}+$AB{r}*(Config!$C$12-Config!$C$14+1))`.
- `Pacing Tracker` **AA**/**AB**: new, routine-written inputs to Q.

Checked after the write: no error cells on either tab; Q sums to $321,695 against
$323,234 for the account, the gap being the AT2026 Demand Gen Prospecting CAN campaign,
which has no tracker row ($842 through yesterday + $47/day x 15). Nothing in the Sheet
reads O, P or Q, so no other figure moved.

## Month-end spend projection

The digest's projected month-end spend, its guardrail comparison and the $0-revenue
headroom all use the trailing run rate (from 2026-11-16):

```
EOM = spend through yesterday + (average daily spend, last 7 complete days) x days left incl. today
```

It replaced a straight line, `MTD / days elapsed x days in month`, which had two problems:

- It averaged the whole month, so a budget change kept projecting at the old pace for
  the rest of the month. On 2026-11-16 spend had stepped down from ~$14k to ~$8.6k a day
  on Nov 10; the straight line said $367,708 and the 7-day rate $323,234.
- It counted today's partial day as a full day. Days elapsed includes today
  (`Expected MTD` = daily budget x 16 on Nov 16), but at the 13:00 UTC run only ~20% of
  the day has booked. So it read low every morning: about 40% low on the 2nd, still ~5%
  low mid-month. Day 1 was meaningless.

The run rate excludes today from both the anchor and the rate, and its window runs back
across the month boundary, so day 1 projects at last month's closing rate.

The digest shows the rate next to the month-to-date average and the straight line it
replaced (on complete days, so the partial-day error is gone from that too):

```
Projected EOM spend (7-day run rate): $323,234  (over the $X guardrail by N%)
   $9,292/day over Nov 9–Nov 15 vs $12,257/day month to date; straight line $367,708
```

A wide gap between the two means spend has changed pace. Whether the change lasts is the
judgment call the projection cannot make.

**Pacing Curve** gets the projected path in its own column, found by any header
containing "run rate" (created at F, formatted like column E, on the first run). It
holds actual cumulative spend through yesterday as the anchor, so the line joins the
actual curve, then the projection to month end; earlier days are blank. Same date-level
series as Actual Cumulative, so the two meet exactly. The chart plots it next to the
straight line (E), which now starts from the same point.

### What the backtest says

Replayed against daily spend for May–Oct 2026 (Windsor, all four platforms): a morning run
on every day of each month, scored against that month's actual total. Mean absolute error:

| Run on day | Straight line, as built | Straight line, complete days | Rolling 7-day | Rolling 14-day |
|---|---:|---:|---:|---:|
| 1–7 (n=35) | 38.8% | 25.6% | 20.1% | 20.5% |
| 8–14 (n=35) | 10.6% | 12.6% | 18.9% | 11.5% |
| 15–21 (n=42) | 11.5% | 11.5% | 16.2% | 13.2% |
| 22–EOM (n=58) | 6.5% | 5.3% | 4.7% | 5.8% |
| All | 15.2% | 12.1% | 13.6% | 11.9% |

May days 1–14 are left out of every column (no April data for the lookback). "As built"
assumes 20% of today booked at run time.

How to read it:

- **The 7-day rate is not better everywhere.** It wins the first week and the last ten
  days, and loses mid-month by 4–6 points. It reacts fast, which helps when a change
  sticks (a budget cut) and hurts when it does not (a promo burst that ends): Jul 7–9,
  Sep 6–16 and Oct 27–Nov 9 were all bursts, and a 7-day window projects each one
  forward for weeks after it ends.
- The partial-day fix accounts for much of the gain over the old number: the straight
  line on complete days alone goes from 15.2% to 12.1%.
- 14 days scored best overall, but 11.9% against 12.1% on six months is within noise.
  `PACING_RUN_RATE_DAYS=14` switches it if the team prefers a slower line.
- No trailing method predicts a planned surge. All four ran 13–17% low on average across
  October's runs from day 8, because none of them could see the Oct 27–31 burst coming.
  Expect the same for BFCM: in late November, read the projection as a floor.
- The actual landed between the straight line and the 7-day rate on only 16% of runs
  from day 8 on. The two usually miss in the same direction, so they do not bracket the
  outcome.

### Why not project from configured budgets

Checked 2026-11-16 and ruled out for now. Two reasons:

- **No history to test it against.** Windsor's Google `budget_amount` by date is the
  current budget repeated on every date. Several campaigns cut on Nov 10 read their
  post-cut budget on Nov 8–9, including one that spent $3,991 against a "$1,000" budget,
  past Google's 2x daily cap. Google's change history (`change_event`, 30 days) is not
  on Sceptergraph's GAQL allowlist.
- **Budgets here are ceilings, mostly unspent.** On Nov 11 Google budgets totalled
  $14,646/day against $7,062 of spend (48%). Ten under-budget campaigns held $11,404 of
  budget and spent $3,155. Budget x days left would have put November ~$80k too high,
  and a budget change only moves spend on capped campaigns (27 of 37, 55% of spend).

A version that could work: current budget for capped campaigns, run rate for the rest.
Testing it needs budget history, which would have to be logged from each run going
forward.

### Per-campaign Projected EOM

Pacing Tracker **Projected EOM** (Q) is on the same basis as the headline:

```
Q = MTD thru Yesterday (AA) + Run Rate, 7d (AB) x days left incl. today
```

The routine writes AA and AB each run from four `:recent` pulls (campaign x date, today
minus 7 through today): AA is the row's MTD less today's spend, AB the average over the 7
complete days. Q falls back to the old straight line on a row where AA or AB is blank:
the Microsoft rows, whose pulls are empty, and a row just added with
`add_tracker_rows.py` until the next run. A failed `:recent` pull leaves that platform's
AA/AB as they were and says so in the digest.

The rows add up to the headline less any campaign with no tracker row: on 2026-11-16,
$321,695 against $323,234.

Still on the partial day: **Expected MTD** (O) is daily budget x days elapsed including
today, so **Pacing %** (P) reads low every morning by the unfinished share of today.
Nothing reads either column.

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

## Campaigns are matched by ID

Tracker rows are tied to campaigns through `campaign_ids.json`, which maps each row name
(column B) to `connector:campaign_id`. Exact-name matching broke on every rename, and
quietly: on 2026-10-30 the video campaign was renamed ATA → ATU and its row stopped
updating while it sat at the top of Cut or Fix, and three UK/AUS campaigns were renamed
`DNU …` and replaced by new campaigns under the old names.

A name-only Windsor pull makes this worse than "the row stops updating". Windsor labels
each cached day with whatever name the campaign had when that day was fetched, so a
renamed campaign's spend comes back **split across its old and new names**: October showed
$3,826 under ATA and $6,292 under ATU for what is one campaign at $10,121. Requesting
`campaign_id` returns one row per campaign under its current name. The routine refuses a
`:mtd`/`:l30` pull that has no IDs.

Resolution order is the map first, then the campaign's current name, so a newly added row
works straight away and is reported until it is pinned. Keeping the map current:

- **Campaign renamed on-platform** — nothing breaks. Add the new name as a second key with
  the same ID if the tracker row is renamed too.
- **New campaign** — it shows as `in Windsor, no tracker row` and in the digest. Add the
  row with `add_tracker_rows.py` (below) and pin its ID in the map.
- **Row with no ID** — reads $0 and is listed in the digest.

### Coverage, not a row count

The run reports the share of Windsor MTD spend that lands on a tracker row, and the
digest warns below 98%. It replaces "~40 of 45 rows matched", which treated a dead $0 row
the same as a $10k campaign and stopped meaning anything once the tracker grew to 52 rows.
On 2026-10-30, before the new rows were added: 99.7%, with the three new campaigns
($600 MTD) as the only gap.

### Rows with no data read $0

A row whose campaign did not spend in either window is written as $0 instead of keeping
the last run's numbers, which otherwise sat in the blend and the queues as if live. The
exception is a platform whose pull came back empty altogether: that is either a failed
pull or a channel dark for 30+ days, and neither is grounds to overwrite. Rows with no
L30D spend are left out of the digest's queues and counted in one line. The four Microsoft
Ads rows that have not spent since September are the current example.

### Adding rows: `add_tracker_rows.py`

A new campaign needs a row in three tabs, not one. Suggestions Tracker (which feeds the
digest's queues) and Alerts each point at Pacing Tracker rows by number, so a row added only
to Pacing Tracker never reaches the digest. The rows also have to go **inside** the block:
the headline, frontier and rank formulas use `$3:$54`, which only expands for an insert
within it.

`add_tracker_rows.py` does all of that from a list at the top of the file, copying formulas
from a Google Ads row. Reddit rows carry a hand-set incrementality factor in column D and
are never used as the template. It dry-runs by default; `--apply` writes. Re-running is safe.

Applied 2026-10-30: the three new UK/AUS Demand Gen rows went in at rows 54–56 (block now
3–57), Suggestions Tracker rows 59–61 and Alerts rows 55–57, and the video row was renamed
ATA → ATU. Checked after the write: every headline, frontier and rank range reads `$3:$57`,
both mirror tabs reference each of the 55 tracker rows exactly once, and no `#REF!`.

The `DNU …` rows stay until their L30D spend reaches $0 (around late November). They still
carry real spend in the 30-day window, and deleting them would also leave `#REF!` in the
two mirror tabs.

## Reddit

Pulled from Windsor's `reddit` connector (account `a2_esoyaxwp5glg`), conversion value
`purchase_total_value`. Until 2026-10-30 the routine skipped Reddit: the four Reddit rows
were filled some other way and ran a day or two behind, and Reddit's spend (~$9.8k MTD,
4.8% of the October total) was missing from the routine's total MTD and the Pacing Curve.
Comparisons of total MTD or the pacing curve across that date are not like for like.

Two caveats. Reddit's incrementality factor (0.654) is set by hand in column D, not from
the Config table, because the D formula only knows Google, Meta and Microsoft. And
Reddit's attribution window is not Google's, so the Google-derived lag gross-up is a rough
fit for Reddit rows.

## Platform Status

Each run rewrites every tracker row's on-platform status: **Active**, **Paused**,
**Removed**, **Ended**, **Budget-limited**, `No delivery (90d)` or `No campaign ID`. Anything
else a platform reports (Meta `WITH_ISSUES`, Microsoft `Suspended`) is passed through as is
rather than forced into one of those.

Before this the routine never knew whether a campaign was running. The digest inferred
"paused" from zero L30D spend, which trails a pause by 30 days: the video campaign was
paused on 2026-11-04 and would have stayed at the top of Cut or Fix into December. Rows
that are Paused, Removed or Ended now leave the queues on the next run, and the digest
names the ones that still carry L30D spend. Status changes since the previous run are
listed in the run log and the digest.

**Where it comes from.** Four undated `last_90d` pulls (`connector:status`), one row per
campaign. Windsor resolves status at query time, not per cached day: on 2026-11-09 the
Microsoft rows for 2026-08-01, cached two months before the 2026-10-06 pause, read
"Paused". So a campaign's latest status comes back however long ago it last delivered,
as long as that was inside the window. Fields: Google and Microsoft `campaign_status`;
Meta `campaign_effective_status` plus `campaign_stop_time`, because a lifetime-budget
campaign past its stop time still reads `ACTIVE`; Reddit `campaign_effective_status`.

**What it cannot see.** A campaign that has not delivered in 90 days is absent from the
pull, so it reads `No delivery (90d)`: paused or not, it is not spending. Microsoft's
last delivery was 2026-10-06, so its rows flip from Paused to `No delivery (90d)` around
2027-01-04. Nothing else changes when that happens: they still sit outside the queues.

**Where it lives.** Found by its header in the header row (`Platform Status`; `Status` and
`Campaign Status` also match), so the column can be moved in the Sheet. If no such header
exists the routine adds one in the first column past the last header (Z) and copies the
header style from the cell to its left. It refuses if that column already holds anything.
Nothing in the Sheet's formulas reads it yet; whether Keep / Cut / Scale should is a
modelling decision.

A failed status pull leaves that platform's cells as they were and says so in the digest.
It never stops the run.

## Known gaps

- **Pacing % (col P) counts today as a full day.** See
  [Per-campaign Projected EOM](#per-campaign-projected-eom). Fixing it means switching O
  and P to MTD thru Yesterday (AA) and complete days.

- **Daily budget (column F) follows the platform from 2026-11-04.** Before that, Google
  and Microsoft budgets were entered by hand and drifted (2026-10-30: the video row said
  $700/day against $450 in Google Ads), and Meta/Reddit were estimated as L30D ÷ 30. It
  is the *configured* budget: a paused campaign that delivered in the last three days
  still shows its budget (Platform Status shows it as paused), and a campaign that has not delivered in three days drops out
  of the pull (Meta/Reddit then fall back to L30D ÷ 30; Google/Microsoft keep the old
  cell). Lifetime-budget Meta campaigns have no daily figure and always use the fallback.
- **The video campaign is judged on purchase iROAS.** It is a Views campaign, so it reads
  0.00x and sits in Cut or Fix whatever it does. Whether it belongs in the model is a
  modelling decision, not a data fix.
- **Two MTD figures that don't quite agree.** The pacing curve is built from date-level
  daily spend; the tracker rows come from a campaign-level pull. Date-level totals run
  ~1% higher, because they include spend not attributable to a currently-reported
  campaign row. Each run prints both and the drift; it only flags above 3%, which would
  mean something structural (a platform dropped out, a filter stopped matching) rather
  than this known gap. Measured 2026-08-12: $61,181 vs $60,551. The month-end
  projection is on the date-level basis, so it runs ~1% above `Total MTD spend` plus the
  run rate on the campaign basis.
- **`total_mtd`** sums every campaign Windsor returns, including any with no sheet row.
