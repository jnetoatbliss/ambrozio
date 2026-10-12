#!/usr/bin/env python3
"""List Clockify workspace/client/project/task IDs using the API key in config.json.

Usage:
  python3 scripts/discover_ids.py                 # everything, grouped by client
  python3 scripts/discover_ids.py --client dept   # only clients whose name contains "dept"
  python3 scripts/discover_ids.py --project dev   # only projects whose name contains "dev"
"""
import argparse, json, sys, urllib.parse, urllib.request
from collections import defaultdict
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--client", help="substring filter on client name (case-insensitive)")
ap.add_argument("--project", help="substring filter on project name (case-insensitive)")
ap.add_argument("--no-tasks", action="store_true", help="skip listing tasks")
args = ap.parse_args()

cfg_path = Path(__file__).resolve().parent.parent / "config.json"
cfg = json.loads(cfg_path.read_text())
key = cfg.get("clockify_api_key", "")
if not key or key.startswith("REPLACE"):
    sys.exit(f"put your API key in {cfg_path} first (clockify_api_key)")

H = {"X-Api-Key": key}
def g(p):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request("https://api.clockify.me/api/v1" + p, headers=H)).read())

def match(needle, hay):
    return not needle or needle.lower() in (hay or "").lower()

user = g("/user")
print(f"user: {user['name']} <{user['email']}>  default workspace: {user['defaultWorkspace']}\n")

for ws in g("/workspaces"):
    print(f"workspace: {ws['name']}  id={ws['id']}")
    clients = {c["id"]: c["name"] for c in g(f"/workspaces/{ws['id']}/clients?page-size=200")}
    by_client = defaultdict(list)
    for p in g(f"/workspaces/{ws['id']}/projects?page-size=200&archived=false"):
        by_client[p.get("clientId") or ""].append(p)

    for cid in sorted(by_client, key=lambda c: clients.get(c, "").lower()):
        cname = clients.get(cid, "(no client)") if cid else "(no client)"
        projs = [p for p in by_client[cid] if match(args.project, p["name"])]
        if not match(args.client, cname) or not projs:
            continue
        print(f"\n  client: {cname}  id={cid or '-'}")
        for p in sorted(projs, key=lambda p: p["name"].lower()):
            flags = []
            if p.get("billable"): flags.append("billable")
            if not p.get("public", True): flags.append("private")
            if p.get("note"): flags.append(f"note={p['note'][:40]!r}")
            extra = f"  [{', '.join(flags)}]" if flags else ""
            print(f"    project: {p['name']}  id={p['id']}{extra}")
            if args.no_tasks:
                continue
            for t in g(f"/workspaces/{ws['id']}/projects/{p['id']}/tasks?page-size=200"):
                status = "" if t.get("status") == "ACTIVE" else f"  ({t.get('status')})"
                print(f"      task: {t['name']}  id={t['id']}{status}")
