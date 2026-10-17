#!/usr/bin/env python3
"""One-line, evidence-limited exercise window summary.

An activity-looking watch window is only a candidate until 使用者 presses the
exercise marker. Duration is the supplied observation window, not a medical or
training-duration inference. Times are the watch's local clock.
"""

from __future__ import annotations

import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

from 覆蓋率 import classify, load_records


DATA_DIR = Path(__file__).resolve().parent.parent / "生理資料" / "手錶橋接"
ON = {"on", "inferred_on"}


def _timestamp(row: dict) -> datetime | None:
    value = row.get("t")
    if not isinstance(value, str) or len(value) < 19:
        return None
    try:
        return datetime.fromisoformat(value[:19])
    except ValueError:
        return None


def _weekly_mark_rank(data_dir: Path, start: datetime, end: datetime) -> int | None:
    monday = (start - timedelta(days=start.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    marks: set[datetime] = set()
    day = monday.date()
    while day <= end.date():
        for row in load_records(data_dir / f"{day}.jsonl"):
            if row.get("type") != "mark" or row.get("tag") != "exercise":
                continue
            moment = _timestamp(row)
            if moment and monday <= moment < end:
                marks.add(moment)
        day += timedelta(days=1)
    in_window = sorted(moment for moment in marks if start <= moment < end)
    if not in_window:
        return None
    return sum(moment <= in_window[0] for moment in marks)


def one_line(data_dir: Path, start: datetime, end: datetime) -> str:
    if end <= start:
        raise ValueError("The observation window must end after it starts")
    if start.tzinfo or end.tzinfo:
        raise ValueError("Use local naive watch timestamps for this project")
    if start.second or start.microsecond or end.second or end.microsecond:
        raise ValueError("Use minute-aligned observation windows")
    total = int((end - start).total_seconds() // 60)
    if total <= 0:
        raise ValueError("The observation window must contain a full minute")

    states = []
    rows: list[dict] = []
    loaded: dict[str, tuple[list[dict], list[str]]] = {}
    for minute in range(total):
        moment = start + timedelta(minutes=minute)
        day = moment.strftime("%Y-%m-%d")
        if day not in loaded:
            records = load_records(data_dir / f"{day}.jsonl")
            loaded[day] = records, classify(records, day)["states"]
            rows.extend(records)
        day_states = loaded[day][1]
        index = moment.hour * 60 + moment.minute
        states.append(day_states[index] if index < len(day_states) else "unknown")

    valid = states.count("valid")
    off = states.count("off")
    unknown = total - valid - off
    rank = _weekly_mark_rank(data_dir, start, end)
    marked = rank is not None

    samples: list[tuple[datetime, float]] = []
    for row in rows:
        moment = _timestamp(row)
        if not moment or not start <= moment < end:
            continue
        index = int((moment - start).total_seconds() // 60)
        if not 0 <= index < total or states[index] != "valid" or row.get("on_body") not in ON:
            continue
        kind = row.get("type")
        value = row.get("hr") if kind == "hr_ibi" else row.get("bpm") if kind == "hr" else None
        if kind == "hr_ibi" and row.get("status") != 1:
            continue
        if isinstance(value, (int, float)) and 30 < value < 220:
            samples.append((moment, float(value)))

    first = [value for moment, value in samples if moment < start + timedelta(minutes=10)]
    last = [value for moment, value in samples if moment >= end - timedelta(minutes=10)]
    if valid / total >= 0.70 and first and last:
        heart = f"心跳從 {statistics.median(first):.0f} 到 {statistics.median(last):.0f}"
    else:
        heart = "起終心跳資料不足，不推變化"

    if marked and off == 0 and unknown == 0:
        opening = f"有本人運動標記：觀察窗做了 {total} 分鐘"
    elif marked:
        opening = f"有本人運動標記：觀察窗 {total} 分鐘，不能把空窗算成運動"
    else:
        opening = f"候選活動窗 {total} 分鐘，沒有本人運動標記"
    ordinal = f"本週第 {rank} 次（按鈕）" if marked else "本週次數未計入"
    return (f"{opening}；資料有效 {valid}／離腕 {off}／未知 {unknown} 分鐘；"
            f"{heart}；{ordinal}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(one_line(DATA_DIR, datetime(2026, 9, 21, 14, 54),
                   datetime(2026, 9, 21, 16, 43)))
