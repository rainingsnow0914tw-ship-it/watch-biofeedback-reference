"""Conservative minute-level evidence for the watch bridge.

An absent record is unknown.  An off-body minute needs an explicit device
state; neither a phone outage nor a stale last reading is off-body evidence.
No state is carried across a gap.  These are data-quality labels, not health
or clinical conclusions.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

ON = {"on", "inferred_on", "True"}
OFF = {"off"}
MIN_VALID_DAY_RATIO = 0.70  # product quality gate; not a medical threshold


def _minute(r: dict, day: str) -> int | None:
    t = r.get("t")
    if not isinstance(t, str) or not t.startswith(day + "T"):
        return None
    try:
        h, m = int(t[11:13]), int(t[14:16])
    except (ValueError, IndexError):
        return None
    return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None


def _has_signal(r: dict) -> bool:
    kind = r.get("type")
    if kind == "hr_ibi":
        return r.get("status") == 1 and isinstance(r.get("hr"), (int, float)) and r["hr"] > 0
    if kind == "hr":
        return isinstance(r.get("bpm"), (int, float)) and r["bpm"] > 0
    if kind == "skin_temp":
        return isinstance(r.get("skin"), (int, float))
    if kind == "motion":
        return r.get("trust") is True and isinstance(r.get("activity"), (int, float))
    return False


def classify(recs: list[dict], day: str, now: datetime | None = None) -> dict:
    """Partition elapsed minutes into valid, explicit off, and unknown.

    A minute with conflicting on/off evidence stays unknown.  Positive
    observation requires both a wearer state and an actual sensor sample.
    """
    d = date.fromisoformat(day)
    now = now or datetime.now()
    if d == now.date():
        elapsed = max(0, min(1440, now.hour * 60 + now.minute + 1))
    elif d > now.date():
        elapsed = 0
    else:
        elapsed = 1440
    on_signal = set()
    off_evidence = set()
    hr_signal = set()
    for r in recs:
        minute = _minute(r, day)
        if minute is None or minute >= elapsed:
            continue
        body = str(r.get("on_body"))
        signal = _has_signal(r)
        if body in ON and signal:
            on_signal.add(minute)
            if r.get("type") in ("hr_ibi", "hr"):
                hr_signal.add(minute)
        if (body in OFF and r.get("type") in ("motion", "availability")) or (
            r.get("type") == "mark" and r.get("tag") == "off_wrist"
        ):
            off_evidence.add(minute)
    valid = on_signal - off_evidence
    off = off_evidence - on_signal
    conflict = on_signal & off_evidence
    states = ["unknown"] * elapsed
    for m in valid:
        states[m] = "valid"
    for m in off:
        states[m] = "off"
    unknown = elapsed - len(valid) - len(off)
    ratio = len(valid) / elapsed if elapsed else 0.0
    if d == now.date() and elapsed < 1440 and ratio >= MIN_VALID_DAY_RATIO:
        quality = "partial"
    else:
        quality = "sufficient" if elapsed == 1440 and ratio >= MIN_VALID_DAY_RATIO else "insufficient"
    return {
        "minutes": elapsed,
        "valid_minutes": len(valid),
        "off_minutes": len(off),
        "unknown_minutes": unknown,
        "conflict_minutes": len(conflict),
        "hr_minutes": len(hr_signal - off_evidence),
        "valid_ratio": round(ratio, 4),
        "quality": quality,
        "states": states,
    }


def load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    out.append(row)
            except json.JSONDecodeError:
                continue
    return out


def window_coverage(data_dir: Path, start: datetime, end: datetime, now: datetime | None = None) -> tuple[int, int]:
    """Return valid/total minutes in a sleep interval, across dates."""
    if end <= start:
        return 0, 0
    now = now or datetime.now()
    cursor = start.replace(second=0, microsecond=0)
    stop = end.replace(second=0, microsecond=0)
    if end.second or end.microsecond:
        stop += timedelta(minutes=1)
    total = valid = 0
    cache: dict[str, list[str]] = {}
    while cursor < stop and cursor <= now:
        day = cursor.strftime("%Y-%m-%d")
        if day not in cache:
            cache[day] = classify(load_records(data_dir / f"{day}.jsonl"), day, now)["states"]
        minute = cursor.hour * 60 + cursor.minute
        total += 1
        if minute < len(cache[day]) and cache[day][minute] == "valid":
            valid += 1
        cursor += timedelta(minutes=1)
    return valid, total


def headline(cover: dict, day: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    scope = f"截至 {now:%H:%M}" if day == now.strftime("%Y-%m-%d") else "全日"
    valid = cover["valid_minutes"]
    total = cover["minutes"]
    ratio = (valid / total * 100) if total else 0
    return (f"資料覆蓋（{scope}）：有效 {valid / 60:.1f}/{total / 60:.1f} 小時 "
            f"({ratio:.0f}%)；明確離腕 {cover['off_minutes'] / 60:.1f} 小時；"
            f"未知 {cover['unknown_minutes'] / 60:.1f} 小時")
