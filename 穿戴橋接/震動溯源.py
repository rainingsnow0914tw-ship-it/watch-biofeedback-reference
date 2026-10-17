#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
震動溯源 —— 「剛才震了一下是你嗎？」這個問題，用證據回答。

## 為什麼要有這支（2026-08-19）

使用者 前後問過至少三次「剛才震動了一下是你的嗎」。
每一次我都只能推論，而**推論在這個專案已經錯過八次**。

手錶的系統自己留著答案：`dumpsys vibrator_manager` 記著
**每一次震動是哪個套件發的、幾點幾分、幾毫秒、什麼用途**。

08-19 那兩次的答案：
```
01:12:35  2177ms  com.google.android.apps.wearable.settings   ← Wear OS 設定
01:13:45  2175ms  com.google.android.apps.wearable.settings
```
**不是我們。** 那是她在設定裡開無線偵錯時系統自己震的。
而我們的 app 在整份震動史裡只出現一次：
```
08-18 23:27:40  87ms  usage=TOUCH  com.example.watchbridge  ← 她按「起床」的確認震
```
（同一秒的資料檔裡就有 `tag:"wake"` 那筆，兩邊對得起來。）

## ⚠ 但這支不能取代手腕上那顆「震了」按鈕

`dumpsys` **只留最近一段，過了就沒了**，而且要 adb ——
**家人那邊不會有電腦。**

更重要的是第三個理由：**她感覺到的震動 ≠ 系統記錄的震動。**
兩份記錄分開存、事後對照，才看得出來：
  · 她覺得震了、系統沒記錄 → 錶帶滑動？心跳誤認？（那不是震動問題）
  · 系統震了、她沒感覺     → **這對警報是致命的**，代表叫不醒她

所以正確的用法是：**兩邊都收，然後對照。** 這支負責「系統那邊」。

用法：
    python 震動溯源.py            # 抓一份存檔 + 跟「震了」標記對照
    python 震動溯源.py --只看今天
"""

from __future__ import annotations

import json
import re
import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0  # 2026-09-23 不閃黑框（使用者 看到了）
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADB = "C:/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe"
OUT = HERE.parent / "生理資料" / "震動史"
DATA = HERE.parent / "生理資料" / "手錶橋接"

# 一行長這樣（欄位用 | 分隔）：
#   08-19 01:12:35.891 | effect | finished | duration: 2177ms | start: ... |
#   usage: UNKNOWN | com.google.android.apps.wearable.settings (uid=1000, ...) | ...
LINE = re.compile(
    r"(\d\d-\d\d \d\d:\d\d:\d\d)\.\d+\s*\|.*?duration:\s*(\d+)ms.*?"
    r"usage:\s*(\w+).*?\|\s*([\w.]+)\s*\(uid="
)



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

def watch_serial() -> str | None:
    r = subprocess.run([ADB, "devices", "-l"], capture_output=True, text=True, creationflags=_NO_WINDOW)
    for line in r.stdout.splitlines()[1:]:
        if "model:SM_R" in line or "model:SM-R" in line:
            return _序號(line)   # ⚠ 不可以用 split()[0]：mDNS 序號含空格
    return None


def pull_history(serial: str) -> list[dict]:
    r = subprocess.run(
        [ADB, "-s", serial, "shell", "dumpsys vibrator_manager"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )
    out = []
    for line in r.stdout.splitlines():
        m = LINE.search(line)
        if m:
            out.append({"t": m.group(1), "ms": int(m.group(2)),
                        "usage": m.group(3), "pkg": m.group(4)})
    # 同一次震動會在多個分類區塊重複出現，去重
    seen, uniq = set(), []
    for r_ in out:
        k = (r_["t"], r_["ms"], r_["pkg"])
        if k not in seen:
            seen.add(k); uniq.append(r_)
    return sorted(uniq, key=lambda x: x["t"])


def buzz_marks(day: str) -> list[str]:
    """她按「震了」的時刻。"""
    f = DATA / f"{day}.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("type") == "mark" and r.get("tag") == "buzz":
            out.append(r["t"])
    return out


def 存檔(hist: list[dict], year: int) -> dict[str, int]:
    r"""按**事件自己的日期**分流存檔，並跟檔案裡已有的去重。

    ## 為什麼要這樣（2026-08-30 抓到的兩個 bug）

    原本是「全部 append 進**今天**的檔案」，錯在兩個地方：

    **一、檔名說謊。** `dumpsys` 是**滾動緩衝區**，留的是最近一段
    （實測橫跨三天）。全部塞進今天的檔案，等於 `2026-08-30.jsonl` 裡
    裝的是 08-26~28 的事。她讀到「今天的震動史」，看到的卻是四天前。

    **二、append 沒去重，同一天跑兩次就翻倍。** 08-20 / 08-21 / 08-26 /
    08-30 四天中招，`2026-08-30.jsonl` 變成兩份一模一樣的 54 筆。
    這會**灌水震動統計** —— 而那份統計是要拿來對照「叫不叫得醒她」的。

    原本註解寫「append 不 overwrite，覆蓋掉等於自己刪證據」——
    **方向對，但只做了一半**：append 是對的（怕漏），
    但必須先跟檔案裡已有的比對過。
    """
    out: dict[str, int] = {}
    分流: dict[str, list[dict]] = {}
    for r in hist:
        分流.setdefault(f"{year}-{r['t'][:5]}", []).append(r)

    for day, rows in 分流.items():
        f = OUT / f"{day}.jsonl"
        已有 = set()
        if f.exists():
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    o = json.loads(line)
                    已有.add((o["t"], o["ms"], o["pkg"]))
                except Exception:
                    continue
        新的 = [r for r in rows if (r["t"], r["ms"], r["pkg"]) not in 已有]
        if 新的:
            with f.open("a", encoding="utf-8") as fh:
                for r in 新的:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        out[day] = len(新的)
    return out


def main() -> None:
    s = watch_serial()
    if not s:
        print("找不到手錶（無線偵錯要開著）")
        raise SystemExit(1)

    hist = pull_history(s)
    OUT.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    存了 = 存檔(hist, datetime.now().year)

    今日 = datetime.now().strftime("%m-%d")
    今天筆數 = sum(1 for r in hist if r["t"].startswith(今日))
    覆蓋 = sorted({r["t"][:5] for r in hist})
    if 覆蓋:
        print(f"dumpsys 現在留著 {len(hist)} 筆，涵蓋 {覆蓋[0]} ~ {覆蓋[-1]}")
    else:
        print("dumpsys 現在一筆震動記錄都沒有")
    for d, n in sorted(存了.items()):
        print(f"  {d}　新增 {n} 筆" if n else f"  {d}　沒有新的（本來就有）")

    # 🔴 這幾行是重點：不要讓她把「緩衝區裡的總筆數」讀成「今天震了幾次」。
    #    2026-08-30 就是這樣騙到她的 —— 報告寫「抓到 54 筆」，
    #    而那 54 筆全是 08-26~28 的，今天實際 0 筆。
    if 今天筆數:
        print(f"\n  今天（{今日}）實際震了 **{今天筆數} 次**")
    else:
        print(f"\n  ⚠ 今天（{今日}）**一次都沒震**"
              f"（緩衝區裡最新的是 {覆蓋[-1] if 覆蓋 else '無'}）")
        print("     半夜長震關掉之後這是預期的，"
              "但『沒在收』長得一模一樣 —— 兩者要靠別的證據分。")

    only_today = "--只看今天" in sys.argv
    today = datetime.now().strftime("%m-%d")

    print("\n=== 誰在震（按套件統計）===")
    tally: dict[str, list[int]] = {}
    for r in hist:
        if only_today and not r["t"].startswith(today):
            continue
        tally.setdefault(r["pkg"], []).append(r["ms"])
    for pkg, ms in sorted(tally.items(), key=lambda x: -len(x[1])):
        mine = "  ← 我們" if "watchbridge" in pkg else ""
        print(f"  {len(ms):4d} 次  {sum(ms)/len(ms):6.0f}ms 平均  {pkg}{mine}")

    marks = buzz_marks(day)
    print(f"\n=== 她按「震了」{len(marks)} 次，各自附近有什麼 ===")
    if not marks:
        print("  （這一天沒有「震了」標記）")
    for mt in marks:
        t = datetime.fromisoformat(mt)
        print(f"  她按的時間 {t:%H:%M:%S}")
        near = [r for r in hist
                if abs((datetime.strptime(f"{t.year}-{r['t']}",
                                          "%Y-%m-%d %H:%M:%S") - t).total_seconds()) <= 180]
        if not near:
            # ⚠ 這種情況**不要當成她記錯**。可能是錶帶滑動、可能是
            #   dumpsys 已經把那段輪掉了。兩個原因的處理方式完全不同。
            print("     ⚠ 前後三分鐘系統沒有任何震動記錄 —— "
                  "可能不是震動（錶帶？），也可能是記錄已經被輪掉了")
        for r in near:
            mine = "  ★ 是我們" if "watchbridge" in r["pkg"] else ""
            print(f"     {r['t'][-8:]}  {r['ms']:5d}ms  {r['usage']:12s} {r['pkg']}{mine}")


if __name__ == "__main__":
    main()
