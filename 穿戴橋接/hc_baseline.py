#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
hc_baseline.py —— 基線層：算出「她的正常長什麼樣」

這是三層架構的第二層，也是最容易被跳過的一層。
沒有基線就沒有異常 —— 「心率 78」本身不代表任何事情，
對某些人那是靜息、對某些人那是緊張。要有她自己的分布才有意義。

而且基線不能只有一個數字。同一個人早上和半夜的靜息心率就不一樣，
所以基線是「按時段切開的分布」，比的時候拿當下的時段去比。

    python hc_baseline.py                # 印報告
    python hc_baseline.py --json         # 給判讀層吃的格式
    python hc_baseline.py --check heart_rate=118
                                         # 問「現在 118 算不算異常」

判讀層要用的是最底下三個函式：
    load_series()   讀時間序列
    build()         算基線
    assess()        拿一個當下數值去比基線，回傳判斷 + 信心

誠實邊界（寫在程式裡，不要在報告裡假裝沒有）：
    · 資料不足時回 status="insufficient"，不是回「正常」。
      「沒看到異常」跟「一切正常」是兩件事，這層必須分得清楚。
    · 皮膚溫度目前這條路拿不到（見交接頁），所以這裡不做溫度判讀。
    · 這不是醫療判斷，只是「跟她自己的過去比，對不對得上」。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from hc_quality import (BASELINE_TARGET_DAYS, baseline_status,
                        heart_rate_evidence, sleep_evidence)

DATA_DIR = Path(__file__).resolve().parent.parent / "生理資料"

# 基線要幾天才算數。少於 MIN_DAYS 一律回 insufficient，不猜。
MIN_DAYS = 3
GOOD_DAYS = 14          # 其他舊型別；心率／睡眠另需 30 個有效日與捕捉比例

# 每個時段桶多寬（小時）。3 小時是折衷：
# 太細 → 每桶樣本太少，分布抖；太粗 → 早上跟半夜被混在一起。
BUCKET_HOURS = 3

# 一筆紀錄要有「時間」，不同型別欄位名不一樣，照順序找
TIME_FIELDS = ("time", "start_time", "session_start_time", "end_time",
               "session_end_time")


# ── 讀檔 ───────────────────────────────────────────────────────────────
def parse_time(value: str) -> datetime | None:
    """ISO-8601 轉本地時間。收進來的都是 UTC（Z 結尾）。"""
    if not isinstance(value, str):
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()          # 轉本地 —— 時段判斷一定要用本地時間
    except Exception:
        return None


def record_time(record: dict) -> datetime | None:
    for field in TIME_FIELDS:
        if field in record:
            dt = parse_time(record[field])
            if dt:
                return dt
    return None


def load_series(dtype: str, data_dir: Path = DATA_DIR) -> list[dict]:
    """讀一個型別的全部紀錄，按時間排好。壞行跳過不炸。"""
    path = data_dir / "series" / f"{dtype}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        dt = record_time(record)
        if dt:
            record["_dt"] = dt
            out.append(record)
    out.sort(key=lambda r: r["_dt"])
    return out


def available_types(data_dir: Path = DATA_DIR) -> list[str]:
    series_dir = data_dir / "series"
    if not series_dir.exists():
        return []
    return sorted(p.stem for p in series_dir.glob("*.jsonl"))


# ── 統計小工具 ─────────────────────────────────────────────────────────
def percentile(values: list[float], q: float) -> float:
    """線性內插百分位。不用 numpy —— 這支要能在任何機器上直接跑。"""
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[int(pos)]
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def bucket_of(dt: datetime) -> int:
    """把時間丟進時段桶。回傳桶的起始小時（0/3/6/…/21）。"""
    return (dt.hour // BUCKET_HOURS) * BUCKET_HOURS


def bucket_label(start: int) -> str:
    return f"{start:02d}-{(start + BUCKET_HOURS) % 24:02d} 點"


# ── 建基線 ─────────────────────────────────────────────────────────────
def numeric_field(dtype: str) -> str | None:
    """每個型別拿哪個欄位當數值。沒列到的型別不做基線（例如睡眠是區間不是點）。"""
    return {
        "heart_rate": "bpm",
        "resting_heart_rate": "bpm",
        "heart_rate_variability": "rmssd_millis",
        "oxygen_saturation": "percentage",
        "respiratory_rate": "rate",
        # 三星的壓力分數 0~100，數字越高壓力越大。
        # 一樣按時段切開比 —— 她半夜的「正常壓力」跟下午的不會一樣。
        "stress": "score",
        "body_temperature": "celsius",
        "basal_metabolic_rate": "watts",
    }.get(dtype)


def build_numeric(dtype: str, records: list[dict]) -> dict:
    """
    某個數值型別的基線：整體分布 + 每個時段桶的分布。

    回傳的 buckets 是判讀層真正要用的東西 ——
    比對時拿「現在幾點」找對應的桶，而不是拿一個全天平均去比。
    """
    field = numeric_field(dtype)
    quality = None
    if dtype == "heart_rate":
        records, quality = heart_rate_evidence(records)
    values, by_bucket = [], defaultdict(list)
    days = set()

    for record in records:
        raw = record.get(field)
        if not isinstance(raw, (int, float)):
            continue
        dt = record["_dt"]
        values.append(float(raw))
        by_bucket[bucket_of(dt)].append(float(raw))
        days.add(dt.date())

    if not values:
        return {"type": dtype, "field": field, "samples": 0, "days": 0,
                "status": "insufficient",
                "reason": ("沒有通過資料品質門檻的完整日" if quality else "沒有任何數值"),
                **({"quality": quality} if quality else {})}

    def dist(vals: list[float]) -> dict:
        return {
            "n": len(vals),
            "p10": round(percentile(vals, 0.10), 2),
            "p50": round(percentile(vals, 0.50), 2),
            "p90": round(percentile(vals, 0.90), 2),
            "mean": round(statistics.fmean(vals), 2),
            "sd": round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0,
        }

    day_count = len(days)
    return {
        "type": dtype,
        "field": field,
        "samples": len(values),
        "days": day_count,
        "status": (baseline_status(day_count, quality["capture_ratio"], MIN_DAYS)
                   if quality else "quality_unverified"),
        **({"reason": "此型別尚未定義有效日規則，不宣稱基線成熟"} if not quality else {}),
        "first_seen": min(r["_dt"] for r in records).isoformat(),
        "last_seen": max(r["_dt"] for r in records).isoformat(),
        "overall": dist(values),
        "buckets": {str(start): dist(vals)
                    for start, vals in sorted(by_bucket.items())},
        **({"quality": quality} if quality else {}),
    }


def build_sleep(records: list[dict]) -> dict:
    """
    睡眠基線。對這個專案特別重要，原因不是健康 ——
    是 使用者 作息日夜顛倒，「現在是早上還是半夜」對她沒有意義，
    有意義的是「她這一覺睡完起來多久了」。care.py 目前用時鐘判斷時段，
    之後應該改吃這裡的結果。

    另外這也是「求證」的主要素材：她說「我剛睡過了」，就是來這裡查。
    """
    sessions, quality = sleep_evidence(records)

    if not sessions:
        return {"type": "sleep", "sessions": 0, "status": "insufficient",
                "reason": "沒有可驗證的睡眠階段紀錄；空白不代表沒睡",
                "quality": quality}

    sessions.sort(key=lambda s: s["start"])
    hours = [s["asleep_hours"] for s in sessions]
    spans = [s["span_hours"] for s in sessions]
    days = {s["start"].date() for s in sessions}
    latest = sessions[-1]

    return {
        "type": "sleep",
        "sessions": len(sessions),
        "days": len(days),
        "status": baseline_status(len(days), quality["capture_ratio"], MIN_DAYS),
        "quality": quality,
        "hours": {
            "p10": round(percentile(hours, 0.10), 2),
            "p50": round(percentile(hours, 0.50), 2),
            "p90": round(percentile(hours, 0.90), 2),
        },
        "session_span_hours": {
            "p50": round(percentile(spans, 0.50), 2),
        },
        # 入睡／起床時刻用「距離午夜幾小時」表示，跨夜才不會被切斷
        "typical_bedtime_hour": round(
            statistics.fmean([s["start"].hour + s["start"].minute / 60
                              for s in sessions]), 1),
        "typical_wake_hour": round(
            statistics.fmean([s["end"].hour + s["end"].minute / 60
                              for s in sessions]), 1),
        "last_session": {
            "start": latest["start"].isoformat(),
            "end": latest["end"].isoformat(),
            "hours": latest["asleep_hours"],
            "span_hours": latest["span_hours"],
        },
    }


def build(data_dir: Path = DATA_DIR) -> dict:
    """把所有型別的基線算出來，外加覆蓋率。"""
    types = available_types(data_dir)
    out: dict = {
        "built_at": datetime.now().astimezone().isoformat(),
        "data_dir": str(data_dir),
        "types": {},
        "coverage": {},
    }

    for dtype in types:
        records = load_series(dtype, data_dir)
        if not records:
            continue
        if dtype == "sleep":
            out["types"][dtype] = build_sleep(records)
        elif numeric_field(dtype):
            out["types"][dtype] = build_numeric(dtype, records)
        else:
            days = {r["_dt"].date() for r in records}
            out["types"][dtype] = {
                "type": dtype, "samples": len(records), "days": len(days),
                "status": "no_baseline",
                "note": "有收資料，但這個型別不做數值基線",
                "last_seen": max(r["_dt"] for r in records).isoformat(),
            }

        out["coverage"][dtype] = coverage(records)

    return out


def coverage(records: list[dict], window_hours: int = 48) -> dict:
    """
    最近 48 小時裡，有幾個小時真的有資料。

    這個數字比基線本身更常救命：判讀層必須分得出
    「量到了而且正常」跟「根本沒量到」。手錶脫下來充電、
    app 被系統殺掉、Wi-Fi 斷線 —— 看起來都像「一切平靜」。
    """
    if not records:
        return {"hours_with_data": 0, "window_hours": window_hours, "ratio": 0.0}
    now = datetime.now().astimezone()
    cutoff = now - timedelta(hours=window_hours)
    hours = {r["_dt"].replace(minute=0, second=0, microsecond=0)
             for r in records if r["_dt"] >= cutoff}
    return {
        "hours_with_data": len(hours),
        "window_hours": window_hours,
        "ratio": round(len(hours) / window_hours, 3),
        "last_record_age_minutes": round(
            (now - records[-1]["_dt"]).total_seconds() / 60, 1),
    }


# ── 判讀：拿當下數值比基線 ─────────────────────────────────────────────
def assess(dtype: str, value: float, when: datetime | None = None,
           baseline: dict | None = None, data_dir: Path = DATA_DIR) -> dict:
    """
    「現在這個數值，對她來說算不算異常？」

    刻意回傳三種狀態而不是二分：
        insufficient —— 基線還不夠，不下判斷（這是最重要的一種）
        normal       —— 落在她自己的 p10~p90 之間
        high / low   —— 超出，附上超出多少個標準差

    低於門檻就閉嘴，比亂報警重要 —— 假警報多了她就會開始忽略，
    整套系統就廢了。
    """
    when = when or datetime.now().astimezone()
    baseline = baseline or build(data_dir)
    info = baseline.get("types", {}).get(dtype)

    # A growing distribution may be useful for inspection, but must not be
    # described to 使用者 as her established normal range.
    if not info or info.get("status") != "mature" or not info.get("overall"):
        quality = (info or {}).get("quality") or {}
        return {"status": "insufficient", "type": dtype, "value": value,
                "reason": (info or {}).get("reason", "基線尚未通過有效日與觀測完整度門檻"),
                "days": (info or {}).get("days", 0),
                "need_days": quality.get("target_days", GOOD_DAYS)}

    start = bucket_of(when)
    bucket = info.get("buckets", {}).get(str(start))
    # 該時段樣本太少就退回全天分布，並在回傳裡註明用了哪一個
    if bucket and bucket["n"] >= 20:
        ref, scope = bucket, bucket_label(start)
    else:
        ref, scope = info["overall"], "全天"

    sd = ref["sd"] or 1e-9
    z = (value - ref["mean"]) / sd

    if value > ref["p90"]:
        verdict = "high"
    elif value < ref["p10"]:
        verdict = "low"
    else:
        verdict = "normal"

    return {
        "status": verdict,
        "type": dtype,
        "value": value,
        "scope": scope,
        "baseline_maturity": info["status"],
        "reference": {"p10": ref["p10"], "p50": ref["p50"],
                      "p90": ref["p90"], "n": ref["n"]},
        "z": round(z, 2),
        "說法": _phrase(dtype, value, verdict, ref, scope),
    }


def _phrase(dtype: str, value: float, verdict: str,
            ref: dict, scope: str) -> str:
    """給人看的一句話。刻意講「跟妳自己比」，不講醫療語言。"""
    name = {"heart_rate": "心率", "resting_heart_rate": "靜息心率",
            "heart_rate_variability": "HRV", "oxygen_saturation": "血氧",
            "respiratory_rate": "呼吸率"}.get(dtype, dtype)
    if verdict == "normal":
        return (f"{name} {value}，落在妳{scope}已觀測資料的 p10~p90 區間"
                f"（{ref['p10']}~{ref['p90']}）；不代表健康正常")
    direction = "高" if verdict == "high" else "低"
    return (f"{name} {value}，比妳{scope}平常{direction}"
            f"（平常 {ref['p10']}~{ref['p90']}，中位 {ref['p50']}）")


# ── 報告 ───────────────────────────────────────────────────────────────
def report(baseline: dict) -> str:
    lines = ["", "  基線報告　" + baseline["built_at"][:16].replace("T", " "), ""]
    types = baseline.get("types", {})
    if not types:
        lines += ["  還沒有任何資料。",
                  "  先跑 hc_receiver.py 並讓手機 app 同步一次。", ""]
        return "\n".join(lines)

    label = {"insufficient": "資料不足", "growing": "累積中",
             "mature": "已成熟", "no_baseline": "只收不判",
             "quality_unverified": "品質待驗"}

    for dtype, info in sorted(types.items()):
        mark = label.get(info.get("status"), info.get("status", "?"))
        days = info.get("days", 0)
        lines.append(f"  {dtype:<26} {mark}　{days} 天　"
                     f"{info.get('samples', info.get('sessions', 0))} 筆")

        if info.get("overall"):
            o = info["overall"]
            lines.append(f"      整體　p10 {o['p10']}　中位 {o['p50']}　"
                         f"p90 {o['p90']}　（n={o['n']}）")
            for start, dist in sorted(info.get("buckets", {}).items(),
                                      key=lambda kv: int(kv[0])):
                if dist["n"] >= 20:
                    lines.append(f"      {bucket_label(int(start))}　"
                                 f"p10 {dist['p10']}　中位 {dist['p50']}　"
                                 f"p90 {dist['p90']}　（n={dist['n']}）")

        if dtype == "sleep" and info.get("hours"):
            h = info["hours"]
            lines.append(f"      每段有紀錄的實睡　p10 {h['p10']}h　中位 {h['p50']}h　"
                         f"p90 {h['p90']}h")
            lines.append(f"      睡眠段跨度中位 {info['session_span_hours']['p50']}h；"
                         "中間醒著時間不算實睡")
            lines.append(f"      通常 {info['typical_bedtime_hour']} 點睡、"
                         f"{info['typical_wake_hour']} 點醒")
            last = info.get("last_session", {})
            if last:
                lines.append(f"      最後一次　{last['start'][:16]} → "
                             f"{last['end'][:16]}（{last['hours']}h）")

        cov = baseline["coverage"].get(dtype, {})
        quality = info.get("quality")
        if quality:
            lines.append(f"      品質規則：{quality['rule']}")
            lines.append(f"      有效日 {quality['valid_days']}/{quality['target_days']}；"
                         f"記錄跨度內捕捉比例 {quality['capture_ratio']:.0%}"
                         f"（成熟需 ≥{quality['target_capture_ratio']:.0%}；空白日不作零值）")
        if cov:
            lines.append(f"      近 48 小時覆蓋　{cov['hours_with_data']}/48 小時"
                         f"　最後一筆是 {cov.get('last_record_age_minutes', '?')} "
                         f"分鐘前")
        lines.append("")

    ready = [d for d, i in types.items() if i.get("status") == "mature"]
    growing = [d for d, i in types.items() if i.get("status") == "growing"]
    lines.append(f"  可以用來判讀的：{'、'.join(ready) if ready else '（還沒有）'}")
    if growing:
        lines.append(f"  還在長的：{'、'.join(growing)}"
                     "　—— 各型別有效日與觀測完整度都通過後才算成熟")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="生理資料基線層")
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--json", action="store_true", help="輸出 JSON 給判讀層")
    ap.add_argument("--check", metavar="TYPE=VALUE",
                    help="問一個當下數值算不算異常，例如 heart_rate=118")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    baseline = build(data_dir)

    if args.check:
        if "=" not in args.check:
            print("格式是 --check heart_rate=118", file=sys.stderr)
            sys.exit(2)
        dtype, raw = args.check.split("=", 1)
        result = assess(dtype.strip(), float(raw), baseline=baseline,
                        data_dir=data_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.json:
        print(json.dumps(baseline, ensure_ascii=False, indent=2))
    else:
        print(report(baseline))


if __name__ == "__main__":
    main()
