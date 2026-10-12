---
name: clockify-report
description: Auto-fill Clockify time entries from Jira tickets. Use when the user asks to report hours to Clockify, log time for the week, sync Jira tickets to Clockify, or run the weekly timesheet. Claude fetches Jira issues via the Atlassian MCP (by status history, not current assignee), then a helper script posts them to Clockify under the project the user is allocated to, distributing 8h/day.
---

# clockify-report

Automates weekly Clockify time entries from the Jira tickets the user actually worked on.

## Flow

### 0. Check for gaps first
```bash
python3 ~/.claude/skills/clockify-report/scripts/check_week.py
```
Lists what Clockify already has from last Monday to today and flags empty weekdays.
If the previous Friday (or any earlier weekday) is empty, include it in this run as a
separate single-day input (see step 4). Days that already have entries are skipped by
the poster unless `--force` is passed.

### 1. Fetch issues via the Atlassian MCP
Use the `mcp__jira__*` tools (`getAccessibleAtlassianResources` → `searchJiraIssuesUsingJql`).
Cloud id for dept-nl: `8f3b02d8-c59e-4884-93f2-8e680906833d`.

**Query by status history, not current assignee.** On BLAC, finished bugs are reassigned
to QA the same day, so `assignee = currentUser()` misses most of the week:

```
project = BLAC AND status CHANGED BY currentUser() DURING ("2026/10/05", "2026/10/12") ORDER BY updated ASC
```

- Dates use slashes (`yyyy/MM/dd`). The end date is exclusive, so pass the *next* Monday.
- Pass `fields: ["summary","status","updated","assignee","parent","issuetype"]` and
  `maxResults: 100`. Results are often too big for the tool window; read the saved file
  with `jq` and print `key, updated, status, assignee, issuetype, summary, parent.summary`.
- Do **not** combine this with `assignee WAS currentUser()` in an OR. The DURING clause
  silently stops filtering and returns everything the user ever touched.

### 2. Filter the list
Drop:
- `[Android]` / `Android` subtasks and parent Stories/Bugs with no assignee. The user bulk-
  transitions siblings and parents when closing a story; only the iOS subtask is their work.
- A parent whose iOS subtask is already in the list (e.g. keep BLAC-4792, drop BLAC-4222).
- `CANCELLED` tickets and anything in `To Do`.
- Tickets whose changelog shows the user's work was in an earlier week. When in doubt call
  `getJiraIssue` with `expand: "changelog"` and look for the user's own status/assignee
  changes inside the week.

Keep `[Mobile]` tickets the user transitioned; those are usually iOS work.

A ticket assigned to the user then handed to someone else with no status change is
borderline (investigation time). Include it, but flag it in the plan.

### 3. Build the description
`BLAC-XXXX - <summary>`. When the subtask summary is just `iOS` or `[iOS]`, prefix the
parent story title so the timesheet is readable:
`BLAC-6011 - iOS - [Mobile] For Flexible Program -> 'Start' button ...`.

### 4. Write the input JSON
```json
{
  "week_start": "2026-10-05",
  "week_end":   "2026-10-09",
  "issues": [
    {"key": "BLAC-5962", "summary": "[iOS] Community - Like changed ...", "updated": "2026-10-06T17:00:52+0200"}
  ]
}
```
- `updated` only sets the ordering (oldest first, day granularity). Use the user's own
  transition timestamp from the changelog when the Jira `updated` field reflects someone
  else's later touch.
- For a missed previous Friday, write a second file with `week_start == week_end`.
- Write files to the session scratchpad, not `/tmp`.

### 5. Dry-run, show the plan, wait for confirmation
```bash
python3 ~/.claude/skills/clockify-report/scripts/report.py --issues-json <path> --dry-run
```
`--max-per-day` defaults to auto: every ticket fits across the weekdays (ceil(n / days)).
List every entry to the user with day, time, key and description, and flag anything
borderline or excluded on purpose. Only post after an explicit "go".

### 6. Post
Drop `--dry-run`. The script refuses days that already have entries unless `--force`.

## What the script does
- Reads `config.json` (Clockify API key, workspace/project/task IDs, timezone, workday start).
- Sorts issues by `updated` ascending, chunks them into weekday buckets, front-loaded.
- Each bucket shares `--hours-per-day` evenly. Entries start at `workday_start_hour`.
- Writes `description`, `projectId`, `taskId`, `start`, `end`, `billable: true`.

## Flags
- `--issues-json PATH` (or `-` / stdin) — required input
- `--hours-per-day 8` — daily target
- `--max-per-day N` — cap tickets per weekday; default auto-fits all tickets
- `--dry-run` — print plan without writing
- `--force` — post even if a day already has entries

## Files
- `scripts/report.py` — Clockify-only poster (no Jira calls)
- `scripts/check_week.py` — list existing entries per weekday, flag gaps
- `scripts/discover_ids.py` — one-off helper to find workspace/project/task IDs
- `config.json` — Clockify API key + IDs (chmod 600, never commit)
