#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
手機在哪 —— 回一個「現在真的連得到手機」的網址，Wi-Fi 不通就自己改走 USB。

## 為什麼要有這支（2026-08-31 03:0x）

早上 👤 使用者 把手機跟手錶都連好叫我對一下，結果第③步是：

```
03:02:41  找不到手機（8788 沒人應）
```

**而手機明明好好的**：服務在跑、自己連自己回 401（＝有回應）、
沒有進 Doze、螢幕亮著、IP 沒變、跟電腦在同一個 SSID。

查出來的證據長這樣：

```
電腦 → 路由器 192.168.0.X:80    HTTP 200   ✅
電腦 → 手機   192.168.0.X:8788 逾時      🔴
電腦 → 手機   192.168.0.X:8787 逾時      🔴
電腦 → 手機   ping                 通         ✅（ICMP 沒被擋）
電腦上跑著    SurfsharkWireGuard 10.0.0.X
```

**放行閘道、擋掉區網其他機器、ICMP 例外** —— 這是 VPN kill switch 的典型長相。
（👤 使用者 的 memory 裡印表機那次也是同一類：「設定前先關 VPN」。）

## 為什麼不叫她關 VPN

因為那是**把問題丟回給人**，而且她不可能每次拉資料前都記得關。
**這條線的價值在於它自己會活著**，不是在於她配合得夠好。

所以改成：Wi-Fi 打不通 → **自動改走 USB 的 adb 隧道**（`adb forward`）。
USB 完全不經過網路，VPN 管不到它。

⚠ 但要誠實：**USB 隧道要線插著。** 線不在的時候這支會照實說「兩條都不通」，
  不會假裝沒事 —— 「我沒看到」跟「沒發生」不可以混，這是這個專案的第一條規矩。

用法：
    from 手機在哪 import 找手機
    base, 怎麼連的 = 找手機(8788)       # -> ("http://127.0.0.1:18788", "USB 隧道")
"""

from __future__ import annotations

import json
import pathlib
import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0  # 2026-09-20 不閃視窗
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

    ⚠ **401 也算活著。** 401 代表那頭有一個伺服器在講話，只是鑰匙不對 ——
      那跟「沒人應」是完全不同的兩件事，混在一起就會把
      「鑰匙過期」誤診成「手機不見了」（08-27 那三天就是這樣沒被發現的）。
    """
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
    for line in (r.stdout or "").splitlines()[1:]:
        if "\tdevice" not in line and " device" not in line:
            continue
        # ⚠ 不可以用 split()[0]：mDNS 的序號含空格（手錶就是），會被切斷。
        serial = line.split("\tdevice")[0].split(" device")[0].strip()
        if not serial or "_adb-tls-connect" in serial:
            continue          # 那是手錶，不是手機
        return serial
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
    serial = _手機序號()
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
            行.append("Surfshark 正在跑 —— 它的防火牆會放行閘道、擋掉區網其他機器。")
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
