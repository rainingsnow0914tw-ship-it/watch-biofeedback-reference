#!/usr/bin/env python3
"""Audit and locally restore closed-day tails still on 使用者's watch.

Default mode only lists byte gaps. --apply copies missing tail bytes over ADB,
checks the SHA-256 of the complete candidate against the watch, saves the old
PC file under 生理資料/備份, and atomically replaces only the matching PC file.
No raw content is printed, uploaded, deleted, or written to the phone/watch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "生理資料"
PC = DATA / "手錶橋接"
BACKUP = DATA / "備份" / "20261002-watch-tail-recovery"
ADB = Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
FILE_RE = re.compile(r"\s(\d+)\s+\d{4}-\d\d-\d\d\s+\d\d:\d\d\s+(\d{4}-\d\d-\d\d)\.jsonl$")


def adb(serial: str, *args: str, timeout: int = 45) -> bytes:
    result = subprocess.run(
        [str(ADB), "-s", serial, "exec-out", "run-as", "com.example.watchbridge", *args],
        capture_output=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError("ADB command failed: " + result.stderr.decode("utf-8", "replace")[:160])
    return result.stdout


def watch_sizes(serial: str) -> dict[str, int]:
    listing = adb(serial, "ls", "-l", "files/生理資料").decode("utf-8", "replace")
    sizes = {}
    for line in listing.splitlines():
        match = FILE_RE.search(line)
        if match:
            sizes[match.group(2)] = int(match.group(1))
    if not sizes:
        raise RuntimeError("watch date files not visible; no repair attempted")
    return sizes


def matching_candidate(old: bytes, tail: bytes, expected_size: int, watch_hash: str) -> bytes | None:
    candidate = old + tail
    if len(candidate) != expected_size:
        return None
    if hashlib.sha256(candidate).hexdigest() != watch_hash:
        return None
    if candidate and not candidate.endswith(b"\n"):
        return None
    return candidate


def restore(serial: str, day: str, watch_size: int) -> str:
    target = PC / f"{day}.jsonl"
    if not target.exists():
        return "missing_pc_file"
    old = target.read_bytes()
    if len(old) >= watch_size:
        return "already_full_or_larger"
    gap = watch_size - len(old)
    watch_path = f"files/生理資料/{day}.jsonl"
    tail = adb(serial, "tail", "-c", str(gap), watch_path, timeout=60)
    line = adb(serial, "sha256sum", watch_path, timeout=60).decode("ascii", "replace")
    remote_hash = line.split()[0] if line.split() else ""
    if not re.fullmatch(r"[0-9a-f]{64}", remote_hash):
        return "watch_hash_unavailable"
    candidate = matching_candidate(old, tail, watch_size, remote_hash)
    if candidate is None:
        return "prefix_or_hash_mismatch"

    BACKUP.mkdir(parents=True, exist_ok=True)
    backup = BACKUP / target.name
    if backup.exists():
        if backup.read_bytes() != old:
            return "backup_conflict"
    else:
        with backup.open("xb") as fh:
            fh.write(old)
    temporary = target.with_name(target.name + ".recovering-20261002")
    if temporary.exists():
        return "temporary_file_exists"
    with temporary.open("xb") as fh:
        fh.write(candidate)
    os.replace(temporary, target)
    entry = {"t": datetime.now().astimezone().isoformat(), "day": day,
             "before_bytes": len(old), "after_bytes": len(candidate),
             "added_bytes": gap, "sha256": remote_hash, "status": "restored"}
    with (BACKUP / "recovery.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return "restored"


def verify_all(serial: str, sizes: dict[str, int], today: str) -> tuple[int, list[str]]:
    """Compare every closed-day PC SHA-256 with the watch without copying raw bytes."""
    checked = 0
    mismatches = []
    for day, size in sorted(sizes.items()):
        if day >= today:
            continue
        path = PC / f"{day}.jsonl"
        if not path.exists() or path.stat().st_size != size:
            mismatches.append(day)
            continue
        line = adb(serial, "sha256sum", f"files/生理資料/{day}.jsonl", timeout=60)
        parts = line.decode("ascii", "replace").split()
        remote_hash = parts[0] if parts else ""
        if not re.fullmatch(r"[0-9a-f]{64}", remote_hash):
            mismatches.append(day)
            continue
        checked += 1
        if hashlib.sha256(path.read_bytes()).hexdigest() != remote_hash:
            mismatches.append(day)
    print(f"full_hash_checked={checked} mismatch_days={len(mismatches)}")
    for day in mismatches:
        print(f"hash_mismatch_day={day}")
    return checked, mismatches


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover missing closed-day watch tails locally")
    parser.add_argument("--serial", required=True, help="ADB device serial/IP:port")
    parser.add_argument("--apply", action="store_true", help="back up and restore verified PC files")
    parser.add_argument("--verify-all", action="store_true", help="compare every closed-day PC hash to watch")
    parser.add_argument("--limit", type=int, default=0, help="maximum gap days to process; 0 = all")
    args = parser.parse_args()
    today = datetime.now().strftime("%Y-%m-%d")
    sizes = watch_sizes(args.serial)
    gaps = [(day, size, size - (PC / f"{day}.jsonl").stat().st_size)
            for day, size in sorted(sizes.items())
            if day < today and (PC / f"{day}.jsonl").exists()
            and (PC / f"{day}.jsonl").stat().st_size < size]
    print(f"closed_day_gaps={len(gaps)} missing_bytes={sum(g for _, _, g in gaps)}")
    if not args.apply:
        if args.verify_all and verify_all(args.serial, sizes, today)[1]:
            raise SystemExit(1)
        return
    if args.limit > 0:
        gaps = gaps[:args.limit]
    counts: dict[str, int] = {}
    for index, (day, size, _) in enumerate(gaps, 1):
        try:
            status = restore(args.serial, day, size)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            status = type(exc).__name__
        counts[status] = counts.get(status, 0) + 1
        if status != "restored":
            print(f"day={day} status={status}")
        if index % 10 == 0 or index == len(gaps):
            print(f"processed={index}/{len(gaps)} restored={counts.get('restored', 0)}")
    print("result=" + json.dumps(counts, sort_keys=True))
    if counts.get("restored", 0) != len(gaps):
        raise SystemExit(1)
    if args.verify_all and verify_all(args.serial, sizes, today)[1]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
