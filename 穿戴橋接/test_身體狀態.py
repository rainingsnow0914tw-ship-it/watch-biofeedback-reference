"""跑法：python -X utf8 test_身體狀態.py（全部 PASS 才重啟哨兵）"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import 身體狀態 as b

ok = fail = 0


def check(name, cond, info=""):
    global ok, fail
    ok += bool(cond); fail += not cond
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))


NOW = dt.datetime(2026, 11, 8, 20, 0)
DAYS = {}   # day → [窗]


def win(day, hh, mm, v, raw=True):
    DAYS.setdefault(day, []).append({"t": dt.datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}"), "rmssd": v, "raw": raw, "conf": 0.9})


b.day_windows = lambda day: DAYS.get(day, [])


def reset():
    DAYS.clear()


def base_days(n_days=4, per=10, v=20.0, raw=True):
    for i in range(1, n_days + 1):
        d = (NOW.date() - dt.timedelta(days=i)).isoformat()
        for k in range(per):
            win(d, 10, k * 5 % 60, v + (k % 3) - 1, raw)


# ── 基準 ──
reset(); base_days(1, 40)
r = b.baseline(NOW, True)
check("只有 1 天 → 基準還在累積（要 ≥2 天）", not r["ok"] and "累積" in r["why"], r)
reset(); base_days(2, 10)
r = b.baseline(NOW, True)
check("2 天但只有 20 窗 → 不夠（要 ≥30）", not r["ok"], r)
reset(); base_days(4, 10)
check("4 天 40 窗 → 基準 OK、中位 ≈20", b.baseline(NOW, True)["ok"] and abs(b.baseline(NOW, True)["median"] - 20) < 1.1)
reset(); base_days(4, 10, v=37.0, raw=False); base_days(1, 5, v=20.0, raw=True)
r = b.baseline(NOW, True)
check("★ 舊錶（37 ms）不混進新錶的基準", not r["ok"] or r["median"] < 25, r)

# ── 累不累 ──
reset(); base_days(4, 10)
for m in range(0, 30, 5):
    win(NOW.date().isoformat(), 18, m, 14.0)
r = b.tired(NOW)
check("★ 比基準低 30% → 累（可信度高：6 窗、4 天）", r["level"] == "累" and r["conf"] == "高" and "低 30%" in r["line"], r)
check("  說法標明「跟她自己比，不是醫療判斷」", "不是醫療判斷" in r["line"], r["line"])
reset(); base_days(4, 10)
for m in range(0, 15, 5):
    win(NOW.date().isoformat(), 18, m, 20.5)
r = b.tired(NOW)
check("跟基準差不多 → 普通（3 窗 → 可信度中）", r["level"] == "普通" and r["conf"] == "中", r)
reset(); base_days(4, 10)
for m in range(0, 15, 5):
    win(NOW.date().isoformat(), 18, m, 25.0)
check("比基準高 25% → 不錯", b.tired(NOW)["level"] == "不錯")
reset(); base_days(4, 10)
win(NOW.date().isoformat(), 18, 0, 10.0)
r = b.tired(NOW)
check("★ 最近只有 1 個窗 → 看不到（不准猜）", r["level"] is None and "看不到" in r["line"], r)
reset(); base_days(1, 40)
for m in range(0, 15, 5):
    win(NOW.date().isoformat(), 18, m, 14.0)
r = b.tired(NOW)
check("基準不夠 → 有數字但不判累不累", r["level"] is None and "先不判斷" in r["line"], r)
reset(); base_days(4, 10)
DAYS[(NOW.date() - dt.timedelta(days=1)).isoformat()] *= 20   # 一天佔八成以上
for m in range(0, 30, 5):
    win(NOW.date().isoformat(), 18, m, 14.0)
check("基準偏在某一天 → 可信度降一級", b.tired(NOW)["conf"] == "中")

# ── 身體電量公式 ──
SES, RHR = {}, {}
b.sleep_sessions = lambda day: SES.get(day, [])
b.resting_hr = lambda a, c: RHR.get(a.date().isoformat())


def sess(day, hours):
    d0 = dt.datetime.fromisoformat(day)
    SES[day] = [{"start": d0 + dt.timedelta(hours=5), "end": d0 + dt.timedelta(hours=5 + hours), "asleep_seconds": int(hours * 3600)}]


D = "2026-11-08"
reset(); base_days(4, 10)
for k in range(6):
    win(D, 8, k * 5, 20.0)     # 夜裡 HRV＝基準
sess(D, 7)
RHR.update({D: 62})
for i in range(1, 4):
    RHR[(dt.date.fromisoformat(D) - dt.timedelta(days=i)).isoformat()] = 62
r = b.body_battery(D)
check("★ 睡 7 小時、HRV＝平常、靜息心率＝平常 → 50", r["ok"] and r["score"] == 50, r)
sess(D, 9); RHR[D] = 57
DAYS[D] = []
for k in range(6):
    win(D, 8, k * 5, 25.0)     # 高 25%
r = b.body_battery(D)
check("★ 睡 9 小時、HRV 高 25%、靜息心率低 5 → 100", r["ok"] and r["score"] == 100, r)
sess(D, 5); RHR[D] = 67
DAYS[D] = []
for k in range(6):
    win(D, 8, k * 5, 15.0)     # 低 25%
r = b.body_battery(D)
check("睡 5 小時、HRV 低 25%、靜息心率高 5 → 0", r["ok"] and r["score"] == 0, r)
check("  理由句講出每一項", all(w in r["line"] for w in ("睡 5.0 小時", "HRV 比平常低 25%", "靜息心率 67")), r["line"])
SES.pop(D); DAYS[D] = []; RHR.pop(D)
r = b.body_battery(D)
check("★ 三項都看不到 → 不給數字、說看不到", not r["ok"] and "看不到" in r["line"] and "score" not in r, r)
sess(D, 6)
r = b.body_battery(D)
check("只看得到睡眠一項 → 還是不給數字（要 ≥2 項）", not r["ok"], r)
RHR[D] = 60
r = b.body_battery(D)
check("睡眠＋靜息心率兩項 → 給數字，HRV 那項說沒量到", r["ok"] and "夜裡 HRV 沒量到" in r["line"], r)
check("分數夾在 0～100", 0 <= r["score"] <= 100, r)

print(f"\n{ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
