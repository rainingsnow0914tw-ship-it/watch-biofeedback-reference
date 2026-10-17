#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
最近身體 —— 把摘要層最近幾小時的紀錄用白話印出來，給任何一個阿寶拿來核對她說的話。

為什麼要有這支（2026-10-17，skill「手錶求證」的配套）：
    verify_claim.py 只認「運動／睡覺／休息」三種主張，而她會說的是
    「我剛洗完澡」「我很累」「我一直坐著」。那些沒有現成判斷式，
    但**摘要層每小時都有一筆**：戴沒戴、心率上下界、動作量、皮膚溫差。
    印出來給阿寶自己看，再由阿寶對照她的話 —— 這才是「AI 的眼睛」。

三條規矩（跟整個專案一樣）：
    ① trusted_minutes 低的小時，數字不可信 —— 一定印涵蓋率
    ② 沒有那一小時的紀錄 = 「我沒看到」，不是「她沒事」
    ③ 只印事實，不下「妳狀態不錯」這種結論；結論由阿寶對著她的話下

用法：
    python 最近身體.py                 # 最近 3 小時
    python 最近身體.py --hours 8
    python 最近身體.py --day 2026-10-16          # 某一整天
    python 最近身體.py --day 2026-10-16 --json   # 原始 JSON 行
"""
from __future__ import annotations
import argparse, json, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "生理資料" / "摘要"

def load_day(day: str):
    f = SUMMARY / f"{day}.jsonl"
    # 今天的摘要只有跑「對一下」時才更新 —— 10-17 07:10 阿寶回話時因此把「有資料」說成「沒傳資料」。
    # 所以今天的檔超過 15 分鐘沒更新就當場重算（讀本機原始檔，幾秒鐘）。
    import time
    if day == dt.datetime.now().strftime("%Y-%m-%d") and f.exists() and time.time() - f.stat().st_mtime > 900:
        import subprocess, sys
        subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).with_name("摘要層.py")), day],
                       capture_output=True, cwd=str(Path(__file__).parent))
    if not f.exists():
        # 那天沒人算過摘要 → 當場算（摘要層.py 讀的是本機原始檔，不碰裝置）
        import subprocess, sys
        subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).with_name("摘要層.py")), day],
                       capture_output=True, cwd=str(Path(__file__).parent))
    if not f.exists():
        return []
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("layer") == "hour":
            rows.append(r)
    return rows

def fmt(r):
    c = r.get("cover", {})
    hr = r.get("hr") or {}
    mo = r.get("motion") or {}
    tp = r.get("temp") or {}
    t = r["t"][11:16]
    trusted = c.get("trusted_minutes", 0)
    worn = c.get("worn_minutes", 0)
    off = c.get("explicit_off_minutes", 0)
    chg = c.get("charging_minutes", 0)
    cover = f"可信{trusted:>2}分 戴{worn:>2} 脫{off:>2} 充{chg:>2}"
    hr_s = f"心率 {hr['lo']:>3}–{hr['p50']:>3}–{hr['hi']:>3}" if hr else "心率  —  —  — "
    mo_s = f"動作 {mo.get('p50', 0):>6.1f}" if mo else "動作     — "
    tp_s = f"溫差 {tp['delta_ref_p50']:>+5.2f}" if tp and tp.get("delta_ref_p50") is not None else "溫差    — "
    flag = "" if trusted >= 30 else "  ⚠ 可信分鐘太少，數字別當真"
    return f"  {t}  {cover}  {hr_s}  {mo_s}  {tp_s}  → {r.get('verdict','?')}{flag}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--day")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    now = dt.datetime.now()
    if a.day:
        rows = load_day(a.day); title = f"{a.day} 整天"
        if a.day == now.strftime("%Y-%m-%d"):
            rows = [r for r in rows if dt.datetime.fromisoformat(r["t"]) <= now]
    else:
        since = now - dt.timedelta(hours=a.hours)
        days = sorted({since.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")})
        rows = [r for d in days for r in load_day(d)
                if since.replace(minute=0, second=0, microsecond=0) <= dt.datetime.fromisoformat(r["t"]) <= now]
        title = f"最近 {a.hours:g} 小時（到 {now:%H:%M}）"
    print(f"═══ 最近身體：{title} ═══")
    if not rows:
        print("  ⚠ 這段時間摘要層沒有任何紀錄 —— 是「我沒看到」，不是「她沒事」。")
        print("    先跑 python 每天對一下.py 或看 生理資料/puller.log。")
        return
    if a.json:
        for r in rows: print(json.dumps(r, ensure_ascii=False))
        return
    print("  時刻   涵蓋（分鐘）                 心率 低–中–高      動作中位   皮膚溫差   判定")
    for r in rows: print(fmt(r))
    n_ok = sum(1 for r in rows if r.get("cover", {}).get("trusted_minutes", 0) >= 30)
    print(f"\n  {len(rows)} 個小時有紀錄，其中 {n_ok} 個可信（≥30 分鐘）。no_data = 那小時完全沒收到東西（沒戴、沒連、或還沒拉）。")
    print("  心率單位 bpm；動作是每分鐘活動量中位（<30 很靜、>200 在動）；溫差 = 皮膚 − 錶身（負很多 = 多半沒戴）。")

if __name__ == "__main__":
    main()
