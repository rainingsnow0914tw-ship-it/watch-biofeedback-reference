#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
「阿寶我現在很累很累很累」→ **那就去看那個時間窗的生理資料。**

## 這支是 使用者 2026-08-20 描述的那件事本身

> 「你就去抓這句話，再去抓那個時間窗…我跟你說『阿寶我現在很累很累很累』，
>   然後你就可以根據我現在這個身體狀況去抓我的生理資料，是這個意思。」

**她要的不是一個錄音檔案庫，是一條「主觀狀態 → 客觀訊號」的對照線。**

## 為什麼這比按鈕重要（而不是取代按鈕）

手腕上那八顆按鈕記的是**離散事件**：起床、吃藥、震了。
語音記的是**帶強度的主觀狀態**：「很累很累很累」——
那個「很」講了三次，是按鈕永遠表達不出來的東西。

而**主觀狀態正好是這整套系統唯一沒有感測器的那一格**。
心率量得到、動作量得到、皮膚溫度量得到，「她覺得自己怎麼樣」量不到。
影子模式要驗的正是「系統覺得不對勁」跟「她真的不對勁」對不對得上 ——
沒有這條線，那個對照根本做不了。

## ⚠ 時間窗要往前拉，不是對稱的

累是**累積**出來的，不是在她講話那一秒發生的。
所以預設看**前 90 分鐘、後 30 分鐘**。
對稱取窗會把「她講話之後休息了」跟「她講話之前一直在撐」混在一起。

## ⚠ 這支只描述、不判斷

它印出來的是**數字和它自己的基線比**，不下「妳是不是真的累」的結論。
理由：現在手上只有幾天資料，而且每一句「很累」對應的生理樣貌可能都不一樣。
**先累積對照，再談推論。** 這條跟「生理期只加註記不動門檻」是同一條紀律。

用法：
    python 那句話前後發生什麼.py                    # 全部語音筆記
    python 那句話前後發生什麼.py 2026-08-20         # 某一天
    python 那句話前後發生什麼.py --前 120 --後 60
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

from 覆蓋率 import classify, headline

HERE = Path(__file__).resolve().parent
VOICE = HERE.parent / "生理資料" / "語音筆記"
BIO = HERE.parent / "生理資料" / "手錶橋接"


def arg(name: str, default: int) -> int:
    if name in sys.argv:
        try:
            return int(sys.argv[sys.argv.index(name) + 1])
        except Exception:
            pass
    return default


def load_day(day: str) -> list[dict]:
    f = BIO / f"{day}.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def T(r: dict) -> datetime:
    return datetime.fromisoformat(r["t"])


def describe(recs: list[dict], a: datetime, b: datetime, label: str,
             states: list[str], day: str) -> None:
    win = [r for r in recs if a <= T(r) <= b]
    print(f"    {label}（{a:%H:%M}–{b:%H:%M}）")
    total = max(0, round((b - a).total_seconds() / 60))
    observed = []
    for i in range(total):
        moment = a + timedelta(minutes=i)
        index = moment.hour * 60 + moment.minute
        if moment.strftime("%Y-%m-%d") == day and index < len(states):
            observed.append(states[index])
        else:
            observed.append("unknown")
    valid = sum(state == "valid" for state in observed)
    off = sum(state == "off" for state in observed)
    print(f"      資料覆蓋：有效 {valid}/{total} 分、明確離腕 {off} 分、未知 {total-valid-off} 分")
    if total == 0 or valid / total < 0.70:
        print("      ⚠ 有效資料不足 70%，這段只能保留她的原話，不能作身體對照")
        return
    if not win:
        # ⚠ 「這段沒資料」跟「這段一切正常」長得完全不一樣，**一定要分開講**。
        print("      ⚠ 這段完全沒有資料 —— 不是沒事，是不知道")
        return

    def usable(r: dict) -> bool:
        t = r.get("t", "")
        index = int(t[11:13]) * 60 + int(t[14:16]) if len(t) >= 16 else -1
        return (t.startswith(day + "T") and 0 <= index < len(states)
                and states[index] == "valid"
                and r.get("on_body") in ("on", "inferred_on"))

    hr = [r["hr"] for r in win if r.get("type") == "hr_ibi" and usable(r)
          and r.get("status") == 1 and isinstance(r.get("hr"), (int, float)) and r["hr"] > 0]
    hr += [r["bpm"] for r in win if r.get("type") == "hr" and usable(r)
           and isinstance(r.get("bpm"), (int, float)) and r["bpm"] > 0]
    mo = [r for r in win if r.get("type") == "motion" and usable(r)]
    act = [m["activity"] for m in mo if m.get("trust") is True
           and isinstance(m.get("activity"), (int, float))]
    temp = [r["skin"] for r in win if r.get("type") == "skin_temp" and usable(r)
            and not r.get("charging") and isinstance(r.get("skin"), (int, float))]
    if hr:
        print(f"      心率  中位 {statistics.median(hr):.0f}"
              f"  範圍 {min(hr)}–{max(hr)}  （{len(hr)} 筆）")
    else:
        print("      心率  這段沒有有效心率")
    if act:
        print(f"      動作量 中位 {statistics.median(act):.0f}"
              f"  最大 {max(act):.0f}  （可信 {len(act)} 筆）")
    if temp:
        print(f"      皮膚感測值 中位 {statistics.median(temp):.2f}（非體溫結論）")


def main() -> None:
    before = arg("--前", 90)
    after = arg("--後", 30)
    days = [a for a in sys.argv[1:] if a.startswith("202")]

    files = sorted(VOICE.glob("*.jsonl"))
    if days:
        files = [f for f in files if f.stem in days]
    if not files:
        print(f"還沒有任何語音筆記（{VOICE}）")
        print("先跑 `python 語音筆記撈回來.py` 把手錶上的錄音撈回來轉錄。")
        return

    for f in files:
        recs = load_day(f.stem)
        print(f"\n══ {f.stem} ══（生理資料 {len(recs)} 筆）")
        cover = classify(recs, f.stem)
        print("  " + headline(cover, f.stem))
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                v = json.loads(line)
            except Exception:
                continue
            t = datetime.fromisoformat(v["t"])
            print(f"\n  🎤 {t:%H:%M:%S}  「{v.get('text', '')}」  （{v.get('dur_s')}s）")
            if not recs:
                print("      ⚠ 這一天的生理資料還沒拉回電腦")
                continue
            # 往前拉得比往後長 —— 理由見檔頭
            describe(recs, t - timedelta(minutes=before), t, f"講之前 {before} 分鐘",
                     cover["states"], f.stem)
            describe(recs, t, t + timedelta(minutes=after), f"講之後 {after} 分鐘",
                     cover["states"], f.stem)


if __name__ == "__main__":
    main()
