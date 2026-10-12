# clockify-report

Claude Code skill that fills in your weekly Clockify timesheet from the Jira tickets you worked on. It pulls issues via the Atlassian MCP by **status history** (tickets you transitioned that week, even if QA now owns them), packs them sequentially into weekdays, and posts the entries to Clockify under the project/task you're allocated to.

## What it does

- **Description** is built as `BLAC-XXXX - <Jira summary>` for each entry.
- **Project / Task** are pre-configured by IDs in `config.json` (no fragile name lookups).
- **Distribution**: tickets are sorted by Jira `updated` ascending (oldest first), then chunked into weekday buckets. By default every ticket is fitted across the week (`ceil(n / weekdays)` per day, sharing the 8h evenly). Pass `--max-per-day N` to cap it.
- **Filtering**: Claude drops Android subtasks, parent stories, cancelled tickets, `To Do` tickets, and anything whose changelog shows the work happened in an earlier week.
- **Gap check**: `scripts/check_week.py` lists what Clockify already has and flags empty weekdays, so a missed Friday gets included in the next run.
- **Safety**: refuses to post on any weekday that already has Clockify entries unless `--force` is passed.

## Layout

```
skills/clockify-report/
├── SKILL.md              # Claude Code skill manifest (auto-discovered)
├── README.md             # this file
├── config.example.json   # template — copy to config.json and fill in
├── config.json           # local secrets (gitignored)
└── scripts/
    ├── report.py         # Clockify-only poster (Jira is pulled by Claude via MCP)
    ├── check_week.py     # list existing entries per weekday, flag gaps
    └── discover_ids.py   # one-off: find workspace/project/task IDs
```

## First-time setup

### 1. Clone the skill into Claude's skills folder

Claude Code discovers personal skills in `~/.claude/skills/`. Symlink (recommended, so the repo stays the source of truth) or copy:

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/clockify-report" ~/.claude/skills/clockify-report
```

### 2. Install the Atlassian MCP

The skill expects the Atlassian MCP to be available so Claude can fetch your Jira issues. Add it to Claude Code (`/mcp` → add server) following Atlassian's docs. After OAuth, you should see tools like `mcp__atlassian__searchJiraIssuesUsingJql`.

### 3. Create `config.json`

```bash
cd skills/clockify-report
cp config.example.json config.json
chmod 600 config.json
```

Fill in the four IDs:

| Field | How to get it |
|---|---|
| `clockify_api_key` | Clockify → Profile Settings → Generate API key |
| `clockify_workspace_id` | `GET /user` → `defaultWorkspace` |
| `clockify_project_id` | `GET /workspaces/{ws}/projects` → find your project |
| `clockify_task_id` | `GET /workspaces/{ws}/projects/{p}/tasks` → find your task |

Quick discovery script:

```bash
python3 - <<'PY'
import json, urllib.request
KEY = "YOUR_API_KEY_HERE"
H = {"X-Api-Key": KEY}
def g(p):
    return json.loads(urllib.request.urlopen(urllib.request.Request("https://api.clockify.me/api/v1"+p, headers=H)).read())
u = g("/user"); ws = u["defaultWorkspace"]
print("workspace_id:", ws)
for p in g(f"/workspaces/{ws}/projects?page-size=200&archived=false"):
    print(" project:", p["name"], p["id"])
    for t in g(f"/workspaces/{ws}/projects/{p['id']}/tasks?page-size=200"):
        print("    task:", t["name"], t["id"])
PY
```

Other config fields:

- `timezone` — IANA name, e.g. `Europe/Lisbon`. Affects when "9am Monday" actually is.
- `workday_start_hour` — integer hour entries start at (default `9`).

## How to use it

### Through Claude Code (the easy way)

In any Claude Code session, just ask:

> "run the clockify report for this week"
> "log my hours to clockify"
> "sync jira to clockify for last week"

Claude will:
1. Run `check_week.py` to see what is already logged and whether the previous Friday is missing.
2. Run a JQL like `project = BLAC AND status CHANGED BY currentUser() DURING ("<monday>", "<next monday>") ORDER BY updated ASC` (slash dates, exclusive end).
3. Filter out Android subtasks, parent stories, cancelled tickets, and prior-week work (verified via the issue changelog).
4. Build an issues JSON (plus a one-day file for any missed Friday).
5. Run `report.py --dry-run` and list every planned entry.
6. Wait for your confirmation, then re-run without `--dry-run`.

Why status history and not assignee: on BLAC a finished bug is reassigned to QA the same day, so by Monday `assignee = currentUser()` only returns the tickets still open on your side.

### Checking for gaps

```bash
python3 scripts/check_week.py                         # last Monday .. today
python3 scripts/check_week.py --from 2026-10-02 --to 2026-10-09
```

### Manually (no Claude)

You can also run the script directly. Build an issues file:

```json
{
  "week_start": "2026-04-06",
  "week_end":   "2026-04-10",
  "issues": [
    {"key": "BLAC-1619", "summary": "[iOS] You tab - My Programs", "updated": "2026-04-10T12:30:52+0200"},
    {"key": "BLAC-1605", "summary": "[iOS] You tab - Overview",    "updated": "2026-04-10T12:30:11+0200"}
  ]
}
```

Then:

```bash
# always dry-run first
python3 scripts/report.py --issues-json /tmp/clockify-week.json --dry-run

# looks good? post for real
python3 scripts/report.py --issues-json /tmp/clockify-week.json
```

### Flags

| Flag | Default | Notes |
|---|---|---|
| `--issues-json PATH` | — | path to the input JSON, or `-` for stdin |
| `--hours-per-day N` | `8.0` | total hours to distribute per weekday |
| `--max-per-day N` | auto | cap tickets per weekday; auto fits all tickets across the week |
| `--dry-run` | off | print the plan without writing to Clockify |
| `--force` | off | post even if a day already has Clockify entries |

## Notes

- Weekends are skipped. Future days are skipped (so mid-week runs only fill up to today).
- The script writes to Clockify only; Jira data is supplied by Claude via the Atlassian MCP. This keeps the script small and avoids storing a Jira token.
- `config.json` is `chmod 600` and gitignored — never commit it.
