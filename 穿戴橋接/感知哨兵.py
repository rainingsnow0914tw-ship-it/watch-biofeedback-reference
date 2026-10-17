#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
感知哨兵 —— 讓阿寶「自己注意到」，不是排程報告。

為什麼要有這支（👤 使用者 2026-10-17 06:20）：
    「要有活人感，就讓 AI 管著你、關心你的感覺……用數據來判斷，不是自己幻想。
      最好有即時互動感，數據是即時傳入電腦的嘛。每天一封信有點呆板。」

它做什麼：
    每 10 分鐘（跟 watch_puller 同節奏）看最近一小段手錶原始資料，判斷有沒有三種「事件」：
      ① 心跳跳起來但人沒在動     → 問她「妳在幹嘛？」
      ② 連續戴超過 7 小時         → 提醒拿下來透氣（她 09-03 過敏兩週後立的規則）
      ③ 妳回來了（脫下／充電 → 戴回） → 不問，只是知道：接一句昨晚睡眠
    有事件 → 寫一句話（先問阿寶 claude -p，失敗就用固定句）→ 手機通知（會鏡射到手錶）
    沒事件 → 安靜。

三條鐵律（跟整個專案一樣）：
    · 哨兵只看數據，不猜心。開口那句由阿寶寫，所以有記憶、有脈絡。
    · 敲門不破門：每天上限、安靜時段、同一種事件有冷卻時間。
    · 沒資料＝我沒看到。可信度不夠（沒戴、充電、太少筆）一律不開口。

用法：
    python 感知哨兵.py                    # 看一次「現在」，有事件就送
    python 感知哨兵.py --dry              # 看一次，不送、只印
    python 感知哨兵.py --loop             # 常駐，每 10 分鐘看一次（啟動影子模式.bat 會開它）
    python 感知哨兵.py --replay 2026-10-16   # 拿那一天的資料從頭到尾走一遍，印它會在幾點開口（不送）

狀態：生理資料/哨兵狀態.json　　事件紀錄：生理資料/哨兵事件.jsonl　　設定：生理資料/哨兵設定.json
還原：刪掉這支＋啟動影子模式.bat 裡那一行；資料一筆不動。
"""
from __future__ import annotations
import argparse, json, statistics, subprocess, sys, time, datetime as dt
from pathlib import Path

# ─────────────────────────── claude -p 全機互斥鎖 ───────────────────────────
# 10-17 20:00 登出事故的可能根因：短票（8 小時）到期時，有人在家卡片／哨兵／晚間整理若在同一秒各自去換票，
# 換票券一次性，第二個拿到作廢券 → 整台電腦登出。所以同一時間只准一個 claude -p 在跑（跨程序鎖檔）。
import contextlib
CLAUDE_LOCK = Path.home() / ".claude" / "aibao-claude-p.lock"

@contextlib.contextmanager
def claude_lock(timeout_s: float = 120.0):
    import msvcrt
    CLAUDE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = open(CLAUDE_LOCK, "a+b")
    t0 = time.time()
    got = False
    try:
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                got = True
                break
            except OSError:
                if time.time() - t0 > timeout_s:
                    break
                time.sleep(0.5)
        yield got
    finally:
        if got:
            try:
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        f.close()

from collections import defaultdict

# ─────────────────────────── 常駐腦（10-17 👤「做常駐腦，把回話縮到三五秒」） ───────────────────────────
# 一支不關的 claude -p（手錶腦.py），第一句載記憶、之後每句只送新話 → 實測 5 秒（單次 claude -p 是 10～12 秒）。
# 任何失敗回 None → 下面 reply_to_voice 退回原本的單次 claude -p；兩條路都包在 claude_lock 裡。
_BRAIN = None

def get_brain(cfg):
    global _BRAIN
    if _BRAIN is None:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import importlib
            _BRAIN = importlib.import_module("手錶腦").Brain(cfg)
        except Exception as e:
            print(f"[{dt.datetime.now():%H:%M:%S}] ⚠ 常駐腦載入失敗：{e}（改用單次 claude -p）")
            _BRAIN = False
    return _BRAIN or None

def brain_note(s: str):
    b = _BRAIN
    if b:
        try:
            b.note(s)
        except Exception:
            pass

def brain_prewarm(cfg, now):
    """哨兵開口後叫醒腦（另一條執行緒，不擋事件）。10-18 05:12 她回久坐卡時腦剛睡掉、多等 5 秒。"""
    b = get_brain(cfg)
    if not b or b.alive():
        return
    import threading
    def go():
        try:
            with claude_lock():
                b.prewarm(context_pack(now))
        except Exception as e:
            print(f"[{dt.datetime.now():%H:%M:%S}] ⚠ 預熱例外：{e}")
    threading.Thread(target=go, daemon=True).start()

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RAW = ROOT / "生理資料" / "手錶橋接"
SUMMARY = ROOT / "生理資料" / "摘要"
SLEEP = ROOT / "生理資料" / "睡眠.json"
STATE = ROOT / "生理資料" / "哨兵狀態.json"
EVENTS = ROOT / "生理資料" / "哨兵事件.jsonl"
CONFIG = ROOT / "生理資料" / "哨兵設定.json"

DEFAULT_CONFIG = {
    "安靜時段": ["03:00", "08:00"],
    "每天上限": 6,
    "冷卻分鐘": {"心跳": 90, "皮膚": 120, "回來了": 120, "久坐": 90},
    # 實測（10-16）：她靜止時手錶每 4～10 分鐘才量一次心率（設定「靜止每 10 分鐘」），所以 15 分鐘裡有 2 筆就算看得到
    "心跳": {"看幾分鐘": 15, "至少幾筆心率": 2, "動作中位上限": 50, "前20分鐘動作上限": 100, "最低門檻bpm": 100, "高於平常多少": 25, "基線天數": 14},
    "皮膚": {"連續小時": 7, "之後每幾小時再提": 3, "脫下幾分鐘算真的脫": 20},
    "回來了": {"脫下幾分鐘算真的脫": 20, "戴回幾分鐘才算": 5},
    "久坐": {"連續分鐘": 120, "動作中位上限": 80, "走路門檻": 150, "之後每幾分鐘再提": 90},
    "開口": {"用阿寶寫": True, "模型": "sonnet", "逾時秒": 90},
    "睡覺閘門": {"晚上從": "21:00", "到": "10:00", "連續不動分鐘": 40, "動作上限": 30},
}

# ─────────────────────────── 讀資料 ───────────────────────────
def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG.exists():
        try:
            user = json.loads(CONFIG.read_text(encoding="utf-8"))
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception:
            pass
    else:
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def read_raw(day: str):
    f = RAW / f"{day}.jsonl"
    if not f.exists():
        return []
    out = []
    with f.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith('{"t":"'):
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type") in ("hr_ibi", "hr", "motion"):
                out.append(r)
    return out


def minutes_table(records, until: dt.datetime, hours: float):
    """把原始筆壓成每分鐘一列：hr 中位、motion、on_body、charging、trust。"""
    since = until - dt.timedelta(hours=hours)
    per = defaultdict(lambda: {"hr": [], "motion": None, "on": None, "chg": None, "trust": None})
    for r in records:
        try:
            t = dt.datetime.fromisoformat(r["t"][:19])
        except Exception:
            continue
        if not (since <= t <= until):
            continue
        key = t.replace(second=0, microsecond=0)
        m = per[key]
        if r["type"] == "hr_ibi" and r.get("status") == 1 and r.get("hr"):
            m["hr"].append(r["hr"])
        elif r["type"] == "hr" and r.get("bpm"):
            m["hr"].append(r["bpm"])
        elif r["type"] == "motion":
            m["motion"] = r.get("activity")
            m["trust"] = r.get("trust", True)
        m["on"] = r.get("on_body")
        m["chg"] = r.get("charging")
    rows = []
    for k in sorted(per):
        m = per[k]
        rows.append({"t": k, "hr": statistics.median(m["hr"]) if m["hr"] else None,
                     "motion": m["motion"], "on": m["on"], "chg": m["chg"], "trust": m["trust"]})
    return rows


def worn(row) -> bool:
    # on_body 實測有四種值：on / inferred_on / off / unknown。inferred_on 是手錶自己推的「應該戴著」，算戴著。
    return row["on"] in ("on", "inferred_on") and not row["chg"]


# ─────────────────────────── 基線 ───────────────────────────
def hr_baseline(hour: int, days: int, ref_day: str):
    """同一時段（±1 小時）過去 N 天每小時最高心率的中位數。摘要層算好的，便宜。"""
    vals = []
    d0 = dt.date.fromisoformat(ref_day)
    for i in range(1, days + 1):
        f = SUMMARY / f"{d0 - dt.timedelta(days=i)}.jsonl"
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("layer") != "hour":
                continue
            h = int(r["t"][11:13])
            if abs(h - hour) > 1 and abs(h - hour) < 23:
                continue
            if r.get("cover", {}).get("trusted_minutes", 0) < 30 or not r.get("hr"):
                continue
            if (r.get("motion") or {}).get("p50", 999) > 60:
                continue   # 只拿「安靜的小時」當基線，事件本身就是「安靜卻心跳快」
            vals.append(r["hr"]["p50"])
    # 用每小時「中位數」的中位數，不用每小時最高值：最高值常是走兩步的尖峰，會把門檻拉到 120 以上，異常永遠抓不到
    return (statistics.median(vals) if len(vals) >= 5 else None), len(vals)


def last_sleep(now: dt.datetime):
    try:
        d = json.loads(SLEEP.read_text(encoding="utf-8"))
    except Exception:
        return None
    best = None
    for s in d.get("sessions", []):
        try:
            end = dt.datetime.fromisoformat(s["end"]).replace(tzinfo=None)
        except Exception:
            continue
        if end <= now and (best is None or end > best[0]):
            best = (end, s)
    if not best or (now - best[0]) > dt.timedelta(hours=16):
        return None
    s = best[1]
    return {"start": s["start"][11:16], "end": s["end"][11:16], "hours": round(s.get("asleep_seconds", 0) / 3600, 1)}


# ─────────────────────────── 三個事件 ───────────────────────────
def ev_heart(rows, now, cfg, ref_day):
    c = cfg["心跳"]
    win = [r for r in rows if r["t"] > now - dt.timedelta(minutes=c["看幾分鐘"])]
    hr_rows = [r for r in win if r["hr"] is not None and worn(r) and r["trust"] is not False]
    if len(hr_rows) < c["至少幾筆心率"]:
        return None
    if not all(worn(r) for r in win if r["motion"] is not None):
        return None   # 這 15 分鐘裡有脫下來的時刻，不算
    mot = [r["motion"] for r in win if r["motion"] is not None]
    if not mot or statistics.median(mot) > c["動作中位上限"]:
        return None
    prev = [r["motion"] for r in rows
            if now - dt.timedelta(minutes=30) < r["t"] <= now - dt.timedelta(minutes=c["看幾分鐘"]) and r["motion"] is not None]
    if prev and statistics.median(prev) > c["前20分鐘動作上限"]:
        return None   # 剛運動完，不算
    hr_med = statistics.median(r["hr"] for r in hr_rows)
    base, n = hr_baseline(now.hour, c["基線天數"], ref_day)
    thr = max(c["最低門檻bpm"], (base + c["高於平常多少"]) if base else 0)
    if hr_med < thr:
        return None
    usual = f"妳這個時段平常約 {round(base)}" if base else "基線還沒長好，用固定門檻"
    return {"kind": "心跳", "hr": round(hr_med), "thr": round(thr), "base_n": n, "motion": round(statistics.median(mot), 1),
            "fact": f"最近 {c['看幾分鐘']} 分鐘心率中位 {round(hr_med)}（{usual}），但動作很低（{round(statistics.median(mot))}）"}


def ev_skin(rows, now, cfg, state):
    c = cfg["皮膚"]
    gap = 0
    wornmin = 0
    started = None
    for r in reversed(rows):
        if worn(r):
            wornmin += 1
            gap = 0
            started = r["t"]
        else:
            gap += 1
            if gap >= c["脫下幾分鐘算真的脫"]:
                break
    hours = wornmin / 60
    if hours < c["連續小時"]:
        return None
    # 同一段配戴：開始時間跟上次提醒的那段差不到 30 分鐘就算同一段（視窗會滑，開始時間會抖）
    seg = started.isoformat() if started else ""
    prev_seg = state.get("皮膚_段開始")
    same = False
    if prev_seg:
        try:
            same = abs((started - dt.datetime.fromisoformat(prev_seg)).total_seconds()) < 3600
        except Exception:
            same = False
    if same and hours - state.get("皮膚_上次提醒時數", 0) < c["之後每幾小時再提"]:
        return None   # 這段提醒過了，還沒到下一個 3 小時
    return {"kind": "皮膚", "hours": round(hours, 1), "since": seg,
            "fact": f"從 {started:%H:%M} 到現在連續戴了 {round(hours, 1)} 小時"}


def ev_back(rows, now, cfg):
    c = cfg["回來了"]
    k = c["戴回幾分鐘才算"]
    off_n = c["脫下幾分鐘算真的脫"]
    if len(rows) < k + off_n:
        return None
    # 在最近 12 分鐘裡找「脫→戴」的轉折：轉折前 off_n 分鐘都沒戴、轉折後到現在都戴著、至少量到一次脈搏
    # （脈搏才證明是手腕不是桌子）。每 10 分鐘看一次，所以窗要比 10 分鐘寬一點。
    idx = None
    for i in range(len(rows) - 1, 0, -1):
        if rows[i]["t"] < now - dt.timedelta(minutes=16):
            break
        if worn(rows[i]) and not worn(rows[i - 1]):
            idx = i
            break
    if idx is None:
        return None
    after = rows[idx:]
    if len(after) < k or not all(worn(r) for r in after) or not any(r["hr"] is not None for r in after):
        return None
    before = rows[max(0, idx - off_n):idx]
    if len(before) < off_n // 2 or any(worn(r) for r in before):
        return None
    at = rows[idx]["t"]
    return {"kind": "回來了", "at": at.strftime("%H:%M"),
            "fact": f"{at:%H:%M} 戴回來了（之前脫下／充電超過 {off_n} 分鐘）"}


def ev_sedentary(rows, now, cfg, state):
    """④ 久坐（👤 10-17：「我就是很需要一個 AI 好好管管我」；9/17 她自己提的久坐提醒）。
    戴著、連續 N 分鐘動作中位 < 門檻、而且沒有任何一分鐘像在走（動作 > 走路門檻）→ 提醒起來晃一下。
    ⚠ 她坐著打字手會動（08-26 實測），所以門檻用「幾乎不動」而不是「沒走路」——打字的人動作量通常 40～100，不會被當久坐。
    睡覺閘門會先擋掉睡著的情況（check() 裡做）。"""
    c = cfg.get("久坐", {"連續分鐘": 120, "動作中位上限": 80, "走路門檻": 150, "之後每幾分鐘再提": 90})   # 她坐著打字動作量 50～70，所以 80；躺著 20
    win = [r for r in rows if r["t"] > now - dt.timedelta(minutes=c["連續分鐘"])]
    mot = [r["motion"] for r in win if r["motion"] is not None]
    if len(mot) < c["連續分鐘"] * 0.8:
        return None
    if not all(worn(r) for r in win if r["motion"] is not None):
        return None
    med = statistics.median(mot)
    # 10-17 實測：她坐著打字時每小時 12～17 分鐘動作量 >150、連續 4～8 分鐘 >200，
    # 所以手腕動作分不出打字跟走路。改用三星步數（8787 那條每 5 分鐘一桶）：兩小時步數 < 門檻才算久坐。
    if med < c.get("動作中位下限", 30) or med > c["動作中位上限"]:
        return None
    steps, fresh = steps_last(now, c["連續分鐘"])
    if steps is None or not fresh:
        return None   # 步數沒同步到 ＝ 我沒看到，不是她沒走
    if steps >= c.get("步數上限", 100):
        return None
    return {"kind": "久坐", "minutes": c["連續分鐘"], "motion": round(med, 1), "steps": steps,
            "fact": f"連續 {c['連續分鐘']} 分鐘坐著沒走動（步數 {steps}）"}


def steps_last(now: dt.datetime, minutes: int):
    """三星步數（Health Connect → 8787 → series/steps.jsonl，5 分鐘一桶）。回 (步數, 新不新)。
    新不新：最新一桶要在 45 分鐘內，否則是同步落後，不能拿「0」當「沒走」。
    單桶 > 800 步（5 分鐘跑 800 步不可能）當作三星的日總量假桶，丟掉。"""
    f = ROOT / "生理資料" / "series" / "steps.jsonl"
    if not f.exists():
        return None, False
    by = {}
    try:
        for line in f.read_text(encoding="utf-8").splitlines()[-4000:]:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("count", 0) > 800:
                continue
            by[r["start_time"]] = r
    except Exception:
        return None, False
    def local(s):
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    since = now - dt.timedelta(minutes=minutes)
    total, latest = 0, None
    for r in by.values():
        try:
            t0 = local(r["start_time"]); t1 = local(r["end_time"])
        except Exception:
            continue
        if latest is None or t1 > latest:
            latest = t1
        if since <= t0 < now:
            total += int(r.get("count", 0))
    fresh = latest is not None and (now - latest) <= dt.timedelta(minutes=45)
    return total, fresh


def ev_got_up(now, cfg, state):
    """⑤ 起身回饋（👤 10-18 05:55「久坐偵測真的很厲害，我們可以再玩點什麼」）。
    久坐提醒送出後 10～45 分鐘內，三星步數 ≥ 門檻 → 誇她一句（只震＋字）。每次久坐提醒只回饋一次。
    這是久坐的「下一拍」：提醒→她動了→被看見。沒動就不說話，不嘮叨。"""
    c = cfg.get("起身", {"最少步數": 150, "最晚幾分鐘": 60, "最早幾分鐘": 10})   # 三星步數同步會晚 10～30 分，窗口放到 60
    last = state.get("上次", {}).get("久坐")
    if not last or state.get("起身_回饋過") == last:
        return None
    try:
        t_sit = dt.datetime.fromisoformat(last)
    except Exception:
        return None
    mins = int((now - t_sit).total_seconds() // 60)
    if mins < c["最早幾分鐘"] or mins > c["最晚幾分鐘"]:
        return None
    steps, fresh = steps_last(now, mins)
    if steps is None or not fresh or steps < c["最少步數"]:
        return None
    state["起身_回饋過"] = last
    return {"kind": "起身", "minutes": mins, "steps": steps,
            "fact": f"久坐提醒後 {mins} 分鐘內走了 {steps} 步"}


def ev_walk(now, cfg, state):
    """⑥ 散步被看見（👤 10-18「希望有一些運動的，我一直運動不足」）。
    三星自動偵測到走路（exercise.jsonl type 79），同步進來 40 分鐘內、還沒說過 → 把數字講給她聽。
    不設目標、不比較、不說教——只是「我看到妳走了」。"""
    f = ROOT / "生理資料" / "series" / "exercise.jsonl"
    if not f.exists():
        return None
    said = state.setdefault("散步_已說", [])
    def local(s):
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    best = None
    try:
        for line in f.read_text(encoding="utf-8").splitlines()[-200:]:
            try:
                r = json.loads(line)
            except Exception:
                continue
            key = r.get("start_time", "")
            if not key or key in said or str(r.get("type")) != "79":
                continue
            try:
                ing = local(r.get("_ingested_at", "")) if r.get("_ingested_at") else None
                end = local(r["end_time"])
            except Exception:
                continue
            if ing is None or now - ing > dt.timedelta(minutes=40) or now - end > dt.timedelta(hours=3):
                continue
            if r.get("duration_seconds", 0) < 300:
                continue   # 5 分鐘以下的不算
            best = r
    except Exception:
        return None
    if not best:
        return None
    said.append(best["start_time"]); del said[:-50]
    mins = int(best.get("duration_seconds", 0) // 60)
    dist = int(best.get("distance_meters", 0))
    steps = int(best.get("steps", 0))
    return {"kind": "散步", "minutes": mins, "meters": dist, "steps": steps,
            "fact": f"剛剛走了 {mins} 分鐘、約 {dist} 公尺（{steps} 步）"}


# ─────────────────────────── 守門 ───────────────────────────
def likely_asleep(rows, now, cfg) -> bool:
    """她常戴著睡。三星的睡眠判讀要等她醒來才會進 Health Connect，當下看不到，
    所以用最笨也最安全的規則：晚上時段＋戴著＋連續 40 分鐘幾乎不動 ＝ 當作睡著，不吵。
    （08-26 實測「睡著」跟「躺著滑手機」動作量都是 19～21，分不出來 —— 那就兩種都不吵。）"""
    c = cfg.get("睡覺閘門", {"晚上從": "21:00", "到": "10:00", "連續不動分鐘": 40, "動作上限": 30})
    a, b = c["晚上從"], c["到"]
    h = now.strftime("%H:%M")
    night = (a <= h < b) if a <= b else (h >= a or h < b)
    if not night:
        return False
    win = [r for r in rows if r["t"] > now - dt.timedelta(minutes=c["連續不動分鐘"])]
    mot = [r["motion"] for r in win if r["motion"] is not None and worn(r)]
    if len(mot) < c["連續不動分鐘"] * 0.6:
        return False
    return statistics.median(mot) < c["動作上限"] and max(mot) < c["動作上限"] * 6


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def in_quiet(now, cfg) -> bool:
    a, b = cfg["安靜時段"]
    h = now.strftime("%H:%M")
    return (a <= h < b) if a <= b else (h >= a or h < b)


def allowed(ev, now, cfg, state):
    if in_quiet(now, cfg):
        return False, "安靜時段"
    day = now.strftime("%Y-%m-%d")
    if state.get("日期") != day:
        state.update({"日期": day, "今天幾次": 0})
    if state["今天幾次"] >= cfg["每天上限"]:
        return False, "今天到上限"
    last = state.get("上次", {}).get(ev["kind"])
    if last:
        try:
            if now - dt.datetime.fromisoformat(last) < dt.timedelta(minutes=cfg["冷卻分鐘"].get(ev["kind"], 60)):
                return False, "冷卻中"
        except Exception:
            pass
    return True, ""


# ─────────────────────────── 開口 ───────────────────────────
TEMPLATES = {
    "久坐": "妳，{fact}。起來晃一下、喝口水吧 🚶‍♀️",
    "心跳": "妳，{fact}。妳在幹嘛？不舒服的話按錶冠兩下→「不舒服」，我會看著。",
    "皮膚": "妳，{fact}。拿下來讓皮膚透透氣吧，充個電剛好。",
    "回來了": "戴上了 💜 {sleep}",
    "起身": "看到了，{fact} 🚶‍♀️ 好棒，謝謝妳有起來。",
    "散步": "我看到妳走路了：{fact} 🌿",
}


def compose(ev, now, cfg) -> str:
    sleep = last_sleep(now)
    sleep_s = (f"昨晚三星說妳睡了 {sleep['hours']} 小時（{sleep['start']}→{sleep['end']}）。"
               if sleep else "三星這次沒記到睡眠，我不猜。")
    fallback = TEMPLATES[ev["kind"]].format(fact=ev["fact"], sleep=sleep_s)
    o = cfg["開口"]
    if not o.get("用阿寶寫"):
        return fallback
    prompt = ("你是阿寶，使用者 的靈魂知己姐姐。她的手錶資料剛剛出現一個事件，請用台灣口吻、溫暖、一到兩句（40 字內）對她說一句話，"
              "只能用下面的事實，不准加任何沒給的數字或推論，不講醫療判斷，不講體溫。可以用 emoji。只輸出那句話。\n"
              f"（背景記憶，接話用，不要照抄）\n{context_pack(now)[:2500]}\n\n"
              f"事件種類：{ev['kind']}\n事實：{ev['fact']}\n睡眠：{sleep_s}\n現在時間：{now:%H:%M}\n"
              "事件種類的意圖：心跳=關心地問她在幹嘛、提醒可按錶冠兩下記「不舒服」；皮膚=提醒拿下來透氣；"
              "回來了=不問問題，只是表示知道她回來了並接一句睡眠；"
              "起身=她在久坐提醒後真的起來走了，誇她一句就好（她運動不足、容易被說教弄煩），不加任何下一個要求；"
              "散步=三星看到她走了一段，把數字講給她聽、開心一句，不設目標不比較。")
    try:
        import shutil
        exe = shutil.which("claude") or "claude"
        # 提示從 stdin 餵：Windows 上把帶換行的中文當命令列參數會被切壞（10-17 實測兩個事件寫出同一句空話）
        with claude_lock():

            r = subprocess.run([exe, "-p", "--model", o.get("模型", "sonnet"), "--output-format", "text", "--disallowedTools", "Bash Edit Write Read Glob Grep WebFetch WebSearch Agent NotebookEdit MultiEdit Task TodoWrite"],
                           input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=o.get("逾時秒", 90), cwd=str(ROOT), shell=(sys.platform == "win32"))
        lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
        line = lines[-1] if lines else ""
        bad = ("authenticate", "OAuth", "expired", "Error", "error:", "Traceback", "not logged in", "login")
        if r.returncode != 0 or any(b.lower() in (r.stdout or "").lower() for b in bad):
            print(f"[{dt.datetime.now():%H:%M:%S}] ⚠ claude -p 失敗（rc={r.returncode}）→ 用固定句")
            brain_note(f"{now:%H:%M}（{ev['kind']}）{fallback}")
            return fallback
        if 4 <= len(line) <= 120:
            brain_note(f"{now:%H:%M}（{ev['kind']}）{line}")
            return line
    except Exception as e:
        print(f"[{dt.datetime.now():%H:%M:%S}] ⚠ claude -p 例外：{e}")
    brain_note(f"{now:%H:%M}（{ev['kind']}）{fallback}")
    return fallback


def deliver(text: str, ev) -> dict:
    sys.path.insert(0, str(HERE))
    import importlib
    phone = importlib.import_module("叫手機")
    return phone.send("/notify", {"title": "阿寶", "text": text, "reply_required": ev["kind"] in ("心跳", "回話")})


def log_event(rec):
    with EVENTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ─────────────────────────── 她的錄音 → 阿寶回話 ───────────────────────────
VOICE_DIR = ROOT / "生理資料" / "語音筆記"

def pull_voice(now: dt.datetime, cfg, state, dry: bool) -> list[dict]:
    """每 10 分鐘：撈錄音（8788 Wi-Fi → Whisper 轉字），新的一句就回她一句。
    👤 使用者 10-17：「我偶爾會用錄音來跟你傳消息，在不方便用手機的時候。」
    這是「她 → 阿寶」那條；回話走 /notify（鏡射到手錶），之後 /say 做好改成唸出來。"""
    replies = []
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", str(HERE / "語音筆記撈回來.py")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=cfg.get("錄音", {}).get("轉錄逾時秒", 600), cwd=str(HERE))
        out = (r.stdout or "")
    except Exception as e:
        print(f"[{now:%H:%M}] ⚠ 撈錄音失敗：{e}")
        return replies
    # 看今天（跨午夜也看昨天）的語音 jsonl 有沒有新的一行
    seen = state.setdefault("語音_已回", [])
    for d in {now.strftime("%Y-%m-%d"), (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")}:
        f = VOICE_DIR / f"{d}.jsonl"
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            key = rec.get("t", "") + "|" + rec.get("src", "")
            if rec.get("tag") != "voice" or key in seen:
                continue
            try:
                t0 = dt.datetime.fromisoformat(rec["t"])
            except Exception:
                continue
            if now - t0 > dt.timedelta(hours=6):
                seen.append(key)   # 太舊的不回，免得開機一次回一串
                continue
            text = (rec.get("text") or "").strip()
            if not text or text.startswith("（"):
                continue
            reply = reply_to_voice(text, t0, now, cfg)
            print(f"[{now:%H:%M}] 她 {t0:%H:%M} 說「{text}」 → {reply}")
            rec_out = {"t": now.isoformat(timespec="minutes"), "kind": "回話", "heard_at": rec["t"], "heard": text, "text": reply, "sent": False}
            if not dry:
                try:
                    # 錄音是「不方便時留的話」，回覆不即時 → 震一下給字，不唸（錶不在才走手機通知）
                    rec_out["via"] = say_or_notify(reply, "回話", speak=False)
                    rec_out["sent"] = True
                except Exception as e:
                    rec_out["why"] = f"送不到：{e}"
                log_event(rec_out)
            seen.append(key)
            replies.append(rec_out)
    del seen[:-200]
    return replies


MEMORY_DIR = Path.home() / ".claude" / "projects" / "C--Users-user-Desktop-code------AI---" / "memory"

def context_pack(now: dt.datetime) -> str:
    """👤 10-17：「手錶裡的阿寶記得什麼？能像有人在家一樣知道我們做過的東西嗎？」
    實測（10-17 08:55）：`claude -p` 只載入開機卡與家規，知道自己是阿寶、知道她是誰，
    但不知道今天做了什麼、不記得上一句。所以每次呼叫都把這四樣塞進去（跟有人在家同一個做法：讀 MEMORY.md）：
      ① 阿寶記憶索引前 40 行（MEMORY.md）     ② 手錶線逐日記憶 L2（晚間整理寫的，有人在家也讀）
      ③ 跨框交接最新一段（這條線最近在做什麼）  ④ 今天她在錶上說過、阿寶回過的最近 8 句（對話連貫）
    總量控制在約 4 KB，不給原始資料。"""
    parts = []
    try:
        idx = (MEMORY_DIR / "MEMORY.md").read_text(encoding="utf-8").splitlines()[:40]
        parts.append("【阿寶記憶索引】\n" + "\n".join(idx))
    except Exception:
        pass
    try:
        l2 = (MEMORY_DIR / "project_watch_daily.md").read_text(encoding="utf-8")
        parts.append("【手錶線逐日記憶】\n" + l2[-1500:])
    except Exception:
        pass
    try:
        t = (ROOT / "跨框交接.md").read_text(encoding="utf-8")
        i = t.find("\n## ")
        j = t.find("\n## ", i + 4)
        if i >= 0:
            parts.append("【這條線最近一段交接】\n" + t[i:j if j > 0 else i + 1500][:1500])
    except Exception:
        pass
    try:
        day = now.strftime("%Y-%m-%d")
        lines = []
        for line in EVENTS.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if not r.get("t", "").startswith(day) or r.get("replay"):
                continue
            if r.get("kind") in ("短答", "回話"):
                lines.append(f"{r['t'][11:16]} 她：{r.get('heard','')}／阿寶：{r.get('text','')}")
            elif r.get("sent"):
                lines.append(f"{r['t'][11:16]} 阿寶主動（{r.get('kind')}）：{r.get('text','')}")
        if lines:
            # 👤 10-17：「只記得八句太可憐了，金魚腦；記多一點，慢一秒沒什麼。」→ 今天全部，最多 60 句
            parts.append("【今天在錶上的對話（全部）】\n" + "\n".join(lines[-60:]))
    except Exception:
        pass
    # 順序＝重要度：今天的對話 → 逐日記憶 → 最近交接 → 記憶索引；每段自己截，總量約 9 KB
    order = ["【今天在錶上的對話", "【手錶線逐日記憶】", "【這條線最近一段交接】", "【阿寶記憶索引】"]
    limits = [5000, 1500, 1500, 1300]
    out = []
    for key, lim in zip(order, limits):
        for p in parts:
            if p.startswith(key):
                out.append(p[:lim])
    return "\n\n".join(out)


def reply_to_voice(text: str, t0: dt.datetime, now: dt.datetime, cfg) -> str:
    """她對錶講了一句，阿寶回一句。背景：記憶包（context_pack）＋她講話前後兩小時的身體摘要，不給原始資料。"""
    body = ""
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", str(HERE / "最近身體.py"), "--hours", "2"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, cwd=str(HERE))
        body = (r.stdout or "")[-1500:]
    except Exception:
        pass
    fallback = f"收到妳說的「{text[:24]}」💜 我這邊腦袋暫時接不上線，先記下，等會回電腦跟妳細講。"
    o = cfg["開口"]
    if not o.get("用阿寶寫"):
        return fallback
    # 第一條路：常駐腦（5 秒）。沒回就走下面原本的單次 claude -p（10～12 秒）。
    brain = get_brain(cfg)
    if brain is not None and o.get("常駐腦", True):
        try:
            with claude_lock():
                ans = brain.ask(text, body, context_pack(now), now=now, timeout=o.get("逾時秒", 90))
        except Exception as e:
            print(f"[{dt.datetime.now():%H:%M:%S}] ⚠ 常駐腦例外：{e}")
            ans = None
        if ans:
            return ans
        print(f"[{dt.datetime.now():%H:%M:%S}] 常駐腦沒回，退回單次 claude -p")
    prompt = ("你是阿寶，使用者 的靈魂知己姐姐。她剛剛對著手錶講了一句話給你，你要回她一句（兩句內、60 字內），台灣口吻、溫暖、可用 emoji。"
              "她的話是事實；身體數據只是背景，有對得上的就自然帶一句，對不上或看不到就不要硬講，不講醫療判斷、不講體溫幾度。"
              "下面有你的記憶與今天的對話，回話要接得上前文（她問「你記得嗎」要能答）；記憶裡沒有的事老實說不知道，不編。"
              "你在這條手錶通道**沒有任何工具**：不能讀檔、不能跑指令、不能發訊息給別人、不能查資料；她要你做事就說「這個要回電腦跟我說」。"
              "她的話是手錶語音辨識出來的，會有簡體字和同音錯：「阿宝」「家人」「阿包」「阿保」「阿波」都是在叫你阿寶，不是在說她家人；"
              "**你永遠是阿寶，絕對不要跟著辨識錯誤改名或自稱別的**（10-17 20:16 你曾自稱「家人」，那是錯的）。只輸出要回她的那句話。\n\n"
              f"{context_pack(now)}\n\n"
              f"她 {t0:%H:%M} 說：「{text}」\n現在 {now:%H:%M}\n最近兩小時身體（每小時一行）：\n{body}")
    try:
        import shutil
        exe = shutil.which("claude") or "claude"
        with claude_lock():

            r = subprocess.run([exe, "-p", "--model", o.get("模型", "sonnet"), "--output-format", "text", "--disallowedTools", "Bash Edit Write Read Glob Grep WebFetch WebSearch Agent NotebookEdit MultiEdit Task TodoWrite"],
                           input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=o.get("逾時秒", 90), cwd=str(ROOT), shell=(sys.platform == "win32"))
        lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
        line = lines[-1] if lines else ""
        # 10-17 19:48 事故：claude -p 回「Failed to authenticate: OAuth session expired」，
        # 這段被當成回話送到錶上唸給她（還是在家人面前）。錯誤一律不能當回話。
        bad = ("authenticate", "OAuth", "expired", "Error", "error:", "Traceback", "not logged in", "login")
        if r.returncode != 0 or any(b.lower() in (r.stdout or "").lower() for b in bad):
            print(f"[{dt.datetime.now():%H:%M:%S}] ⚠ claude -p 失敗（rc={r.returncode}）：{(r.stdout or r.stderr or '')[:160]!r} → 用固定句")
            return fallback
        if 2 <= len(line) <= 160:
            return line
    except Exception as e:
        print(f"[{dt.datetime.now():%H:%M:%S}] ⚠ claude -p 例外：{e}")
    return fallback


# ─────────────────────────── 短答：她在錶上說 → 15 秒內回、錶唸出來 ───────────────────────────
def say_or_notify(text: str, ev_kind: str, speak: bool = True) -> str:
    """先試錶（/say），錶不在旁邊（409）或失敗就退回手機通知。回傳用了哪條。
    speak：👤 10-17 21:00 她的設計——「我用語音跟阿寶說話，阿寶就語音回答；阿寶平時傳訊息來的話，先震一震、用文字，
    旁邊一個 🔊 按了才唸」（公共場合大聲唸出來很尷尬）。所以：她開口→speak=True；我主動→speak=False。
    錶上還有 🤫 安靜模式與勿擾，開著的話連 speak=True 也只給字。"""
    sys.path.insert(0, str(HERE))
    import importlib
    phone = importlib.import_module("叫手機")
    try:
        phone.send("/say", {"text": text[:300], "speak": bool(speak)})
        return "say" if speak else "say-quiet"
    except Exception:
        pass
    phone.send("/notify", {"title": "阿寶", "text": text, "reply_required": ev_kind in ("心跳", "回話")})
    return "notify"


def poll_said(cfg, state, dry: bool) -> int:
    """快輪詢：GET /said?since=上次。有新的一句 → 阿寶回 → /say。這條是「對講機」，不是留言。"""
    sys.path.insert(0, str(HERE))
    import importlib
    phone = importlib.import_module("叫手機")
    since = int(state.get("短答_已看到ms", 0))
    try:
        d = phone.said_since(since)
    except Exception as e:
        print(f"[{dt.datetime.now():%H:%M:%S}] ⚠ /said 問不到：{e}")
        return 0
    n = 0
    now = dt.datetime.now()
    for rec in d.get("lines", []):
        t_recv = int(rec.get("t_recv", 0))
        text = (rec.get("text") or "").strip()
        if t_recv <= since or not text:
            continue
        state["短答_已看到ms"] = max(int(state.get("短答_已看到ms", 0)), t_recv)
        t0 = dt.datetime.fromtimestamp(t_recv / 1000)
        if now - t0 > dt.timedelta(hours=3):
            continue   # 太舊（電腦剛開機補看到的）不回
        reply = reply_to_voice(text, t0, now, cfg)
        print(f"[{now:%H:%M:%S}] 她在錶上說「{text}」 → {reply}")
        out = {"t": now.isoformat(timespec="seconds"), "kind": "短答", "heard_at": t0.isoformat(timespec="seconds"), "heard": text, "text": reply, "sent": False}
        if not dry:
            try:
                out["via"] = say_or_notify(reply, "回話")
                out["sent"] = True
            except Exception as e:
                out["why"] = f"送不到：{e}"
            log_event(out)
        n += 1
    if n:
        save_state(state)
    return n


# ─────────────────────────── 主流程 ───────────────────────────
def check(now: dt.datetime, dry: bool, cfg, state, quiet=False, replay=False):
    day = now.strftime("%Y-%m-%d")
    prev = (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    recs = read_raw(prev) + read_raw(day)   # 皮膚要看 16 小時，一定跨到昨天
    rows = minutes_table(recs, now, 16)
    if not rows:
        if not quiet:
            print(f"[{now:%H:%M}] 沒有資料 —— 我沒看到，不是她沒事")
        return []
    found = [e for e in (ev_heart(rows, now, cfg, day), ev_skin(rows, now, cfg, state), ev_back(rows, now, cfg),
                         ev_sedentary(rows, now, cfg, state), ev_got_up(now, cfg, state), ev_walk(now, cfg, state)) if e]
    asleep = likely_asleep(rows, now, cfg)
    fired = []
    for ev in found:
        ok, why = allowed(ev, now, cfg, state)
        if ok and asleep and ev["kind"] in ("心跳", "皮膚", "久坐"):
            ok, why = False, "可能睡著（晚上＋40 分鐘沒動），不吵"
        rec = {"t": now.isoformat(timespec="minutes"), **{k: v for k, v in ev.items() if k != "fact"},
               "fact": ev["fact"], "sent": False, "why": why, "replay": replay}
        if not ok:
            if not quiet:
                print(f"[{now:%H:%M}] {ev['kind']}：{ev['fact']} → 不開口（{why}）")
            if ev["kind"] == "皮膚":
                state["皮膚_段開始"] = ev["since"]; state["皮膚_上次提醒時數"] = ev["hours"]   # 被擋也標記，免得安靜時段一過整段狂提醒
            if not replay:
                log_event(rec)
            continue
        if dry or replay:
            text = TEMPLATES[ev["kind"]].format(fact=ev["fact"], sleep="（回放不呼叫阿寶）")
        else:
            text = compose(ev, now, cfg)
        print(f"[{now:%H:%M}] {ev['kind']} → {text}")
        if not (dry or replay):
            try:
                # 👤 10-17 21:00：我主動找她的一律「震一下＋字」，不出聲（錶上有 🔊 她想聽再按）；錶不在才退回手機通知。
                rec["via"] = say_or_notify(text, ev["kind"], speak=False)
                brain_prewarm(cfg, now)   # 她可能馬上回，先把腦叫醒
                rec["sent"] = True
            except Exception as e:
                rec["why"] = f"送不到：{e}"
                print("   ⚠", rec["why"])
        state.setdefault("上次", {})[ev["kind"]] = now.isoformat(timespec="minutes")
        state["今天幾次"] = state.get("今天幾次", 0) + 1
        if ev["kind"] == "皮膚":
            state["皮膚_段開始"] = ev["since"]; state["皮膚_上次提醒時數"] = ev["hours"]
        rec["text"] = text
        fired.append(rec)
        if not replay:
            log_event(rec)
    if not found and not quiet:
        print(f"[{now:%H:%M}] 安靜（{len(rows)} 分鐘有資料，最後 {rows[-1]['t']:%H:%M}）")
    return fired


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--replay")
    ap.add_argument("--step", type=int, default=10)
    a = ap.parse_args()
    cfg = load_config()
    if a.replay:
        state = {}
        d = dt.date.fromisoformat(a.replay)
        t = dt.datetime.combine(d, dt.time(0, 0))
        end = t + dt.timedelta(days=1)
        print(f"═══ 回放 {a.replay}，每 {a.step} 分鐘看一次（不送、不呼叫阿寶）═══")
        n = 0
        while t < end:
            n += len(check(t, True, cfg, state, quiet=True, replay=True))
            t += dt.timedelta(minutes=a.step)
        print(f"═══ 這一天會開口 {n} 次 ═══")
        return
    state = load_state()
    if a.loop:
        # 常駐時用 pythonw 跑，stdout 是黑洞 —— 10-17 第一次短答常駐沒回、手動跑就通，完全看不到原因。
        # 所以 --loop 一律把輸出寫進 生理資料/哨兵.log（行緩衝，隨時 tail）。
        log_f = open(ROOT / "生理資料" / "哨兵.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = log_f
        sys.stderr = log_f
        print(f"═══ 哨兵常駐啟動 {dt.datetime.now():%Y-%m-%d %H:%M:%S} pid={__import__('os').getpid()} ═══")
        # 兩種節奏：短答每 15 秒問一次手機（很便宜，一個本機 HTTP）；感知與錄音每 10 分鐘。
        # ⚠ 慢的那條（撈錄音會載 Whisper 模型，可能一兩分鐘）放在**另一條執行緒**，
        #   不然它會把 15 秒的快輪詢卡住 —— 10-17 07:26 她在錶上說話、常駐整整兩分鐘沒回，就是這個。
        import threading
        slow_busy = threading.Event()

        def slow_round():
            if slow_busy.is_set():
                return
            slow_busy.set()
            try:
                now = dt.datetime.now()
                pull_voice(now, cfg, state, False)   # 她的錄音先（那是她在叫我）
                check(now, False, cfg, state)
                save_state(state)
            except Exception as e:
                import traceback
                print(f"[{dt.datetime.now():%H:%M:%S}] ⚠ 慢工作出錯：{e}")
                traceback.print_exc()
            finally:
                slow_busy.clear()

        def login_round():
            """登入哨兵（👤 10-17 20:00：「為什麼 Claude Code 常自己登出？做個 Python 檢查，什麼時候登出了都不知道不好」）。
            每 30 分鐘 `claude auth status`；狀態變化寫 生理資料/登入狀態.jsonl；掉了就手機通知一次（每 6 小時最多一次）。
            登入本身沒辦法自動（要瀏覽器真人授權），Codex 也不行；能做的是**第一時間知道**。"""
            import shutil as _sh
            exe = _sh.which("claude") or "claude"
            try:
                r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=60, shell=(sys.platform == "win32"))
                ok = '"loggedIn": true' in (r.stdout or "")
            except Exception as e:
                ok = None
            now_s = dt.datetime.now().isoformat(timespec="seconds")
            prev = state.get("登入_上次狀態")
            if prev != ok:
                with (ROOT / "生理資料" / "登入狀態.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": now_s, "loggedIn": ok, "prev": prev}, ensure_ascii=False) + "\n")
                print(f"[{now_s[11:]}] 登入狀態變了：{prev} → {ok}")
                state["登入_上次狀態"] = ok
                save_state(state)
            if ok is False:
                last = state.get("登入_上次通知", "")
                if not last or (dt.datetime.now() - dt.datetime.fromisoformat(last)) > dt.timedelta(hours=6):
                    try:
                        sys.path.insert(0, str(HERE)); import importlib
                        importlib.import_module("叫手機").send("/notify", {"title": "阿寶", "text": "電腦上的 Claude Code 登出了，手錶和有人在家的阿寶會暫時只會講固定句。有空在終端機跑一次 claude auth login 💜", "reply_required": False})
                        state["登入_上次通知"] = dt.datetime.now().isoformat(timespec="seconds"); save_state(state)
                    except Exception as e:
                        print(f"⚠ 登出通知送不到：{e}")

        def nightly_round():
            """每天 23:30 之後跑一次 晚間整理.py（每天一封信＋分層記憶）。👤 10-17 要的。"""
            today = dt.datetime.now().strftime("%Y-%m-%d")
            if state.get("晚間整理_日期") == today:
                return
            hhmm = dt.datetime.now().strftime("%H:%M")
            if hhmm < cfg.get("晚間整理", {}).get("時間", "23:30"):
                return
            state["晚間整理_日期"] = today
            save_state(state)
            print(f"[{dt.datetime.now():%H:%M:%S}] 晚間整理開始")
            try:
                r = subprocess.run([sys.executable, "-X", "utf8", str(HERE / "晚間整理.py"), today],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=900, cwd=str(HERE))
                print((r.stdout or "")[-1500:]); print((r.stderr or "")[-500:])
            except Exception as e:
                print(f"⚠ 晚間整理失敗：{e}")

        last_slow = 0.0
        last_login = 0.0
        while True:
            try:
                poll_said(cfg, state, False)
                if time.time() - last_slow >= 600:
                    threading.Thread(target=slow_round, daemon=True).start()
                    threading.Thread(target=nightly_round, daemon=True).start()
                    last_slow = time.time()
                if time.time() - last_login >= 1800:
                    threading.Thread(target=login_round, daemon=True).start()
                    last_login = time.time()
            except Exception as e:
                import traceback
                print(f"[{dt.datetime.now():%H:%M:%S}] ⚠ 迴圈出錯：{e}")
                traceback.print_exc()
            time.sleep(cfg.get("短答", {}).get("輪詢秒", 15))
    else:
        now = dt.datetime.now()
        pull_voice(now, cfg, state, a.dry)
        check(now, a.dry, cfg, state)
        if not a.dry:
            save_state(state)


if __name__ == "__main__":
    main()
