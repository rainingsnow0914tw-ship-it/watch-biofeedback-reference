"""Evidence-only quality gates for Health Connect heart rate and sleep.

Missing days are observation gaps.  They never mean zero heart rate or no
sleep.  These cutoffs control whether a personal baseline can be used; they
are provisional data-quality rules, not clinical thresholds.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

SAMSUNG_ORIGIN = "com.sec.android.app.shealth"
BASELINE_TARGET_DAYS = 30
MIN_CAPTURE_RATIO = 0.70
HEART_MIN_HOURS = 17
HEART_MIN_SAMPLES_PER_HOUR = 3
SLEEP_MIN_STAGE_COVERAGE = 0.95
ASLEEP_STAGES = {2, 4, 5, 6}
AWAKE_STAGES = {1, 3, 7}
KNOWN_STAGES = ASLEEP_STAGES | AWAKE_STAGES


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except ValueError:
        return None


def _origin(record: dict) -> str | None:
    metadata = record.get("metadata")
    return metadata.get("data_origin") if isinstance(metadata, dict) else None


def _capture_ratio(valid_days: set, source_days: set | None = None) -> float:
    span_days = source_days or valid_days
    if not span_days:
        return 0.0
    span = (max(span_days) - min(span_days)).days + 1
    return round(len(valid_days) / span, 4)


def baseline_status(valid_days: int, capture_ratio: float, minimum_days: int = 3) -> str:
    if valid_days < minimum_days:
        return "insufficient"
    if valid_days >= BASELINE_TARGET_DAYS and capture_ratio >= MIN_CAPTURE_RATIO:
        return "mature"
    return "growing"


def heart_rate_evidence(records: list[dict], now: datetime | None = None) -> tuple[list[dict], dict]:
    """Require 17 completed hours with 3 distinct Samsung samples each."""
    now = now or datetime.now().astimezone()
    by_day: dict = defaultdict(lambda: defaultdict(dict))
    source_days = set()
    for record in records:
        if _origin(record) != SAMSUNG_ORIGIN:
            continue
        dt = record.get("_dt")
        bpm = record.get("bpm")
        if not isinstance(dt, datetime) or not isinstance(bpm, (int, float)) or bpm <= 0:
            continue
        if dt.date() >= now.date():
            continue  # today is still growing
        source_days.add(dt.date())
        by_day[dt.date()][dt.hour].setdefault(dt.isoformat(), record)

    valid_days = {day for day, hours in by_day.items()
                  if sum(len(samples) >= HEART_MIN_SAMPLES_PER_HOUR
                         for samples in hours.values()) >= HEART_MIN_HOURS}
    accepted = [record for day in sorted(valid_days)
                for hour in by_day[day].values() for record in hour.values()]
    ratio = _capture_ratio(valid_days, source_days)
    return accepted, {
        "rule": "Samsung 來源；完整日 ≥17 小時且每小時 ≥3 個不同時間點；不補空窗",
        "source_days": len(source_days),
        "valid_days": len(valid_days),
        "capture_ratio": ratio,
        "target_days": BASELINE_TARGET_DAYS,
        "target_capture_ratio": MIN_CAPTURE_RATIO,
        "excluded_days": len(source_days - valid_days),
    }


def _union_seconds(intervals: list[tuple[datetime, datetime]]) -> float:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return sum((end - start).total_seconds() for start, end in merged)


def sleep_evidence(records: list[dict]) -> tuple[list[dict], dict]:
    """Keep distinct, staged Samsung sessions; report observed nights only."""
    source_sessions = 0
    source_days = set()
    accepted: dict[tuple[datetime, datetime], dict] = {}
    for record in records:
        if _origin(record) != SAMSUNG_ORIGIN:
            continue
        source_sessions += 1
        end = _parse_time(record.get("end_time") or record.get("session_end_time"))
        start = _parse_time(record.get("start_time") or record.get("session_start_time"))
        duration = record.get("duration_seconds")
        if end and start is None and isinstance(duration, (int, float)) and duration > 0:
            start = end - timedelta(seconds=duration)
        if not start or not end or end <= start:
            continue
        source_days.add(start.date())
        span_seconds = (end - start).total_seconds()
        all_intervals: list[tuple[datetime, datetime]] = []
        asleep_intervals: list[tuple[datetime, datetime]] = []
        bad_stage = False
        stages = record.get("stages")
        if not isinstance(stages, list) or not stages:
            continue
        for stage in stages:
            if not isinstance(stage, dict):
                bad_stage = True
                break
            try:
                kind = int(stage.get("stage"))
            except (TypeError, ValueError):
                bad_stage = True
                break
            a = _parse_time(stage.get("start_time"))
            b = _parse_time(stage.get("end_time"))
            if kind not in KNOWN_STAGES or not a or not b or b <= a or a < start or b > end:
                bad_stage = True
                break
            all_intervals.append((a, b))
            if kind in ASLEEP_STAGES:
                asleep_intervals.append((a, b))
        if bad_stage:
            continue
        stage_seconds = _union_seconds(all_intervals)
        # Overlapping awake/asleep intervals cannot support an honest total.
        if sum((b - a).total_seconds() for a, b in all_intervals) - stage_seconds > 1:
            continue
        asleep_seconds = _union_seconds(asleep_intervals)
        stage_ratio = stage_seconds / span_seconds
        if stage_ratio < SLEEP_MIN_STAGE_COVERAGE or asleep_seconds <= 0:
            continue
        session = {
            "start": start,
            "end": end,
            "span_hours": round(span_seconds / 3600, 2),
            "asleep_hours": round(asleep_seconds / 3600, 2),
            "stage_coverage": round(stage_ratio, 4),
        }
        accepted.setdefault((start, end), session)

    sessions = sorted(accepted.values(), key=lambda item: item["start"])
    days = {session["start"].date() for session in sessions}
    ratio = _capture_ratio(days, source_days)
    return sessions, {
        "rule": "Samsung 來源；起迄可解析、階段覆蓋 ≥95%、含實睡階段；同區間去重",
        "source_sessions": source_sessions,
        "valid_sessions": len(sessions),
        "valid_days": len(days),
        "capture_ratio": ratio,
        "target_days": BASELINE_TARGET_DAYS,
        "target_capture_ratio": MIN_CAPTURE_RATIO,
        "excluded_sessions": source_sessions - len(sessions),
        "missing_days_mean": "沒有紀錄不代表沒有睡",
    }
