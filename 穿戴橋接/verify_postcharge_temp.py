"""Check September charge-to-wear temperature cases against the hourly guard.

Read-only.  Exit 1 if an on-body post-charge temperature says off outside
an hour that contains charging evidence.
"""

import datetime as dt
import json
import pathlib
import sys

BASE = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "穿戴橋接"))
from 摘要層 import temp_opinion

ON = {"on", "inferred_on"}


def moment(raw):
    value = str(raw).split("[")[0].replace("Z", "+00:00")
    return dt.datetime.fromisoformat(value)


motion = []
temps = []
for path in sorted((BASE / "生理資料" / "手錶橋接").glob("2026-09-*.jsonl")) + [
    BASE / "生理資料" / "手錶橋接" / "2026-10-01.jsonl"
]:
    if not path.exists():
        continue
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
                kind = row.get("type")
                if kind not in {"motion", "skin_temp"}:
                    continue
                row["_time"] = moment(row["t"])
                (motion if kind == "motion" else temps).append(row)
            except (ValueError, KeyError, TypeError):
                continue
motion.sort(key=lambda r: r["_time"])
temps.sort(key=lambda r: r["_time"])

events = []
for previous, current in zip(motion, motion[1:]):
    if previous.get("charging") is not True or current.get("charging") is not False:
        continue
    gap = (current["_time"] - previous["_time"]).total_seconds() / 60
    if gap < 0 or gap > 10:
        continue
    first_on = next((r for r in motion if current["_time"] <= r["_time"] <= current["_time"] + dt.timedelta(minutes=45)
                     and r.get("on_body") in ON), None)
    if first_on is None:
        continue
    readings = [r for r in temps if first_on["_time"] <= r["_time"] <= first_on["_time"] + dt.timedelta(minutes=25)
                and r.get("on_body") in ON and isinstance(r.get("skin"), (int, float))
                and isinstance(r.get("watch_ref"), (int, float))]
    outcomes = []
    for row in readings:
        delta = row["skin"] - row["watch_ref"]
        opinion, _ = temp_opinion(row["skin"], delta, charged=False)
        hour_start = row["_time"].replace(minute=0, second=0, microsecond=0)
        same_hour_charge = any(m.get("charging") is True and hour_start <= m["_time"] < hour_start + dt.timedelta(hours=1)
                               for m in motion)
        outcomes.append((opinion, delta, same_hour_charge))
    events.append({"day": first_on["_time"].strftime("%Y-%m-%d"),
                   "end_hour": current["_time"].strftime("%H:%M"),
                   "worn_delay_min": round((first_on["_time"] - current["_time"]).total_seconds() / 60, 1),
                   "readings": len(outcomes),
                   "off": sum(o == "off" for o, _, _ in outcomes),
                   "off_unprotected": sum(o == "off" and not charged for o, _, charged in outcomes),
                   "min_delta": round(min((delta for _, delta, _ in outcomes), default=0), 2)})

print("charge_to_worn_events", len(events))
print("events_with_temperature", sum(e["readings"] > 0 for e in events))
print("temperature_readings", sum(e["readings"] for e in events))
print("off_opinions", sum(e["off"] for e in events))
print("off_without_same_hour_charge", sum(e["off_unprotected"] for e in events))
for event in events:
    if event["readings"]:
        print("event", event)
if sum(e["off_unprotected"] for e in events):
    raise SystemExit(1)
