#!/usr/bin/env python3
"""Show what Clockify already has for a date range and flag empty weekdays.

Run this BEFORE building the issue list so a missed day (typically the previous
Friday) is caught and included in the report.

Usage:
  python3 scripts/check_week.py                      # last Monday .. today
  python3 scripts/check_week.py --from 2026-10-02 --to 2026-10-09
  python3 scripts/check_week.py --weeks 2            # the last two full weeks + this one
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
CLOCKIFY_API = "https://api.clockify.me/api/v1"


def get(cfg: dict, path: str) -> dict | list:
    req = urllib.request.Request(f"{CLOCKIFY_API}{path}", headers={"X-Api-Key": cfg["clockify_api_key"]})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8") or "null")


def main() -> None:
    ap = argparse.ArgumentParser(description="List Clockify entries per weekday and flag gaps")
    ap.add_argument("--from", dest="start", help="first day (YYYY-MM-DD)")
    ap.add_argument("--to", dest="end", help="last day (YYYY-MM-DD), default today")
    ap.add_argument("--weeks", type=int, default=1, help="how many Mondays back to start from (default 1)")
    args = ap.parse_args()

    if not CONFIG_PATH.exists():
        sys.exit(f"missing config at {CONFIG_PATH}")
    cfg = json.loads(CONFIG_PATH.read_text())
    tz = ZoneInfo(cfg.get("timezone") or "UTC")

    today = date.today()
    end = date.fromisoformat(args.end) if args.end else today
    if args.start:
        start = date.fromisoformat(args.start)
    else:
        this_monday = today - timedelta(days=today.weekday())
        start = this_monday - timedelta(weeks=args.weeks)

    user = get(cfg, "/user")
    ws = cfg["clockify_workspace_id"]
    q_start = datetime.combine(start, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    q_end = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    params = urllib.parse.urlencode({
        "start": q_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": q_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "page-size": 200,
    })
    entries = get(cfg, f"/workspaces/{ws}/user/{user['id']}/time-entries?{params}")

    by_day: dict[date, list[tuple[datetime, datetime, str]]] = defaultdict(list)
    for e in entries:
        iv = e["timeInterval"]
        s = datetime.fromisoformat(iv["start"].replace("Z", "+00:00")).astimezone(tz)
        en = datetime.fromisoformat(iv["end"].replace("Z", "+00:00")).astimezone(tz) if iv.get("end") else s
        by_day[s.date()].append((s, en, e.get("description") or ""))

    print(f"clockify entries {start} → {end} ({tz.key})\n")
    gaps: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            rows = sorted(by_day.get(day, []))
            total = sum((en - s).total_seconds() for s, en, _ in rows) / 3600
            label = f"{day.isoformat()} ({day.strftime('%a')})"
            if not rows:
                print(f"=== {label} ===  ⚠ NO ENTRIES")
                gaps.append(day)
            else:
                print(f"=== {label} ===  {total:.2f}h")
                for s, en, desc in rows:
                    print(f"  {s.strftime('%H:%M')}–{en.strftime('%H:%M')}  {desc}")
            print()
        day += timedelta(days=1)

    if gaps:
        print("weekdays with no entries:", ", ".join(d.isoformat() for d in gaps))
    else:
        print("no gaps.")


if __name__ == "__main__":
    main()
