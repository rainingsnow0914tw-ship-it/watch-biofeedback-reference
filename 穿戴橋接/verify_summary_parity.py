#!/usr/bin/env python3
"""Compare installed phone Kotlin summary to Python using the same phone raw bytes.

Only field paths and statuses are printed.  Raw physiology stays in memory on
this computer; the phone's /summary?build=1 refreshes its derived summary.
Use closed dates so an in-progress file cannot change between reads.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import 摘要層

DATA = Path(__file__).resolve().parent.parent / "生理資料"
CFG = DATA / "手錶橋接" / "puller_config.json"


def differences(left: object, right: object, path: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        out = []
        for key in sorted(left.keys() | right.keys()):
            child = f"{path}.{key}" if path else key
            if key not in left or key not in right:
                out.append(child)
            else:
                out.extend(differences(left[key], right[key], child))
        return out
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return [path + ".length"]
        return [p for i, (a, b) in enumerate(zip(left, right))
                for p in differences(a, b, f"{path}[{i}]")]
    return [] if left == right else [path]


def python_summary(day: str, records: list[dict]) -> list[dict]:
    by_hour: dict[int, list[dict]] = defaultdict(list)
    for record in records:
        try:
            by_hour[摘要層.T(record).hour].append(record)
        except (KeyError, TypeError, ValueError):
            continue
    hours = [摘要層.hour_summary(day, hour, by_hour[hour]) for hour in range(24)]
    return hours + [摘要層.day_summary(day, hours, records)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare same-input Python and phone Kotlin summaries")
    parser.add_argument("--host", help="current phone IP or http://host:port; leaves saved config unchanged")
    parser.add_argument("days", nargs="+", help="closed dates, YYYY-MM-DD")
    args = parser.parse_args()
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    host = str(args.host or cfg["ip"])
    base = host if host.startswith("http") else f"http://{host}:8788"

    def get(path: str) -> bytes:
        req = urllib.request.Request(base + path,
                                     headers={"Authorization": "Bearer " + cfg["token"]})
        with urllib.request.urlopen(req, timeout=45) as response:
            return response.read()

    failed = False
    for day in args.days:
        if day >= datetime.now().strftime("%Y-%m-%d"):
            print(f"{day}: in-progress or future date; skipped")
            failed = True
            continue
        before = json.loads(get("/status"))
        raw = [json.loads(line) for line in get(f"/day?d={day}").splitlines() if line.strip()]
        mobile = [json.loads(line) for line in get(f"/summary?d={day}&build=1").splitlines()
                  if line.strip()]
        after = json.loads(get("/status"))
        def fingerprint(status: dict):
            return next(((f.get("bytes"), f.get("modified")) for f in status.get("files", [])
                         if f.get("day") == day), None)
        if fingerprint(before) != fingerprint(after):
            print(f"{day}: phone raw file changed during comparison; inconclusive")
            failed = True
            continue
        expected = python_summary(day, raw)
        paths = differences(expected, mobile)
        phone_day = next((r for r in mobile if r.get("layer") == "day"), {})
        print(f"{day}: phone_rows={len(mobile)} raw_records={len(raw)} "
              f"python={expected[-1]['verdict']} phone={phone_day.get('verdict')} "
              f"different_fields={len(paths)}")
        if paths:
            print("  different_paths=" + ", ".join(paths[:16]))
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
