#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
watch_deploy.py —— 手錶一上線就立刻裝好、授權、啟動，全部一氣呵成

為什麼要一氣呵成（2026-08-12 實測）：
    手錶的無線偵錯**非常短命**。實測過連上之後不到十秒就掉，
    以至於「先 connect、再 install」兩個指令中間那幾秒就足以失敗：

        [16:07:08] ✅ 手錶已連上
        [16:07:15]  adb install → device not found

    所以不能分兩步。這支把「等待 → 連線 → 安裝 → 授權 → 啟動」
    全部綁在一起，抓到連線的那一刻就把事情做完。

用法：
    python watch_deploy.py                       # 裝預設的 APK
    python watch_deploy.py --apk <路徑>
    python watch_deploy.py --no-launch           # 只裝不啟動

她那邊要做的（做完就可以走人）：
    手錶關藍牙 → 設定 → 開發人員選項 → ADB 偵錯 + 無線偵錯 都打開
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0  # 2026-09-23 不閃黑框（使用者 看到了）
import sys
import time
from datetime import datetime
from pathlib import Path

ADB = Path(os.environ.get("LOCALAPPDATA", "")) / \
    "Android" / "Sdk" / "platform-tools" / "adb.exe"
PKG = "com.example.watchbridge"
DEFAULT_APK = (Path(__file__).resolve().parent.parent /
               "手錶app" / "wear" / "build" / "outputs" / "apk" / "debug" /
               "wear-debug.apk")
PERMS = ["android.permission.BODY_SENSORS",
         "android.permission.BODY_SENSORS_BACKGROUND"]


def adb(*args: str, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run([str(ADB), *args], capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def watch_serial() -> str | None:
    """
    已連上的手錶。

    ⚠ 這個函式被我改壞過兩次，兩次原因相反，記在這裡免得再犯：

    第一次（用 `line.split()[0]`）：
        手錶有時以 `adb-XXXX (2)._adb-tls-connect._tcp` 這種**帶空格**的名字出現，
        空白切割會切成 `adb-XXXX`，adb 回 device not found。

    第二次（改用 `adb devices -l` + tab 切）：
        `devices -l` 的欄位是**空白對齊，不是 tab**，
        於是整行都被當成序號，adb 回
        `unknown host service 'projectxulxx model:SM_R925F ...'`。

    正解：用**不帶 -l** 的 `adb devices` —— 那個格式是
        `<序號>	<狀態>`，tab 分隔，序號裡的空格才切不壞。
    """
    _, out = adb("devices")
    for line in out.splitlines()[1:]:
        if "	" not in line:
            continue
        serial, _, state = line.partition("	")
        if state.strip() != "device":
            continue
        if "_adb-tls-connect" in serial:      # 手錶走無線偵錯，名字裡一定有這段
            return serial.strip()
    return None


def discover() -> list[str]:
    _, out = adb("mdns", "services")
    return re.findall(r"_adb-tls-connect\._tcp\s+(\d+\.\d+\.\d+\.\d+:\d+)", out)


def deploy(serial: str, apk: Path, launch: bool) -> bool:
    """連上的當下立刻做完所有事。中間不留空窗。"""
    print(f"[{stamp()}] 安裝中…")
    code, out = adb("-s", serial, "install", "-r", str(apk), timeout=180)
    if "Success" not in out:
        print(f"[{stamp()}] ✗ 安裝失敗：{out.strip()[:160]}")
        return False
    print(f"[{stamp()}] ✅ 安裝成功")

    for p in PERMS:
        adb("-s", serial, "shell", "pm", "grant", PKG, p, timeout=20)
    _, out = adb("-s", serial, "shell", "dumpsys", "package", PKG, timeout=30)
    for line in out.splitlines():
        if "BODY_SENSORS" in line and "granted" in line:
            print(f"[{stamp()}]    {line.strip()[:70]}")

    if launch:
        adb("-s", serial, "shell", "am", "start", "-n",
            f"{PKG}/.MainActivity", timeout=20)
        print(f"[{stamp()}] ✅ 已啟動")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="手錶一上線就立刻部署")
    ap.add_argument("--apk", default=str(DEFAULT_APK))
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--no-launch", action="store_true")
    args = ap.parse_args()

    apk = Path(args.apk)
    if not apk.exists():
        print(f"找不到 APK：{apk}")
        sys.exit(1)

    print(f"\n  APK　{apk.name}（{apk.stat().st_size/1_048_576:.1f} MB）")
    print("  在等手錶。妳那邊：關藍牙 → 開發人員選項 → ADB 偵錯 + 無線偵錯\n")

    start = time.time()
    while time.time() - start < args.timeout:
        s = watch_serial()
        if s:
            if deploy(s, apk, not args.no_launch):
                return
            time.sleep(3)
            continue

        for addr in discover():
            adb("connect", addr, timeout=15)
            # 連上之後**不等**、不印訊息，直接做事 —— 空窗就是失敗
            s = watch_serial()
            if s:
                print(f"[{stamp()}] 抓到 {s}")
                if deploy(s, apk, not args.no_launch):
                    return
        time.sleep(2)

    print(f"[{stamp()}] 等了 {args.timeout} 秒還沒成功。"
          f"手錶的無線偵錯可能又自己關掉了。")


if __name__ == "__main__":
    main()
