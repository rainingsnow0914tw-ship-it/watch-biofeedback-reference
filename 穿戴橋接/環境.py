"""原始紀錄（生理資料/手錶橋接/<日>.jsonl，type=env）：
  hpa / hpa_min / hpa_max / hpa_n   這一分鐘氣壓中位、最小、最大、筆數（1 Hz，應接近 60）
  lux / lux_min / lux_max / lux_n   這一分鐘最後一筆亮度、最小、最大、事件數（on-change：沒變就 0，不是沒量到）

兩個偵測（規則寫死、附理由，不是模型）：
          層數 = round(Δ / 0.36)；上升 = 氣壓降。配三星步數：那幾分鐘有步數 → 樓梯，沒有 → 電梯。

      from 環境 import lights_off_at, floor_changes      哨兵用"""
from __future__ import annotations

import argparse
import datetime as dt
import json, statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "生理資料" / "手錶橋接"
HPA_PER_FLOOR = 0.36
FLOOR_MIN_DELTA = 0.30
FLOOR_WINDOW_MIN = 4
DARK_LUX = 3.0
DARK_MINUTES = 3


def load(day: str) -> list[dict]:
    f = RAW / f"{day}.jsonl"
    if not f.exists():
        return []
    out = []
    with f.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith('{"t":"') or '"type":"env"' not in line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                r["dt"] = dt.datetime.fromisoformat(r["t"][:19])
            except Exception:
                continue
            out.append(r)
    out.sort(key=lambda r: r["dt"])
    return out


def load_range(since: dt.datetime, until: dt.datetime) -> list[dict]:
    days = sorted({(since + dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range((until.date() - since.date()).days + 1)})   # M27：跨多日逐日列
    return [r for d in days for r in load(d) if since <= r["dt"] <= until]


def lights_off_at(rows: list[dict], dark_lux: float = DARK_LUX, minutes: int = DARK_MINUTES) -> list[dict]:
    """ """
    out = []
    i = 0
    while i < len(rows):
        r = rows[i]
        if r.get("lux", -1) < 0 or r["lux"] >= dark_lux:
            i += 1; continue

        run = []
        for x in rows[i:]:
            if not (0 <= x.get("lux", -1) < dark_lux) or (run and (x["dt"] - run[-1]["dt"]) > dt.timedelta(minutes=2)):
                break
            run.append(x)
            if (x["dt"] - r["dt"]) >= dt.timedelta(minutes=minutes - 1):
                break
        if not run or (run[-1]["dt"] - r["dt"]) < dt.timedelta(minutes=minutes - 1):
            i += 1; continue
        before = [x for x in rows[max(0, i - 10):i] if x.get("lux", -1) >= dark_lux * 5 and (r["dt"] - x["dt"]) <= dt.timedelta(minutes=10)]
        if not before:
            i += 1; continue
        out.append({"t": r["dt"], "lux_before": before[-1]["lux"], "lux_after": r["lux"]})
        # 跳過這段暗，直到再亮起來
        while i < len(rows) and 0 <= rows[i].get("lux", -1) < dark_lux:
            i += 1
    return out


def floor_changes(rows: list[dict], steps_fn=None) -> list[dict]:
    """回每一次樓層變化：{t0, t1, delta_hpa, floors, direction, how}。
    掃描：以每一分鐘為起點，往後最多 FLOOR_WINDOW_MIN 分鐘，氣壓差 ≥ FLOOR_MIN_DELTA 就算一次；合併相鄰的。
    steps_fn(t0, t1) → 那段三星步數（None = 不知道）；有步數 → 樓梯，0 → 電梯。"""
    pts = [(r["dt"], r["hpa"]) for r in rows if r.get("hpa", -1) > 0 and r.get("hpa_n", 0) >= 20]
    out = []
    i = 0
    while i < len(pts):
        t0, p0 = pts[i]
        best = None
        for j in range(i + 1, len(pts)):
            t1, p1 = pts[j]
            if (t1 - t0) > dt.timedelta(minutes=FLOOR_WINDOW_MIN):
                break
            d = round(p1 - p0, 2)
            if abs(d) >= FLOOR_MIN_DELTA and (best is None or abs(d) > abs(best[2])):
                best = (j, t1, d)
        if best is None:
            i += 1; continue
        j, t1, d = best
        # 往後延伸：還在同方向繼續變就併進去（電梯跑好幾層）
        while j + 1 < len(pts) and (pts[j + 1][0] - pts[j][0]) <= dt.timedelta(minutes=2) and \
                (pts[j + 1][1] - pts[j][1]) * d > 0 and abs(pts[j + 1][1] - pts[j][1]) >= 0.08:
            j += 1
        t1, p1 = pts[j]; d = round(p1 - p0, 2)
        floors = max(1, round(abs(d) / HPA_PER_FLOOR))
        steps = steps_fn(t0, t1) if steps_fn else None
        how = "不知道" if steps is None else ("樓梯" if steps >= 15 * floors else "電梯")
        out.append({"t0": t0, "t1": t1, "delta_hpa": round(d, 2), "floors": floors,
                    "direction": "上" if d < 0 else "下", "how": how, "steps": steps})
        i = j
    return out


TREND_STRONG = 2.5   # 24 小時變這麼多＝明顯
TREND_MILD = 1.5
SHORT_STRONG = 4.0   # 沒有 24 小時資料時，6 小時內要變這麼多才算（大於潮汐）
GAP_MIN = 20         # 資料斷超過 20 分鐘、前後差 >0.6 百帕 → 當成換了樓層／地點，接起來


def pressure_series(rows: list[dict]) -> list[tuple[dt.datetime, float]]:
    """扣掉樓層變化後的每分鐘氣壓（從第一筆所在的樓層當基準）。樓層段用 floor_changes；資料斷掉又跳的也接起來。"""
    pts = [(r["dt"], r["hpa"]) for r in rows if r.get("hpa", -1) > 0 and r.get("hpa_n", 0) >= 20]
    fc = floor_changes(rows)
    ev = [(x["t1"], x["delta_hpa"]) for x in fc]
    moving = [(x["t0"], x["t1"]) for x in fc]
    out, off, k, prev = [], 0.0, 0, None
    for t, p in pts:
        while k < len(ev) and ev[k][0] <= t:
            off += ev[k][1]; k += 1
        if any(a < t < b for a, b in moving):
            continue   # 正在搭電梯／爬樓梯的那幾分鐘不是天氣，跳過
        c = p - off
        if prev and (t - prev[0]) > dt.timedelta(minutes=GAP_MIN) and abs(c - prev[1]) > 0.6:
            off += c - prev[1]; c = p - off
        out.append((t, c)); prev = (t, c)
    return out


def _hour_median(series, at: dt.datetime, half_min: int = 30):
    v = [c for t, c in series if abs((t - at).total_seconds()) <= half_min * 60]
    return statistics.median(v) if len(v) >= 10 else None


def pressure_trend(now: dt.datetime | None = None, rows: list[dict] | None = None) -> dict:
    """ """
    now = now or dt.datetime.now()
    if rows is None:
        rows = load_range(now - dt.timedelta(hours=30), now)
    s = pressure_series(rows)
    cur = _hour_median(s, now - dt.timedelta(minutes=15), 15) if s else None
    if cur is None:
        return {"level": "看不出來", "d24": None, "d3": None, "line": "氣壓：最近沒收到錶的氣壓（錶沒戴或資料還沒傳回來）"}
    y = _hour_median(s, now - dt.timedelta(hours=24), 30)
    b3 = _hour_median(s, now - dt.timedelta(hours=3), 30)
    b6 = _hour_median(s, now - dt.timedelta(hours=6), 30)
    d24 = None if y is None else round(cur - y, 1)
    d3 = None if b3 is None else round(cur - b3, 1)
    if d24 is not None:
        d = d24
        level = ("明顯在掉" if d <= -TREND_STRONG else "在掉" if d <= -TREND_MILD else
                 "明顯在升" if d >= TREND_STRONG else "在升" if d >= TREND_MILD else "平穩")
    elif b6 is not None and abs(cur - b6) >= SHORT_STRONG:
        d = round(cur - b6, 1)
        level = "明顯在掉" if d < 0 else "明顯在升"
    else:
        return {"level": "看不出來", "d24": None, "d3": d3, "line": "氣壓：還沒有 24 小時前的資料可比（每天本來就會起落 2～3 百帕，只看幾小時會誤判）"}
    mean = {"明顯在掉": "一路在掉，可能要變天（低壓、鋒面或颱風靠近時會這樣）", "在掉": "有點在掉",
            "平穩": "平穩", "在升": "有點在升", "明顯在升": "明顯在升，像冷空氣／東北季候風來了（常帶來降溫、下雨）"}[level]
    span = "24 小時" if d24 is not None else "6 小時"
    return {"level": level, "d24": d24, "d3": d3,
            "line": f"氣壓：{span}{'+' if d > 0 else ''}{d} 百帕（已扣掉上下樓），{mean}"}


def daily_floors(day: str, rows: list[dict] | None = None, steps_fn=None) -> dict:
    """ """
    if rows is None:
        rows = load(day)
    ev = floor_changes(rows, steps_fn if steps_fn is not None else samsung_steps)
    d = {"次數": len(ev), "上": sum(x["floors"] for x in ev if x["direction"] == "上"),
         "下": sum(x["floors"] for x in ev if x["direction"] == "下"),
         "樓梯": sum(1 for x in ev if x["how"] == "樓梯"), "電梯": sum(1 for x in ev if x["how"] == "電梯"),
         "不知道": sum(1 for x in ev if x["how"] == "不知道")}
    if not ev:
        d["line"] = "樓層：今天沒抓到上下樓（錶沒戴、或一直在同一層）"
        return d
    how = "、".join(x for x in (f"樓梯 {d['樓梯']} 次" if d["樓梯"] else "", f"電梯 {d['電梯']} 次" if d["電梯"] else "",
                                 f"{d['不知道']} 次不知道怎麼上下的（三星步數沒來）" if d["不知道"] else "") if x)
    d["line"] = f"樓層：今天上 {d['上']} 層、下 {d['下']} 層（{how}）"
    return d


def samsung_steps(t0: dt.datetime, t1: dt.datetime):
    """那段時間的三星步數（series/steps.jsonl，5 分鐘一桶）。桶跟區間有重疊就算進去。None = 檔不在。"""
    f = ROOT / "生理資料" / "series" / "steps.jsonl"
    if not f.exists():
        return None
    total = 0
    try:
        for line in f.read_text(encoding="utf-8").splitlines()[-3000:]:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("count", 0) > 800:
                continue
            a = dt.datetime.fromisoformat(r["start_time"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
            b = dt.datetime.fromisoformat(r["end_time"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
            lo, hi = max(a, t0), min(b, t1)
            if hi > lo and b > a:
                total += int(round(int(r.get("count", 0)) * (hi - lo).total_seconds() / (b - a).total_seconds()))
    except Exception:
        return None
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--day")
    a = ap.parse_args()
    now = dt.datetime.now()
    rows = load(a.day) if a.day else load_range(now - dt.timedelta(hours=a.hours), now)
    print(f"═══ 環境（光＋氣壓）：{a.day or f'最近 {a.hours:g} 小時'} ═══")
    if not rows:
        print("  ⚠ 沒有 env 紀錄：錶要 code63 以上、資料要拉到電腦。沒紀錄≠沒事。"); return
    print("  時刻   氣壓 hPa（最小–中位–最大，筆數）   亮度 lux（最後／最小～最大，事件）")
    last_print = None
    for r in rows:
        key = r["dt"].strftime("%H:%M")
        if last_print and (r["dt"] - last_print) < dt.timedelta(minutes=5) and r is not rows[-1]:
            continue
        last_print = r["dt"]
        n_warn = "" if r.get("hpa_n", 0) >= 40 else "  ⚠ 氣壓筆數少（批次沒生效？）"
        print(f"  {key}  {r.get('hpa_min', -1):7.2f}–{r.get('hpa', -1):7.2f}–{r.get('hpa_max', -1):7.2f} ({r.get('hpa_n', 0):>2})   "
              f"{r.get('lux', -1):7.1f}／{r.get('lux_min', -1):.1f}～{r.get('lux_max', -1):.1f} ({r.get('lux_n', 0)}){n_warn}")
    print("\n  關燈了：")
    offs = lights_off_at(rows)
    for e in offs:
        print(f"    {e['t']:%H:%M}  亮度 {e['lux_before']:.0f} → {e['lux_after']:.0f}")
    if not offs:
        print("    （沒有）")
    print("  樓層變了：")
    fl = floor_changes(rows, samsung_steps)
    for e in fl:
        print(f"    {e['t0']:%H:%M}–{e['t1']:%H:%M}  氣壓 {e['delta_hpa']:+.2f} → {e['direction']}{e['floors']} 層，{e['how']}（步數 {e['steps']}）")
    if not fl:
        print("    （沒有）")
    print("\n  一層樓 ≈ 0.36 hPa；幾分鐘內變 0.3 以上才算樓層，幾小時慢慢變是天氣。亮度 <3 lux 連續 3 分鐘＝暗。")


if __name__ == "__main__":
    main()
