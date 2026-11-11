#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""```
app/com.samsung.android.wear.voicerecorder/...MainActivity     ← 我推的
   com.samsung.android.wear.voicerecorder/...MainActivity      ← UI 實際寫的
```
多了一個 `app/`。系統沒有報錯，只是**安靜地退回去開套件的預設畫面** ——


用法：
    python 設定差異.py 前     # 改設定**之前**先拍一張
    python 設定差異.py 後     # 拍第二張，印出差在哪"""

from __future__ import annotations

import json
import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAP = HERE.parent / "生理資料" / "設定快照"
ADB = "C:/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe"
# ⚠ 三個命名空間都拍。Samsung 把設定散在三處，只拍一個會漏掉，

NS = ("global", "system", "secure")


def _序號(line: str) -> str:
    """從 `adb devices -l` 的一行取序號。

    ⚠ **不可以用 `line.split()[0]`** —— mDNS 的序號含空格：
      切錯之後每一條 adb 指令都會失敗，而且是**安靜地**失敗 ——"""
    for state in (" device ", " offline ", " unauthorized ", " authorizing "):
        if state in line:
            return line.split(state)[0].strip()
    return line.split()[0]

def watch() -> str | None:
    r = subprocess.run([ADB, "devices", "-l"], capture_output=True, text=True, creationflags=_NO_WINDOW)
    for line in r.stdout.splitlines()[1:]:
        if any(k in line for k in ("model:SM_R", "model:SM-R", "model:SM_L", "model:SM-L")):
            return _序號(line)   # ⚠ 不可以用 split()[0]：mDNS 序號含空格
    return None


def snap(w: str) -> dict:
    out = {}
    for ns in NS:
        r = subprocess.run([ADB, "-s", w, "shell", f"settings list {ns}"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        for line in r.stdout.replace("\r", "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                # Phenotype_flags 是一坨幾千字的旗標，每次都在變，純噪音
                if k.strip() == "Phenotype_flags":
                    continue
                out[f"{ns}.{k.strip()}"] = v
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    which = sys.argv[1] if len(sys.argv) > 1 else "前"
    w = watch()
    if not w:
        print("找不到手錶（無線偵錯要開著）")
        raise SystemExit(1)

    SNAP.mkdir(parents=True, exist_ok=True)
    cur = snap(w)
    f = SNAP / f"{which}.json"
    f.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"拍好了：{len(cur)} 個設定 → {f.name}")

    if which != "後":
        print("現在去手錶／Galaxy Wearable 上把那個設定改好，改完跑：")
        print("    python 設定差異.py 後")
        return

    before_f = SNAP / "前.json"
    if not before_f.exists():
        print("找不到「前」那張快照 —— 要先跑一次 `python 設定差異.py 前`")
        return
    before = json.loads(before_f.read_text(encoding="utf-8"))

    changed = [(k, before.get(k), v) for k, v in cur.items() if before.get(k) != v]
    gone = [k for k in before if k not in cur]

    if not changed and not gone:

        #   （存在 app 自己的 SharedPreferences 或雲端）。要講清楚。
        print("\n這三個命名空間裡沒有任何變化。")
        print("**這不代表她沒改** —— 有些設定存在 app 自己的偏好檔或雲端，")
        print("`settings list` 看不到。那種就只能走 UI，adb 幫不上。")
        return

    print(f"\n=== 變了 {len(changed)} 個 ===")
    for k, old, new in changed:
        print(f"\n  {k}")
        print(f"     舊：{old}")
        print(f"     新：{new}")
        ns, key = k.split(".", 1)
        print(f"     → 以後要設就用："
              f"adb shell settings put {ns} {key} '{new}'")
    for k in gone:
        print(f"\n  {k}　（不見了）")


if __name__ == "__main__":
    main()
