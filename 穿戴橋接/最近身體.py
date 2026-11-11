#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""但**摘要層每小時都有一筆**：戴沒戴、心率上下界、動作量、皮膚溫差。

三條規矩（跟整個專案一樣）：
    ① trusted_minutes 低的小時，數字不可信 —— 一定印涵蓋率

用法：
    python 最近身體.py                 # 最近 3 小時
    python 最近身體.py --hours 8"""
from __future__ import annotations
import argparse, json, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "生理資料" / "摘要"

def load_day(day: str):
    f = SUMMARY / f"{day}.jsonl"

    # 所以今天的檔超過 15 分鐘沒更新就當場重算（讀本機原始檔，幾秒鐘）。
    import time
    raw = ROOT / "生理資料" / "手錶橋接" / f"{day}.jsonl"
    stale_today = day == dt.datetime.now().strftime("%Y-%m-%d") and f.exists() and time.time() - f.stat().st_mtime > 900


    stale_any = f.exists() and raw.exists() and raw.stat().st_mtime > f.stat().st_mtime
    if stale_today or stale_any:
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

SLEEP = ROOT / "生理資料" / "睡眠.json"
SLEEP_SERIES = ROOT / "生理資料" / "series" / "sleep.jsonl"

def samsung_sleep_sessions():
    """三星健康判的睡眠段（手機→8787→睡眠.json）。回 [(start, end)]，本地時間、無時區。"""
    out = []

    try:
        for line in SLEEP_SERIES.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                b = dt.datetime.fromisoformat(r["session_end_time"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
                a = b - dt.timedelta(seconds=int(r.get("duration_seconds", 0)))
                if b > a:
                    out.append((a, b))
            except Exception:
                continue
    except Exception:
        pass
    try:
        d = json.loads(SLEEP.read_text(encoding="utf-8"))
        for s_ in d.get("sessions", []):
            try:
                a = dt.datetime.fromisoformat(s_["start"]).replace(tzinfo=None)
                b = dt.datetime.fromisoformat(s_["end"]).replace(tzinfo=None)
                if not any(abs((a - x).total_seconds()) < 1800 for x, _ in out):
                    out.append((a, b))
            except Exception:
                pass
    except Exception:
        pass

    merged = []
    for a, b in sorted(set(out)):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged

def samsung_minutes(t_hour: dt.datetime, sessions) -> int:
    """這一小時裡三星說在睡的分鐘數。"""
    h0, h1 = t_hour, t_hour + dt.timedelta(hours=1)
    m = 0
    for a, b in sessions:
        lo, hi = max(a, h0), min(b, h1)
        if hi > lo:
            m += int((hi - lo).total_seconds() // 60)
    return m

def phone_col(r):
    """ """
    try:
        import 手機情境
        t0 = dt.datetime.fromisoformat(r["t"]); t1 = min(t0 + dt.timedelta(hours=1), dt.datetime.now())
        return 手機情境.fmt_hour(手機情境.window_stats(t0, t1))
    except Exception:
        return "手機 —"


def posture_desc(tilt, spread=None) -> str:
    """ """
    if tilt is None:
        return ""
    if tilt < 25: d = "錶面朝上近水平（放著／手平攤）"
    elif tilt < 65: d = "像手放桌上／鍵盤前"
    elif tilt < 110: d = "手垂著或立著"
    elif tilt < 150: d = "錶面翻向自己（像躺著舉手機看）"
    else: d = "錶面朝下"
    if spread is not None and spread >= 60: d += "，姿勢換來換去"
    elif spread is not None and spread < 10: d += "，很穩"
    return d


def posture_line(now: dt.datetime) -> str:
    """ """
    raw = ROOT / "生理資料" / "手錶橋接"
    pts = []
    for d in {(now - dt.timedelta(minutes=15)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")}:
        f = raw / f"{d}.jsonl"
        if not f.exists():
            continue
        try:
            with f.open("rb") as fh:
                fh.seek(0, 2); size = fh.tell(); fh.seek(max(0, size - 400_000))
                tail = fh.read().decode("utf-8", errors="replace")
        except Exception:
            continue
        for line in tail.splitlines():
            if '"type":"motion"' not in line or '"tilt"' not in line:
                continue
            try:
                r = json.loads(line); t = dt.datetime.fromisoformat(r["t"][:19])
            except Exception:
                continue
            if now - dt.timedelta(minutes=15) <= t <= now and isinstance(r.get("tilt"), (int, float)) and r["tilt"] >= 0:
                pts.append((t, r["tilt"], r.get("activity")))
    if len(pts) < 3:
        return "  姿態：最近 15 分鐘沒有傾角資料（錶沒送到／code62 以前）。"
    tilts = sorted(x[1] for x in pts); med = tilts[len(tilts) // 2]; spread = tilts[-1] - tilts[0]
    mots = [x[2] for x in pts if x[2] is not None]; mo = sorted(mots)[len(mots) // 2] if mots else None
    return (f"  姿態（最近 15 分鐘，{len(pts)} 筆）：錶面傾角中位 {med:.0f} 度、最大最小差 {spread:.0f} 度"
            + (f"、動作中位 {mo:.0f}" if mo is not None else "") + f" → {posture_desc(med, spread)}。")


def fmt(r, sessions=None):
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
    ti = mo.get("tilt_p50") if mo else None
    ti_s = f"傾角 {ti:>5.0f}°" if ti is not None else "傾角   — "
    flag = "" if trusted >= 30 else "  ⚠ 可信分鐘太少，數字別當真"

    ss = ""
    if sessions is not None:
        m = samsung_minutes(dt.datetime.fromisoformat(r["t"]), sessions)
        ss = f"  三星:{'睡' + str(m).rjust(2) if m else ' — '}"
        if m >= 30 and r.get("verdict") in ("no_data", "off", "charging"):
            flag += "  ⚠ 三星說在睡、錶說沒戴／沒資料：兩邊不一致，別只信一邊"
    return f"  {t}  {cover}  {hr_s}  {mo_s}  {ti_s}  {tp_s}{ss}  {phone_col(r):<22}→ {r.get('verdict','?')}{flag}"

def rhythm_line(now: dt.datetime) -> str:
    """這一行是給腦看的：醒不到 8 小時不要催睡。"""
    rows = [r for d in sorted({(now - dt.timedelta(hours=h)).strftime("%Y-%m-%d") for h in (0, 12, 24, 36)})
            for r in load_day(d) if dt.datetime.fromisoformat(r["t"]) <= now and dt.datetime.fromisoformat(r["t"]) >= now - dt.timedelta(hours=36)]
    def restish(r):
        c = r.get("cover", {}); mo = (r.get("motion") or {}).get("p50")
        return c.get("trusted_minutes", 0) < 10 or (mo is not None and mo < 25)
    blocks, cur = [], None
    for r in rows:
        t = dt.datetime.fromisoformat(r["t"])
        if restish(r):
            if cur and t == cur[1]:            # H12：只接真正相鄰的小時
                cur[1] = min(t + dt.timedelta(hours=1), now)   # H13：終點不超過 now
            else:
                if cur: blocks.append(cur)
                cur = [t, min(t + dt.timedelta(hours=1), now)]
        else:
            if cur: blocks.append(cur); cur = None   # H12：中間有活動的小時就斷
    if cur: blocks.append(cur)
    long = [b for b in blocks if (b[1] - b[0]) >= dt.timedelta(hours=3)]
    parts = []
    if long:
        a, b = long[-1]
        inblk = [r for r in rows if a <= dt.datetime.fromisoformat(r["t"]) < b]
        n_off = sum(1 for r in inblk if r.get("cover", {}).get("trusted_minutes", 0) < 10)
        kind = "沒戴／充電，這段我看不到她" if n_off >= len(inblk) * 0.6 else "戴著但很靜（像在睡）"
        awake_h = max(0.0, (now - b).total_seconds() / 3600)
        if n_off >= len(inblk) * 0.6:

            parts.append(f"錶看：{a:%m-%d %H:%M}～{b:%H:%M} 沒戴／充電（這段我看不到她），之後有資料約 {awake_h:.1f} 小時")
        elif awake_h < 0.2:
            parts.append(f"錶看：{a:%m-%d %H:%M} 起到現在都很靜（像在睡／休息）")
        else:
            parts.append(f"錶看：最後一段長休息 {a:%m-%d %H:%M}～{b:%H:%M}（{kind}），之後醒著約 {awake_h:.1f} 小時")
    else:
        parts.append("錶看：過去 36 小時沒有 ≥3 小時的長休息段")
    ss = [x for x in samsung_sleep_sessions() if x[1] >= now - dt.timedelta(hours=36)]
    if ss:
        a, b = ss[-1]
        parts.append(f"三星：最近一段睡眠 {a:%m-%d %H:%M}～{b:%H:%M}")
    else:
        parts.append("三星：36 小時內沒有睡眠紀錄（可能還沒同步）")
    return "  作息：" + "；".join(parts) + "。她作息不固定——醒不到 8 小時別催睡。"


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
    try:
        print(rhythm_line(now))
    except Exception as e:
        print(f"  作息：算不出來（{e}）")
    try:
        print(posture_line(now))
    except Exception as e:
        print(f"  姿態：算不出來（{e}）")
    if not rows:
        print("  ⚠ 這段時間摘要層沒有任何紀錄 —— 是「我沒看到」，不是「她沒事」。")
        print("    先跑 python 每天對一下.py 或看 生理資料/puller.log。")
        return
    if a.json:
        for r in rows: print(json.dumps(r, ensure_ascii=False))
        return
    sessions = samsung_sleep_sessions()
    print("  時刻   涵蓋（分鐘）                 心率 低–中–高      動作中位   傾角      皮膚溫差   三星睡眠  手機（在家／螢幕／電量）  判定")
    for r in rows: print(fmt(r, sessions))

    run = max_run = 0
    for r in rows:
        run = run + 1 if r.get("verdict") == "no_data" else 0
        max_run = max(max_run, run)
    if max_run >= 2:
        print(f"  ⚠ 有連續 {max_run} 小時 no_data：**沒收到 ≠ 沒戴**。先看管路（python 四條管路.py），別跟她說「應該沒戴」。")
    n_ok = sum(1 for r in rows if r.get("cover", {}).get("trusted_minutes", 0) >= 30)
    print(f"\n  {len(rows)} 個小時有紀錄，其中 {n_ok} 個可信（≥30 分鐘）。no_data = 那小時完全沒收到東西（沒戴、沒連、或還沒拉）。")

    print("  傾角＝錶面相對水平：~50 手在桌上／鍵盤前、70～110 垂著或立著、>110 錶面朝下或翻向自己——要配動作看：動作 <60 很靜才像躺著舉手機；動作 >200 是走路手垂著甩，不是舉手機（實測：在室內走來走去時傾角 115～132、動作 260～480，曾被誤說成舉手機 15 分鐘）。一小時的傾角是中位數，不能說成「這 15 分鐘都怎樣」。")
    print("  手機欄：在家＝手機連著家裡 Wi-Fi；亮＝螢幕亮的分鐘；錶很靜＋手機亮＝在滑手機，不是睡著也不是放鬆。")
    print("  心率單位 bpm；動作是每分鐘活動量中位（<30 很靜、>200 在動）；溫差 = 皮膚 − 錶身（負很多 = 多半沒戴）。")

if __name__ == "__main__":
    main()
