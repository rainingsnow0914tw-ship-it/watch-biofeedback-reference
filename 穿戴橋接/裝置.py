#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""找裝置、跟裝置說話。**共用的，因為同一個 bug 在八支程式裡各寫了一次。**


去查才發現兩個疊在一起的 bug：

```
① adb devices -l 的序號長這樣：
   **序號裡面有空格**（那個 "(2)" 是 mDNS 撞名時加的）。

```

> 這條在這個專案已經咬過很多次（三星沒送 vs 我們濾光了、
> 手錶斷線 vs 沒有震動記錄）。所以這裡的 sh() **失敗一定要炸出來**，不准回空字串。"""

from __future__ import annotations

import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

ADB = "C:/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe"


class 裝置不見了(Exception):
    """ """


class 指令失敗(Exception):
    """ """


def _serial(line: str) -> str:
    """從 `adb devices -l` 的一行取出序號。

    ⚠ 不可以用 `line.split()[0]` —— mDNS 的序號含空格："""
    for state in (" device ", " offline ", " unauthorized ", " authorizing "):
        if state in line:
            return line.split(state)[0].strip()
    return line.split()[0]


def 找(kind: str = "watch") -> str:
    """ """
    want = ("model:SM_R", "model:SM-R", "model:SM_L", "model:SM-L") if kind == "watch" else ("model:SM_S", "model:SM-S")
    r = subprocess.run([ADB, "devices", "-l"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    for line in r.stdout.splitlines()[1:]:
        if not line.strip() or " device " not in line:
            continue
        if any(w in line for w in want):
            return _serial(line)
    raise 裝置不見了(
        f"找不到{'手錶' if kind == 'watch' else '手機'}。"
        + ("手錶要開無線偵錯；如果 mDNS 有廣播卻連不上，多半是配對記錄掉了，"
           "要用 `sh 配對並裝新版.sh <六位數>` 重新配對。" if kind == "watch"
           else "手機要用 USB 接上並允許偵錯。")
    )


def sh(serial: str, cmd: str, 容許失敗: bool = False) -> str:
    """在裝置上跑一行 shell。

    ⚠ **預設失敗就丟例外。** 這是刻意的 ——"""
    r = subprocess.run([ADB, "-s", serial, "shell", cmd],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    out = (r.stdout or "").replace("\r", "")
    if r.returncode != 0:
        if 容許失敗:
            return out
        raise 指令失敗(f"adb -s {serial} shell {cmd[:60]}…\n"
                       f"  離開碼 {r.returncode}\n  stderr: {(r.stderr or '').strip()[:200]}")
    return out
