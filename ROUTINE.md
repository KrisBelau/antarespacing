# Scheduling the daily refresh

**Nothing is scheduled right now.** Create the Routine from the claude.ai Routines UI —
not from a Claude session — for the reason below, then paste the prompt at the bottom.

## Why the UI, specifically

Routines carry their own set of MCP connectors, and where the Routine is created decides
whether it gets any:

| Created via | Carries connectors? |
|---|---|
| claude.ai Routines UI (`http_api`) | yes — Windsor.ai, Slack, Google Drive, … |
| A Claude session, via the trigger tool (`meta_mcp`) | no |

A Routine with no connectors fires a session with no `mcp__*` tools, so the
Windsor pull and the Slack post both fail. The trigger tool does accept a `connectors`
parameter, but it is disabled for this org, so a session cannot grant them.

The existing "Check Row House Pacing" and "Antares Performance Review" Routines are
built this way and do carry connectors — same pattern, same time slot.

## Settings

| | |
|---|---|
| Schedule | `0 14 * * *` (UTC) = 07:00 Pacific during PDT |
| Repository | `KrisBelau/antarespacing` |
| Connectors | Windsor.ai, Slack, Google Drive |
| Notifications | push, so a failed run surfaces |

**Daylight saving:** cron is UTC and does not follow it. `0 14` is 07:00 PDT but becomes
06:00 PST when DST ends (2026-11-01). Change to `0 15 * * *` then, back to `0 14` in
March. Consider `0 14 * * 1-5` if weekend runs are noise — spend still accrues at
weekends, so the curve simply catches up on Monday.

## Prompt

```
Daily Antares / AutoTune paid-media pacing refresh.

The authoritative procedure is `.claude/commands/antares-pacing-refresh.md` in this repo
(KrisBelau/antarespacing). Invoke it as /antares-pacing-refresh. It covers the nine
Windsor.ai pulls, the run command, and the Slack post.

PREREQUISITE the command file does not cover: the Google service-account key is
deliberately not committed to the repo. Before running, fetch it from Google Drive —
file id 1gmvZrCxDJpIK1PYE3Nq5gTf313Yng3tJ ("ziggyadsbot-8795c9006c2c.json", in the
AntaresPacing folder). Base64-decode it to a path OUTSIDE the repo working tree
(e.g. /tmp/sa-key.json), chmod 600, and pass that path as GSHEET_SA_KEYFILE. Never
commit it or write it anywhere inside the repo.

Python deps: if gspread / google-auth are missing, `pip install gspread google-auth
google-api-python-client`. If `cryptography` then fails to import, reinstall it with
`pip install --upgrade --force-reinstall cffi cryptography`.

Post the digest to #antares-pacing-internal (channel id C0BQR78495Y). This is a
private, non-shared channel — #antares-pacing (C0BPYHZUH9S) is an external Slack
Connect channel and rejects bot-posted messages.

STOP AND DO NOT POST if any of these are true — report the failure instead:
  - any of the nine Windsor pulls returns empty
  - the Sheet is unreachable or the service account gets a 403
  - fewer than ~40 of the 45 campaign rows match by name
  - the run log shows a drift flag above 3% on the MTD cross-check

Do not change the Sheet's formulas or the routine's logic. All budget suggestions in the
digest are review-gated; never apply them to the ad platforms.
```

## If you would rather not depend on connector auth

The routine also runs fully headless over REST, with no MCP at all, given three
environment secrets: `WINDSOR_API_KEY`, `SLACK_BOT_TOKEN`, `GSHEET_SA_KEY_JSON`. Drop
`WINDSOR_CACHE` and `SLACK_OUT` from the invocation and it goes end to end. More setup,
but it does not break if a connector's OAuth lapses.
