#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate an auditable, offline daily coverage report.

The classifier lives in ``覆蓋率.py``.  This module adds a repeatable date-range
report, an independent set tally for spot checks, and the 2026-09-02 report
regression.  It never treats a missing record as proof that the watch was off.
"""

from __future__ import annotations

import argparse
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from 覆蓋率 import classify, load_records

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_DATA = ROOT / "生理資料" / "手錶橋接"


def iter_days(start: str, end: str):
    current = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    while current <= stop:
        yield current.isoformat()
        current += timedelta(days=1)


def _minute(row: dict, day: str) -> int | None:
    stamp = row.get("t")
    if not isinstance(stamp, str) or not stamp.startswith(day + "T"):
        return None
    try:
        hour, minute = int(stamp[11:13]), int(stamp[14:16])
    except (IndexError, ValueError):
        return None
    return hour * 60 + minute if 0 <= hour < 24 and 0 <= minute < 60 else None


def _positive_signal(row: dict) -> bool:
    kind = row.get("type")
    if kind == "hr_ibi":
        return row.get("status") == 1 and isinstance(row.get("hr"), (int, float)) and row["hr"] > 0
    if kind == "hr":
        return isinstance(row.get("bpm"), (int, float)) and row["bpm"] > 0
    if kind == "skin_temp":
        return isinstance(row.get("skin"), (int, float))
    if kind == "motion":
        return row.get("trust") is True and isinstance(row.get("activity"), (int, float))
    return False


def independent_tally(rows: list[dict], day: str, elapsed: int = 1440) -> dict:
    """Recount minute sets without calling ``classify``.

    This deliberately repeats the small evidence rule so a spot check can catch
    accidental changes in the production classifier instead of merely echoing it.
    """
    on_signal: set[int] = set()
    explicit_off: set[int] = set()
    for row in rows:
        minute = _minute(row, day)
        if minute is None or minute >= elapsed:
            continue
        if str(row.get("on_body")) in {"on", "inferred_on", "True"} and _positive_signal(row):
            on_signal.add(minute)
        direct_off = str(row.get("on_body")) == "off" and row.get("type") in {"motion", "availability"}
        marked_off = row.get("type") == "mark" and row.get("tag") == "off_wrist"
        if direct_off or marked_off:
            explicit_off.add(minute)
    conflict = on_signal & explicit_off
    valid = on_signal - explicit_off
    off = explicit_off - on_signal
    return {
        "on_signal": len(on_signal),
        "off_evidence": len(explicit_off),
        "valid_minutes": len(valid),
        "off_minutes": len(off),
        "unknown_minutes": elapsed - len(valid) - len(off),
        "conflict_minutes": len(conflict),
    }


def _hours(minutes: int) -> str:
    return f"{minutes / 60:.1f}h"


def _report_0902() -> tuple[bool, list[str]]:
    import 今日報告

    text = 今日報告.report("2026-09-02")
    lines = text.splitlines()
    old_claim = bool(re.search(r"(?<![\d.])18(?:\.0+)?\s*小時", text))
    admits_unknown = any(marker in text for marker in (
        "有沒有睡不知道", "不能推論沒有睡", "資料不足", "尚不能當作實睡"
    ))
    return (bool(lines) and lines[0].startswith("資料覆蓋")
            and not old_claim and admits_unknown), lines[:3]


def render_report(data_dir: Path, start: str, end: str,
                  manual_days: list[str], now: datetime,
                  checks: list[bool] | None = None) -> str:
    results: dict[str, dict] = {}
    rows_by_day: dict[str, list[dict]] = {}
    for day in iter_days(start, end):
        rows = load_records(data_dir / f"{day}.jsonl")
        rows_by_day[day] = rows
        results[day] = classify(rows, day, now)

    lines = [
        "# 每日三態覆蓋率｜2026-10-04",
        "",
        f"- 範圍：{start}～{end}，全程離線讀取 `生理資料/手錶橋接/*.jsonl`。",
        "- `有效`：同一分鐘有 on／inferred_on 與可信感測訊號。",
        "- `明確離腕`：同一分鐘有 motion／availability 的 off 或 `off_wrist` 標記。",
        "- `未知`：沒有足夠證據，或 on/off 證據衝突。沒有收到資料本身永遠不是離腕證據。",
        "- 70% 是暫定資料品質門檻，不是醫療門檻；達標也不代表健康。",
        "",
        "| 日期 | 有效 | 明確離腕 | 未知 | 衝突 | 有效率 | 日品質 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for day in iter_days(start, end):
        c = results[day]
        lines.append(
            f"| {day} | {c['valid_minutes']} 分（{_hours(c['valid_minutes'])}） | "
            f"{c['off_minutes']} 分（{_hours(c['off_minutes'])}） | "
            f"{c['unknown_minutes']} 分（{_hours(c['unknown_minutes'])}） | "
            f"{c['conflict_minutes']} 分 | {c['valid_ratio'] * 100:.1f}% | `{c['quality']}` |"
        )

    lines += ["", "## 三天獨立手算對帳", ""]
    for day in manual_days:
        c = results[day]
        tally = independent_tally(rows_by_day[day], day, c["minutes"])
        expected = (c["valid_minutes"], c["off_minutes"], c["unknown_minutes"], c["conflict_minutes"])
        actual = (tally["valid_minutes"], tally["off_minutes"], tally["unknown_minutes"], tally["conflict_minutes"])
        matches = actual == expected
        if checks is not None:
            checks.append(matches)
        mark = "過" if matches else "沒過"
        lines.append(
            f"- **{day}｜{mark}**：on 訊號分鐘 {tally['on_signal']}、off 證據分鐘 "
            f"{tally['off_evidence']}、衝突 {tally['conflict_minutes']}；集合相減後"
            f"有效／離腕／未知 = {tally['valid_minutes']}／{tally['off_minutes']}／"
            f"{tally['unknown_minutes']}，合計 {sum(actual[:3])} 分，"
            + ("與正式分類相同。" if matches else "與正式分類不一致。")
        )

    regression_ok, excerpt = _report_0902()
    if checks is not None:
        checks.append(regression_ok)
    lines += [
        "",
        "## 9/2「18 小時睡眠」回歸",
        "",
        f"- 結果：**{'過' if regression_ok else '沒過'}**。正式日報不再輸出舊的 18 小時睡眠斷言；"
        "當三星睡眠快照未涵蓋該日時，明說睡眠資料不足／不知道，不用兩個低心率小時硬拉成一整段。",
        "- 本次實際輸出前三行：",
        "",
        "```text",
        *excerpt,
        "```",
        "",
        "## 結論",
        "",
        f"- 共 {len(results)} 天；達暫定 70% 門檻 {sum(1 for c in results.values() if c['quality'] == 'sufficient')} 天。",
        f"- 其餘 {sum(1 for c in results.values() if c['quality'] != 'sufficient')} 天不作睡眠推估、全日 HRV 與跨日趨勢判讀；仍可列三星明確睡眠紀錄與局部 HRV，必須附覆蓋限制。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="離線產生每日三態覆蓋率報表")
    parser.add_argument("--start", default="2026-09-14")
    parser.add_argument("--end", default="2026-10-03")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--manual-day", action="append", dest="manual_days")
    parser.add_argument("--output", default="-")
    args = parser.parse_args()
    manual_days = args.manual_days or ["2026-09-15", "2026-09-24", "2026-09-30"]
    checks: list[bool] = []
    text = render_report(args.data_dir, args.start, args.end, manual_days, datetime.now(), checks)
    if args.output == "-":
        print(text, end="")
    else:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0 if checks and all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
