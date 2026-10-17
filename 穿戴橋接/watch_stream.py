#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
watch_stream.py —— 把手錶上的即時資料接進電腦。

為什麼需要這支（2026-08-12 的實話）：
    手錶 app 已經量得到 IBI 和皮膚溫度了，但那些數字**只活在手錶的 logcat 裡**。
    logcat 是環形緩衝區，滿了就從頭蓋掉 —— 不接出來就是白量。

架構上的誠實話：
    這是**過渡方案**，不是最終解。它靠 adb over Wi-Fi，
    而手錶一連上藍牙就會把 Wi-Fi 關掉（Samsung 的省電策略）。
    所以這支只有在「手錶掛在 Wi-Fi 上」的時候能用 —— 例如放在充電座上、
    或她刻意把藍牙關掉的時候。

    真正的長期解是走藍牙的 Wearable Data Layer（手錶→手機→電腦），
    那要多寫一個手機端 app。這支先讓資料**開始累積**，不要再空轉。

用法：
    python watch_stream.py              # 一直收
    python watch_stream.py --hrv        # 已停用；不能把一分鐘的碎拍拼成 HRV
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0  # 2026-09-23 不閃黑框（使用者 看到了）
import sys
from datetime import datetime
from pathlib import Path

ADB = Path(os.environ.get("LOCALAPPDATA", "")) / \
    "Android" / "Sdk" / "platform-tools" / "adb.exe"
OUT = Path(__file__).resolve().parent.parent / "生理資料" / "手錶即時"

# 手錶 app 印出來的兩種行。格式定在 SamsungSensors.kt 的 describe()，
# 兩邊要一起改 —— 這是刻意的耦合，寧可明講也不要弄一套過度設計的協定。
RE_HRV = re.compile(r"HRV HR=(\d+) status=(-?\d+) IBI=([\d,]*)")
RE_SKIN = re.compile(r"SKINTEMP 皮膚=([\d.]+) 環境=([\d.]+)")

# logcat 每行開頭的時間戳，例如 "08-12 19:10:16.123"。
# 2026-08-12 踩到的坑：原本我用 datetime.now() 當時間，
# 結果 adb logcat 一啟動會**先把整個環形緩衝區倒出來**，
# 那幾百筆積壓的舊資料全部被蓋上「現在」的時間戳 ——
# 587 筆全部變成同一秒。這種資料進了基線就是毒。
# → 一定要用 logcat 自己那行的時間，不是電腦收到的時間。
RE_TIME = re.compile(r"^(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})\.(\d{3})")


def line_time(line: str) -> str:
    """從 logcat 行首取時間。取不到才退回本機時間（並不理想，但至少不會崩）。"""
    if m := RE_TIME.match(line.strip()):
        year = datetime.now().year          # logcat 不給年份
        return f"{year}-{m[1]}-{m[2]}T{m[3]}.{m[4]}"
    return datetime.now().isoformat(timespec="milliseconds")


def watch_serial() -> str | None:
    """已連上的手錶。Galaxy Watch 的 mDNS 名字裡有 adb-R…_adb-tls-connect。"""
    r = subprocess.run([str(ADB), "devices"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30, creationflags=_NO_WINDOW)
    for line in r.stdout.splitlines()[1:]:
        # 注意：手錶有時會出現兩個條目，其中一個名字裡帶 " (2)."。
        # 那個帶空格的序號會被很多工具的 split() 切斷（實測踩過），
        # 所以只認不帶空格的那一個。
        if "\tdevice" in line and "_adb-tls-connect" in line:
            serial = line.split("\t")[0]
            if " " not in serial:
                return serial
    return None


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def main() -> None:
    ap = argparse.ArgumentParser(description="把手錶的即時資料接進電腦")
    ap.add_argument("--hrv", action="store_true", help="已停用；請改用完整日 IBI 窗口")
    args = ap.parse_args()
    if args.hrv:
        ap.error("--hrv 已停用：即時 logcat 沒有可驗相鄰性的 5 分鐘窗口；"
                 "請用 ibi_windows.windows_from_file() + hrv.analyse()，並核對佩戴證據")

    serial = watch_serial()
    if not serial:
        print("手錶沒連上。它的無線偵錯可能又自己關掉了。")
        sys.exit(1)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{datetime.now():%Y-%m-%d}.jsonl"
    print(f"手錶 {serial}\n寫入 {path}\n")

    proc = subprocess.Popen(
        [str(ADB), "-s", serial, "logcat", "-s", "WatchBridge"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=_NO_WINDOW,
    )

    n_hrv = n_skin = 0

    try:
        with path.open("a", encoding="utf-8") as f:
            for line in proc.stdout:            # type: ignore[union-attr]
                now = line_time(line)

                if m := RE_HRV.search(line):
                    hr, status, ibi_raw = int(m[1]), int(m[2]), m[3]
                    ibis = [int(x) for x in ibi_raw.split(",") if x]
                    rec = {"t": now, "type": "hr_ibi", "hr": hr,
                           "status": status, "ibi": ibis}
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    n_hrv += 1
                elif m := RE_SKIN.search(line):
                    rec = {"t": now, "type": "skin_temp",
                           "skin": float(m[1]), "ambient": float(m[2])}
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    n_skin += 1

                else:
                    continue

                if (n_hrv + n_skin) % 25 == 0:
                    print(f"[{stamp()}] 心跳 {n_hrv} 筆、皮膚溫度 {n_skin} 筆")

    except KeyboardInterrupt:
        print(f"\n收工。心跳 {n_hrv} 筆、皮膚溫度 {n_skin} 筆 → {path}")
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
