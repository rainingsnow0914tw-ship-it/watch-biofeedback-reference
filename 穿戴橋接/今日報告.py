#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""⚠ 這不是美觀問題。**看不完的報告等於沒有報告** ——

## 排版原則

```
1. 結論在最上面        —— 一句話，不用捲
4. 有對照才有意義       —— 數字後面附上前幾天，不然 5.0% 是好是壞看不出來
```

  三千筆算出來的，也可能是三筆 —— 兩個長得一樣，意思完全相反。"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path
from 覆蓋率 import classify, headline, window_coverage

HERE = Path(__file__).resolve().parent
BIO = HERE.parent / "生理資料" / "手錶橋接"
SUM = HERE.parent / "生理資料" / "摘要"
VOICE = HERE.parent / "生理資料" / "語音筆記"
BUZZ = HERE.parent / "生理資料" / "震動史"
VERDICT = HERE.parent / "生理資料" / "手錶橋接" / "判斷日誌.jsonl"

TAG_NAME = {"unwell": "不舒服", "buzz": "震了", "wake": "起床", "sleep": "睡覺",
            "exercise": "運動", "medicine": "吃藥", "period": "生理期", "other": "其他",


            "off_wrist": "脫錶"}


def _load(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def T(r: dict) -> datetime:
    return datetime.fromisoformat(r["t"])


def _restless(recs: list[dict], a: datetime, b: datetime) -> tuple[float, int] | None:
    """睡眠窗內的翻身比例。回（比例, 可信分鐘）。

      結果每一天都算出 0.0%，因為要測的東西被前一步濾掉了。"""
    win = [r for r in recs if r.get("type") == "motion" and a <= T(r) <= b
           and str(r.get("on_body")) in ("on", "inferred_on") and r.get("trust") is True]
    acts = [r.get("activity") or 0 for r in win]
    if len(acts) < 100:
        return None
    base = statistics.median(acts)
    return sum(1 for x in acts if x > base * 5) / len(acts), len(acts)


def _hist(day: str, n: int = 4) -> list[tuple[str, dict]]:
    """前幾天的日摘要，給對照用。"""
    out = []
    d0 = datetime.fromisoformat(day)
    for i in range(1, n + 1):
        d = (d0 - timedelta(days=i)).strftime("%Y-%m-%d")
        rows = _load(SUM / f"{d}.jsonl")
        ds = [r for r in rows if r.get("layer") == "day"]
        if ds and ds[0].get("cover", {}).get("quality") == "sufficient":
            out.append((d, ds[0]))
    return out


def format_hrv_evidence(windows: list, coverage: dict) -> str:
    """Report observed five-minute windows, never a made-up whole-day HRV."""
    if not windows:
        return "  HRV：沒有可用的連續 5 分鐘 IBI 窗口；資料不足，不給全日數字"
    minutes = sum(w.duration_s for w in windows) / 60
    hours = len({datetime.fromtimestamp(w.start_ms / 1000).hour for w in windows})
    base = f"  HRV：{len(windows)} 個可用 5 分鐘窗口（累計 {minutes:.0f} 分鐘，分布 {hours} 個時段）"
    if coverage["quality"] != "sufficient":
        return base + "；全日覆蓋不足，只代表這些片段，跨日趨勢不判讀"
    return base + "；尚無相同時段與情境的成熟基線，不給全日 HRV 或跨日趨勢"


def hrv_evidence_line(day: str, coverage: dict, bio_dir: Path | None = None) -> str:
    path = (bio_dir or BIO) / f"{day}.jsonl"
    if not path.exists():
        return "  HRV：原始資料未到，不能判讀"
    try:
        from ibi_windows import windows_from_file
        windows = [w for w in windows_from_file(path)
                   if datetime.fromtimestamp(w.start_ms / 1000).strftime("%Y-%m-%d") == day]
    except Exception as exc:
        return f"  HRV：窗口檢查失敗（{type(exc).__name__}），不能判讀"
    return format_hrv_evidence(windows, coverage)


ULTRA2_SINCE = "2026-11-03"
""" """
BATT_MAX_GAP_S = 180
"""相鄰兩筆 motion（每分鐘一筆）隔超過 3 分鐘就不接 —— 中間可能關機、沒戴、資料沒到，算進去會把耗電稀釋。"""
STILL_RATIO = 0.9


def battery_drain(path: Path) -> dict | None:
    """ """
    if not path.exists():
        return None
    rows = []
    for r in _load(path):
        if r.get("type") == "motion" and isinstance(r.get("batt"), (int, float)) and r.get("t"):
            try:
                rows.append((datetime.fromisoformat(r["t"]), r))
            except ValueError:
                continue
    rows.sort(key=lambda x: x[0])
    h = drop = sh = sdrop = 0.0
    for (t0, a), (t1, b) in zip(rows, rows[1:]):
        gap = (t1 - t0).total_seconds()
        if gap <= 0 or gap > BATT_MAX_GAP_S or a.get("charging") or b.get("charging"):
            continue
        d = float(a["batt"]) - float(b["batt"])
        if d < 0:       # 沒充電卻變多：電量計校正跳一下，不算
            continue
        h += gap / 3600; drop += d

        if str(a.get("on_body")) in ("on", "inferred_on") and (a.get("still_ratio") or 0) >= STILL_RATIO:
            sh += gap / 3600; sdrop += d
    return {"hours": h, "drop": drop, "rate": drop / h if h >= 1 else None,
            "still_hours": sh, "still_rate": sdrop / sh if sh >= 0.5 else None}


def battery_line(day: str, bio_dir: Path | None = None) -> str:
    base_dir = bio_dir or BIO
    today = battery_drain(base_dir / f"{day}.jsonl")
    if not today or today["rate"] is None:
        hrs = today["hours"] if today else 0
        return f"  錶耗電  不充電資料只有 {hrs:.1f} 小時，不夠算（要 ≥1 小時）"
    s = f"  錶耗電  不充電 {today['hours']:.1f} 小時：整體每小時 −{today['rate']:.1f}%"
    if today["still_rate"] is not None:
        s += f"（戴著靜止 −{today['still_rate']:.1f}%，{today['still_hours']:.1f} 小時）"
    else:
        s += f"（戴著靜止只有 {today['still_hours']:.1f} 小時，不算；佩戴狀態不明的時段不猜）"
    d0 = datetime.strptime(day, "%Y-%m-%d")
    prev = []
    for i in range(1, 8):
        dd = (d0 - timedelta(days=i)).strftime("%Y-%m-%d")
        if dd < ULTRA2_SINCE:
            break
        x = battery_drain(base_dir / f"{dd}.jsonl")
        if x and x["rate"] is not None and x["hours"] >= 2:
            prev.append(x["rate"])
    if prev:
        med = statistics.median(prev)
        diff = today["rate"] - med
        s += f"；前 7 天中位 −{med:.1f}%（{len(prev)} 天，新錶起算）{'多' if diff >= 0 else '少'} {abs(diff):.1f}"
    else:
        s += "；前 7 天沒有可比的日子（新錶起算、每天要 ≥2 小時不充電）"
    s += f"　→ 滿電約撐 {100 / today['rate']:.0f} 小時"
    return s


def report(day: str) -> str:
    recs = _load(BIO / f"{day}.jsonl")
    summary_path = SUM / f"{day}.jsonl"
    raw_path = BIO / f"{day}.jsonl"
    if recs and (not summary_path.exists() or raw_path.stat().st_mtime > summary_path.stat().st_mtime):
        # A direct report must not quietly use last month's summary.
        import 摘要層
        hours, dsum = 摘要層.build(day)
    else:
        srows = _load(summary_path)
        hours = [r for r in srows if r.get("layer") == "hour"]
        dsum = next((r for r in srows if r.get("layer") == "day"), None)
    marks = [r for r in recs if r.get("type") == "mark"]
    voice = _load(VOICE / f"{day}.jsonl")
    L = []
    coverage = classify(recs, day)

    def line(s: str = "") -> None:
        L.append(s)

    line(headline(coverage, day))
    line(f"═══ {day} ═══")
    if not recs:
        line("  這一天一筆資料都沒有 —— **不是沒事，是不知道**")
        return "\n".join(L)


    #


    #   中間全部當成睡覺 —— **完全不檢查連不連續**。


    #


    #   校準完的東西沒有上線，等於沒校準。
    a = b = None
    how = ""
    sleep_windows: list[tuple[datetime, datetime]] = []
    實睡秒 = None   # 三星有給實睡時，標題要用它，不能用跨度
    sleep_snapshot_scope = False
    sleep_snapshot_notice = "⚠ 三星睡眠快照未取得；空白不代表沒睡"

    # ① 三星自己判的（最可信；由 手機資料接回來.py 存下來）
    try:
        sleep_path = BIO.parent / "睡眠.json"
        _s = json.loads(sleep_path.read_text(encoding="utf-8"))
        checked_at = datetime.fromtimestamp(sleep_path.stat().st_mtime)
        query_days = int(_s.get("days") or 0)
        queried_from = (checked_at - timedelta(days=query_days)).date()
        sleep_snapshot_scope = (bool(_s.get("ok")) and query_days > 0
                                and queried_from <= datetime.fromisoformat(day).date() <= checked_at.date()
                                and (datetime.now() - checked_at).total_seconds() <= 24 * 3600)
        sleep_snapshot_notice = (f"三星睡眠快照 {checked_at:%m-%d %H:%M} 更新，查近 {query_days} 天"
                                 if sleep_snapshot_scope else
                                 f"⚠ 三星睡眠快照 {checked_at:%m-%d %H:%M}，已過期或未涵蓋這一天；空白不是沒睡")
        _segs = []
        for _x in _s.get("sessions", []):
            _st = datetime.fromisoformat(str(_x.get("start", "")).split("[")[0]).replace(tzinfo=None)
            _en = datetime.fromisoformat(str(_x.get("end", "")).split("[")[0]).replace(tzinfo=None)
            if _st.strftime("%Y-%m-%d") == day or _en.strftime("%Y-%m-%d") == day:
                _segs.append((_st, _en, int(_x.get("asleep_seconds", 0))))
        if _segs:
            _segs.sort()
            a, b = _segs[0][0], _segs[-1][1]
            sleep_windows = [(x[0], x[1]) for x in _segs]
            實睡秒 = sum(x[2] for x in _segs)
            how = f"三星判的，{len(_segs)} 段"
    except Exception as exc:
        sleep_snapshot_notice = f"⚠ 三星睡眠快照讀取失敗（{type(exc).__name__}）；不能判斷有沒有睡"


    if a is None:
        sleep_mk = next((m for m in marks if m.get("tag") == "sleep"), None)
        wake_mk = next((m for m in reversed(marks) if m.get("tag") == "wake"), None)
        if sleep_mk and wake_mk and T(wake_mk) > T(sleep_mk):
            a, b, how = T(sleep_mk), T(wake_mk), "妳自己按的"
            sleep_windows = [(a, b)]

    # ③ 心率推 —— **必須是連續的一段**
    if a is None and sleep_snapshot_scope and coverage["quality"] == "sufficient":
        _low = [h for h in hours if (h.get("hr") or {}).get("p50", 999) < 72
                and h["cover"]["worn_minutes"] > 30]
        _best, _cur = [], []
        for _h in _low:
            _t = datetime.fromisoformat(_h["t"])
            if _cur and (_t - datetime.fromisoformat(_cur[-1]["t"])).total_seconds() > 3900:
                if len(_cur) > len(_best):
                    _best = _cur
                _cur = []
            _cur.append(_h)
        if len(_cur) > len(_best):
            _best = _cur
        if _best:
            a = datetime.fromisoformat(_best[0]["t"])
            b = datetime.fromisoformat(_best[-1]["t"]) + timedelta(hours=1)
            sleep_windows = [(a, b)]
            # ⚠ 講清楚這是猜的，而且是最弱的一種猜。
            how = "心率推的（連續段；三星沒判到、妳也沒按）"

    # ── 一句話結論 ──────────────────────────────────────────
    verdict = (dsum or {}).get("verdict", "?")
    head = {"ok": "有資料的時段沒有踩到固定門檻", "partial": "今日尚未結束，這是暫報", "insufficient": "資料不足，不能作全日判斷", "flag": "⚠ 有小時踩到門檻",
            "no_data": "這一天沒有資料"}.get(verdict, verdict)
    if sleep_windows:
        covered = total = 0
        for st, en in sleep_windows:
            v, n = window_coverage(BIO, st, en)
            covered += v
            total += n
        segment_label = ("三星睡眠段" if 實睡秒 is not None else
                         "心率推估段" if how.startswith("心率推的") else "睡眠標記區間")
        L[0] += f"；{segment_label}覆蓋 {covered / 60:.1f}/{total / 60:.1f} 小時"


    #   **睡兩段中間醒著十幾個小時，不叫睡了十九小時。**
    if a and b:
        _時 = (實睡秒 / 3600) if 實睡秒 is not None else (b - a).total_seconds() / 3600
        if 實睡秒 is not None:
            _尾 = f"三星紀錄實睡 {_時:.1f} 小時（{how}；請連同覆蓋率看）。"
        elif how.startswith("心率推的"):
            _尾 = f"心率推估連續低心率段 {_時:.1f} 小時，尚不能當作實睡。"
        else:
            _尾 = f"標記的睡眠區間 {_時:.1f} 小時，尚不能當作實睡。"
    else:
        _尾 = ("三星本次沒有記到可確認的睡眠段；不能推論沒有睡。"
              if sleep_snapshot_scope else "三星睡眠資料尚未覆蓋這一天；有沒有睡不知道。")
    line(f"  {head}。" + _尾)
    line()

    # ── 身體 ────────────────────────────────────────────────
    line("【身體】")
    if coverage["quality"] == "insufficient":
        line("  ⚠ 覆蓋不足：睡眠推估、HRV 與跨日趨勢暫不判讀；以下只列實際收到的局部資料。")
    line(hrv_evidence_line(day, coverage))
    try:
        import 環境
        line("  🏢 " + 環境.daily_floors(day)["line"])
        end = min(datetime.fromisoformat(day) + timedelta(hours=23, minutes=59), datetime.now())
        line("  🌦 " + 環境.pressure_trend(end)["line"] + f"（到 {end:%H:%M}）")
        heads = []
        ev_file = HERE.parent / "生理資料" / "哨兵事件.jsonl"
        for l in ev_file.read_text(encoding="utf-8").splitlines():
            if '"頭不舒服"' in l and l.startswith('{"t": "' + day):
                r = json.loads(l); heads.append(f"{r['t'][11:16]}「{r['heard'][:20]}」")
        if heads:
            line("  🤕 她說頭不舒服：" + "、".join(heads))
    except Exception as e:
        line(f"  🏢🌦 樓層／氣壓：算不出來（{e}）")
    try:
        import 身體狀態
        line("  " + 身體狀態.body_battery(day)["line"])
    except Exception as e:
        line(f"  🔋 身體電量：算不出來（{e}）")
    if a and b:
        r = _restless(recs, a, b) if len(sleep_windows) == 1 and coverage["quality"] == "sufficient" else None
        hrs = [h for h in hours if "hr" in h and any(
            st <= datetime.fromisoformat(h["t"]) < en for st, en in sleep_windows)]
        if hrs:
            p = sorted(h["hr"]["p50"] for h in hrs)
            line(f"  睡覺  {a:%H:%M} → {b:%H:%M}　心率中位 {p[len(p)//2]}　"
                 f"最低 {min(h['hr']['lo'] for h in hrs)}")
        if r:

            line(f"  翻身比例 {r[0]:.1%}（{r[1]} 分鐘可信）"
                 f"　　（沒有通用標準：請拿你自己不舒服的夜晚和平常的夜晚互相對照）")
    awake = [h for h in hours if "hr" in h and h["verdict"] == "ok"
             and not any(st <= datetime.fromisoformat(h["t"]) < en for st, en in sleep_windows)]
    if awake and coverage["quality"] == "sufficient":
        p = sorted(h["hr"]["p50"] for h in awake)
        line(f"  醒著  {len(awake)} 小時　心率中位 {p[len(p)//2]}")
    if dsum:
        w = dsum["wear"]
        warn = "　⚠ 壓瘡耐受約 2 小時" if w["longest_run_h"] > 4 else ""
        gap_note = ("　⚠ 覆蓋不足，連續段可能含未知空窗"
                    if coverage["quality"] != "sufficient" else "")
        line(f"  戴著  {dsum['cover']['worn_minutes']/60:.1f} 小時　"
             f"最長連續 {w['longest_run_h']} 小時{warn}{gap_note}")
        if coverage["quality"] == "sufficient":
            hist = _hist(day)
            if hist:
                line("        前幾個可比有效日：" + "、".join(
                    f"{d[5:]} {x['wear']['longest_run_h']}h" for d, x in hist))
            else:
                line("        前 4 日沒有可比的有效日，暫不作跨日對照")
        else:
            line("        全日覆蓋不足，不列跨日對照")
    line()


    line(f"【妳按的】{len(marks)} 次" + (f"，語音 {len(voice)} 段" if voice else ""))
    if not marks and not voice:
        line("  （今天沒按也沒錄 —— 那這一天只有生理資料，對不了）")
    buzz = _load(BUZZ / f"{day}.jsonl")
    seen = set()
    bz = []
    for x in buzz:
        k = (x["t"], x["ms"], x["pkg"])
        if k not in seen:
            seen.add(k); bz.append(x)
    for m in marks:
        t = T(m)
        name = TAG_NAME.get(m.get("tag"), m.get("label", "?"))
        why = _explain(m, t, bz, hours, a, b)
        line(f"  {t:%H:%M}  {name:4s}  {why}")
    for v in voice:
        t = datetime.fromisoformat(v["t"])
        line(f"  {t:%H:%M}  語音    「{v.get('text','')[:46]}」")
    line()

    # ── 系統 ────────────────────────────────────────────────
    line("【系統】")
    try:
        line(battery_line(day))
    except Exception as exc:
        line(f"  錶耗電  算不出來（{type(exc).__name__}）")
    line("  " + sleep_snapshot_notice)
    if recs:
        newest = max(T(r) for r in recs)
        if day == datetime.now().strftime("%Y-%m-%d"):
            age = (datetime.now() - newest).total_seconds() / 60
            line(f"  資料最新 {newest:%H:%M}（落後 {age:.0f} 分）"
                 + ("  ✓" if age < 30 else "  ⚠ 要看連線"))
        else:
            line(f"  本日最後一筆 {newest:%H:%M}（歷史日期，不用現在時間判連線）")
    if dsum:
        blind = sum(1 for h in hours if h["verdict"] in ("no_data", "unknown"))
        summary_size = ((f"{summary_path.stat().st_size/1024:.1f} KB")
                        if summary_path.exists() else "即時計算、尚未存檔")
        line(f"  盲區 {blind}/24 小時　"
             f"資料 {dsum['cover']['records']:,} 筆 → 摘要 "
             f"{summary_size}")
    can = [r for r in _load(VERDICT) if r.get("kind") == "canary" and r["t"][:10] == day]
    if can:
        ok = sum(1 for c in can if c.get("pass"))
        line(f"  金絲雀 {ok}/{len(can)}" + ("  ✓" if ok == len(can) else "  ⚠ 沒全過"))
    return "\n".join(L)


def _explain(m: dict, t: datetime, bz: list[dict], hours: list[dict],
             a: datetime | None, b: datetime | None) -> str:
    """ """
    tag = m.get("tag")
    if tag == "buzz":

        near = []
        for x in bz:
            try:
                bt = datetime.strptime(f"{t.year}-{x['t']}", "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if -300 <= (bt - t).total_seconds() <= 60:
                near.append((bt, x))
        near.sort()
        ours = [x for x in near if "watchbridge" in x[1]["pkg"]]
        other = [x for x in near if "watchbridge" not in x[1]["pkg"]]
        if ours and other:
            press = ours[-1][0]
            cause = [x for x in other if x[0] < press]
            if cause:
                pkg = cause[-1][1]["pkg"].split(".")[-1]
                gap = (press - cause[-1][0]).total_seconds()
                return f"← {pkg} 震了 {cause[-1][1]['ms']}ms，妳 {gap:.0f} 秒後按"
        if other:
            return f"← {other[-1][1]['pkg'].split('.')[-1]}（沒量到反應時間）"

        return "← 前後沒有震動記錄 —— **我沒抓到，不代表沒震**"
    if tag in ("sleep", "wake") and a and b:
        h = next((x for x in hours if x["t"][11:13] == f"{t.hour:02d}"), None)
        if h and "hr" in h:
            return f"← 那小時心率中位 {h['hr']['p50']}"
    if tag == "unwell":
        return "★ 最有價值的一筆 —— 對應「該不該報警」"
    return ""


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    day = next((x for x in sys.argv[1:] if x.startswith("202")),
               datetime.now().strftime("%Y-%m-%d"))
    print(report(day))


if __name__ == "__main__":
    main()
