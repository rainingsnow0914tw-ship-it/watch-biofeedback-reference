#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用法：
  python 身體狀態.py tired            # 現在累不累"""
from __future__ import annotations
import datetime as dt, json, statistics, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WATCH = ROOT / "生理資料" / "手錶橋接"
sys.path.insert(0, str(HERE))

BASE_DAYS = 14
BASE_MIN_WIN = 30        # 基準至少 30 個可信窗
BASE_MIN_DAYS = 2        # 而且至少來自 2 天
NOW_HOURS = 6
NOW_MIN_WIN = 3
TIRED_RATIO = 0.80       # 比基準低 20% 以上＝累
GOOD_RATIO = 1.10        # 比基準高 10% 以上＝不錯

_CACHE: dict = {}


def day_windows(day: str) -> list[dict]:
    """那天錶檔裡可信的 HRV 窗 → [{t, rmssd, raw, conf}]。依檔案大小＋修改時間快取（一天約 0.3 秒）。"""
    f = WATCH / f"{day}.jsonl"
    if not f.exists():
        return []
    st = f.stat()
    k = (day, st.st_size, st.st_mtime)
    if k in _CACHE:
        return _CACHE[k]
    from ibi_windows import windows_from_file
    from hrv import analyse
    out = []
    try:
        for w in windows_from_file(f):
            r = analyse(w)
            if r and r.trusted:
                out.append({"t": dt.datetime.fromtimestamp(w.start_ms / 1000), "rmssd": r.rmssd_ms,
                            "raw": bool(getattr(w, "raw", False)), "conf": r.confidence})
    except Exception:
        out = []
    _CACHE[k] = out
    return out


def _windows(since: dt.datetime, until: dt.datetime) -> list[dict]:
    out = []
    d = since.date()
    while d <= until.date():
        out += [w for w in day_windows(d.isoformat()) if since <= w["t"] < until]
        d += dt.timedelta(days=1)
    return out


def baseline(now: dt.datetime, raw: bool | None = None, days: int = BASE_DAYS) -> dict:
    """ """
    ws = _windows(dt.datetime.combine(now.date() - dt.timedelta(days=days), dt.time()), dt.datetime.combine(now.date(), dt.time()))
    if raw is not None:
        ws = [w for w in ws if w["raw"] == raw]
    by_day = {}
    for w in ws:
        by_day.setdefault(w["t"].date(), []).append(w["rmssd"])
    n, nd = len(ws), len(by_day)
    if n < BASE_MIN_WIN or nd < BASE_MIN_DAYS:
        return {"ok": False, "n": n, "days": nd, "why": f"基準還在累積（這支錶近 {days} 天只有 {n} 個可信靜止窗、{nd} 天；要 ≥{BASE_MIN_WIN} 窗、≥{BASE_MIN_DAYS} 天）"}
    top = max(len(v) for v in by_day.values())
    return {"ok": True, "median": statistics.median(w["rmssd"] for w in ws), "n": n, "days": nd,
            "lopsided": top > 0.8 * n}   # 一天佔八成以上 → 基準偏，可信度降一級


def tired(now: dt.datetime | None = None, hours: int = NOW_HOURS) -> dict:
    """現在累不累：最近 hours 小時的可信窗中位 ÷ 基準 → 累／普通／不錯＋可信度。看不到就 level=None。"""
    now = now or dt.datetime.now()
    recent = _windows(now - dt.timedelta(hours=hours), now)
    if len(recent) < NOW_MIN_WIN:
        return {"level": None, "line": f"HRV 看不到：最近 {hours} 小時只有 {len(recent)} 個可信的靜止窗（要 ≥{NOW_MIN_WIN}；錶要戴著、手靜下來 5 分鐘才量得到）"}
    raw = recent[-1]["raw"]
    base = baseline(now, raw)
    cur = statistics.median(w["rmssd"] for w in recent)
    if not base["ok"]:
        return {"level": None, "rmssd": round(cur, 1), "line": f"HRV {cur:.1f} ms，但{base['why']}，先不判斷累不累"}
    ratio = cur / base["median"]
    level = "累" if ratio < TIRED_RATIO else "不錯" if ratio > GOOD_RATIO else "普通"
    conf = "高" if len(recent) >= 6 and base["days"] >= 4 else "中" if len(recent) >= NOW_MIN_WIN else "低"
    if base["lopsided"] and conf != "低":
        conf = {"高": "中", "中": "低"}[conf]
    pct = round((ratio - 1) * 100)
    return {"level": level, "conf": conf, "rmssd": round(cur, 1), "base": round(base["median"], 1), "pct": pct, "n": len(recent),
            "line": f"HRV {cur:.1f} ms，比她平常（{base['median']:.1f}）{'高' if pct >= 0 else '低'} {abs(pct)}% → {level}"
                    f"（可信度 {conf}，{len(recent)} 個靜止窗；這是跟她自己比，不是醫療判斷）"}


# ─────────────────────────── 身體電量 ───────────────────────────
def sleep_sessions(day: str) -> list[dict]:
    """ """
    try:
        import importlib
        s = importlib.import_module("感知哨兵") if "感知哨兵" not in sys.modules else sys.modules["感知哨兵"]
        ses = s.sleep_sessions()
    except Exception:
        return []
    d0 = dt.datetime.fromisoformat(day)
    return [x for x in ses if d0 <= x["end"] <= d0 + dt.timedelta(hours=20)]


def night_span(day: str, ses: list[dict]) -> tuple[dt.datetime, dt.datetime]:
    """ """
    if ses:
        return min(x["start"] for x in ses), max(x["end"] for x in ses)
    d0 = dt.datetime.fromisoformat(day)
    return d0 + dt.timedelta(hours=5), d0 + dt.timedelta(hours=15, minutes=40)


def _hr_minutes(since: dt.datetime, until: dt.datetime) -> list[float]:
    """錶的每分鐘心率（hr／hr_ibi status=1）。"""
    per = {}
    d = since.date()
    while d <= until.date():
        f = WATCH / f"{d.isoformat()}.jsonl"
        if f.exists():
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"hr' not in line:
                        continue
                    try:
                        r = json.loads(line)
                        t = dt.datetime.fromisoformat(str(r["t"])[:19])
                    except Exception:
                        continue
                    if not (since <= t < until):
                        continue
                    v = r.get("bpm") if r.get("type") == "hr" else (r.get("hr") if r.get("type") == "hr_ibi" and r.get("status") == 1 else None)
                    if v:
                        per.setdefault(t.replace(second=0, microsecond=0), []).append(v)
        d += dt.timedelta(days=1)
    return [statistics.median(per[k]) for k in sorted(per)]


def resting_hr(since: dt.datetime, until: dt.datetime) -> float | None:
    """夜裡的靜息心率＝連續 10 分鐘心率中位數裡最低的那段（至少要 30 分鐘心率資料）。"""
    m = _hr_minutes(since, until)
    if len(m) < 30:
        return None
    return min(statistics.median(m[i:i + 10]) for i in range(0, len(m) - 9))


def _clamp(x: float) -> float:
    return max(-1.0, min(1.0, x))


def body_battery(day: str) -> dict:
    """電量 = 50 + 20×睡眠分 + 20×HRV分 + 10×靜息心率分（四捨五入、夾在 0～100）
         睡眠分   = clamp((睡著小時 − 7) ÷ 2)                     7 小時＝0，9 小時＝+1，5 小時＝−1
         靜息心率分 = clamp((前幾晚靜息心率中位 − 今晚) ÷ 5)       比平常低 5 下＝+1（越低越有休息到）"""
    ses = sleep_sessions(day)
    a, b = night_span(day, ses)
    parts, why, seen = {}, [], 0
    if ses:
        hrs = sum(x["asleep_seconds"] for x in ses) / 3600
        parts["睡眠"] = _clamp((hrs - 7) / 2); seen += 1
        why.append(f"三星記到睡 {hrs:.1f} 小時")
    else:
        parts["睡眠"] = 0.0; why.append("三星沒記到昨晚的睡眠")
    nw = _windows(a, b)
    if len(nw) >= NOW_MIN_WIN:
        cur = statistics.median(w["rmssd"] for w in nw)
        base = baseline(dt.datetime.fromisoformat(day), nw[-1]["raw"])
        if base["ok"]:
            r = cur / base["median"]
            parts["HRV"] = _clamp((r - 1) / 0.25); seen += 1
            why.append(f"夜裡 HRV 比平常{'高' if r >= 1 else '低'} {abs(round((r - 1) * 100))}%")
        else:
            parts["HRV"] = 0.0; why.append("HRV 基準還在累積")
    else:
        parts["HRV"] = 0.0; why.append("夜裡 HRV 沒量到")
    rhr = resting_hr(a, b)
    prev = []
    for i in range(1, 8):
        d = (dt.date.fromisoformat(day) - dt.timedelta(days=i)).isoformat()
        pa, pb = night_span(d, sleep_sessions(d))
        v = resting_hr(pa, pb)
        if v:
            prev.append(v)
    if rhr and len(prev) >= 2:
        base_rhr = statistics.median(prev)
        parts["靜息心率"] = _clamp((base_rhr - rhr) / 5); seen += 1
        diff = round(rhr - base_rhr)
        why.append(f"靜息心率 {rhr:.0f}（平常 {base_rhr:.0f}，{'高' if diff > 0 else '低'} {abs(diff)}）" if diff else f"靜息心率 {rhr:.0f}，跟平常一樣")
    else:
        parts["靜息心率"] = 0.0; why.append("靜息心率沒量到" if not rhr else "靜息心率還沒有前幾晚可比")
    if seen < 2:
        return {"ok": False, "day": day, "parts": parts, "why": why,
                "line": "🔋 身體電量：看不到（" + "、".join(why) + "）"}
    score = round(max(0, min(100, 50 + 20 * parts["睡眠"] + 20 * parts["HRV"] + 10 * parts["靜息心率"])))
    word = "滿滿" if score >= 75 else "還可以" if score >= 50 else "有點低" if score >= 30 else "很低"
    return {"ok": True, "day": day, "score": score, "parts": {k: round(v, 2) for k, v in parts.items()}, "why": why,
            "line": f"🔋 身體電量 {score}（{word}）：" + "、".join(why)}


if __name__ == "__main__":
    a = sys.argv[1:] or ["tired"]
    if a[0] == "tired":
        print(tired()["line"])
    elif a[0] == "battery":
        d = a[1] if len(a) > 1 else dt.date.today().isoformat()
        print(json.dumps(body_battery(d), ensure_ascii=False, default=str))
