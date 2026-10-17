#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
merge_samsung_export.py —— 把 Samsung Health 的「下載個人資料」併進時間序列

為什麼需要這支（2026-08-12 發現）：
    我原本斷定 HRV 和呼吸率「走這條路拿不到，要等自己寫手錶 app」。
    那句話只對了一半 —— 拿不到的是「即時 API」，
    但 Samsung Health 的匯出功能一直都給得出來：

        com.samsung.health.hrv.csv            ← HRV
        com.samsung.health.respiratory_rate   ← 呼吸率
        com.samsung.shealth.step_daily_trend  ← 179 天的步數歷史

    而且匯出那幾下可以用 adb 自動點完，所以這不是一次性的救援，
    是一條可以固定跑的補給線。

三星的檔案格式（踩過才知道）：
    · CSV 第 1 行是 metadata，第 2 行才是欄位名，第 3 行起才是資料
    · 真正細的數值不在 CSV 裡，在 jsons\<型別>\<數字>\<uuid>.binning_data.json
      CSV 只放摘要（最大/最小/平均）
    · 時間是「當地時間的字串」，時區另外放在 time_offset 欄（UTC+0800）
    · HRV 的 json 長這樣：
      {"start_time":epoch_ms, "end_time":..., "sdnn":..., "rmssd":...}

用法：
    python merge_samsung_export.py                    # 併最新的那包
    python merge_samsung_export.py --dir <匯出資料夾>
    python merge_samsung_export.py --dry-run          # 只看會併進什麼，不寫檔
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hc_receiver import DATA_DIR, Store

TZ = timezone(timedelta(hours=8))       # 三星檔案裡的時間是本地時間


# ── 讀三星的檔 ─────────────────────────────────────────────────────────
def read_samsung_csv(path: Path) -> list[dict]:
    """三星 CSV：第 1 行 metadata、第 2 行欄位名、第 3 行起資料。"""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) < 3:
        return []
    header = rows[1]
    out = []
    for row in rows[2:]:
        if not any(row):
            continue
        out.append({h: (row[i] if i < len(row) else "")
                    for i, h in enumerate(header) if h})
    return out


def parse_local(text: str) -> str | None:
    """
    '2026-08-11 06:56:00.000' → UTC 的 ISO 字串。

    ⚠ 這裡踩過一個會毀資料的坑（2026-08-12）：
    我原本以為那些時間字串是「本地時間」，因為旁邊還有一欄 time_offset=UTC+0800。
    照那個假設換算完，同一次心跳在兩份資料裡差了整整 8 小時 ——
    收集器記 18:40:29、匯出檔變成 10:40:29，去重完全失效，
    而且會在基線裡憑空多出一段「半夜心率」。

    **三星 CSV 裡的時間本來就是 UTC**，time_offset 只是告訴你顯示時要加多少。
    驗證方法：拿一筆兩邊都有的紀錄對時間 —— 睡眠起點 06:56Z
    正好對上 Health Connect 記的 14:56 本地時間。

    教訓：跨系統合併資料，**先找一筆兩邊都有的東西對時**，再寫轉換。
    """
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return None


def parse_epoch_ms(ms) -> str | None:
    try:
        return (datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc)
                .isoformat().replace("+00:00", "Z"))
    except Exception:
        return None


def find_json(root: Path, dtype: str, ref: str) -> Path | None:
    r"""
    binning_data 欄位放的是檔名，實際檔案散在 jsons\<型別>\<數字>\ 底下。
    數字那層是三星自己的分桶，沒有規律可循，所以直接遞迴找。
    """
    if not ref:
        return None
    base = ref.split("/")[-1]
    hits = list((root / "jsons" / dtype).rglob(base))
    return hits[0] if hits else None


# ── 各型別的轉換 ───────────────────────────────────────────────────────
def extract_heart_rate(root: Path) -> list[dict]:
    """心率。CSV 每列是一次量測；binning json 裡有更細的取樣。"""
    out = []
    for path in root.glob("*tracker.heart_rate*.csv"):
        for row in read_samsung_csv(path):
            t = parse_local(row.get("com.samsung.health.heart_rate.start_time", ""))
            raw = row.get("com.samsung.health.heart_rate.heart_rate", "")
            if not t or not raw:
                continue
            try:
                out.append({"bpm": round(float(raw)), "time": t})
            except ValueError:
                pass
    return out


def extract_hrv(root: Path) -> list[dict]:
    """
    HRV。CSV 只有指標，真正的數字在 json 裡。
    取 rmssd —— 那是 hc_baseline 認得的欄位，也是壓力指標最常用的那個。
    """
    out = []
    for path in root.glob("*health.hrv*.csv"):
        for row in read_samsung_csv(path):
            jf = find_json(root, "com.samsung.health.hrv",
                           row.get("binning_data", ""))
            if not jf:
                continue
            try:
                blocks = json.loads(jf.read_text(encoding="utf-8"))
            except Exception:
                continue
            for b in blocks:
                t = parse_epoch_ms(b.get("start_time"))
                if t is None or b.get("rmssd") is None:
                    continue
                out.append({"rmssd_millis": round(float(b["rmssd"]), 2),
                            "sdnn_millis": (round(float(b["sdnn"]), 2)
                                            if b.get("sdnn") is not None else None),
                            "time": t})
    return out


def extract_respiratory(root: Path) -> list[dict]:
    out = []
    for path in root.glob("*respiratory_rate*.csv"):
        for row in read_samsung_csv(path):
            t = parse_local(row.get("start_time", ""))
            raw = row.get("average", "")
            if not t or not raw:
                continue
            try:
                out.append({"rate": round(float(raw), 2), "time": t})
            except ValueError:
                pass
    return out


def extract_steps_daily(root: Path) -> list[dict]:
    """
    每日步數。刻意存成 steps_daily 而不是 steps ——
    收集器拿到的 steps 是 5 分鐘一格，兩種顆粒度混在一起會讓基線失真。
    """
    out = []
    for path in root.glob("*step_daily_trend*.csv"):
        for row in read_samsung_csv(path):
            # day_time 是「2025-10-11 00:00:00.000」這種本地時間字串，
            # 不是 epoch —— 跟 HRV 的 json 不一樣，踩過一次才知道。
            # 同一天會有兩列（source_type 0 和 -2，手機與彙總），
            # 數字一樣，靠語意去重自然收斂成一筆。
            t = parse_local(row.get("day_time", ""))
            raw = row.get("count", "")
            if not t or not raw:
                continue
            try:
                rec = {"count": int(float(raw)), "time": t}
            except ValueError:
                continue
            for k, name in (("distance", "distance_meters"),
                            ("calorie", "calories")):
                v = row.get(k, "")
                if v:
                    try:
                        rec[name] = round(float(v), 2)
                    except ValueError:
                        pass
            out.append(rec)
    return out


# 三星的睡眠階段代碼（從資料裡反推 + 官方文件對照）
SLEEP_STAGE = {"40001": "awake", "40002": "light", "40003": "deep", "40004": "rem"}


def extract_sleep_stage(root: Path) -> list[dict]:
    out = []
    for path in root.glob("*sleep_stage*.csv"):
        for row in read_samsung_csv(path):
            s = parse_local(row.get("start_time", ""))
            e = parse_local(row.get("end_time", ""))
            if not s:
                continue
            code = (row.get("stage", "") or "").strip()
            out.append({"start_time": s, "end_time": e,
                        "stage": SLEEP_STAGE.get(code, code),
                        "sleep_id": row.get("sleep_id", "")})
    return out


def extract_stress(root: Path) -> list[dict]:
    """
    壓力分數。2026-08-12 把手錶的壓力量測從「僅限手動」改成「連續測量」之後才有的。

    三星的壓力分數 0~100，**數字越高壓力越大**
    （0-29 低、30-59 一般、60-79 高、80-100 很高）。
    原理是看 HRV —— 交感神經上來，心跳間隔變規律，分數就往上跑。

    ⚠ 誠實邊界：這是三星自己的演算法，不是臨床壓力量表。
    人在打字、走動、剛喝咖啡的時候分數都會高。
    **一樣只能跟她自己的基線比趨勢，不能拿單一個數字下結論。**
    """
    out = []
    for path in root.glob("*shealth.stress*.csv"):
        for row in read_samsung_csv(path):
            jf = find_json(root, "com.samsung.shealth.stress",
                           row.get("binning_data", ""))
            if not jf:
                continue
            try:
                blocks = json.loads(jf.read_text(encoding="utf-8"))
            except Exception:
                continue
            for b in blocks:
                t = parse_epoch_ms(b.get("start_time"))
                if t is None or b.get("score") is None:
                    continue
                out.append({"score": float(b["score"]),
                            "level": b.get("level"),
                            "time": t})
    return out


EXTRACTORS = {
    "heart_rate": extract_heart_rate,
    "stress": extract_stress,
    "heart_rate_variability": extract_hrv,
    "respiratory_rate": extract_respiratory,
    "steps_daily": extract_steps_daily,
    "sleep_stage": extract_sleep_stage,
}


# ── 語意去重 ───────────────────────────────────────────────────────────
def semantic_keys(dtype: str, data_dir: Path) -> set[tuple]:
    """
    已經有的紀錄的「語意指紋」：同一個時間、同一個數值就算同一筆。

    為什麼不能靠 Store 原本的雜湊去重：
    收集器從 Health Connect 拿到的心率長這樣
        {"bpm":95,"time":"...","metadata":{...}}
    匯出檔轉出來的長這樣
        {"bpm":95,"time":"..."}
    內容不同 → 雜湊不同 → 同一次心跳會被存兩次。
    所以這裡另外用「時間 + 數值」比對，重疊的那段才不會算兩遍。
    """
    path = data_dir / "series" / f"{dtype}.jsonl"
    keys: set[tuple] = set()
    if not path.exists():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = r.get("time") or r.get("start_time") or ""
        val = (r.get("bpm") or r.get("rmssd_millis") or r.get("rate")
               or r.get("count") or r.get("stage"))
        keys.add((t[:19], val))          # 秒級對齊，忽略毫秒差異
    return keys


def main() -> None:
    ap = argparse.ArgumentParser(description="併入 Samsung Health 匯出資料")
    ap.add_argument("--dir", help="匯出資料夾；不給就找最新的一包")
    ap.add_argument("--dry-run", action="store_true", help="只看不寫")
    args = ap.parse_args()

    if args.dir:
        root = Path(args.dir)
    else:
        cands = sorted(DATA_DIR.glob("三星匯出_*"))
        if not cands:
            print(f"在 {DATA_DIR} 找不到「三星匯出_*」資料夾")
            sys.exit(1)
        root = cands[-1]

    if not root.exists():
        print(f"找不到 {root}")
        sys.exit(1)

    print(f"\n  來源　{root.name}")
    print(f"  目標　{DATA_DIR}\n")

    store = None if args.dry_run else Store()
    total_new = 0

    for dtype, fn in EXTRACTORS.items():
        records = fn(root)
        if not records:
            print(f"  {dtype:<24} 匯出檔裡沒有")
            continue

        seen = semantic_keys(dtype, DATA_DIR)
        fresh = []
        for r in records:
            t = r.get("time") or r.get("start_time") or ""
            val = (r.get("bpm") or r.get("rmssd_millis") or r.get("rate")
                   or r.get("count") or r.get("stage"))
            key = (t[:19], val)
            if key in seen:
                continue
            seen.add(key)
            fresh.append(r)

        times = sorted(r.get("time") or r.get("start_time") or "" for r in records)
        span = f"{times[0][:10]} → {times[-1][:10]}" if times else ""
        print(f"  {dtype:<24} 匯出 {len(records):>5} 筆　新增 {len(fresh):>5} 筆"
              f"　{span}")

        if fresh and store:
            result = store.ingest({"timestamp": datetime.now(timezone.utc)
                                   .isoformat().replace("+00:00", "Z"),
                                   "app_version": "samsung-export",
                                   dtype: fresh}, "samsung_export")
            total_new += sum(result["added"].values())

    if args.dry_run:
        print("\n  （--dry-run，什麼都沒寫）\n")
    else:
        print(f"\n  總共併進 {total_new} 筆。跑 python hc_baseline.py 看結果。\n")


if __name__ == "__main__":
    main()
