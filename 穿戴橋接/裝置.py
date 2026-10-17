#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
找裝置、跟裝置說話。**共用的，因為同一個 bug 在八支程式裡各寫了一次。**

## 為什麼要有這支（2026-08-26 使用者 抓到的）

她問：「你剛才不是才撈過資料嗎？怎麼沒有看到錄音啊」

去查才發現兩個疊在一起的 bug：

```
① adb devices -l 的序號長這樣：
       adb-<watch-serial>-sziG0j (2)._adb-tls-connect._tcp device product:... model:SM_R925F
   **序號裡面有空格**（那個 "(2)" 是 mDNS 撞名時加的）。
   而八支程式都寫 line.split()[0] → 切成 adb-<watch-serial>-sziG0j → 之後每一條 adb 指令都失敗。

② 失敗之後 sh() 回空字串，而呼叫端把「空字串」當成「找不到東西」，
   於是印出「手錶上目前沒有任何錄音檔」——
   **一個假的確定句。而她其實錄了兩筆。**
```

> 🔴 第二個才是真正嚴重的：**「我沒辦法看」跟「我看了，沒有」長得一模一樣。**
> 這條在這個專案已經咬過很多次（三星沒送 vs 我們濾光了、
> 手錶斷線 vs 沒有震動記錄）。所以這裡的 sh() **失敗一定要炸出來**，不准回空字串。
"""

from __future__ import annotations

import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0  # 2026-09-23 不閃黑框（使用者 看到了）

ADB = "C:/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe"


class 裝置不見了(Exception):
    """找不到裝置。**跟「裝置上沒有那個東西」是兩件事，不要混。**"""


class 指令失敗(Exception):
    """adb 指令沒跑成功。**不准把它當成「結果是空的」。**"""


def _serial(line: str) -> str:
    """從 `adb devices -l` 的一行取出序號。

    ⚠ 不可以用 `line.split()[0]` —— mDNS 的序號含空格：
      `adb-<watch-serial>-sziG0j (2)._adb-tls-connect._tcp device product:...`
    序號是「` device ` 之前的全部」。
    """
    for state in (" device ", " offline ", " unauthorized ", " authorizing "):
        if state in line:
            return line.split(state)[0].strip()
    return line.split()[0]


def 找(kind: str = "watch") -> str:
    """kind: watch（SM-R…）或 phone（SM-S…）。找不到就丟例外，不回 None。"""
    want = ("model:SM_R", "model:SM-R") if kind == "watch" else ("model:SM_S", "model:SM-S")
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

    ⚠ **預設失敗就丟例外。** 這是刻意的 ——
      回空字串會讓呼叫端把「我沒辦法看」寫成「我看了，沒有」。
      真的想要「失敗當空的」再明確傳 容許失敗=True。
    """
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
