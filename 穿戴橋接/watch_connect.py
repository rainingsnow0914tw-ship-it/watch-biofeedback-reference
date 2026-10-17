#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
watch_connect.py —— 盯著手錶，它一上線就自動接上

為什麼需要這支（2026-08-12 使用者 的抱怨）：
    「我們之前做過這個動作啊……怎麼又要再做一次……無線偵錯又關起來了……」

    她說得對，而且問題在我：**上次是她先打開，我才開始找**。
    Wear OS 的無線偵錯只在畫面亮著、Wi-Fi 沒斷的那幾十秒內廣播，
    等我開始掃的時候常常已經沒了 —— 於是又要請她重來一次。

    正確的做法是反過來：**我一直在等，她什麼時候打開都接得到。**
    這樣她只要撥一下開關就可以走人，不用站在旁邊配合我的節奏。

用法：
    python watch_connect.py                 # 一直等，接到就停
    python watch_connect.py --keep          # 接到之後繼續守著，斷了自動重連
    python watch_connect.py --timeout 600   # 最多等 10 分鐘

Wear OS 的無線偵錯特性（踩過才知道）：
    · 埠號**每次開都不一樣**，所以不能寫死，要靠 mDNS 探索
    · 手錶跟手機藍牙連著時會自動關 Wi-Fi 省電 → 廣播會斷斷續續
    · **配對是永久的**，第一次配過之後就不用再輸配對碼
    · 螢幕一暗常常就停止廣播，所以要用「短間隔一直掃」而不是「掃一次」
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



def _序號(line: str) -> str:
    """從 `adb devices -l` 的一行取序號。

    ⚠ **不可以用 `line.split()[0]`** —— mDNS 的序號含空格：
      `adb-<watch-serial>-sziG0j (2)._adb-tls-connect._tcp device product:...`
      切錯之後每一條 adb 指令都會失敗，而且是**安靜地**失敗 ——
      2026-08-26 因為這個，兩筆錄音躺在錶上沒撈到，程式卻印
      「手錶上目前沒有任何錄音檔」。
    """
    for state in (" device ", " offline ", " unauthorized ", " authorizing "):
        if state in line:
            return line.split(state)[0].strip()
    return line.split()[0]

def adb(*args: str, timeout: int = 20) -> str:
    try:
        r = subprocess.run([str(ADB), *args], capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        return r.stdout or ""
    except Exception:
        return ""


def discover() -> list[str]:
    """mDNS 上正在廣播的 adb 服務。手錶開著無線偵錯時才會出現。"""
    out = adb("mdns", "services")
    return re.findall(r"_adb-tls-connect\._tcp\s+(\d+\.\d+\.\d+\.\d+:\d+)", out)


def 埠開著(addr: str, timeout: float = 1.5) -> bool:
    """那個 ip:port 真的有人在聽嗎。

    adb 的 mDNS 清單會留著已經死掉的紀錄，所以「發現」不等於「還在」。
    這裡不用 adb，自己開一條 TCP —— 連得上就是有人聽，拒絕就是沒有。
    """
    import socket
    try:
        ip, port = addr.rsplit(":", 1)
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def connected_watch() -> str | None:
    """已經接上的手錶（型號含 R9 開頭的是 Galaxy Watch）。"""
    for line in adb("devices", "-l").splitlines()[1:]:
        if ":" in line and "device " in line and "model:SM_R" in line:
            return _序號(line)   # ⚠ 不可以用 split()[0]：mDNS 序號含空格
    return None


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def main() -> None:
    ap = argparse.ArgumentParser(description="等手錶上線並自動連上")
    ap.add_argument("--timeout", type=int, default=0,
                    help="最多等幾秒；0 = 一直等")
    ap.add_argument("--interval", type=float, default=3.0, help="幾秒掃一次")
    ap.add_argument("--keep", action="store_true",
                    help="接上之後繼續守著，斷線自動重連")
    args = ap.parse_args()

    if not ADB.exists():
        print(f"找不到 adb：{ADB}")
        sys.exit(1)

    print("\n  在等手錶了。")
    print("  妳那邊只要：手錶 → 設定 → 開發人員選項 → 無線偵錯 → 打開")
    print("  打開之後就可以放著走人，我會自己接上。\n")

    start = time.time()
    announced = False

    while True:
        already = connected_watch()
        if already:
            if not announced:
                print(f"[{stamp()}] ✅ 手錶已連上：{already}")
                announced = True
            if not args.keep:
                return
            time.sleep(args.interval * 3)
            continue

        announced = False
        for addr in discover():
            # 🔴 2026-09-23：先自己敲一下那個埠，再交給 adb。
            #   那天 01:58 adb 一連「發現」了 8 次 192.168.0.X 的兩個埠，
            #   每次都連不上。我以為手錶剛好停止廣播，**其實是 adb 的 mDNS 快取**——
            #   埠早就關了（手動打過去是 10061 目標拒絕連線＝沒有人在聽）。
            #   「廣播是舊的」跟「手錶在但拒絕我」長得一模一樣，而**結論完全相反**：
            #   前者要她去錶上重開無線偵錯，後者要重新配對。追錯方向會白等一小時。
            if not 埠開著(addr):
                print(f"[{stamp()}] {addr} 是 mDNS 舊快取（埠沒人聽），跳過")
                continue
            print(f"[{stamp()}] 發現 {addr}，連連看…")
            說 = (adb("connect", addr) or "").strip()
            time.sleep(2.5)
            got = connected_watch()
            if got:
                print(f"[{stamp()}] ✅ 接上了：{got}")
                if not args.keep:
                    return
                break
            else:
                # 🔴 2026-09-23：這裡本來只印「還沒成功」，把 adb 的原話丟掉了。
                #   那天等了 15 分鐘，log 有 8 次「發現→還沒成功」，
                #   **而我完全不知道是配對掉了、還是廣播剛好停了** —— 只能猜。
                #   猜出來的原因會變成叫她「再打開一次無線偵錯」，
                #   如果真正的原因是配對掉了，她照做也不會好。
                print(f"[{stamp()}]    還沒成功：{說 or '（adb 什麼都沒說）'}")
                if "authenticate" in 說 or "10061" in 說 or "refused" in 說.lower():
                    # 這兩種是**不同的病**，藥也不同 —— 要分開講。
                    print("           ↑ 這是『它拒絕我』不是『它不在』——"
                          "配對可能掉了，要在錶上重新配對一次（會給 6 位數碼）。")

        if args.timeout and (time.time() - start) > args.timeout:
            print(f"[{stamp()}] 等了 {args.timeout} 秒還沒等到。"
                  f"手錶的無線偵錯可能沒開，或畫面暗掉就停止廣播了。")
            return

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
