#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
校準進度 —— 她標記的東西，還差多少才夠用。

## 為什麼要有這支（使用者 2026-08-26）

她問：

> 「我現在把我所有做的事情……上班走路去算運動告訴你，脫下手錶去洗澡告訴你，
>   戴上手錶告訴你，睡覺告訴你，上廁所尿尿告訴你，起床告訴你 ——
>   **這樣子的話，能讓你的數據變得有意義嗎？**」

**會。但前一句話更重要：這件事有期限，不是做一輩子。**

而她前一天說過「做了很多天有點沮喪」。一個看不到終點的每日雜務，
只會把那個沮喪放大。**所以要有一條進度條。**

## 這支在算什麼

每一種標記，需要累積夠多**乾淨的樣本**，我才能學出「使用者 的 X 長什麼樣」。
夠了之後，那一項就**不必再標**了 —— 我自己認得出來。

```
還在收集   → 請繼續標
夠了       → **可以不用標了**
永遠要標   → 資料上永遠分不出來的那幾種
```

## ⚠ 兩個誠實的地方

**一、樣本數不是全部，離散程度才是。**
七個「起床」樣本橫跨動作量 19~247 的話，七個等於零 ——
所以這裡同時看 n 跟四分位距，兩個都要過。

**二、有些東西永遠需要她講，不會畢業。**
```
脫錶 / 戴錶     感測器永遠分不出「脫下來」跟「戴著沒動」（實測 76.6% 模糊）
不舒服          這是警報要對照的**唯一**主觀真相，沒有替代品
任何她覺得怪的  按鈕沒有的格子，本來就只有她知道
```
**把這幾項標成「永久」不是偷懶，是誠實。**

用法：
    python 校準進度.py
"""

from __future__ import annotations

import glob
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from 覆蓋率 import classify

HERE = Path(__file__).resolve().parent
BIO = HERE.parent / "生理資料" / "手錶橋接"

# 每一種要幾個樣本才算學得起來。
# 依據：Fitbit 靜息心率用 30 天基線、Samsung 活力分數建議 7 天。
# 這裡的單位是「事件次數」不是天數 —— 走路一天可能發生四次，睡覺一天一次。
TARGET = {
    # ⚠ "wake"／"sleep" 已於 2026-08-30 退休，改吃三星的判讀（見下方 三星接手）。
    "exercise": 20, "buzz": 20,
    "medicine": 10, "period": 6,
    # ⚠ 2026-08-26 下午修正。她問了兩個很準的問題，我查完資料才發現
    #   第一版的目標數字是我沒查就寫的。
    #
    #   ① 「要是每次上廁所我都記，這個數據有用嗎？」——**她猜對了，白天沒用。**
    #      白天上廁所 = 走一分鐘，跟任何一次短走路在資料上一模一樣，一天還好幾次。
    #      **半夜的才有用**，而且很有用：實測八個晚上，每晚有 4~6 次「起身」
    #      （動作量衝過 518），**我一次都不知道原因** —— 上廁所？惡夢？不舒服？翻身？
    #      而「夜間起身次數」本身在臨床上就是指標（睡眠品質、跌倒風險、夜尿）。
    #      → 所以目標數字按「一晚一次」抓，而且只算 22:00~08:00 的。
    "toilet": 10,
    # ② 「吃飯要用按鍵嗎？」——**建議先不要，我查過了。**
    #    她清醒時每小時心率中位數本來就在 67~84 之間晃（四分位距 17 bpm），
    #    而進食造成的心率上升文獻上是 5~15 bpm。
    #    **餐後效應比她本來的起伏還小** —— 要看得出來需要非常多樣本。
    #    目標壓到 6，當成「順口提到就好」，不要變成每天的功課。
    #    ⚠ 但有一種情況很值錢：**吃完之後不舒服**。那時候的「剛吃過」是關鍵脈絡。
    "meal": 6,
}

# 只在這個時段內才算數的標籤。
# 半夜起身有臨床意義，白天上廁所沒有 —— 分不出來就別浪費她的力氣。
ONLY_AT_NIGHT = {"toilet": (22, 8)}
# 🤖 已經不用她按、改由三星判讀自動補上的（2026-08-30 立）。
#
# 👤 使用者 2026-08-30 清晨：
#   「我最近有點恍惚，老是忘記壓，要不是就是累到直接睡著，
#     不然就起床明明呼呼、沒有按起床。所以這是我的問題。」
#
# **不是她的問題。**「睡著的那一刻」跟「剛醒的那一刻」，
# 正好是一個人最按不到按鈕的兩個時刻 —— 要求她按，是設計錯了。
# 而**家人將來更按不到**：這個洞不補，將來在他身上會裂得更大。
#
# 三星健康自己就在判，而且分得出深眠／REM。08-30 已接進我們自己的 app
# （`/sleep`，見 HealthConnect.kt），不再經過第三方。
三星接手 = {
    "sleep": "三星判得出入睡時刻，還分得出深眠／REM",
    "wake":  "睡眠段的結束就是起床時刻",
}
# 永遠需要她講的，不設目標。
FOREVER = {
    "off_wrist": "感測器永遠分不出「脫下來」跟「戴著沒動」",
    "unwell": "警報要對照的唯一主觀真相，沒有替代品",
}
NAME = {"meal": "吃飯", "toilet": "上廁所", "unwell": "不舒服", "buzz": "震了", "wake": "起床", "sleep": "睡覺",
        "exercise": "運動", "medicine": "吃藥", "period": "生理期",
        "off_wrist": "脫錶", "other": "其他"}

# 離散程度的門檻：四分位距 / 中位數。超過這個代表樣本還太亂，
# 不管收了幾個都還學不起來。
MAX_SPREAD = 0.8


def T(r: dict) -> datetime:
    return datetime.fromisoformat(r["t"])


def load_all() -> tuple[list[dict], set[str], int]:
    """Only samples from fully observed, quality-gated days can advance calibration.

    Keep this separate from recording: a human mark on an ineligible day still
    exists in the raw file, but its surrounding sensor context cannot train a
    dependable motion profile.
    """
    out: list[dict] = []
    valid_days: set[str] = set()
    total_days = 0
    for f in sorted(glob.glob(str(BIO / "2026-*.jsonl"))):
        day = Path(f).stem
        total_days += 1
        day_rows: list[dict] = []
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.startswith("{"):
                    continue
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        day_rows.append(row)
                except Exception:
                    continue
        if classify(day_rows, day)["quality"] != "sufficient":
            continue
        valid_days.add(day)
        out.extend(r for r in day_rows if r.get("type") in ("motion", "mark"))
    out.sort(key=lambda r: r["t"])
    return out, valid_days, total_days


def bar(n: int, target: int, w: int = 18) -> str:
    fill = min(w, round(w * n / target)) if target else w
    return "█" * fill + "░" * (w - fill)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    recs, valid_days, total_days = load_all()
    marks = [r for r in recs if r.get("type") == "mark"]
    mo = [r for r in recs if r.get("type") == "motion" and r.get("trust") is True]

    # ★ 2026-08-26：**語音也要算進來。**
    #
    #   她說「我都會用語音告訴你」。在這之前語音只有內容、沒有標籤 ——
    #   於是她講一百句「我開始走路了」，進度條還是 1/20。**那等於白講。**
    #
    #   ⚠ 語音來的標籤是**我猜的**，跟她親手按的不一樣，
    #     所以分開數、輸出用 🎤 標出來。混在一起就再也分不開了。
    import 聽懂她說什麼
    events: list[tuple[datetime, str, str]] = [
        (T(m), (m.get("tag") or m.get("label") or "?"), "button") for m in marks
    ]
    voice_n = 0
    for f in sorted(glob.glob(str(HERE.parent / "生理資料" / "語音筆記" / "2026-*.jsonl"))):
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    v = json.loads(line)
                except Exception:
                    continue
                if str(v.get("t", ""))[:10] not in valid_days:
                    continue
                for tg in 聽懂她說什麼.抽標籤(v.get("text", ""), v.get("t"))[0]:
                    events.append((datetime.fromisoformat(v["t"]), tg, "voice"))
                    voice_n += 1

    prof: dict[str, list[float]] = defaultdict(list)
    src: dict[str, int] = defaultdict(int)
    skipped_daytime = 0
    for t, tag, how in events:
        # 白天的上廁所不算 —— 資料上跟任何一次短走路一模一樣，記了也學不到東西。
        if tag in ONLY_AT_NIGHT:
            lo, hi = ONLY_AT_NIGHT[tag]
            if not (t.hour >= lo or t.hour < hi):
                skipped_daytime += 1
                continue
        a = [r["activity"] for r in mo if 0 <= (T(r) - t).total_seconds() <= 900]
        if a:
            prof[tag].append(statistics.median(a))
            if how == "voice":
                src[tag] += 1

    print("═══ 校準進度：我還要多少樣本，才認得出「妳的 X」═══\n")
    print(f"  有效日 {len(valid_days)}/{total_days}（只採完整日且有效資料 ≥70%；今天未完不計）\n")
    if voice_n:
        print(f"  （其中 {voice_n} 個是**從語音聽出來的**，用 🎤 標出來 —— "
              "那是我猜的，不是妳按的。看到我認錯了跟我說。）\n")
    done, doing = [], []
    for tag, target in TARGET.items():
        v = sorted(prof.get(tag, []))
        n = len(v)
        spread = None
        if n >= 4:
            q1, q3 = v[n // 4], v[3 * n // 4]
            med = v[n // 2]
            spread = (q3 - q1) / med if med else 99
        ok = n >= target and spread is not None and spread <= MAX_SPREAD
        (done if ok else doing).append((tag, n, target, spread, v))

    if doing:
        print("  還在收集（請繼續標）")
        for tag, n, target, spread, v in sorted(doing, key=lambda x: -x[1] / x[2]):
            s = f"  {NAME.get(tag, tag):5s} {bar(n, target)} {n:3d}/{target}"
            if src.get(tag):
                s += f"　🎤{src[tag]}"
            if spread is not None and spread > MAX_SPREAD:
                s += f"　⚠ 但目前還很散（動作量 {v[0]:.0f}~{v[-1]:.0f}）"
            elif n == 0:
                s += "　（一次都還沒有）"
            print(s)
    if done:
        print("\n  ★ 夠了，可以不用再標")
        for tag, n, target, spread, v in done:
            print(f"  {NAME.get(tag, tag):5s} {bar(n, target)} {n:3d}/{target}"
                  + (f"　🎤{src[tag]}" if src.get(tag) else "")
                  + f"　動作量中位 {v[len(v)//2]:.0f}")

    print("\n  ⊙ 這幾項**永遠**要妳講（不會畢業）")
    for tag, why in FOREVER.items():
        n = len(prof.get(tag, []))
        print(f"  {NAME.get(tag, tag):5s} 有效日可用 {n:3d} 次（其他日原始標記保留）　—— {why}")

    print(f"{chr(10)}  🤖 這幾種**已經不用妳按了**（三星自己判，08-30 起）")
    for tag, why in 三星接手.items():
        n = len(prof.get(tag, []))
        print(f"  {NAME.get(tag, tag):5s} 有效日可用 {n:3d} 次（不代表歷史總次數）　—— {why}")
    print("     （睡著跟剛醒，是最按不到按鈕的兩個時刻。要妳按是設計錯了，不是妳忘記。）")
    print("\n  ⊘ 這幾種**不用記**（記了我也學不到東西）")
    print("    白天上廁所　—— 走一分鐘，跟任何短走路一模一樣，一天還好幾次")
    print("    戴上手錶　　—— 脈搏回來我就知道了")
    print("    充電　　　　—— 電路上的物理事實，我 100% 分得出來")
    if skipped_daytime:
        print(f"    （已經略過 {skipped_daytime} 個白天的上廁所記錄）")

    # 進度總結
    tn = sum(min(len(prof.get(t, [])), g) for t, g in TARGET.items())
    tg = sum(TARGET.values())
    print(f"\n  總進度 {tn}/{tg}　{bar(tn, tg, 30)}　{tn/tg:.0%}")
    print(f"  手錶資料 30 天基線：{len(valid_days)}/30 個有效日　"
          "（這是資料品質進度，尚未重建 HC 各型別基線）")
    days = len({m["t"][:10] for m in marks})
    if days and tn:
        rate = tn / days
        left = (tg - tn) / rate if rate else 0
        print(f"  照目前速度（{days} 天 {tn} 個有效樣本，每天 {rate:.1f} 個），"
              f"**還要約 {left:.0f} 天**")
        print("  ⚠ 但那是照『目前』的速度算的 —— 她說要開始標所有事，")
        print("    真的標起來會快很多。這個數字每天會自己往下修。")


if __name__ == "__main__":
    main()
