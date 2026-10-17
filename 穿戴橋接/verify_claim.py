#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
verify_claim.py —— 求證層：拿身體資料去對她說的話

這是整個專案真正的目的。使用者 2026-08-12 自己舉的例子：

    「你問我說 使用者 妳今天有沒有去健身房？我說有啊！
      然後你就可以看看我的心率 —— 你說你騙我？
      你今天心率都那麼穩定怎麼可能有去運動？」

而她更早說過為什麼要這樣：
    「我不在意被我的 AI 抓包，大家都要誠實；
      人要求 AI 誠實又不要亂猜，那總要給他多點的背景數據。」

用法：
    python verify_claim.py 運動 --hours 3
    python verify_claim.py 睡覺 --hours 8
    python verify_claim.py 休息 --hours 2

設計上的三個堅持：
    ① **只回三種結論**：對得上／對不上／資料不足。
       「沒看到證據」跟「證明沒有」是兩件事，混在一起就是在亂猜。
    ② **一定附證據**。不能只說「你騙我」，要說「你那三小時心率最高 78，
       而你平常那個時段就在 71~96 之間」——她要能自己判斷我對不對。
    ③ **先檢查覆蓋率**。那段時間根本沒量到，就不能說「沒有運動」。
       手錶脫下來充電、app 被殺掉、Wi-Fi 斷線 —— 在資料上長得跟「沒動」一模一樣。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from hc_baseline import DATA_DIR, bucket_of, build, load_series


def window(records: list[dict], hours: float) -> list[dict]:
    cutoff = datetime.now().astimezone() - timedelta(hours=hours)
    return [r for r in records if r["_dt"] >= cutoff]


def coverage_ok(recs: list[dict], hours: float, need: float = 0.34) -> tuple[bool, str]:
    """
    這段時間到底有沒有在量。

    need=0.34 的意思是：至少三分之一的小時有資料才敢下判斷。
    這個門檻是刻意保守的 —— 寧可說「不知道」，也不要因為手錶在充電
    就指控她說謊。
    """
    if not recs:
        return False, "這段時間完全沒有量到資料"
    hrs = {r["_dt"].replace(minute=0, second=0, microsecond=0) for r in recs}
    ratio = len(hrs) / max(1.0, hours)
    if ratio < need:
        return False, (f"這段時間只有 {len(hrs)} 個小時有資料"
                       f"（共 {hours:g} 小時），太少，不下判斷")
    return True, f"{len(hrs)}/{hours:g} 小時有資料"


def baseline_band(baseline: dict, dtype: str, dt: datetime) -> dict | None:
    info = baseline.get("types", {}).get(dtype)
    # growing / quality_unverified 不能當「她的平常」來反駁真人記憶。
    if not info or info.get("status") != "mature":
        return None
    b = info.get("buckets", {}).get(str(bucket_of(dt)))
    if b and b["n"] >= 20:
        return b
    return info.get("overall")


# ── 各種說法怎麼查 ─────────────────────────────────────────────────
def check_exercise(hours: float, baseline: dict) -> dict:
    """
    「我去運動了」

    運動的特徵不是「心率高」，是**心率明顯高過她自己那個時段的平常**，
    而且持續一段時間。所以拿的是她的基線，不是教科書上的公式。
    """
    hr = window(load_series("heart_rate"), hours)
    ok, cov = coverage_ok(hr, hours)
    if not ok:
        return {"verdict": "資料不足", "reason": cov}

    peak = max(r["bpm"] for r in hr if isinstance(r.get("bpm"), (int, float)))
    band = baseline_band(baseline, "heart_rate", datetime.now().astimezone())
    if band is None:
        return {"verdict": "資料不足", "reason": "心率基線還沒長好"}

    # 運動階段（sessions）有的話最直接
    ex = window(load_series("exercise"), hours) + \
         window(load_series("exercise_session"), hours)

    steps = window(load_series("steps"), hours)
    step_sum = sum(r.get("count", 0) for r in steps if isinstance(r.get("count"), (int, float)))

    if ex:
        return {"verdict": "對得上", "reason": f"有 {len(ex)} 筆運動紀錄",
                "evidence": f"心率最高 {peak}，覆蓋 {cov}"}

    if peak > band["p90"]:
        return {"verdict": "對得上",
                "reason": f"心率最高衝到 {peak}，超過妳那個時段的 p90（{band['p90']}）",
                "evidence": f"覆蓋 {cov}；步數 {step_sum}"}

    return {"verdict": "對不上",
            "reason": (f"這 {hours:g} 小時心率最高只有 {peak}，"
                       f"而妳那個時段平常就在 {band['p10']}~{band['p90']} 之間"),
            "evidence": f"覆蓋 {cov}；步數 {step_sum}",
            "說法": (f"妳說有運動，可是這段時間心率最高才 {peak}，"
                     f"跟妳平常坐著的時候（中位 {band['p50']}）差不多。"
                     f"是不是強度比較低，還是手錶沒戴？")}


def check_sleep(hours: float, baseline: dict) -> dict:
    """
    「我剛睡過了」

    先看有沒有睡眠紀錄 —— 那是最硬的證據。
    沒有紀錄時心率只當第二意見；高心率不能否定她睡過。
    """
    now = datetime.now().astimezone()
    cutoff = now - timedelta(hours=hours)
    sleeps = [r for r in load_series("sleep") if r["_dt"] >= cutoff] + \
             [r for r in load_series("sleep_stage") if r["_dt"] >= cutoff]
    if sleeps:
        return {"verdict": "對得上",
                "reason": f"這 {hours:g} 小時內有 {len(sleeps)} 筆睡眠紀錄"}

    hr = window(load_series("heart_rate"), hours)
    ok, cov = coverage_ok(hr, hours)
    if not ok:
        return {"verdict": "資料不足", "reason": cov}

    band = baseline_band(baseline, "heart_rate", now)
    if band is None:
        return {"verdict": "資料不足", "reason": "心率基線還沒長好"}

    lowest = min(r["bpm"] for r in hr if isinstance(r.get("bpm"), (int, float)))
    if lowest <= band["p10"]:
        return {"verdict": "說不準",
                "reason": (f"沒有睡眠紀錄，但心率最低到 {lowest}，"
                           f"落在妳的低段（p10={band['p10']}）—— 可能只是躺著休息"),
                "evidence": f"覆蓋 {cov}"}

    return {"verdict": "說不準",
            "reason": (f"這 {hours:g} 小時沒有睡眠紀錄，心率最低 {lowest} "
                       f"未落在已觀測低段（p10={band['p10']}）；"
                       "這仍不能否定睡眠，可能沒戴、沒同步或系統漏判"),
            "evidence": f"覆蓋 {cov}",
            "說法": ("我沒有找到可確認的睡眠段；可能是手錶沒戴、資料沒同步或三星沒判到。"
                     "先保留妳記得的睡眠，不拿空白反駁妳。")}


def check_rest(hours: float, baseline: dict) -> dict:
    """「我在休息／我很放鬆」—— 看心率有沒有落在她的低段。"""
    hr = window(load_series("heart_rate"), hours)
    ok, cov = coverage_ok(hr, hours)
    if not ok:
        return {"verdict": "資料不足", "reason": cov}
    band = baseline_band(baseline, "heart_rate", datetime.now().astimezone())
    if band is None:
        return {"verdict": "資料不足", "reason": "心率基線還沒長好"}
    vals = [r["bpm"] for r in hr if isinstance(r.get("bpm"), (int, float))]
    avg = sum(vals) / len(vals)
    if avg <= band["p50"]:
        return {"verdict": "對得上",
                "reason": f"平均心率 {avg:.0f}，低於妳那個時段的中位（{band['p50']}）",
                "evidence": f"覆蓋 {cov}"}
    return {"verdict": "對不上",
            "reason": (f"平均心率 {avg:.0f}，高於妳那個時段的中位（{band['p50']}）"),
            "evidence": f"覆蓋 {cov}",
            "說法": f"妳說在休息，但這段時間平均心率 {avg:.0f}，比妳平常這時候還高一點。"}


CHECKS = {
    "運動": check_exercise, "健身": check_exercise, "exercise": check_exercise,
    "睡覺": check_sleep, "睡了": check_sleep, "sleep": check_sleep,
    "休息": check_rest, "放鬆": check_rest, "rest": check_rest,
}


def main() -> None:
    ap = argparse.ArgumentParser(description="拿身體資料對照她說的話")
    ap.add_argument("claim", help="運動 / 睡覺 / 休息")
    ap.add_argument("--hours", type=float, default=3.0, help="往回看幾小時")
    args = ap.parse_args()

    fn = CHECKS.get(args.claim)
    if fn is None:
        print(f"還不會查「{args.claim}」。目前會的：{'、'.join(sorted(set(CHECKS)))}")
        sys.exit(2)

    result = fn(args.hours, build())

    icon = {"對得上": "✓", "對不上": "✗", "資料不足": "?", "說不準": "~"}
    print()
    print(f"  說法：我{args.claim}了（往回看 {args.hours:g} 小時）")
    print(f"  結論：{icon.get(result['verdict'], ' ')} {result['verdict']}")
    print(f"  依據：{result['reason']}")
    if result.get("evidence"):
        print(f"  資料：{result['evidence']}")
    if result.get("說法"):
        print(f"\n  可以這樣跟她說：\n    「{result['說法']}」")
    print()


if __name__ == "__main__":
    main()
