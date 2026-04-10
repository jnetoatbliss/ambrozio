#!/usr/bin/env python3
"""Post weekly Clockify time entries from a Jira issue list.

Jira is fetched by Claude via the Atlassian MCP and piped in as JSON.
This script only talks to Clockify. See SKILL.md.

Input JSON (via --issues-json PATH or stdin):
{
  "week_start": "2026-04-06",   // optional; defaults to this week's Monday
  "week_end":   "2026-04-10",   // optional; defaults to Friday
  "issues": [
    {"key": "BLAC-1619", "summary": "[iOS] You tab - My Programs", "updated": "2026-04-10T12:30:52+0200"},
    ...
  ]
}
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
CLOCKIFY_API = "https://api.clockify.me/api/v1"


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        die(f"missing config at {CONFIG_PATH}")
    cfg = json.loads(CONFIG_PATH.read_text())
    for key in ("clockify_api_key", "clockify_workspace_id", "clockify_project_id"):
        if not cfg.get(key):
            die(f"config missing '{key}'")
    return cfg


def http_json(req: urllib.request.Request) -> dict | list:
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8") or "null"
            return json.loads(body)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        die(f"HTTP {e.code} {e.reason} on {req.full_url}\n{detail}")
    except urllib.error.URLError as e:
        die(f"network error on {req.full_url}: {e.reason}")


# ---------- Clockify ----------

def headers(cfg: dict) -> dict:
    return {"X-Api-Key": cfg["clockify_api_key"], "Content-Type": "application/json"}


def get(cfg: dict, path: str) -> dict | list:
    return http_json(urllib.request.Request(f"{CLOCKIFY_API}{path}", headers=headers(cfg)))


def post(cfg: dict, path: str, body: dict) -> dict | list:
    req = urllib.request.Request(
        f"{CLOCKIFY_API}{path}",
        data=json.dumps(body).encode(),
        headers=headers(cfg),
        method="POST",
    )
    return http_json(req)


def resolve_workspace_and_project(cfg: dict) -> tuple[str, str, str, str | None]:
    user = get(cfg, "/user")
    return (
        cfg["clockify_workspace_id"],
        user["id"],
        cfg["clockify_project_id"],
        cfg.get("clockify_task_id"),
    )


def existing_entries_for_day(cfg, workspace_id, user_id, day_start, day_end) -> list[dict]:
    params = urllib.parse.urlencode({
        "start": day_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": day_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "page-size": 200,
    })
    return get(cfg, f"/workspaces/{workspace_id}/user/{user_id}/time-entries?{params}")


# ---------- Planning ----------

@dataclass
class PlannedEntry:
    day: date
    start: datetime
    end: datetime
    description: str
    issue_key: str
    task_id: str | None


def _parse_iso(value: str) -> datetime | None:
    v = value.strip().replace("Z", "+00:00")
    # Normalize "+0200" / "-0530" → "+02:00" / "-05:30" for Python < 3.11
    if len(v) >= 5 and v[-5] in "+-" and v[-3] != ":":
        v = v[:-2] + ":" + v[-2:]
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def issue_updated_date(issue: dict, tz: ZoneInfo) -> date | None:
    updated = issue.get("updated")
    if not updated:
        return None
    dt = _parse_iso(updated)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).date()


def chunk_issues_into_days(issues: list[dict], num_days: int, max_per_day: int = 2) -> list[list[dict]]:
    """Pack issues into `num_days` buckets with up to `max_per_day` issues each.

    Front-loads days to `max_per_day` items, leaving fewer-item buckets at the end.
    Order is preserved (caller is expected to sort by Jira `updated` ascending so
    older work lands earlier in the week — matching "finish one, then next").
    """
    if num_days <= 0:
        return []
    n = len(issues)
    if n > num_days * max_per_day:
        raise ValueError(
            f"too many issues ({n}) for {num_days} days at max {max_per_day}/day; "
            f"raise --max-per-day or trim the list"
        )
    if n <= num_days:
        sizes = [1] * n + [0] * (num_days - n)
    else:
        extras = n - num_days
        sizes = [2 if i < extras else 1 for i in range(num_days)]
    chunks: list[list[dict]] = []
    cursor = 0
    for size in sizes:
        chunks.append(issues[cursor : cursor + size])
        cursor += size
    return chunks


def plan_week(
    issues: list[dict],
    task_id: str | None,
    week_start: date,
    week_end: date,
    hours_per_day: float,
    tz: ZoneInfo,
    day_start_hour: int,
    max_per_day: int = 2,
) -> list[PlannedEntry]:
    if not issues:
        return []

    today = date.today()
    weekdays: list[date] = []
    for offset in range((week_end - week_start).days + 1):
        day = week_start + timedelta(days=offset)
        if day.weekday() >= 5 or day > today:
            continue
        weekdays.append(day)
    if not weekdays:
        return []

    sorted_issues = sorted(issues, key=lambda i: issue_updated_date(i, tz) or date.min)
    chunks = chunk_issues_into_days(sorted_issues, len(weekdays), max_per_day)

    plan: list[PlannedEntry] = []
    for day, day_issues in zip(weekdays, chunks):
        if not day_issues:
            continue
        per_issue = hours_per_day / len(day_issues)
        cursor = datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(hour=day_start_hour)
        for issue in day_issues:
            end = cursor + timedelta(hours=per_issue)
            plan.append(
                PlannedEntry(
                    day=day,
                    start=cursor,
                    end=end,
                    description=f"{issue['key']} - {issue['summary'].strip()}",
                    issue_key=issue["key"],
                    task_id=task_id,
                )
            )
            cursor = end
    return plan


def print_plan(plan: list[PlannedEntry]) -> None:
    if not plan:
        print("no entries to create")
        return
    current_day: date | None = None
    for e in plan:
        if e.day != current_day:
            print(f"\n=== {e.day.isoformat()} ({e.day.strftime('%a')}) ===")
            current_day = e.day
        hours = (e.end - e.start).total_seconds() / 3600
        print(f"  {e.start.strftime('%H:%M')}–{e.end.strftime('%H:%M')} ({hours:.2f}h)  {e.description}")
    total = sum((e.end - e.start).total_seconds() for e in plan) / 3600
    print(f"\ntotal: {total:.2f}h across {len({e.day for e in plan})} day(s)")


def create_entries(cfg, workspace_id, project_id, plan: list[PlannedEntry]) -> None:
    for e in plan:
        body = {
            "start": e.start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": e.end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "billable": True,
            "description": e.description,
            "projectId": project_id,
        }
        if e.task_id:
            body["taskId"] = e.task_id
        post(cfg, f"/workspaces/{workspace_id}/time-entries", body)
        print(f"  ✓ {e.day} {e.start.strftime('%H:%M')} {e.description}")


def load_input(path: str | None) -> dict:
    if path and path != "-":
        raw = Path(path).read_text()
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        die("no input JSON provided (use --issues-json PATH or pipe to stdin)")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        die(f"invalid JSON input: {e}")


def default_week() -> tuple[date, date]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Post Clockify time entries from a Jira issue list")
    parser.add_argument("--issues-json", help="Path to issues JSON (use '-' or omit for stdin)")
    parser.add_argument("--hours-per-day", type=float, default=8.0)
    parser.add_argument("--max-per-day", type=int, default=1, help="max tickets per weekday (default 1)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="create entries even if day already has logged time")
    args = parser.parse_args()

    cfg = load_config()
    tz = ZoneInfo(cfg.get("timezone") or "UTC")

    payload = load_input(args.issues_json)
    issues = payload.get("issues") or []
    if not issues:
        die("input JSON has no 'issues'")

    default_start, default_end = default_week()
    week_start = date.fromisoformat(payload["week_start"]) if payload.get("week_start") else default_start
    week_end = date.fromisoformat(payload["week_end"]) if payload.get("week_end") else default_end
    print(f"week: {week_start} → {week_end}   issues: {len(issues)}")

    workspace_id, user_id, project_id, task_id = resolve_workspace_and_project(cfg)
    print(f"clockify: workspace={workspace_id} project={project_id} task={task_id}")

    plan = plan_week(
        issues,
        task_id,
        week_start,
        week_end,
        args.hours_per_day,
        tz,
        cfg.get("workday_start_hour", 9),
        max_per_day=args.max_per_day,
    )
    print_plan(plan)

    if args.dry_run:
        print("\n(dry-run: no entries created)")
        return

    if not args.force:
        days_with_time: set[date] = set()
        for offset in range((week_end - week_start).days + 1):
            day = week_start + timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
            day_end = day_start + timedelta(days=1)
            if existing_entries_for_day(cfg, workspace_id, user_id, day_start, day_end):
                days_with_time.add(day)
        if days_with_time:
            die(
                f"these day(s) already have Clockify entries: {sorted(days_with_time)}. "
                f"Re-run with --force to add anyway."
            )

    print("\ncreating entries:")
    create_entries(cfg, workspace_id, project_id, plan)
    print("done.")


if __name__ == "__main__":
    main()
