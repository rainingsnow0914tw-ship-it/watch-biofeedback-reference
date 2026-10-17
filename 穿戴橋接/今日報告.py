#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
今日報告 —— 把「對一下」跑出來的東西，整理成一頁看得完的東西。

## 為什麼重寫（使用者 2026-08-23）

> 「你先整理一下對一對裡面的資訊」

她說得對。原本的輸出是**一面牆**：每個標記印五行、每個震動印一行，
一天六個標記就是三十幾行，而**最重要的那句「今天怎麼樣」被埋在裡面**。

⚠ 這不是美觀問題。**看不完的報告等於沒有報告** ——
  她每天要看的東西如果需要往下捲三次，她就會不看，
  而不看的話那些按鈕就白按了（她自己說的：「不然我就白記錄了」）。

## 排版原則

```
1. 結論在最上面        —— 一句話，不用捲
2. 身體 → 妳按的 → 系統  —— 由「她關心的」排到「我關心的」
3. 每一筆標記一行       —— 旁邊直接寫「這是什麼造成的」，不列原始震動
4. 有對照才有意義       —— 數字後面附上前幾天，不然 5.0% 是好是壞看不出來
```

⚠ **`cover`（涵蓋率）一定要出現。** 沒有它，「心率中位 66」可能是
  三千筆算出來的，也可能是三筆 —— 兩個長得一樣，意思完全相反。
"""

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
            # 2026-08-26 新增。**這一顆是唯一會直接改寫佩戴判斷的標記** ——
            # 她按下去，手錶當場把 on_body 設成 off，不再靠脈搏猜。
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

    ⚠ **不要先把小動作濾掉再數大動作** —— 2026-08-21 我這樣寫，
      結果每一天都算出 0.0%，因為要測的東西被前一步濾掉了。
    """
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

    # ── 睡眠窗：三星 > 她自己按的 > 心率推（而且要連續）──────────
    #
    # 🔴 **2026-09-02 修的一個會說謊的 bug。**
    #   舊版取「該小時心率中位 < 72」的**第一個**跟**最後一個**小時，
    #   中間全部當成睡覺 —— **完全不檢查連不連續**。
    #   09-02 那天 01:00 是 69、17:00 是 68，於是它宣布
    #   **「睡了 18.0 小時」**，而三星判的是 2h02m。差十六小時。
    #
    #   而且它印在她每天看的報告第一行，語氣完全肯定。
    #   08-30 我把偵測器校準到 F1 0.77，**卻從來沒把它接進這裡**——
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

    # ② 她自己按的
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
    # ⚠ **標題用「實睡」，不是「第一段開始到最後一段結束的跨度」。**
    #   2026-09-02 第一次修完還是錯：三星判 2 段共 6.0 小時，
    #   而 21:52 → 隔天 17:45 的跨度是 19.9 小時 —— 它印「睡了 19.9 小時」。
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
    if a and b:
        r = _restless(recs, a, b) if len(sleep_windows) == 1 and coverage["quality"] == "sufficient" else None
        hrs = [h for h in hours if "hr" in h and any(
            st <= datetime.fromisoformat(h["t"]) < en for st, en in sleep_windows)]
        if hrs:
            p = sorted(h["hr"]["p50"] for h in hrs)
            line(f"  睡覺  {a:%H:%M} → {b:%H:%M}　心率中位 {p[len(p)//2]}　"
                 f"最低 {min(h['hr']['lo'] for h in hrs)}")
        if r:
            # ⚠ 一定要附對照。「5.0%」單獨看不出是好是壞。
            line(f"  翻身比例 {r[0]:.1%}（{r[1]} 分鐘可信）"
                 f"　　對照：08-21 她說出冷汗那晚是 10.8%，沒事的兩晚是 4.4% / 5.0%")
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

    # ── 妳按的 ──────────────────────────────────────────────
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
    """一行講清楚「這一筆旁邊發生了什麼」。**不列原始震動，只給結論。**"""
    tag = m.get("tag")
    if tag == "buzz":
        # 震動一定在她按之前 —— 往前找 5 分鐘，往後只留 1 分鐘。
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
        # ⚠ 「沒查到」不是「沒發生」。這句要講清楚。
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
