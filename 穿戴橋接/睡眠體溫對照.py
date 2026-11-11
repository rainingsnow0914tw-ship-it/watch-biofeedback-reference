"""三星健康**沒有**把皮膚溫度寫進 Health Connect（它的 HC 寫入權限清單裡根本沒有
    WRITE_SKIN_TEMPERATURE），所以 HC 線拿不到；用的是同一顆錶背感測器的原始讀數。
  - 睡眠階段、醒來：三星健康 → Health Connect → hc_puller（series/sleep.jsonl）。


用法：python 睡眠體溫對照.py            → 重算全部、寫 生理資料/睡眠體溫對照.md 與 .jsonl
      python 睡眠體溫對照.py --days 14  → 只看最近 14 晚"""
from __future__ import annotations

import argparse
import json
import statistics as st
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "生理資料"
TZ = timezone(timedelta(hours=8))
AWAKE = {"1", "7"}                           # HC：1=清醒、7=躺著醒著
MERGE_GAP = timedelta(minutes=90)            # 兩段睡眠隔不到 90 分鐘算同一覺
MAIN_MIN = timedelta(hours=3)                # 合起來 ≥3 小時才算主睡眠
WAKE_MIN = timedelta(minutes=5)


def _z(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)


def _local(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def _jsonl(p: Path):
    if not p.exists():
        return
    for line in p.open(encoding="utf-8", errors="replace"):
        try:
            yield json.loads(line)
        except Exception:
            continue


# ── 睡眠：去重 → 合併成一覺一覺 ──────────────────────────────────────
def sleep_blocks() -> list[dict]:
    uniq: dict[str, dict] = {}
    for r in _jsonl(DATA / "series" / "sleep.jsonl"):
        if r.get("stages") and r.get("session_end_time"):
            uniq[r["session_end_time"]] = r          # 同一段後到的覆蓋（階段會補齊）
    sess = []
    for r in uniq.values():
        stg = [(_z(x["start_time"]), _z(x["end_time"]), str(x["stage"])) for x in r["stages"]]
        stg.sort()
        sess.append({"start": stg[0][0], "end": _z(r["session_end_time"]), "stages": stg})
    sess.sort(key=lambda s: s["start"])
    blocks: list[dict] = []
    for s in sess:
        if blocks and s["start"] - blocks[-1]["end"] < MERGE_GAP:
            b = blocks[-1]
            b["end"] = max(b["end"], s["end"]); b["stages"] += s["stages"]; b["pieces"] += 1
        else:
            blocks.append({**s, "pieces": 1})
    return blocks          # 全部（含小睡）；主睡眠在 build() 裡篩


def wakes(stages) -> tuple[int, int]:
    """ """
    runs, cur = [], None
    for a, b, k in sorted(stages):
        if k in AWAKE:
            if cur and a - cur[1] <= timedelta(minutes=1):
                cur[1] = max(cur[1], b)
            else:
                cur = [a, b]; runs.append(cur)
        else:
            cur = None
    if not stages:
        return 0, 0
    first, last = min(s[0] for s in stages), max(s[1] for s in stages)
    inner = [r for r in runs if r[0] > first + timedelta(minutes=1) and r[1] < last - timedelta(minutes=1)]
    n = sum(1 for r in inner if r[1] - r[0] >= WAKE_MIN)
    mins = round(sum((r[1] - r[0]).total_seconds() for r in inner) / 60)
    return n, mins


# ── 皮膚溫度 ─────────────────────────────────────────────────────────
_skin_cache: dict[str, list] = {}


def skin_between(a: datetime, b: datetime) -> list[tuple[datetime, float]]:
    out = []
    d = a.date()
    while d <= b.date():
        key = d.isoformat()
        if key not in _skin_cache:
            rows = []
            for r in _jsonl(DATA / "手錶橋接" / f"{key}.jsonl"):
                if r.get("type") != "skin_temp" or r.get("charging"):
                    continue
                v = r.get("skin")
                if isinstance(v, (int, float)) and v >= 30:      # <30 不是戴在手腕上（摘要層的 SKIN_FLOOR 同理）
                    try:
                        rows.append((_local(r["t"]), float(v)))
                    except Exception:
                        pass
            _skin_cache[key] = rows
        out += [x for x in _skin_cache[key] if a <= x[0] <= b]
        d += timedelta(days=1)
    return sorted(out)


def ten_min_medians(pts):
    buckets: dict[datetime, list[float]] = {}
    for t, v in pts:
        buckets.setdefault(t.replace(minute=t.minute // 10 * 10, second=0, microsecond=0), []).append(v)
    return [(k, st.median(v)) for k, v in sorted(buckets.items())]


def steps_between(a: datetime, b: datetime) -> int:
    seen: dict[tuple, int] = {}
    for r in _jsonl(DATA / "series" / "steps.jsonl"):
        try:
            s = _z(r["start_time"])
        except Exception:
            continue
        if a <= s < b:
            seen[(r["start_time"], r.get("end_time"))] = int(r.get("count") or 0)
    return sum(seen.values())


def pc_minutes(a: datetime, b: datetime) -> int | None:
    n, any_file = 0, False
    d = a.date()
    while d <= b.date():
        p = DATA / "電腦活動" / f"{d.isoformat()}.jsonl"
        if p.exists():
            any_file = True
            for r in _jsonl(p):
                try:
                    t = _local(r["t"])
                except Exception:
                    continue
                if a <= t < b and r.get("active") and not r.get("locked"):
                    n += 1
        d += timedelta(days=1)
    return n if any_file else None


# ── 主程式 ───────────────────────────────────────────────────────────
def build(days: int | None) -> list[dict]:
    every = sleep_blocks()
    blocks = [b for b in every if b["end"] - b["start"] >= MAIN_MIN]
    rows = []
    for b in blocks:
        pts = skin_between(b["start"], b["end"])
        tm = ten_min_medians(pts)
        need = (b["end"] - b["start"]).total_seconds() / 600 * 0.5   # 至少一半的 10 分鐘格有資料
        row = {
            "醒來日": b["end"].date().isoformat(),
            "睡": b["start"].strftime("%m-%d %H:%M"),
            "起": b["end"].strftime("%m-%d %H:%M"),
            "睡多久h": round((b["end"] - b["start"]).total_seconds() / 3600, 1),
            "段數": b["pieces"],
        }
        row["醒來次數"], row["清醒分鐘"] = wakes(b["stages"])
        if len(tm) >= max(3, need):
            vals = [v for _, v in tm]
            head = [v for t, v in tm if t < b["start"] + timedelta(minutes=40)] or vals[:3]
            jumps = sum(1 for x, y in zip(vals, vals[1:]) if abs(y - x) >= 0.5)
            low_t, low_v = min(tm, key=lambda x: x[1])
            row.update({
                "皮膚_入睡": round(st.median(head), 2),
                "皮膚_整晚中位": round(st.median(vals), 2),
                "皮膚_最低": round(low_v, 2),
                "最低在": low_t.strftime("%H:%M"),
                "皮膚_最高": round(max(vals), 2),
                "下降": round(st.median(head) - low_v, 2),
                "忽冷忽熱次數": jumps,                 # 相鄰 10 分鐘中位差 ≥0.5°C 的次數
                "皮膚覆蓋": f"{len(tm)}/{int(need * 2)}",
            })
        else:
            row["皮膚_整晚中位"] = None
            row["皮膚覆蓋"] = f"{len(tm)}/{int(need * 2)}"

        prev = [x["end"] for x in every if x["end"] <= b["start"] and x["end"] - x["start"] >= timedelta(minutes=30)]
        prev_end = max(prev) if prev else None
        row["睡前清醒h"] = round((b["start"] - prev_end).total_seconds() / 3600, 1) if prev_end else None
        win = b["start"] - timedelta(hours=24)
        row["睡前24h步數"] = steps_between(win, b["start"])
        row["睡前24h電腦分鐘"] = pc_minutes(win, b["start"])
        rows.append(row)


    med = [r["皮膚_整晚中位"] for r in rows if r.get("皮膚_整晚中位") is not None]
    drp = [r["下降"] for r in rows if r.get("下降") is not None]
    base_m = st.median(med) if med else None
    base_d = st.median(drp) if drp else None
    for r in rows:
        flags = []
        if base_m is not None and r.get("皮膚_整晚中位") is not None and r["皮膚_整晚中位"] <= base_m - 0.5:
            flags.append("整晚偏冷")
        if base_d is not None and r.get("下降") is not None and r["下降"] >= base_d + 0.8:
            flags.append("掉得比平常多")
        if r.get("忽冷忽熱次數", 0) >= 4:
            flags.append("起伏多")
        r["跟自己比"] = "、".join(flags)
    if days:
        cut = (datetime.now(TZ) - timedelta(days=days)).date().isoformat()
        rows = [r for r in rows if r["醒來日"] >= cut]
    return rows, base_m, base_d


def write(rows, base_m, base_d) -> Path:
    (DATA / "睡眠體溫對照.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    cols = ["睡", "起", "睡多久h", "醒來次數", "清醒分鐘", "皮膚_入睡", "皮膚_整晚中位", "皮膚_最低", "最低在",
            "下降", "忽冷忽熱次數", "睡前清醒h", "睡前24h步數", "睡前24h電腦分鐘", "跟自己比"]
    md = [f"# 睡眠皮膚溫度 × 睡前累不累（自動產生 {datetime.now(TZ):%Y-%m-%d %H:%M}）", "",
          "> 皮膚溫度來自手錶阿寶 App（錶背感測器），睡眠階段來自三星健康。**不是體溫、不是醫療判斷**，只跟妳自己的其他晚上比。",
          f"> 她自己的基準：整晚中位 {base_m if base_m is None else round(base_m, 2)}°C、"
          f"入睡→最低 平常掉 {base_d if base_d is None else round(base_d, 2)}°C。",
          "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        md.append("| " + " | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + " |")
    p = DATA / "睡眠體溫對照.md"
    p.write_text("\n".join(md) + "\n", encoding="utf-8")
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int)
    a = ap.parse_args()
    rows, bm, bd = build(a.days)
    p = write(rows, bm, bd)
    print(f"{len(rows)} 晚 → {p}")
