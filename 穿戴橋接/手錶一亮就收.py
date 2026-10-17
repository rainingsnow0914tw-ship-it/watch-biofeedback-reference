#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
手錶一亮就收 —— 無線偵錯只開六分鐘，所以錶一連上，所有要 adb 的事一口氣做完。

## 為什麼要有這支（👤 使用者 2026-09-23 21:00）

> 「為什麼這一次自動對資料這麼困難呢？因為無線偵錯只會開六分鐘，你知道的啊。」

她說得對，而且這件事早就寫在 skill `wearos-watch-control` 裡：
「手錶的無線偵錯極短命，所有動作必須融合成一氣」。

那天凌晨的實際經過是：
```
她打開無線偵錯 → 傳訊息給我 → 我開始查四條管路、拉手機資料、建 APK……
→ 01:53 才開始找手錶 → 那時候六分鐘早就過了
→ adb 的 mDNS 舊快取讓我又追了十五分鐘的鬼
```
**錯在順序：我把「等她的錶」排在自己的工作後面。**
六分鐘的窗口裡，錶是最稀缺的東西 —— 應該是錶一亮、別的都停下來先收它。

## 做法

先在背景守著（不閃黑框、每 3 秒看一次），錶一連上就依序：

```
① 震動史      dumpsys 是滾動緩衝，約三天就被新的蓋掉 → 最容易消失，最先收
② 裝新版 APK  只有錶上的版本比電腦上建好的舊才裝，裝完輕輕震一次讓她感覺
③ 錄音        下載很快；轉錄（whisper）比較慢，但檔案已經在電腦上了，斷線也不怕
```

每一步都有時限，一步卡住不會吃掉後面的時間。
全部寫進 `生理資料/手錶一亮就收.log`，下一個阿寶（或我自己）回來直接讀。

用法：
    python 手錶一亮就收.py              # 一直等，收完就結束
    python 手錶一亮就收.py --不裝       # 只收資料，不裝 APK
"""

from __future__ import annotations

import datetime
import pathlib
import re
import subprocess
import sys
import time

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

HERE = pathlib.Path(__file__).resolve().parent
LOG = HERE.parent / "生理資料" / "手錶一亮就收.log"
APK = HERE.parent / "手錶app" / "wear" / "build" / "outputs" / "apk" / "debug" / "wear-debug.apk"
GRADLE = HERE.parent / "手錶app" / "wear" / "build.gradle.kts"
PKG = "com.example.watchbridge"

sys.path.insert(0, str(HERE))
from watch_connect import adb, connected_watch, discover, 埠開著  # noqa: E402


def 記(msg: str) -> None:
    line = f"[{datetime.datetime.now():%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def 跑(script: str, *args: str, timeout: int) -> bool:
    """跑同資料夾的另一支，有時限。卡住就放棄這一步，不要吃掉後面的時間。"""
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, str(HERE / script), *args],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=str(HERE),
                           creationflags=_NO_WINDOW,
                           env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
        tail = "\n".join((r.stdout or "").strip().splitlines()[-4:])
        記(f"   {script} 結束（{time.time()-t0:.0f} 秒，離開碼 {r.returncode}）")
        for l in tail.splitlines():
            記(f"     {l}")
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        記(f"   ⚠ {script} 超過 {timeout} 秒，放棄這一步（窗口只有六分鐘）")
        return False


def 錶上版本(serial: str) -> int | None:
    out = adb("-s", serial, "shell", f"dumpsys package {PKG}", timeout=20)
    m = re.search(r"versionCode=(\d+)", out)
    return int(m.group(1)) if m else None


def 電腦上版本() -> int | None:
    try:
        m = re.search(r"versionCode\s*=\s*(\d+)", GRADLE.read_text(encoding="utf-8"))
        return int(m.group(1)) if m else None
    except Exception:
        return None


def 等錶() -> str:
    記("在等手錶。她撥開無線偵錯的那一刻就會接上。")
    while True:
        got = connected_watch()
        if got:
            return got
        for addr in discover():
            if not 埠開著(addr):
                continue          # mDNS 舊快取，09-23 凌晨被它騙了十五分鐘
            記(f"發現 {addr}，連線中")
            adb("connect", addr)
            time.sleep(2)
            got = connected_watch()
            if got:
                return got
        time.sleep(3)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    serial = 等錶()
    t0 = time.time()
    記(f"✅ 接上了：{serial}　—— 開始計時，六分鐘")

    記("① 震動史（最容易消失，先收）")
    跑("震動溯源.py", timeout=90)

    if "--不裝" not in sys.argv:
        記("② 看要不要裝新版")
        舊, 新 = 錶上版本(serial), 電腦上版本()
        記(f"   錶上 {舊}、電腦上建好的 {新}")
        if 新 and APK.exists() and (舊 is None or 舊 < 新):
            out = adb("-s", serial, "install", "-r", str(APK), timeout=150)
            ok = "Success" in out
            記(f"   {'✅ 裝好了' if ok else '🔴 沒裝成功：' + out.strip()[-120:]}")
            if ok and 錶上版本(serial) == 新:
                記(f"   ✅ 錶上現在是 {新}（問過錶本人，不是看安裝指令說成功）")
                adb("-s", serial, "shell",
                    f"am broadcast -a {PKG}.TEST_WEIGHT -p {PKG}", timeout=20)
                記("   已經輕輕震兩下＋跳一行字 —— 要問她：有感覺到嗎？太輕還是剛好？")
        else:
            記("   不用裝")

    記("③ 錄音")
    跑("語音筆記撈回來.py", timeout=max(60, 330 - int(time.time() - t0)))

    記(f"全部收完，用了 {time.time()-t0:.0f} 秒。")


if __name__ == "__main__":
    main()
