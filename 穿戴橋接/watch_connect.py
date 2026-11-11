#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""watch_connect.py —— 電腦接上手錶的正牌工具（家規十一：只有這一支，別另開 連錶.py / X2）

用法：
    python watch_connect.py                 # 找錶、接上、打開固定埠 5555，接到就停
    python watch_connect.py --keep          # 接上之後繼續守著，斷了自動重連
    python watch_connect.py --timeout 600   # 最多等 10 分鐘
    python watch_connect.py --no-wake       # 錶 Wi-Fi 睡著時，不要用遙控叫醒它


  · **`adb tcpip 5555` 在這支錶有效**：之後 192.168.0.X:5555 直接連，不用知道隨機埠、不用 mdns、不用配對碼。
    **重開機就會失效**（service.adb.tcp.port 不是 persist），那時才需要無線偵錯的隨機埠一次，本程式會自己掃到。

  1. 已經連著 → 完成
  2. 記住的位址（生理資料/手錶位址.json，依網段記）:5555
  4. mDNS 廣播（舊方法，家裡有時有用）
  5. 知道 IP 但 5555 不在（錶重開機過）→ 掃那個 IP 30000-49999 找無線偵錯的隨機埠 → 接上 → 自動 tcpip 5555
  接上任何一條非 5555 的路，都會順手 `adb tcpip 5555`，下次就走第 2 步。

Wear OS 舊筆記（仍成立）：
  · 無線偵錯的埠號每次開都不一樣；配對是永久的
  · 手錶跟手機藍牙連著時會自動關 Wi-Fi 省電
  · mDNS 序號含空格，不可以用 split()[0]"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import importlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
HERE = Path(__file__).resolve().parent
ADB = Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
BOOK = HERE.parent / "生理資料" / "手錶位址.json"
FIXED_PORT = 5555
WATCH_MODELS = ("model:SM_R", "model:SM-R", "model:SM_L", "model:SM-L")


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def say(msg: str) -> None:
    print(f"[{stamp()}] {msg}", flush=True)


def _序號(line: str) -> str:
    """ """
    for state in (" device ", " offline ", " unauthorized ", " authorizing "):
        if state in line:
            return line.split(state)[0].strip()
    return line.split()[0]


def adb(*args: str, timeout: int = 20) -> str:
    try:
        r = subprocess.run([str(ADB), *args], capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        return (r.stdout or "") + (r.stderr or "")
    except Exception:
        return ""


def 埠開著(ip: str, port: int, timeout: float = 1.5) -> bool:
    """ """
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def 埠狀態(ip: str, port: int, timeout: float = 1.5) -> str:
    """open／refused（錶醒著但沒人聽＝重開機過，5555 沒了）／timeout（錶 Wi-Fi 在打盹，等一下再敲）。"""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        r = s.connect_ex((ip, port))
    finally:
        s.close()
    if r == 0:
        return "open"
    return "refused" if r in (10061, 111) else "timeout"


def 戳錶() -> None:
    """用藍牙問錶一句 status —— 錶整支醒來，Wi-Fi 跟著醒。"""
    p = phone()
    if p:
        try:
            p.watch_diag("status")
        except Exception:
            pass


def 敲到開(ip: str, port: int, seconds: float = 45) -> str:
    """打盹的錶：敲不開就用藍牙戳一下再敲（每 10 秒最多戳一次）；遇到 refused 立刻回。"""
    end = time.time() + seconds
    last = "timeout"
    poked = 0.0
    while time.time() < end:
        last = 埠狀態(ip, port)
        if last != "timeout":
            return last
        if time.time() - poked > 10:
            戳錶()
            poked = time.time()
            continue
        time.sleep(2)
    return last


def _is_watch(line: str) -> bool:
    return any(m in line for m in WATCH_MODELS)


def connected_watch() -> str | None:
    """ """
    best = None
    for line in adb("devices", "-l").splitlines()[1:]:
        if " offline" in line and ":" in line:
            adb("disconnect", _序號(line))
            continue
        if " device " in line and _is_watch(line):
            s = _序號(line)
            if best is None or s.endswith(f":{FIXED_PORT}"):
                best = s
    return best


def my_prefix() -> str:
    """電腦現在的區網網段（192.168.x.）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.0.X", 9))
        ip = s.getsockname()[0]
        s.close()
        if ip.startswith("192.168."):
            return ip.rsplit(".", 1)[0] + "."
    except OSError:
        pass
    return ""


def book() -> dict:
    try:
        return json.loads(BOOK.read_text(encoding="utf-8"))
    except Exception:
        return {}


def remember(ip: str) -> None:
    b = book()
    b[ip.rsplit(".", 1)[0] + "."] = ip
    b["_更新"] = time.strftime("%Y-%m-%d %H:%M")
    try:
        BOOK.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def try_connect(addr: str, tries: int = 4) -> str | None:
    """ """
    for _ in range(tries):
        adb("disconnect", addr)
        out = adb("connect", addr).strip()
        time.sleep(2)
        if adb("-s", addr, "get-state").strip() == "device":
            return addr
        if "authenticate" in out or "unauthorized" in out:
            say(f"   {addr} 說：{out} ← 這是『它拒絕我』不是『它不在』，配對可能掉了，要在錶上重新配對一次（6 位數碼）")
            return None
        time.sleep(2)
    return None


def make_fixed(serial: str) -> str | None:
    """從隨機埠的連線打開固定埠 5555，回新序號。"""
    ip = serial.rsplit(":", 1)[0]
    if serial.endswith(f":{FIXED_PORT}"):
        remember(ip)
        return serial
    for _ in range(4):
        out = adb("-s", serial, "tcpip", str(FIXED_PORT))
        if "restarting" in out:
            break
        adb("connect", serial)
        time.sleep(3)
    time.sleep(4)
    got = try_connect(f"{ip}:{FIXED_PORT}")
    if got:
        remember(ip)
        say(f"🔒 固定埠打開了：{got}（到錶重開機前都用這個）")
    return got


def scan_debug_port(ip: str) -> int | None:
    """錶重開機過、5555 不在 → 無線偵錯開著的話，隨機埠在 30000-49999 之間（實測 36035、41347）。約 20 秒。"""
    say(f"掃 {ip} 找無線偵錯的埠（約 20 秒）…")
    def t(p: int) -> int | None:
        s = socket.socket()
        s.settimeout(0.3)
        try:
            return p if s.connect_ex((ip, p)) == 0 else None
        finally:
            s.close()
    with cf.ThreadPoolExecutor(300) as ex:
        found = [p for p in ex.map(t, range(30000, 50000)) if p]
    return found[0] if found else None


_phone = None
LAST_DIAG: dict = {}
_told_off = {"v": False}


def phone():
    """叫手機.py（藍牙遙控那條）。載不到就當沒有。"""
    global _phone
    if _phone is None:
        try:
            sys.path.insert(0, str(HERE))
            _phone = importlib.import_module("叫手機")
        except Exception as e:
            say(f"（藍牙遙控載入失敗：{e}）")
            _phone = False
    return _phone or None


def ask_watch_ip(wake: bool) -> tuple[str | None, bool]:
    """藍牙遙控問錶的 Wi-Fi IP。回 (ip, 我們有沒有開保持Wi-Fi)。"""
    p = phone()
    if not p:
        return None, False
    try:
        r = p.watch_diag("status")
    except Exception as e:
        say(f"藍牙遙控問不到（{e}）")
        return None, False
    w = next((x for x in r.get("watches", []) if x.get("ok")), None)
    if w:
        LAST_DIAG.clear(); LAST_DIAG.update(w)
    if not w:
        say(f"藍牙遙控：錶沒回（{r.get('error', '?')}）")
        return None, False
    ip = w.get("wifi_ip")
    if ip:
        say(f"藍牙遙控：錶說它的 IP 是 {ip}（code{w.get('version_code')}）")
        return ip, False
    if not wake or w.get("keep_wifi_holding"):
        say("藍牙遙控：錶現在沒有 Wi-Fi")
        return None, False
    say("錶的 Wi-Fi 睡著了 → 遙控打開「保持Wi-Fi」（我開的，結束時關回去）")
    try:
        p.watch_diag("wifi_on")
    except Exception:
        return None, False
    for _ in range(6):
        time.sleep(5)
        try:
            w2 = next((x for x in p.watch_diag("status").get("watches", []) if x.get("ok")), {})
        except Exception:
            w2 = {}
        if w2.get("wifi_ip"):
            say(f"錶醒了：{w2['wifi_ip']}")
            return w2["wifi_ip"], True
    return None, True


def discover_mdns() -> list[str]:
    return re.findall(r"_adb-tls-connect\._tcp\s+(\d+\.\d+\.\d+\.\d+:\d+)", adb("mdns", "services"))


def find_and_connect(wake: bool) -> tuple[str | None, bool]:
    """跑一輪找錶。回 (序號, 我們有沒有遙控開了保持Wi-Fi)。"""
    got = connected_watch()
    if got:
        return make_fixed(got) or got, False

    tried: list[str] = []
    prefix = my_prefix()
    known = book().get(prefix) if prefix else None
    if known:
        tried.append(known)
        if 敲到開(known, FIXED_PORT, 20) == "open":
            say(f"記住的位址 {known}:{FIXED_PORT} 開著，連連看")
            got = try_connect(f"{known}:{FIXED_PORT}")
            if got:
                remember(known)
                return got, False

    ip, woke = ask_watch_ip(wake)
    if ip and ip not in tried:
        tried.append(ip)
        for _ in range(3):   # 錶 Wi-Fi 打盹：敲開那一刻就連，握手失敗再敲
            if 敲到開(ip, FIXED_PORT, 45) != "open":
                break
            got = try_connect(f"{ip}:{FIXED_PORT}", tries=2)
            if got:
                remember(ip)
                return got, woke

    for addr in discover_mdns():
        a_ip, a_port = addr.rsplit(":", 1)
        if not 埠開著(a_ip, int(a_port)):
            continue   # mDNS 舊快取
        say(f"mDNS 發現 {addr}，連連看")
        got = try_connect(addr)
        if got:
            return make_fixed(got) or got, woke


    if LAST_DIAG.get("adb_wifi_enabled") == "0":
        if not _told_off["v"]:
            say("錶說它的「無線偵錯」是關的 —— 只有這時需要妳：錶上 設定→開發人員選項→無線偵錯 撥開，"
                "撥完放著就好（不用唸 IP、不用關手機藍牙），我一看到就自己接上。")
            _told_off["v"] = True
        return None, woke
    for cand in tried:   # 5555 不在但錶醒著（拒絕），或錶說無線偵錯開著 → 掃隨機埠（重開機、剛撥開開關都是這種）
        st = 敲到開(cand, FIXED_PORT, 10)
        if st == "refused" or (st != "open" and LAST_DIAG.get("adb_wifi_enabled") == "1"):
            戳錶()
            port = scan_debug_port(cand)
            if port:
                say(f"找到無線偵錯的埠 {cand}:{port}")
                got = try_connect(f"{cand}:{port}")
                if got:
                    return make_fixed(got) or got, woke
    return None, woke


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="找手錶、接上、打開固定埠 5555")
    ap.add_argument("--timeout", type=int, default=0, help="最多等幾秒；0 = 一直等")
    ap.add_argument("--interval", type=float, default=10.0, help="幾秒找一輪")
    ap.add_argument("--keep", action="store_true", help="接上之後繼續守著，斷線自動重連")
    ap.add_argument("--no-wake", action="store_true", help="不要用藍牙遙控叫醒錶的 Wi-Fi")
    args = ap.parse_args()

    if not ADB.exists():
        print(f"找不到 adb：{ADB}")
        sys.exit(1)

    start = time.time()
    announced = None
    woke_any = False
    try:
        while True:
            got, woke = find_and_connect(wake=not args.no_wake)
            woke_any = woke_any or woke
            if got:
                if got != announced:
                    say(f"✅ 手錶接上了：{got}")
                    announced = got
                if not args.keep:
                    return
                time.sleep(args.interval * 3)
                continue
            announced = None
            if args.timeout and time.time() - start > args.timeout:
                say(f"等了 {args.timeout} 秒還沒接上。最後一招才需要妳：錶上 設定→開發人員選項→無線偵錯 打開"
                    "（開著放著就好，我會自己掃到埠）。")
                sys.exit(2)
            time.sleep(args.interval)
    finally:
        if woke_any and not args.keep:
            p = phone()
            if p:
                try:
                    p.watch_diag("wifi_off")
                    say("已把剛才遙控打開的「保持Wi-Fi」關回去（5555 仍有效，錶 Wi-Fi 下次醒來就能連）")
                except Exception:
                    pass


if __name__ == "__main__":
    main()
