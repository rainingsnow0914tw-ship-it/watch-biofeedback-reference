#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""```
```

**而手機明明好好的**：服務在跑、自己連自己回 401（＝有回應）、
沒有進 Doze、螢幕亮著、IP 沒變、跟電腦在同一個 SSID。

查出來的證據長這樣：

```
電腦 → 路由器 192.168.0.X:80    HTTP 200   ✅
電腦 → 手機   192.168.0.X:8788 逾時      🔴
電腦 → 手機   192.168.0.X:8787 逾時      🔴
電腦 → 手機   ping                 通         ✅（ICMP 沒被擋）
電腦上跑著    VPN 介面 10.0.0.X
```

**放行閘道、擋掉區網其他機器、ICMP 例外** —— 這是 VPN kill switch 的典型長相。


所以改成：Wi-Fi 打不通 → **自動改走 USB 的 adb 隧道**（`adb forward`）。
USB 完全不經過網路，VPN 管不到它。


用法：
    from 手機在哪 import 找手機"""

from __future__ import annotations

import json
import pathlib
import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent / "生理資料"
ADB = r"C:\Users\<you>\AppData\Local\Android\Sdk\platform-tools\adb.exe"
# 本機這一端的埠 = 手機那一端 + 10000，避免跟本機其他東西撞。
偏移 = 10000


def _設定(port: int) -> dict:
    f = ROOT / "手錶橋接" / "puller_config.json" if port == 8788 else ROOT / "puller_config.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _活著嗎(base: str, token: str | None, timeout: float = 6.0) -> bool:
    """打一下看有沒有人應。

    ⚠ **401 也算活著。** 401 代表那頭有一個伺服器在講話，只是鑰匙不對 ——"""
    req = urllib.request.Request(base + "/status" if ":8788" in base or "8788" in base else base + "/ping")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True          # 有回應就算通
    except Exception:
        return False


def _手機序號() -> str | None:
    try:
        r = subprocess.run([ADB, "devices"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20,
                           creationflags=_NO_WINDOW)
    except Exception:
        return None
    serials = []
    for line in (r.stdout or "").splitlines()[1:]:
        if "\tdevice" not in line and " device" not in line:
            continue
        # ⚠ 不可以用 split()[0]：mDNS 的序號含空格（手錶就是），會被切斷。
        serial = line.split("\tdevice")[0].split(" device")[0].strip()
        if not serial or "_adb-tls-connect" in serial:
            continue          # 那是手錶，不是手機
        serials.append(serial)

    try:
        want = _設定(8788).get("serial")
    except Exception:
        want = None
    if want:
        if want in serials:
            return want
        # Wi-Fi adb 的裝置名長得像 192.168.x.x:5555，要問它本人的序號才知道是不是同一台
        for sid in serials:
            if ":" in sid:
                try:
                    q = subprocess.run([ADB, "-s", sid, "shell", "getprop", "ro.serialno"], capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=10, creationflags=_NO_WINDOW)
                    if (q.stdout or "").strip() == want:
                        return sid
                except Exception:
                    continue
        return None
    return serials[0] if serials else None


#


#   手機情境／手錶資料整整斷 90 分鐘。手動 adb connect 192.168.0.X:5555 之後兩支 puller 當輪就接回。
#

#     ① 手機自己報的：手機情境每一筆都帶 "ip"（手機自己看到的 Wi-Fi 位址）→ 依網段記進 手機位址.json

#     ③ 設定檔的家裡位址（最後才試）


#     之後那個網段就記住了。

ADDR_BOOK = ROOT / "手機位址.json"
CONTEXT_DIR = ROOT / "手機情境"


def _是區網位址(ip) -> bool:
    if not isinstance(ip, str) or not ip.startswith("192.168."):
        return False
    parts = ip.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 and str(int(p)) == p for p in parts)


def _網段(ip: str) -> str:
    return ip.rsplit(".", 1)[0] + "."


def 我的網段() -> list[str]:
    """電腦現在的區網網段（192.168.x.），主要那張卡排第一；VPN／Tailscale 不算。"""
    import socket
    out: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("192.168.0.X", 9))
        ip = s.getsockname()[0]; s.close()
        if _是區網位址(ip):
            out.append(_網段(ip))
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _是區網位址(ip) and _網段(ip) not in out:
                out.append(_網段(ip))
    except OSError:
        pass
    return out


def 手機自己報的位址(context_dir: pathlib.Path | None = None) -> str | None:
    """手機情境最新一筆帶的 ip（手機自己看到的位址）。讀最新那天的檔尾巴就好，便宜。"""
    d = context_dir or CONTEXT_DIR
    try:
        files = sorted(d.glob("????-??-??.jsonl"))
    except OSError:
        return None
    for f in reversed(files[-2:]):
        try:
            with f.open("rb") as fh:
                fh.seek(0, 2); size = fh.tell(); fh.seek(max(0, size - 8192))
                lines = fh.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for ln in reversed(lines):
            try:
                ip = json.loads(ln).get("ip")
            except Exception:
                continue
            if _是區網位址(ip):
                return ip
    return None


def _讀通訊錄(book_file: pathlib.Path | None = None) -> dict:
    try:
        b = json.loads((book_file or ADDR_BOOK).read_text(encoding="utf-8"))
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def 記住手機位址(ip: str, book_file: pathlib.Path | None = None) -> bool:
    """ """
    if not _是區網位址(ip):
        return False
    f = book_file or ADDR_BOOK
    b = _讀通訊錄(f)
    if b.get(_網段(ip)) == ip:
        return False
    import time as _t
    b[_網段(ip)] = ip
    b["_更新"] = _t.strftime("%Y-%m-%d %H:%M")
    try:
        f.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
        return True
    except OSError:
        return False


def 手機位址候選(prefixes: list[str] | None = None, context_dir: pathlib.Path | None = None,
               book_file: pathlib.Path | None = None, config_ips: list[str] | None = None) -> list[str]:
    """這次該試 adb connect 的手機位址，照可信度排。順手把手機自己報的位址學進通訊錄。"""
    自報 = 手機自己報的位址(context_dir)
    if 自報:
        記住手機位址(自報, book_file)
    book = _讀通訊錄(book_file)
    mine = 我的網段() if prefixes is None else prefixes
    if config_ips is None:
        config_ips = [_設定(8788).get("ip"), _設定(8787).get("host")]
    out: list[str] = []
    for p in mine:

        #   自報和通訊錄都還停在舊的 .97（連不上就拿不到新的自報）。watch_puller 掃到新位址會寫進 puller_config，

        here_cfg = [ip for ip in config_ips if isinstance(ip, str) and ip.startswith(p)]
        for ip in (*here_cfg, 自報 if 自報 and 自報.startswith(p) else None, book.get(p)):
            if _是區網位址(ip) and ip not in out:
                out.append(ip)
    for ip in config_ips:
        if _是區網位址(ip) and ip not in out:
            out.append(ip)
    return out


def adb接手機(candidates: list[str] | None = None) -> str | None:
    """ """
    s = _手機序號()
    if s:
        return s
    try:
        已連 = subprocess.run([ADB, "devices"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=15, creationflags=_NO_WINDOW).stdout or ""
    except Exception:
        已連 = ""
    for ip in (手機位址候選() if candidates is None else candidates):
        addr = f"{ip}:5555"
        if addr + "\t" in 已連:
            continue
        try:
            subprocess.run([ADB, "connect", addr], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=12, creationflags=_NO_WINDOW)
        except Exception:
            continue
        s = _手機序號()
        if s:
            return s

        try:
            subprocess.run([ADB, "disconnect", addr], capture_output=True, timeout=8, creationflags=_NO_WINDOW)
        except Exception:
            pass
    return None


def 找手機(port: int = 8788) -> tuple[str | None, str]:
    """回 (base_url, 怎麼連上的)。兩條都不通就回 (None, 原因)。"""
    cfg = _設定(port)
    ip = cfg.get("ip") or cfg.get("host")
    token = cfg.get("token")

    # ① 先走 Wi-Fi。通的話就用它 —— 不需要插線是比較好的狀態。
    if ip:
        base = f"http://{ip}:{port}"
        if _活著嗎(base, token):
            return base, "Wi-Fi"

    # ② Wi-Fi 不通 → 走 USB 隧道。VPN 的防火牆管不到 USB。
    serial = adb接手機()
    if not serial:
        return None, ("Wi-Fi 打不通，USB 也沒插線。\n"
                      "     ⚠ 這是『我看不到』，不是『手機壞了』——兩者長得一模一樣。")
    本機埠 = port + 偏移
    try:
        subprocess.run([ADB, "-s", serial, "forward", f"tcp:{本機埠}", f"tcp:{port}"],
                       capture_output=True, timeout=20,
                       creationflags=_NO_WINDOW)
    except Exception as e:
        return None, f"Wi-Fi 不通，開 USB 隧道也失敗：{e}"
    base = f"http://127.0.0.1:{本機埠}"
    if _活著嗎(base, token):
        return base, f"USB 隧道（Wi-Fi 不通 —— 多半是 VPN 的防火牆擋區網）"
    return None, ("Wi-Fi 跟 USB 兩條都不通。\n"
                  "     手機那支 app 可能沒在跑 —— 開一下「阿寶橋接」再試。")


def 診斷() -> str:
    """兩條都不通的時候，回一段**看得懂而且能動手**的說明。"""
    行 = []
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-Process | Where-Object {$_.Name -like '*urfshark*'} "
                            "| Measure-Object).Count"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=25,
                           creationflags=_NO_WINDOW)
        if (r.stdout or "0").strip() not in ("0", ""):
            行.append("VPN 正在跑 —— 它的防火牆會放行閘道、擋掉區網其他機器。")
            行.append("這不是要妳關 VPN：插上 USB 線就繞過去了，隧道不經過網路。")
    except Exception:
        pass
    if not 行:
        行.append("VPN 沒在跑，那就要查手機那支 app 是不是被系統收掉了。")
    return "\n     ".join(行)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for p in (8788, 8787):
        base, how = 找手機(p)
        名 = "我們自己的橋接" if p == 8788 else "第三方 HC Webhook"
        if base:
            print(f"  ✅ {名}（{p}）　{how}　→ {base}")
        else:
            print(f"  🔴 {名}（{p}）　{how}")
            print(f"     {診斷()}")
