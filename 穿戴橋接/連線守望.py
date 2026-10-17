#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
守著「手錶跟手機還連著嗎」，斷了先自己救，救不回來才吵她。

## 為什麼要有這支（使用者 2026-08-20 問的）

> 「我怎麼知道手錶跟手機還連著？雖然我藍牙會一直開著，那斷了怎麼辦？」

她問到了 08-20 那次事故的核心：**斷線的樣子跟正常一模一樣。**
錶面正常、心率照收、電量正常、藍牙圖示亮著 —— 而資料六個小時沒進手機。
Galaxy Wearable **不會自己重連，也不會通知**，它只是安靜停在「未連接」等人按。

## 設計：先自己修，修不好才出聲

```
每 10 分鐘 → 連著？ → 是 → 什麼都不做（不出聲就是好消息）
                    → 否 → 自己跑一次「手錶失聯救回來」
                            → 成功 → 只寫日誌，不吵她
                            → 失敗 → **跳桌面通知**
```

⚠ 為什麼「成功就不出聲」：一個一直在講話的監看，跟沒有監看一樣 ——
   她會學會忽略它。**只有真的需要她的時候才出聲。**

⚠ 為什麼「失敗才出聲」而不是「一斷就出聲」：斷線很常見（她走遠了、
   手機重開），多數會自己好。一斷就叫等於把她訓練成忽略警報。

用法：  python 連線守望.py           # 一直跑
        python 連線守望.py --once    # 檢查一次
"""

from __future__ import annotations

import json
import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0  # 2026-09-20 不閃視窗
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADB = "C:/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe"
NOTIFY = Path("C:/Users/<you>/ClaudeBackup/notify.ps1")
LOG = HERE.parent / "生理資料" / "連線日誌.jsonl"
EVERY_MIN = 10
WATCH_BT = "DD:3C"



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

def phone() -> str | None:
    r = subprocess.run([ADB, "devices", "-l"], capture_output=True, text=True,
                   creationflags=_NO_WINDOW)
    for line in r.stdout.splitlines()[1:]:
        if "model:SM_S" in line or "model:SM-S" in line:
            return _序號(line)   # ⚠ 不可以用 split()[0]：mDNS 序號含空格
    return None


def linked(p: str) -> bool | None:
    """True=連著 False=斷了 None=問不到（手機沒插）"""
    r = subprocess.run(
        [ADB, "-s", p, "shell",
         f"dumpsys bluetooth_manager | grep -m1 '{WATCH_BT}(Public'"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    if "ACL BR/EDR:Y" in r.stdout:
        return True
    if "ACL BR/EDR:N" in r.stdout:
        return False
    return None


def toast(title: str, body: str) -> None:
    # ⚠ 2026-08-18 的教訓：AUMID 沒註冊的話 Windows 會**收下呼叫、
    #   log 寫已送出、螢幕上什麼都沒有**。所以用那支已經驗過的 notify.ps1，
    #   它自己帶了 NotifyIcon 氣泡當退路。
    if not NOTIFY.exists():
        print(f"  ⚠ 找不到 {NOTIFY}，通知送不出去")
        return
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(NOTIFY), "-Title", title, "-Body", body],
                   capture_output=True, creationflags=_NO_WINDOW)


def note(**kw) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"), **kw},
                           ensure_ascii=False) + "\n")


def check() -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    p = phone()
    if not p:
        print(f"{stamp}  手機沒接上電腦，這次跳過")
        note(state="no_phone")
        return

    st = linked(p)
    if st is True:
        print(f"{stamp}  連著")
        note(state="ok")
        return
    if st is None:
        print(f"{stamp}  問不到狀態（手錶不在手機的清單裡）")
        note(state="unknown")
        return

    print(f"{stamp}  ⚠ 斷了 —— 自己救救看")
    r = subprocess.run(["sh", str(HERE / "手錶失聯救回來.sh")],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(HERE),
                       creationflags=_NO_WINDOW)
    ok = r.returncode == 0
    note(state="recovered" if ok else "failed", out=(r.stdout or "")[-400:])
    if ok:
        print(f"{stamp}  救回來了（沒吵她）")
    else:
        print(f"{stamp}  ✗ 救不回來 —— 跳通知")
        toast("手錶跟手機斷線了",
              "自動重連失敗。手錶還在自己記錄、資料沒有丟，"
              "但要有人去手機的 Galaxy Wearable 按「連接」。")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    once = "--once" in sys.argv
    while True:
        try:
            check()
        except Exception as e:
            print(f"  守望自己出錯了：{e}")
            note(state="watcher_error", err=str(e))
        if once:
            break
        time.sleep(EVERY_MIN * 60)


if __name__ == "__main__":
    main()
