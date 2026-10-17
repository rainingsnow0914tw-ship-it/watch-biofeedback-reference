#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
睡眠偵測校準 —— 拿三星的判讀當標準答案，把我們自己的偵測器調到對得上。

## 為什麼要有這支（2026-08-30）

### 前情：我寫的睡眠偵測器是壞的，而且差點拿它嚇她

早上寫了一個「找安靜＋心率低的連續區塊」的偵測器，八天全部判成「沒睡」。
**差一點就跟 👤 使用者 說「妳連續八天沒睡」。**

去量分佈才發現是自己的問題：

```
第一版門檻 still_ratio ≥ 0.85  →  實測落在 p99.4，只有 0.6% 的分鐘符合
改成量出來的 p90 (0.806)       →  還是全紅
拆開單獨看才找到真兇：
    只看『不動』     最長撐 9~34 分
    只看『心率低』   最長撐 2~3 分   ← 真兇
```

**逐分鐘的心率中位數抖太兇**，撐不到 3 分鐘待在當日 p25 以下。
那是訊號特性，不是生理事實。

### 為什麼現在可以修了

因為**有標準答案了**。08-30 把 Health Connect 接進我們自己的 app 之後，
拿得到三星自己判的睡眠段（含深眠／REM）。那就是標籤。

> 🔴 **這支的方法論才是重點：不要猜門檻，要拿標籤把門檻搜出來。**
> 我在這個專案裡因為「憑感覺下門檻」出過至少四次錯
> （心率空檔 4 分鐘、溫度空檔、溫度新鮮度、還有上面這個 0.85）。
> 每一次的修法都一樣：**先量分佈，或者找標籤來搜。**

### 這支不取代三星，它是第二意見

三星是黑盒子，而且家人那邊不保證裝得起來、也不保證 Health Connect 同步得到。
兩份獨立判斷**互相對照**才看得出「有一邊怪怪的」——
這跟震動史那條「她感覺到的震動 ≠ 系統記錄的震動」是同一個道理。

用法：
    python 睡眠偵測校準.py              # 網格搜尋 + 印出最好的參數
    python 睡眠偵測校準.py --days 10
"""

from __future__ import annotations

import datetime
import json
import pathlib
import statistics
import sys
from functools import lru_cache

from hc_quality import ASLEEP_STAGES, sleep_evidence

BASE = pathlib.Path(__file__).resolve().parent.parent / "生理資料"
橋 = BASE / "手錶橋接"


# ── 標準答案：本機已備份的三星 Health Connect 睡眠 ───────────────

LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=8))


def _時間(value: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def _睡著分鐘(start: datetime.datetime, end: datetime.datetime) -> set[tuple[str, str]]:
    """只算重疊至少 30 秒的本地分鐘；跨午夜要歸到正確日期。"""
    out: set[tuple[str, str]] = set()
    minute = start.replace(second=0, microsecond=0)
    while minute < end:
        next_minute = minute + datetime.timedelta(minutes=1)
        overlap = (min(end, next_minute) - max(start, minute)).total_seconds()
        if overlap >= 30:
            out.add((minute.strftime("%Y-%m-%d"), minute.strftime("%H:%M")))
        minute = next_minute
    return out

def 三星的答案(days: int) -> dict[str, set[str]]:
    """回 {日期: {HH:MM, ...}}；只用品質合格的三星實睡階段。

    全程讀本機封存，不依賴此刻手機/手錶是否在線。沒有記錄的夜晚不是沒睡。
    """
    source = BASE / "series" / "sleep.jsonl"
    if not source.exists():
        raise SystemExit(f"沒有本機三星睡眠標籤：{source}")
    records = []
    with source.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    accepted, _ = sleep_evidence(records)
    valid = {(s["start"].astimezone(LOCAL_TZ), s["end"].astimezone(LOCAL_TZ))
             for s in accepted}
    cutoff = datetime.datetime.now(LOCAL_TZ).date() - datetime.timedelta(days=days)
    out: dict[str, set[str]] = {}
    seen_sessions = set()
    for record in records:
        end = _時間(record["session_end_time"])
        start = end - datetime.timedelta(seconds=record["duration_seconds"])
        key = (start, end)
        if key not in valid or key in seen_sessions:
            continue
        seen_sessions.add(key)
        for stage in record["stages"]:
            if int(stage["stage"]) not in ASLEEP_STAGES:
                continue
            for date, minute in _睡著分鐘(_時間(stage["start_time"]),
                                           _時間(stage["end_time"])):
                if datetime.date.fromisoformat(date) >= cutoff:
                    out.setdefault(date, set()).add(minute)
    return out


# ── 我們自己的原始資料 ────────────────────────────────────────

@lru_cache(maxsize=90)
def 每分鐘(day: str) -> dict[str, dict]:
    """把一天的原始資料壓成「每分鐘一格」。"""
    f = 橋 / f"{day}.jsonl"
    if not f.exists():
        return {}
    mins: dict[str, dict] = {}
    with f.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line[0] != "{":
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            t = str(r.get("t") or "")
            if len(t) < 16:
                continue
            m = mins.setdefault(t[11:16], {"hr": []})
            if r.get("type") == "motion":
                m["still"] = r.get("still_ratio", 0.0)
                m["worn"] = str(r.get("on_body", "")).endswith("on")
            # 離腕 PPG 可能吐看似正常的數值；只採有佩戴證據的有效心率。
            kind = r.get("type")
            v = r.get("hr") if kind == "hr_ibi" else r.get("bpm") if kind == "hr" else None
            valid_ibi = kind != "hr_ibi" or r.get("status") == 1
            on_body = r.get("on_body") in {"on", "inferred_on"}
            if on_body and valid_ibi and isinstance(v, (int, float)) and 30 < v < 220:
                m["hr"].append(v)
    return mins


def 滑動(mins: dict[str, dict], win: int) -> dict[str, tuple[float, float, bool]]:
    """對每一分鐘算「前後 win 分鐘」的中位數。

    🔴 **這是修好第一版的關鍵。** 第一版逐分鐘比大小，
      而逐分鐘的心率中位數抖到撐不過 3 分鐘 —— 拿它當「持續低」的判準，
      等於要求訊號比它實際能做到的還穩定。
      滑動窗把抖動抹掉，留下的才是趨勢。
    """
    ks = sorted(mins)
    out: dict[str, tuple[float, float, bool]] = {}
    by_minute = {int(k[:2]) * 60 + int(k[3:]): mins[k] for k in ks}
    hr_by_minute = {i: statistics.median(row["hr"]) for i, row in by_minute.items()
                    if row.get("hr")}
    h = win // 2
    for k in ks:
        index = int(k[:2]) * 60 + int(k[3:])
        indices = [i for i in range(max(0, index - h), min(1439, index + h) + 1)
                   if i in by_minute]
        window = [by_minute[i] for i in indices]
        wh = [hr_by_minute[i] for i in indices if i in hr_by_minute]
        ws = [row.get("still", 0.0) for row in window]
        ww = [bool(row.get("worn")) for row in window]
        out[k] = (
            statistics.median(wh) if wh else -1.0,
            statistics.median(ws) if ws else 0.0,
            sum(ww) >= len(ww) * 0.5,
        )
    return out


@lru_cache(maxsize=360)
def _滑動日(day: str, win: int) -> dict[str, tuple[float, float, bool]]:
    return 滑動(每分鐘(day), win)


# ── 搜門檻 ────────────────────────────────────────────────────

def 判(day: str, win: int, hr_q: float, still_q: float,
      最短: int) -> set[str]:
    """用一組參數判斷一天裡哪些分鐘在睡。門檻用**當日分位數**，不用絕對值。"""
    mins = 每分鐘(day)
    if len(mins) < 120:
        return set()
    sl = _滑動日(day, win)
    hs = sorted(v[0] for v in sl.values() if v[0] > 0)
    ss = sorted(v[1] for v in sl.values())
    if not hs or not ss:
        return set()
    hr門 = hs[min(len(hs) - 1, int(len(hs) * hr_q))]
    st門 = ss[min(len(ss) - 1, int(len(ss) * still_q))]

    ks = sorted(sl)
    在睡 = [k for k in ks if sl[k][2] and 0 < sl[k][0] <= hr門 and sl[k][1] >= st門]
    # 只留連續 >= 最短 分鐘的段（睡眠是連續的，零星幾分鐘是雜訊）
    out: set[str] = set()
    run: list[str] = []
    前 = None
    for k in 在睡:
        cur = int(k[:2]) * 60 + int(k[3:])
        if 前 is not None and cur - 前 > 2:
            if len(run) >= 最短:
                out.update(run)
            run = []
        run.append(k)
        前 = cur
    if len(run) >= 最短:
        out.update(run)
    return out


def 分數(判的: set[str], 真的: set[str]) -> tuple[float, float, float]:
    if not 判的 and not 真的:
        return 1.0, 1.0, 1.0
    tp = len(判的 & 真的)
    prec = tp / len(判的) if 判的 else 0.0
    rec = tp / len(真的) if 真的 else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


def 日期分組(days: list[str]) -> tuple[list[str], list[str], list[str]]:
    """依日期前後切開，中間留一日，避免同一夜跨午夜落入兩邊。"""
    if len(days) < 8:
        raise ValueError("可配對日期少於 8，不能做有意義的留出驗收")
    split = max(4, int(len(days) * 0.65))
    return days[:split], days[split:split + 1], days[split + 1:]


def 合計分數(days: list[str], 答案: dict[str, set[str]],
             params: tuple[int, float, float, int]) -> tuple[float, float, float, int, int, int]:
    tp = fp = fn = 0
    for day in days:
        predicted = 判(day, *params)
        truth = 答案[day]
        tp += len(predicted & truth)
        fp += len(predicted - truth)
        fn += len(truth - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1, tp, fp, fn


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    days = 60
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    print("本機三星有效睡眠階段：先搜訓練日，再用較晚日期驗收")
    print("⚠ 無記錄的夜晚是未知；這裡只評估有三星記錄且有手錶原始檔的日期。\n")

    答案 = 三星的答案(days)
    有資料 = [d for d in sorted(答案) if (橋 / f"{d}.jsonl").exists()]
    if not 有資料:
        raise SystemExit("三星有睡眠判讀的那幾天，我們這邊沒有對應的原始資料。")
    try:
        訓練日, 隔離日, 驗收日 = 日期分組(有資料)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"  可配對日期 {len(有資料)}：訓練 {len(訓練日)}、隔離 {len(隔離日)}、留出驗收 {len(驗收日)}")
    print(f"  日期範圍 {有資料[0]}～{有資料[-1]}；驗收從 {驗收日[0]} 開始")
    print(f"  訓練標籤 {sum(len(答案[d]) for d in 訓練日)} 分；驗收標籤 {sum(len(答案[d]) for d in 驗收日)} 分\n")

    最佳 = None
    grid = [(w, hq, sq, mn)
            for w in (15, 21, 31, 45)
            for hq in (0.20, 0.30, 0.40, 0.50)
            for sq in (0.40, 0.55, 0.70)
            for mn in (20, 30, 45)]
    print(f"  只在訓練日搜 {len(grid)} 組參數⋯⋯", flush=True)
    for params in grid:
        prec, rec, f1, *_ = 合計分數(訓練日, 答案, params)
        if 最佳 is None or (f1, prec) > (最佳[0], 最佳[1]):
            最佳 = (f1, prec, params)

    _, _, params = 最佳
    w, hq, sq, mn = params
    train_prec, train_rec, train_f1, *_ = 合計分數(訓練日, 答案, params)
    prec, rec, f1, tp, fp, fn = 合計分數(驗收日, 答案, params)
    print(f"\n═══ 訓練選出的參數 ═══")
    print(f"  滑動窗 {w} 分鐘　心率取當日最低 {int(hq*100)}%　"
          f"安靜取當日最高 {int((1-sq)*100)}%　最短連續 {mn} 分")
    print(f"  訓練集 F1 {train_f1:.3f}；留出驗收 F1 {f1:.3f}、精確率 {prec:.1%}、召回率 {rec:.1%}")
    print(f"  留出驗收 TP {tp}、FP {fp}、FN {fn} 分")
    covered = sum(len(set(每分鐘(d)) & 答案[d]) for d in 驗收日)
    labeled = sum(len(答案[d]) for d in 驗收日)
    print(f"  驗收標籤中有手錶原始記錄的分鐘 {covered}/{labeled}（{covered/labeled:.1%}）\n")

    if f1 < 0.85:
        print("  🔴 未達預定 F1 0.85：不能把自家偵測器接進正式睡眠結論。")
    else:
        print("  ✅ 留出集達預定 F1 0.85；仍只算三星已記錄夜晚的第二意見，未驗證無記錄夜晚。")

    print("\n  留出日期逐日對照：")
    for d in 驗收日:
        我 = 判(d, w, hq, sq, mn)
        p, r, f = 分數(我, 答案[d])
        print(f"    {d[5:]}　三星 {len(答案[d]):4d} 分　我判 {len(我):4d} 分　"
              f"重疊 {len(我 & 答案[d]):4d}　F1 {f:.2f}")


if __name__ == "__main__":
    main()
