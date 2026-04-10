---
name: clockify-report
description: Auto-fill Clockify time entries from Jira tickets. Use when the user asks to report hours to Clockify, log time for the week, sync Jira tickets to Clockify, or run the weekly timesheet. Claude fetches Jira issues via the Atlassian MCP, then a helper script posts them to Clockify under the project the user is allocated to, distributing 8h/day.
---

# clockify-report

Automates weekly Clockify time entries from Jira tickets assigned to the user.

## Flow
1. **Fetch issues** via the Atlassian MCP (`mcp__atlassian__getAccessibleAtlassianResources` → `mcp__atlassian__searchJiraIssuesUsingJql`) using JQL like:
   ```
   project = BLAC AND assignee = currentUser() AND updated >= "<monday>" ORDER BY updated DESC
   ```
   Default project is `BLAC` (BLACKROLL). Use the cloudId returned by the resources tool.

2. **Build an input JSON** for the script:
   ```json
   {
     "week_start": "2026-04-06",
     "week_end":   "2026-04-10",
     "issues": [
       {"key": "BLAC-1619", "summary": "[iOS] You tab - My Programs", "updated": "2026-04-10T12:30:52+0200"}
     ]
   }
   ```
   Write it to a tempfile (e.g. `/tmp/clockify-week.json`).

3. **Dry-run first**, always:
   ```bash
   python3 ~/.claude/skills/clockify-report/scripts/report.py \
     --issues-json /tmp/clockify-week.json --dry-run
   ```
   Show the plan to the user and wait for explicit confirmation.

4. **Post for real** only after confirmation (drop `--dry-run`). The script refuses to post on days that already have Clockify entries unless `--force` is passed.

## What the script does
- Reads `config.json` (Clockify API key, project name, timezone, workday start).
- Resolves workspace + the configured project (`Development: Project Dept`) and its tasks.
- Distributes `--hours-per-day` (default 8) evenly across the issues active on each weekday.
- Writes entries with:
  - **description**: `BLAC-XXXX - <Jira summary>`
  - **projectId** / **taskId**: from the configured project (task matched by issue key in name, else the first active task).
  - **start / end**: weekday working hours starting at `workday_start_hour`.

## Flags
- `--issues-json PATH` (or `-` / stdin) — required input
- `--hours-per-day 8` — daily target
- `--dry-run` — print plan without writing
- `--force` — post even if day already has entries

## Files
- `scripts/report.py` — Clockify-only poster (no Jira calls)
- `config.json` — Clockify API key + project name (chmod 600, never commit)
