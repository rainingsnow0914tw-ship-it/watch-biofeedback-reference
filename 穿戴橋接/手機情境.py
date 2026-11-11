"""資料從哪來：手機 app（code13）PhoneContext 每次狀態改變寫一行到手機的 手機情境/<日期>.jsonl，
watch_puller 每 10 分鐘 GET /context?d=<日期> 拉回 生理資料/手機情境/<日期>.jsonl（整檔覆蓋，手機那份是真相）。

每一行都帶完整現況 {t(ms), kind, home, wifi, ip, screen, battery, charging}，
kind ∈ start / home / away / wifi / screen_on / screen_off / unlock / charge_on / charge_off / tick / config

給誰用："""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "生理資料" / "手機情境"


_HOME_CACHE: dict = {}


def places() -> dict[str, list[str]]:
    """ """
    cfg = ROOT / "生理資料" / "哨兵設定.json"
    default = {"家": ["192.168.0."], "公司": ["192.168.1."]}
    try:
        mt = cfg.stat().st_mtime
        if _HOME_CACHE.get("mt") != mt:
            _HOME_CACHE["mt"] = mt
            _HOME_CACHE["v"] = json.loads(cfg.read_text(encoding="utf-8")).get("地點網段") or default
        return _HOME_CACHE["v"]
    except Exception:
        return default


def place_of(ip) -> str | None:
    """ip → 地點名；不在任何已知網段回 None（＝出門／別處）。"""
    if not isinstance(ip, str) or not ip:
        return None
    for name, pfxs in places().items():
        if any(ip.startswith(p) for p in pfxs):
            return name
    return None


def load(day: str) -> list[dict]:
    f = DIR / f"{day}.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "t" in r:
            r["dt"] = dt.datetime.fromtimestamp(r["t"] / 1000)


            r["home_phone_said"] = r.get("home")
            r["place"] = place_of(r.get("ip"))
            r["home"] = r["place"] is not None
            out.append(r)
    out.sort(key=lambda r: r["t"])
    return out


def load_range(since: dt.datetime, until: dt.datetime) -> list[dict]:
    days = sorted({(since + dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range((until - since).days + 2)})
    return [r for d in days for r in load(d) if since <= r["dt"] <= until]


def latest(now: dt.datetime | None = None) -> dict | None:
    """最後一筆（今天沒有就看昨天）。回 None = 手機還沒裝 code13 或沒拉到。"""
    now = now or dt.datetime.now()
    for d in (now.strftime("%Y-%m-%d"), (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")):
        rows = load(d)
        if rows:
            return rows[-1]
    return None


def state_at(t: dt.datetime, rows: list[dict] | None = None) -> dict | None:
    """t 那一刻的狀態＝t 之前最後一筆。rows 不給就自己載兩天。"""
    if rows is None:
        rows = load((t - dt.timedelta(days=1)).strftime("%Y-%m-%d")) + load(t.strftime("%Y-%m-%d"))
    prev = None
    for r in rows:
        if r["dt"] <= t:
            prev = r
        else:
            break
    return prev


def window_stats(t0: dt.datetime, t1: dt.datetime, rows: list[dict] | None = None) -> dict:
    """一段時間裡：螢幕亮了幾分鐘、解鎖幾次、在家比例、最後電量、有沒有資料。
    螢幕分鐘＝把 screen 狀態沿時間積分（開頭狀態取 t0 之前最後一筆）。"""
    if rows is None:
        rows = load((t0 - dt.timedelta(days=1)).strftime("%Y-%m-%d")) + load(t0.strftime("%Y-%m-%d")) + \
               ([] if t1.date() == t0.date() else load(t1.strftime("%Y-%m-%d")))
    rows = [r for r in rows if r["dt"] <= t1]
    before = state_at(t0, rows)
    inside = [r for r in rows if t0 < r["dt"] <= t1]
    if before is None and not inside:
        return {"known": False}

    if not inside and before is not None and (t0 - before["dt"]) > dt.timedelta(hours=2):
        return {"known": False}
    MAX_HOLD = 2 * 3600.0
    screen_sec = home_sec = 0.0; unknown_sec = 0.0
    cur_screen = bool(before and before.get("screen"))
    cur_home = bool(before and before.get("home"))
    cursor = t0
    if before is None:
        cursor = inside[0]["dt"]; t0 = cursor
        cur_screen, cur_home = bool(inside[0].get("screen")), bool(inside[0].get("home"))
    for r in inside:
        span = (r["dt"] - cursor).total_seconds()
        held = min(span, MAX_HOLD); unknown_sec += span - held
        if cur_screen: screen_sec += held
        if cur_home: home_sec += held
        cur_screen, cur_home = bool(r.get("screen")), bool(r.get("home"))
        cursor = r["dt"]
    span = (t1 - cursor).total_seconds()
    held = min(span, MAX_HOLD); unknown_sec += span - held
    if cur_screen: screen_sec += held
    if cur_home: home_sec += held
    total = max((t1 - t0).total_seconds(), 1)
    observed = max(0.0, 1.0 - unknown_sec / total)

    if before is None and inside and (t1 - inside[0]["dt"]).total_seconds() < total * 0.5:
        observed = min(observed, (t1 - inside[0]["dt"]).total_seconds() / max((t1 - t0).total_seconds(), 1))
    last = inside[-1] if inside else before
    return {
        "known": True,
        "screen_min": round(screen_sec / 60),
        "unlocks": sum(1 for r in inside if r.get("kind") == "unlock"),
        "home_ratio": round(home_sec / total, 2),
        "observed_ratio": round(observed, 2),   # 這段時間裡有多少比例是真的有狀態可依（<0.5 別拿在家／出門下整段結論）
        "home": cur_home,
        "place": (last.get("place") if last else None),
        "battery": last.get("battery", -1),
        "charging": bool(last.get("charging")),
        "events": len(inside),
        "activity": last.get("activity") if last.get("activity_on") else None,
        "vehicle": any(r.get("state") == "in_vehicle" for r in inside if r.get("kind") == "activity"),
    }


ACT_ZH = {"still": "靜止", "walking": "走路", "running": "跑步", "in_vehicle": "🚗車上", "on_bicycle": "🚲騎車", "on_foot": "步行", "unknown": "？"}


def activity_at(t: dt.datetime, rows: list[dict] | None = None) -> str | None:
    """t 那一刻 Play 服務最後說的活動（still/walking/…）；沒資料或 app 沒登記 → None（＝我沒看到）。"""
    r = state_at(t, rows)
    if not r or not r.get("activity_on"):
        return None
    return r.get("activity")


def activities(t0: dt.datetime, t1: dt.datetime, rows: list[dict] | None = None) -> list[dict]:
    """一段時間裡的活動轉換事件（kind=activity）。"""
    if rows is None:
        rows = load((t0 - dt.timedelta(days=1)).strftime("%Y-%m-%d")) + load(t0.strftime("%Y-%m-%d")) + \
               ([] if t1.date() == t0.date() else load(t1.strftime("%Y-%m-%d")))
    return [r for r in rows if r.get("kind") == "activity" and t0 < r["dt"] <= t1]


def in_vehicle(now: dt.datetime, minutes: int = 30, rows: list[dict] | None = None) -> bool | None:
    """ """
    if rows is None:
        rows = load((now - dt.timedelta(days=1)).strftime("%Y-%m-%d")) + load(now.strftime("%Y-%m-%d"))
    cur = activity_at(now, rows)
    if cur is None and not any(r.get("activity_on") for r in rows[-5:]):
        return None
    if cur == "in_vehicle":
        return True
    return any(r.get("state") == "in_vehicle" for r in activities(now - dt.timedelta(minutes=minutes), now, rows))


def screen_on_within(now: dt.datetime, minutes: int = 10, rows: list[dict] | None = None) -> bool | None:
    """最近 N 分鐘內手機螢幕有沒有亮過：有 screen_on／unlock 事件，或現在就是亮的（且最後一筆不超過 2 小時舊）。
    None＝沒資料。給睡覺閘門用：亮過 → 不算睡著。"""
    if rows is None:
        rows = load((now - dt.timedelta(days=1)).strftime("%Y-%m-%d")) + load(now.strftime("%Y-%m-%d"))
    rows = [r for r in rows if r["dt"] <= now]
    if not rows:
        return None
    last = rows[-1]
    if (now - last["dt"]) > dt.timedelta(hours=2):
        return None
    since = now - dt.timedelta(minutes=minutes)
    if any(r.get("kind") in ("screen_on", "unlock") and r["dt"] > since for r in rows):
        return True
    return bool(last.get("screen"))


def fmt_hour(st: dict) -> str:
    """最近身體.py 那一欄用的短字串。"""
    if not st.get("known"):
        return "手機 —"
    where = (st.get("place") or "在家") if st["home_ratio"] >= 0.5 else "出門"
    if where == "家": where = "在家"
    if 0.2 < st["home_ratio"] < 0.8:
        where = "進出"
    bat = f"🔋{st['battery']}%" if st.get("battery", -1) >= 0 else ""
    bat += "⚡" if st.get("charging") else ""
    act = ACT_ZH.get(st.get("activity") or "", "")
    if st.get("vehicle") and "車" not in act:
        act = (act + " 🚗坐過車").strip()
    return f"{where} 亮{st['screen_min']:>2}分 解鎖{st['unlocks']:>2} {bat}" + (f" {act}" if act else "")


KIND_ZH = {"start": "app 啟動", "home": "🏠 回到家", "away": "🚪 出門了", "wifi": "Wi-Fi 變了", "screen_on": "螢幕亮",
           "screen_off": "螢幕暗", "unlock": "解鎖", "charge_on": "插電", "charge_off": "拔電", "tick": "·", "config": "設定",
           "activity": "活動", "activity_on": "活動辨識開了"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--now", action="store_true", help="只印最後一筆")
    a = ap.parse_args()
    now = dt.datetime.now()
    if a.now:
        r = latest(now)
        if not r:
            print("⚠ 沒有手機情境紀錄（手機要裝 code13、watch_puller 要拉過 /context）"); return
        age = (now - r["dt"]).total_seconds() / 60
        print(f"{r['dt']:%m-%d %H:%M}（{age:.0f} 分鐘前）{KIND_ZH.get(r['kind'], r['kind'])}  "
              f"{(r.get('place') or '不在崗位')}  螢幕{'亮' if r.get('screen') else '暗'}  "
              f"🔋{r.get('battery')}%{'⚡' if r.get('charging') else ''}  ip={r.get('ip')}"
              + (f"  活動={ACT_ZH.get(r.get('activity') or '', '？')}" if r.get('activity_on') else "  活動=（沒登記）"))
        return
    if a.day:
        rows = load(a.day); title = f"{a.day} 整天"
    else:
        rows = load_range(now - dt.timedelta(hours=a.hours), now); title = f"最近 {a.hours:g} 小時"
    print(f"═══ 手機情境：{title} ═══")
    if not rows:
        print("  ⚠ 沒有紀錄 —— 是「我沒看到」（沒裝 code13／沒拉到），不是她沒用手機。"); return
    for r in rows:
        if r["kind"] == "tick":
            continue
        print(f"  {r['dt']:%H:%M:%S}  {KIND_ZH.get(r['kind'], r['kind']):<8}  "
              f"{'🏠' if r.get('home') else '🚪'} {'📱亮' if r.get('screen') else '📱暗'}  🔋{r.get('battery')}%{'⚡' if r.get('charging') else ''}"
              + (f"  → {ACT_ZH.get(r.get('state') or '', '不知道')}（{r.get('transition')} {r.get('what')}）" if r.get('kind') == 'activity' else "")
              + ("  （看門狗補的）" if r.get("missed") else ""))
    st = window_stats(rows[0]["dt"], now, rows)
    print(f"\n  整段：{fmt_hour(st)}（{st.get('events', 0)} 筆事件）")


if __name__ == "__main__":
    main()
