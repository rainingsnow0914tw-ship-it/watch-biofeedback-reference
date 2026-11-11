#!/usr/bin/env python3
"""Training-only error audit for the frozen sleep detector; no retuning.

The held-out days are selected but never passed to 判() or the report. This is
an error description, not a new score claim or a medical sleep assessment.
"""

from __future__ import annotations

import json
from collections import Counter

import 睡眠偵測校準 as calibration
from 覆蓋率 import classify, load_records


FROZEN_PARAMS = (45, 0.50, 0.40, 45)


def minute_number(value: str) -> int:
    return int(value[:2]) * 60 + int(value[3:])


def period(value: str) -> str:
    hour = int(value[:2])
    if hour < 6:
        return "00-05"
    if hour < 12:
        return "06-11"
    if hour < 18:
        return "12-17"
    return "18-23"


def runs(minutes: set[str], minimum_gap: int = 2) -> list[set[str]]:
    groups: list[set[str]] = []
    last = None
    for value in sorted(minutes):
        number = minute_number(value)
        if last is None or number - last >= minimum_gap:
            groups.append(set())
        groups[-1].add(value)
        last = number
    return groups


def main() -> None:
    answers = calibration.三星的答案(60)
    paired = [day for day in sorted(answers)
              if (calibration.橋 / f"{day}.jsonl").exists()]
    train, gap, holdout = calibration.日期分組(paired)
    if not train or not gap or not holdout:
        raise SystemExit("Unable to maintain train / gap / holdout separation")

    fn_state = Counter()
    fp_state = Counter()
    fn_period = Counter()
    fp_period = Counter()
    total = Counter()
    short = Counter()
    day_rows = []
    multi_block = Counter()
    for day in train:
        truth = answers[day]
        predicted = calibration.判(day, *FROZEN_PARAMS)
        missed = truth - predicted
        false_alarm = predicted - truth
        states = classify(load_records(calibration.橋 / f"{day}.jsonl"), day)["states"]
        for value in missed:
            fn_state[states[minute_number(value)]] += 1
            fn_period[period(value)] += 1
        for value in false_alarm:
            fp_state[states[minute_number(value)]] += 1
            fp_period[period(value)] += 1
        for group in runs(truth):
            if len(group) < 45:
                short["label_minutes"] += len(group)
                short["missed_minutes"] += len(group & missed)
                short["runs"] += 1
        # Calendar-day blocks can contain two different nights across midnight.
        # Call them separated label blocks, not proof of fragmented sleep.
        blocks = len(runs(truth, minimum_gap=30))
        cohort = "multiple_blocks" if blocks >= 2 else "single_block"
        multi_block[cohort + "_days"] += 1
        multi_block[cohort + "_missed"] += len(missed)
        multi_block[cohort + "_labels"] += len(truth)
        total.update(tp=len(predicted & truth), fp=len(false_alarm), fn=len(missed),
                     labels=len(truth), predicted=len(predicted))
        day_rows.append({"day": day, "label_minutes": len(truth),
                         "missed": len(missed), "false_positive": len(false_alarm),
                         "separated_label_blocks": blocks})

    output = {
        "train_days_only": len(train),
        "gap_days_not_scored": len(gap),
        "holdout_days_not_scored": len(holdout),
        "frozen_params": FROZEN_PARAMS,
        "total": dict(total),
        "missed_by_watch_state": dict(fn_state),
        "false_positive_by_watch_state": dict(fp_state),
        "missed_by_hour_bucket": dict(fn_period),
        "false_positive_by_hour_bucket": dict(fp_period),
        "short_label_runs_under_45_minutes": dict(short),
        "separated_calendar_label_blocks": dict(multi_block),
        "worst_missed_train_days": sorted(day_rows, key=lambda row: row["missed"], reverse=True)[:5],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
