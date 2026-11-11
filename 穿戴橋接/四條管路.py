#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""而這幾天的答案有一個很清楚的形狀：

```
```

**四次故障，沒有一次壞在手錶。**

同一時間，手錶自己的紀錄是：
```
```

> **手錶是整條鏈裡最可靠的一環。所有的故障都發生在它後面的傳遞過程裡。**
> 而傳遞過程的每一段都是**安靜地壞** —— 沒有例外。


```
① 手錶 → 手機      Wearable Data Layer（藍牙）
② 手機 → 電腦      我們自己的 app，8788
③ 三星 → 手機      Health Connect（睡眠判讀）
④ 手錶 → 電腦      adb 無線偵錯（震動史、錄音）
```

②④ 一直好的，所以每日檢查全程綠燈，而 ③ 躺了三天。

用法：
    python 四條管路.py"""

from __future__ import annotations

import datetime
import json
import pathlib
import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
import sys
import urllib.error
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent.parent / "生理資料"
橋 = BASE / "手錶橋接"
ADB = r"C:\Users\<you>\AppData\Local\Android\Sdk\platform-tools\adb.exe"


def _談(base: str, path: str, token: str | None, t: float = 8.0) -> tuple[bool, str]:
    req = urllib.request.Request(base + path)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        urllib.request.urlopen(req, timeout=t)
        return True, "通"
    except urllib.error.HTTPError as e:


        return False, f"鑰匙不對（{e.code}）"
    except Exception as e:
        return False, f"沒人應（{type(e).__name__}）"


def 手錶到手機() -> tuple[str, str]:
    """問**手機本人**：它最後一次寫入是什麼時候。

    它讀電腦上的副本，而副本只在 `watch_puller` 跑的時候才更新 ——

    **一個健康檢查量錯對象，比沒有檢查更危險** ——
    它會用綠燈或紅燈的權威，講一件它根本沒看的事。

    現在改成問手機的 `/status`：它回每個資料檔的 `modified`，
    那是**手機寫檔的時刻**，也就是藍牙那段真正的心跳。"""
    from 手機在哪 import 找手機
    day = datetime.datetime.now().strftime("%Y-%m-%d")
    try:
        cfg = json.loads((橋 / "puller_config.json").read_text(encoding="utf-8"))
    except Exception as e:
        return "?", f"讀不到設定：{e}"
    base, _ = 找手機(8788)
    if base is None:
        return "?", "連不到手機，所以問不到藍牙那段（這是「我沒看到」，不是「沒發生」）"
    req = urllib.request.Request(base + "/status")
    req.add_header("Authorization", "Bearer " + cfg.get("token", ""))
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))
    except Exception as e:
        return "?", f"問不到：{e}"
    今 = [f for f in d.get("files", []) if f.get("day") == day]
    if not 今:
        return "⚠", f"手機上今天（{day}）還沒有檔案"
    f = 今[0]
    age = (d.get("now", 0) - f.get("modified", 0)) / 60000.0
    mb = f.get("bytes", 0) / 1_048_576
    if age <= 5:
        return "✅", f"手機 {age:.0f} 分鐘前還在寫（今天 {mb:.1f} MB）"
    if age <= 30:
        return "⚠", f"手機 {age:.0f} 分鐘前最後寫入（今天 {mb:.1f} MB）—— 手錶沒戴？"
    return "🔴", f"手機已經 {age/60:.1f} 小時沒寫東西了（今天 {mb:.1f} MB）—— 藍牙那段要查"

def 手機到電腦() -> tuple[str, str]:
    from 手機在哪 import 找手機
    cfg = json.loads((橋 / "puller_config.json").read_text(encoding="utf-8"))
    base, how = 找手機(8788)
    if base is None:
        return "🔴", how
    ok, why = _談(base, "/status", cfg.get("token"))
    if not ok:
        return "🔴", why
    return ("✅" if how == "Wi-Fi" else "⚠"), (
        "Wi-Fi 直達" if how == "Wi-Fi" else f"{how}（線拔掉就斷）"
    )


def 三星到手機() -> tuple[str, str]:
    from 手機在哪 import 找手機
    cfg = json.loads((橋 / "puller_config.json").read_text(encoding="utf-8"))
    base, how = 找手機(8788)
    if base is None:
        return "🔴", "連不到手機，這條也就問不到"
    req = urllib.request.Request(base + "/sleep?days=2")
    req.add_header("Authorization", "Bearer " + cfg.get("token", ""))
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
    except Exception as e:
        return "🔴", f"問不到：{e}"
    if not d.get("ok"):
        return "🔴", str(d.get("why"))
    n = d.get("count", 0)
    if n == 0:

        return "⚠", "近兩天沒有睡眠記錄 —— 這是「沒記到」，不是「沒睡」"
    return "✅", f"近兩天 {n} 段睡眠"


def 手錶到電腦() -> tuple[str, str]:
    try:
        r = subprocess.run([ADB, "devices"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=25, creationflags=_NO_WINDOW)
    except Exception as e:
        return "🔴", f"adb 叫不動：{e}"
    if "_adb-tls-connect" in (r.stdout or ""):
        return "✅", "無線偵錯連著（震動史、錄音靠這條）"
    return "🔴", "手錶沒連上 —— 震動史跟錄音這次抓不到（它們過了就沒了）"


def 電池白名單() -> tuple[str, str]:
    好 = []
    壞 = []
    try:
        r = subprocess.run([ADB, "devices"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=25, creationflags=_NO_WINDOW)
        for line in (r.stdout or "").splitlines()[1:]:
            serial = line.split("\tdevice")[0].split(" device")[0].strip()
            if not serial:
                continue


            ch = subprocess.run([ADB, "-s", serial, "shell", "getprop ro.build.characteristics"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=20, creationflags=_NO_WINDOW)
            名 = "手錶" if "watch" in (ch.stdout or "") else "手機"
            g = subprocess.run([ADB, "-s", serial, "shell",
                                "dumpsys deviceidle whitelist"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30, creationflags=_NO_WINDOW)
            (好 if "watchbridge" in (g.stdout or "") else 壞).append(名)
    except Exception as e:
        return "?", f"查不到：{e}"
    if 壞:
        return "🔴", ("、".join(壞) + " 不在白名單 —— 系統可以在她睡著時把橋接省電掉，"
                      "而且不會報錯。修：adb shell dumpsys deviceidle whitelist +com.example.watchbridge")
    if not 好:
        return "?", "沒有裝置連著，查不到"
    return "✅", "、".join(好) + " 都在白名單"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("═══ 四條管路 ═══")
    print("  ⚠ 一條綠不代表另一條綠。08-27~30 就是 ②④ 一直綠、而 ③ 躺了三天。\n")
    for 名, fn in (
        ("① 手錶 → 手機　藍牙", 手錶到手機),
        ("② 手機 → 電腦　8788", 手機到電腦),
        ("③ 三星 → 手機　睡眠", 三星到手機),
        ("④ 手錶 → 電腦　偵錯", 手錶到電腦),
    ):
        mark, why = fn()
        print(f"  {mark} {名}　{why}")
    mark, why = 電池白名單()
    print(f"\n  {mark} 電池白名單　{why}")
    print("\n  🔴 這幾天的教訓：**四次故障沒有一次壞在手錶。**")
    print("     手錶是最可靠的一環，問題全在它後面的傳遞過程 —— 而且每一段都是安靜地壞。")


if __name__ == "__main__":
    main()
