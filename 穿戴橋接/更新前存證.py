#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
更新前存證 —— 手錶系統更新之前，把「現在是好的」這件事變成可比對的證據。

## 為什麼要有這支（使用者 2026-08-24）

> 「我們先做這 5 件事，萬一哪一天我突然間手滑不小心按到怎麼辦？先做好準備。」

**她問到重點了**：更新鎖是防「自動」，防不了「手滑」。
而手滑之後最糟的不是更新本身，是**我們分不出什麼東西被更新弄壞了**。

08-18 那次 One UI 8 是事後才去湊證據的 —— 這次要事前存。

## 存哪五樣（每一樣都對應一個「更新可能弄壞」的東西）

```
① 全部設定快照        → 更新後逐項 diff，看它偷偷改了什麼
② APK + versionCode  → 我們的 app 被移除或降版時，直接裝回去
③ 關鍵權限與狀態      → 開發者模式／三星 SDK／通知監聽／按鍵手勢
④ 手錶上的原始資料    → 更新會重開機，08-18 那次掉了 20 分鐘
⑤ 復原指令清單        → 更新後要重下的那幾條（通知監聽一定會掉）
```

⚠ **第③樣最容易被忽略，也最致命**：三星 SDK 的開發者模式一被清掉，
  **皮膚溫度與 IBI 就直接消失** —— 而心率、動作量、app、通知全都正常，
  **只有那兩項悄悄不見**。沒有存證，事後根本不知道是更新弄的。

用法：
    python 更新前存證.py            # 存一份，資料夾用今天的日期
    python 更新前存證.py --比對      # 更新之後跑這個，逐項比對
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0  # 2026-09-23 不閃黑框（使用者 看到了）
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADB = "C:/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe"
BASE = HERE.parent / "生理資料" / "更新前存證"
APK = HERE.parent / "手錶app" / "wear" / "build" / "outputs" / "apk" / "debug" / "wear-debug.apk"
PKG = "com.example.watchbridge"

# 更新之後最可能掉、而且掉了看不出來的東西
CHECKS = {
    "系統版本": "getprop ro.build.display.id",
    "OneUI": "getprop ro.build.version.oneui",
    "Android": "getprop ro.build.version.release",
    "安全性修補": "getprop ro.build.version.security_patch",
    "我們的 app 版本": f"dumpsys package {PKG} | grep -m1 versionCode",
    "通知監聽授權": "settings get secure enabled_notification_listeners",
    "ADB 偵錯": "settings get global adb_enabled",
    "跌倒偵測": "settings get global sos_fall_detection_on",
    "首頁鍵按兩下": "settings get global setting_home_key_double_press",
    "敲門手勢": "settings get global setting_gesture_launch_app",
    "更新鎖1": "settings get global galaxy_system_update_block",
    "更新鎖2": "settings get global ota_disable_automatic_update",
    "更新程式狀態": f"pm list packages -d | grep -cE 'wssyncmldm|soagent'",
}



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

def sh(w: str, cmd: str) -> str:
    r = subprocess.run([ADB, "-s", w, "shell", cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    return (r.stdout or "").replace("\r", "").strip()


def watch() -> str | None:
    r = subprocess.run([ADB, "devices", "-l"], capture_output=True, text=True, creationflags=_NO_WINDOW)
    for line in r.stdout.splitlines()[1:]:
        if "model:SM_R" in line or "model:SM-R" in line:
            return _序號(line)   # ⚠ 不可以用 split()[0]：mDNS 序號含空格
    return None


def snap_settings(w: str) -> dict:
    out = {}
    for ns in ("global", "system", "secure"):
        for line in sh(w, f"settings list {ns}").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == "Phenotype_flags":
                    continue
                out[f"{ns}.{k.strip()}"] = v
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    w = watch()
    if not w:
        print("找不到手錶（無線偵錯要開著）")
        raise SystemExit(1)

    compare = "--比對" in sys.argv
    if compare:
        snaps = sorted(BASE.glob("*/狀態.json"))
        if not snaps:
            print("找不到任何存證 —— 要先跑一次 `python 更新前存證.py`")
            raise SystemExit(1)
        before = json.loads(snaps[-1].read_text(encoding="utf-8"))
        print(f"═══ 跟 {snaps[-1].parent.name} 的存證比對 ═══\n")
        bad = 0
        for name, cmd in CHECKS.items():
            now = sh(w, cmd)
            was = before["checks"].get(name, "（當時沒存）")
            same = (now == was)
            if not same:
                bad += 1
            print(f"{'  OK ' if same else '  ✗  '}{name}")
            if not same:
                print(f"        更新前：{was[:110]}")
                print(f"        現在　：{now[:110]}")
        # 設定層級的 diff
        old = before.get("settings", {})
        cur = snap_settings(w)
        NOISE = ("time", "etag", "brightness", "watchlist", "seq", "count", "boot")
        ch = [(k, old.get(k), v) for k, v in cur.items()
              if old.get(k) != v and not any(n in k.lower() for n in NOISE)]
        print(f"\n═══ 設定變動 {len(ch)} 項（已濾掉時間戳類噪音）═══")
        for k, o, n in ch[:30]:
            print(f"  {k}\n     舊 {o}\n     新 {n}")
        print(f"\n{'★ 關鍵項目全部沒變' if bad == 0 else f'🔴 有 {bad} 項關鍵項目變了 —— 上面 ✗ 那些'}")
        return

    day = datetime.now().strftime("%Y-%m-%d_%H%M")
    d = BASE / day
    d.mkdir(parents=True, exist_ok=True)
    print(f"═══ 存證到 {d} ═══\n")

    # ── ① 全部設定快照 ─────────────────────────────────────
    st = snap_settings(w)
    print(f"① 設定快照　{len(st)} 項")

    # ── ③ 關鍵狀態（放在 ② 之前是因為它最快，先拿到再說）─────
    checks = {}
    for name, cmd in CHECKS.items():
        checks[name] = sh(w, cmd)
        print(f"③ {name}：{checks[name][:80]}")

    (d / "狀態.json").write_text(
        json.dumps({"t": datetime.now().isoformat(timespec="seconds"),
                    "checks": checks, "settings": st},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    # ── ② APK 備份 ────────────────────────────────────────
    # ⚠ 從**手錶上那份**撈，不是從 build 資料夾複製 ——
    #   build 資料夾裡那份可能比裝在錶上的新，那就不是「現在是好的」那一份。
    p = sh(w, f"pm path {PKG}").replace("package:", "").strip()
    if p:
        subprocess.run([ADB, "-s", w, "pull", p, str(d / "wear-裝在錶上的.apk")],
                       capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        got = (d / "wear-裝在錶上的.apk")
        print(f"② APK　{got.stat().st_size/1024/1024:.1f} MB（從手錶上撈的）"
              if got.exists() else "② APK　✗ 撈不到")
    if APK.exists():
        shutil.copy2(APK, d / "wear-build資料夾.apk")

    # ── ④ 手錶上的原始資料全部拉回來 ──────────────────────
    #   更新會重開機，08-18 那次掉了 20 分鐘的資料。
    ls = sh(w, "run-as com.example.watchbridge ls files/生理資料")
    days = [x for x in ls.split() if x.endswith(".jsonl")]
    raw = d / "手錶原始資料"
    raw.mkdir(exist_ok=True)
    ok = 0
    for f in days:
        r = subprocess.run(
            [ADB, "-s", w, "exec-out",
             f"run-as com.example.watchbridge cat 'files/生理資料/{f}'"],
            capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        if r.stdout:
            (raw / f).write_bytes(r.stdout.replace(b"\r\n", b"\n"))
            ok += 1
    tot = sum(x.stat().st_size for x in raw.glob("*.jsonl")) / 1024 / 1024
    print(f"④ 原始資料　{ok}/{len(days)} 天，共 {tot:.1f} MB")

    # ── ⑤ 復原清單 ────────────────────────────────────────
    (d / "更新後要做這些.md").write_text(f"""# 更新之後要做這些

存證時間：{datetime.now():%Y-%m-%d %H:%M}
存證當時的版本：`{checks.get('系統版本','?')}`　安全性修補 `{checks.get('安全性修補','?')}`

---

## 第一步：先比對，不要憑感覺

```bash
python 更新前存證.py --比對
```
它會逐項告訴妳什麼變了。**「看起來還好」不算驗證。**

---

## 第二步：一定要重下的（08-17 實測過會掉）

### 通知監聽（掉了 FallWatcher 就聾了，而且畫面上看不出來）
```bash
adb shell cmd notification allow_listener {PKG}/{PKG}.FallWatcher
```

### 無線偵錯（每次重開機必掉，而且配對記錄也可能掉）
手錶 → 設定 → 開發人員選項 → 無線偵錯
⚠ 配對記錄被清的話要重新配對，**配對碼只在錶面上，得由人念**

---

## 第三步：要親眼確認的（我遠端驗不到）

### 三星 SDK 開發者模式 —— **最致命的一個**
Samsung Health（手錶上）→ 設定 → 關於 → 連點版本

⚠ 它被清掉的症狀：**心率、動作量、app、通知全部正常，只有皮膚溫度跟 IBI 悄悄消失。**
驗法：
```bash
python -c "print(open(r'...\\生理資料\\手錶橋接\\<今天>.jsonl',encoding='utf-8').read().count('skin_temp'))"
```
更新後那個數字如果變 0，就是被清掉了。

### 安全與緊急狀況（每一頁都要拍照，我讀不到 app 私有儲存區）
- 跌倒偵測 開／關
- 需要滑動以撥號 —— **一定要開**（關掉會倒數十秒自動撥 119）
- SOS 收件人

---

## 第四步：如果我們的 app 不見了或被降版

```bash
adb install -r "{d / 'wear-裝在錶上的.apk'}"
```
那是**更新前裝在錶上的那一份**，不是 build 資料夾裡的。

---

## 第五步：資料

存證時已經把 {ok} 天、{tot:.1f} MB 拉回 `{raw}`。
更新造成的重開機大概會掉 15~20 分鐘（08-18 實測 20 分鐘），**那段補不回來。**

---

## 更新鎖現在的狀態

```
galaxy_system_update_block    = {checks.get('更新鎖1','?')}
ota_disable_automatic_update  = {checks.get('更新鎖2','?')}
更新程式（停用中的個數）        = {checks.get('更新程式狀態','?')}   ← 2 = 兩支都停了
```
要開回來：`sh 更新鎖還原.sh`
""", encoding="utf-8")
    print(f"⑤ 復原清單　{d / '更新後要做這些.md'}")
    print(f"\n★ 存好了。手滑按到也不怕 —— 更新後跑 `python 更新前存證.py --比對`")


if __name__ == "__main__":
    main()
