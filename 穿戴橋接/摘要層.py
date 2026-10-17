#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
摘要層 —— 把一天 7 萬筆原始資料，壓成 24 筆小時摘要 + 1 筆日摘要。

## 規格與理由都在 `摘要層-規格.md`，這裡只重複三條最要命的

### 一、`cover` 一定放最前面，而且沒有它就不准讀數值

```
hr.p50 = 68  配 hr_samples = 1840  →  「她心率 68」
hr.p50 = 68  配 hr_samples = 3     →  「我幾乎不知道」
```
**兩個長得一模一樣，意思完全相反。**

### 二、沒資料的小時／沒資料的日子，**還是要有一筆**

使用者 的原話：「正常的數據也不能全部刪除…**但是沒了、變斷層也很奇怪**。」

她說的是這個專案的頭號病：**「沒有資料」跟「一切正常」長得一樣。**
發作過至少四次（triage 拿最後一筆當現在、動作量缺席回綠燈、
離腕的錶比睡著的人靜、補送餓死 87,050 筆）。

**所以有洞的時候，洞自己要出現在清單上。**

### 三、現在還不准說「正常」

查過別人怎麼做（`別人怎麼做-三個問題的答案.md`）：
```
Fitbit 靜息心率  30 天基線 ±2 標準差
Fitbit 準備度    最少 7 晚，完整 30 天
Samsung 活力分數 官方建議戴滿 7 天
Apple 高心率     ★ 不用基線，固定門檻 120 bpm ＋ 靜止 10 分鐘閘門
```

**Apple 那條是關鍵：個人基線是從歷史算的，而歷史可能已經是不正常的。**
一個心臟不好的人，他的「個人基線」本來就偏高 —— 用它當門檻等於把病當成常態。

所以這一版**只用固定門檻**，而且 verdict 的字彙裡**沒有「正常」** ——
最好的結果叫 `ok`，意思是「沒有踩到任何固定門檻」，不是「她很健康」。

等累積滿 30 天再談基線，那是下一版的事。

用法：
    python 摘要層.py                 # 今天
    python 摘要層.py 2026-08-20      # 指定
    python 摘要層.py --全部          # 所有有資料的日子
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from 覆蓋率 import classify

HERE = Path(__file__).resolve().parent
BIO = HERE.parent / "生理資料" / "手錶橋接"
OUT = HERE.parent / "生理資料" / "摘要"

# ── 固定門檻（Apple 式：不用個人基線）─────────────────────
HR_HIGH = 120        # Apple 高心率通知的預設值
HR_LOW = 40          # 低於這個一律看一眼
STILL_MOTION = 25    # 動作量低於此視為「幾乎沒動」（v4 指標，**尚未校準**）

MIN_HR_SAMPLES = 30      # 一小時少於這麼多筆有效心率，數值不可信
MIN_WORN_MINUTES = 10    # 戴不到這麼久，這小時不做身體判斷

BAR = {"ok": "·", "flag": "!", "off": "_", "charging": "+",
       "no_data": "x", "unknown": "?", "insufficient": "?", "partial": "?"}


def T(r: dict) -> datetime:
    return datetime.fromisoformat(r["t"])


def load(day: str) -> list[dict]:
    f = BIO / f"{day}.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _worn(r: dict) -> bool:
    return str(r.get("on_body")) in ("on", "inferred_on", "True")


def round_summary(value: float, digits: int) -> float:
    """Decimal HALF_EVEN shared with Kotlin Summary.kt.

    Python round(float, n) and Java Math.round(float * scale) disagree at
    binary/decimal ties; a named decimal rule keeps both device paths aligned.
    """
    unit = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(unit, rounding=ROUND_HALF_EVEN))


# ── 溫度第二意見 ────────────────────────────────────────────
# ⚠ 2026-08-25 改：**溫差是主訊號，絕對溫度只當離譜值的煞車。**
#
#   舊版把絕對皮膚溫的門檻設在 32.5，結果她在**冷氣餐廳**（皮膚 32.01、
#   溫差 +0.98）被判成「不確定」—— 而她當時明明戴著。
#   原因很簡單：**絕對皮膚溫完全跟著環境跑**，冷氣房裡手腕就是 32 度。
#   而溫差不跟著跑 —— 因為環境同時影響皮膚感測器跟錶身，會互相抵銷。
#
#   實測（同一支錶、九天）：
#       錶放桌上      皮膚 27.67  錶身 32.41  溫差 -4.74   ← 現場實測
#       冷氣餐廳戴著  皮膚 32.01  錶身 31.03  溫差 +0.98
#       戶外站著戴著  皮膚 33.42  錶身 33.06  溫差 +0.36
#       睡覺蓋被戴著  皮膚 34.73  錶身 34.32  溫差 +0.41
#   → 絕對溫度 27.67~34.73 跨 7 度，而「戴著」的溫差全部 > +0.3。
SKIN_FLOOR = 30.0    # 低於這個連冷氣房都解釋不了，一定不在人身上
DELTA_WORN = 0.15    # ★ 主訊號。實測戴著全部 > +0.3、放桌上 -4.74
DELTA_OFF = -1.5     # 2026-08-26 從 -0.3 放寬。九天實測：
                     #   -0.3 → 誤殺戴著的 0.90%、抓到沒戴的 37.0%
                     #   -1.5 → 誤殺戴著的 0.09%、抓到沒戴的 33.1%
                     # 誤殺少十倍、只少抓 4 個百分點，而**誤判沒戴的代價是警報關掉**。


def temp_opinion(skin: float | None, delta: float | None,
                 charged: bool = False) -> tuple[str, str]:
    """用皮膚溫度判「錶到底在不在人身上」。回（結論, 理由）。

    ⚠ **兩個訊號必須同向，否則一律回「不確定」。**

    2026-08-21 的教訓：23 時那小時 `skin 34.07`（明顯是體溫）
    但 `溫差 −0.03`。我第一版寫成「skin 低 **或** 溫差為負 → 判定不在人身上」，
    於是一個 34 度的讀數被判成「離腕」——**因為我用了 or。**

    這兩個訊號會不同向是有原因的：剛脫下來的錶，皮膚感測器跟錶身都還是熱的，
    溫差會收斂到 0，但絕對溫度還沒降。**那個狀態本來就分不出來，
    正確答案是「不確定」，不是硬選一邊。**

    而且門檻掃描早就說了分不乾淨（最佳 +0.15 也只有 83%）——
    **一個 83% 的訊號沒有資格單獨下判決。**
    """
    if skin is None or delta is None:
        return "unknown", "沒有溫度資料"
    # ⚠⚠ 2026-08-26：**這小時有充過電的話，溫度不能用。**
    #
    #   充電會把錶身加熱到 34~35°C，而手腕要十幾分鐘才把皮膚感測器暖回來。
    #   實測 09:39:56 她剛從充電座拿起來戴上：
    #       錶身 34.92°C  皮膚 28.97°C  → 溫差 **-5.95**，而她明明戴著。
    #   一直到 09:55:11 才轉正。**整整 15 分鐘的反向訊號。**
    #
    #   而「剛充完電就戴上」正是每天最常做的動作 ——
    #   不擋掉的話，等於在她最常戴錶的時刻讓溫度給出相反的判決。
    if charged:
        return "unknown", "這小時有充過電 —— 充電把錶身加熱了，溫度這時候說什麼都不算"
    if skin < SKIN_FLOOR:
        return "off", f"皮膚只有 {skin:.2f}°C —— 那不是活人手腕的溫度"
    if delta > DELTA_WORN:
        return "worn", f"皮膚比錶身高 {delta:+.2f}（{skin:.2f}°C）—— 熱源在身體那側"
    if delta < DELTA_OFF:
        return "off", f"皮膚比錶身低 {delta:+.2f}（{skin:.2f}°C）—— 熱源不在身體那側"
    # ⚠ 中間那條寬帶（-1.5 ~ +0.15）本來就分不出來。
    #   2026-08-26 量了九天才知道這條帶要開這麼寬：
    #       戴著（非剛充電）  5% +0.10   中位 +0.51
    #       確定沒戴（非充電） 5% -4.12   **中位 +0.17** ← 中位居然是正的
    #   → 溫度只在尾巴那一段有鑑別力。**誠實回不確定。**
    return "unknown", (f"溫差只有 {delta:+.2f}（皮膚 {skin:.2f}°C）—— "
                       "剛脫下來、或環境跟體溫差不多的時候就長這樣。不確定")


def hour_summary(day: str, hour: int, recs: list[dict]) -> dict:
    """一小時一筆。**就算這小時什麼都沒有，也回一筆。**"""
    mo = [r for r in recs if r.get("type") == "motion"]
    hrs = [r["hr"] for r in recs
           if r.get("type") == "hr_ibi" and (r.get("hr") or 0) > 0 and r.get("status") == 1]
    hrs += [r["bpm"] for r in recs if r.get("type") == "hr" and (r.get("bpm") or 0) > 0]
    # ⚠ 2026-08-21 修：`worn` 本來只數 motion 記錄，結果出現這種矛盾的行：
    #       02:00  off  戴 6 分  心率 1701 筆
    #   量到 1,701 筆有效心率卻判「沒戴」——**那不可能**。
    #   原因是動作量一分鐘才一筆，而心率是連續流；只看 motion 等於
    #   把大部分證據丟掉。
    #   改成：**跨所有記錄型別，數「有幾個不同的分鐘是戴著的」。**
    worn_minutes = {r["t"][:16] for r in recs if _worn(r)}
    explicit_off_minutes = {r["t"][:16] for r in recs
                            if (str(r.get("on_body")) == "off" and r.get("type") in ("motion", "availability"))
                            or (r.get("type") == "mark" and r.get("tag") == "off_wrist")}
    charging = [r for r in mo if r.get("charging")]
    trusted = [r for r in mo if _worn(r) and r.get("trust") is True]
    acts = [r.get("activity") or 0 for r in trusted]
    temps = [r for r in recs if r.get("type") == "skin_temp"
             and isinstance(r.get("skin"), (int, float))]
    marks = [r for r in recs if r.get("type") == "mark"]

    out: dict = {
        "t": f"{day}T{hour:02d}:00",
        "layer": "hour",
        # cover 放第一個。沒有它就不准讀下面的數值。
        "cover": {
            "motion_minutes": len(mo),
            "worn_minutes": len(worn_minutes),
            "explicit_off_minutes": len(explicit_off_minutes),
            "charging_minutes": len(charging),
            "trusted_minutes": len(trusted),
            "hr_samples": len(hrs),
        },
    }
    if hrs:
        h = sorted(hrs)
        out["hr"] = {"lo": h[0], "p50": h[len(h) // 2], "hi": h[-1]}
    if acts:
        a = sorted(acts)
        out["motion"] = {"p50": round_summary(a[len(a) // 2], 1),
                         "hi": round_summary(a[-1], 1)}
    if temps:
        sk = sorted(r["skin"] for r in temps)
        out["temp"] = {"skin_p50": round_summary(sk[len(sk) // 2], 2)}
        d = sorted(r["skin"] - r["watch_ref"] for r in temps
                   if isinstance(r.get("watch_ref"), (int, float)))
        if d:
            # skin − watch_ref：實測 2026-08-20，戴著中位 +0.47、沒戴 −0.36。
            # 分不乾淨（門檻 +0.15 大約 83%），所以**只當第二意見**，
            # 用在「錶說在手上但量不到心跳」那種矛盾的時候。
            out["temp"]["delta_ref_p50"] = round_summary(d[len(d) // 2], 2)
    if marks:
        out["marks"] = [{"t": m["t"][11:19], "tag": m.get("tag")} for m in marks]

    out["verdict"], out["why"] = _judge_hour(out)
    return out


def _judge_hour(o: dict) -> tuple[str, str]:
    """
    ⚠ **字彙裡刻意沒有「正常 / green」。**
    最好的結果是 `ok` = 「沒有踩到任何固定門檻」，不等於「她很健康」。
    要說「健康」需要個人基線，而基線需要 30 天 —— 我們還沒有。
    """
    c = o["cover"]
    if c["motion_minutes"] == 0 and c["hr_samples"] == 0:
        return "no_data", "這一小時完全沒有資料 —— **不是沒事，是不知道**"
    if c["worn_minutes"] == 0 and c["charging_minutes"] >= 30:
        return "charging", f"在充電（{c['charging_minutes']} 分鐘），沒戴"
    if c["worn_minutes"] < MIN_WORN_MINUTES:
        # ⚠⚠ 2026-08-21 發現的事，寫在這裡是因為它看起來像矛盾、其實不是：
        #
        #   會出現「說沒戴、卻有一兩千筆有效心率」的小時（08-20 的 02 時、10 時）。
        #   用皮膚溫度當第三方查證：
        #       02 時 off：skin 31.58、溫差 −3.10
        #       10 時 off：skin 29.18、溫差 −0.40
        #       對照戴著：溫差 +0.36 ~ +0.49
        #   **29~31°C 遠低於皮膚表面溫度，溫差也是負的 —— 錶不在人身上。**
        #
        #   所以結論是反過來的：**`status == 1` 不等於真的有脈搏。**
        #   離腕的 PPG 會吐出看起來很合理的數字（95、96、63~94），
        #   而且通過三星自己的有效性旗標。
        #
        #   → 這正是最怕的失效方向的原料：「阿公把錶放床頭櫃 → 系統說一切正常」。
        #   → 目前擋住它的是 CollectorService 那條
        #     「availability 主動說 off 時，優先於脈搏」。**那條規則是承重牆，不要拆。**
        #   → 這裡把矛盾明寫出來，不要讓它看起來只是一行奇怪的數字。
        conflict = c["hr_samples"] >= 300
        why = (f"戴著只有 {c['worn_minutes']} 分鐘 —— "
               "身體判斷這小時做不了（沒戴的錶比睡著的人還靜）")
        if conflict:
            # 讓溫度當第三方，把矛盾判開 —— 但它有權說「不確定」。
            t = o.get("temp", {})
            op, reason = temp_opinion(t.get("skin_p50"), t.get("delta_ref_p50"),
                                      charged=c.get("charging_minutes", 0) > 0)
            o["conflict"] = {"kind": "off_but_hr", "temp_says": op, "temp_why": reason}
            why += f"　⚠ 但同時有 {c['hr_samples']} 筆「有效」心率。溫度說：{reason}"
            if op == "off":
                why += "　→ **心率是離腕假訊號，不要當成脈搏**"
            elif op == "worn":
                why += "　→ **其實戴著，是 availability 報錯**"
        # An isolated off-body event cannot label the rest of an hour as off.
        # The missing minutes remain unknown until the watch supplies evidence.
        if c["explicit_off_minutes"] < 30:
            return "unknown", (f"只有 {c['explicit_off_minutes']} 分鐘明確離腕；"
                               "其餘資料不足，不能推成整小時沒戴。" + (" " + why if conflict else ""))
        return "off", why
    if c["hr_samples"] < MIN_HR_SAMPLES:
        return "unknown", (f"戴著 {c['worn_minutes']} 分鐘，但只有 {c['hr_samples']} "
                           "筆有效心率 —— 數值不可信")

    hr = o.get("hr", {})
    p50, lo = hr.get("p50", 0), hr.get("lo", 999)
    still = o.get("motion", {}).get("p50", 999) < STILL_MOTION

    # Apple 式閘門：高心率只有在「人幾乎沒動」的時候才算異常。
    # 運動、爬樓梯、洗熱水澡都會讓心率上去 —— 那些不是問題。
    if still and p50 > HR_HIGH:
        return "flag", (f"幾乎沒動，但心率中位 {p50} 超過固定門檻 {HR_HIGH}"
                        "（Apple 高心率通知的預設值）")
    if lo < HR_LOW:
        return "flag", f"出現 {lo} 的心率，低於固定門檻 {HR_LOW}"
    return "ok", (f"戴著 {c['worn_minutes']} 分鐘、{c['hr_samples']} 筆心率，"
                  "沒有踩到任何固定門檻" + ("（幾乎沒動）" if still else ""))


def day_summary(day: str, hours: list[dict], recs: list[dict]) -> dict:
    """一天一筆。**沒資料的日子也要有。**"""
    with_data = [h for h in hours if h["verdict"] != "no_data"]
    worn_min = sum(h["cover"]["worn_minutes"] for h in hours)
    evidence = classify(recs, day)
    mo = sorted((r for r in recs if r.get("type") == "motion"), key=lambda r: r["t"])

    # 最長連續配戴 —— 壓瘡那條線要看的數字。
    # ⚠ 用「明確 off」切段，`unknown` **不算脫下** ——
    #   2026-08-19 我用 unknown 切，把 9.3 小時算成 1.7 小時，差了五倍。
    runs, start, last = [], None, None
    for r in mo:
        ob = r.get("on_body")
        if ob in ("on", "inferred_on"):
            start = start or T(r)
            last = T(r)
        elif ob == "off":
            if start and last:
                runs.append((last - start).total_seconds() / 3600)
            start = last = None
    if start and last:
        runs.append((last - start).total_seconds() / 3600)

    batts = [r["batt"] for r in mo if isinstance(r.get("batt"), int) and r["batt"] >= 0]
    marks = [r for r in recs if r.get("type") == "mark"]

    out = {
        "t": day, "layer": "day",
        "cover": {
            "hours_with_data": len(with_data),
            "worn_minutes": worn_min,
            "records": len(recs),
            **{k: evidence[k] for k in ("minutes", "valid_minutes", "off_minutes",
                                          "unknown_minutes", "conflict_minutes", "hr_minutes",
                                          "valid_ratio", "quality")},
        },
        "wear": {
            # 醫學上壓瘡耐受約 2 小時。這裡只**報數字**，不出聲、不警報 ——
            # 因為系統的誘因跟皮膚安全是相反的（越緊訊號越乾淨），
            # 所以這一格永遠只描述，不建議調緊。
            "longest_run_h": round_summary(max(runs), 1) if runs else 0,
            "runs_over_2h": sum(1 for x in runs if x > 2),
        },
        "marks": [{"t": m["t"][11:19], "tag": m.get("tag")} for m in marks],
    }
    if batts:
        out["batt"] = {"first": batts[0], "last": batts[-1]}

    if not with_data:
        out["verdict"] = "no_data"
        out["why"] = "這一天一筆資料都沒有 —— **不是沒事，是不知道**"
        return out

    flags = [h for h in hours if h["verdict"] == "flag"]
    out["verdict"] = "flag" if flags else ("ok" if evidence["quality"] == "sufficient"
                                          else evidence["quality"])
    out["why"] = ((f"{len(flags)} 個小時踩到固定門檻："
                   + "、".join(h["t"][11:16] for h in flags)) if flags else
                  (f"有效資料 {evidence['valid_minutes']}/{evidence['minutes']} 分鐘；"
                   + ("今日尚未結束，這是暫報" if evidence["quality"] == "partial"
                      else "覆蓋不足，不能作全日判斷" if evidence["quality"] == "insufficient"
                      else "有資料的時段沒有踩到固定門檻")))
    return out


def build(day: str) -> tuple[list[dict], dict]:
    recs = load(day)
    by_hour: dict[int, list[dict]] = {h: [] for h in range(24)}
    for r in recs:
        try:
            by_hour[T(r).hour].append(r)
        except Exception:
            continue
    # range(24) —— 每一小時都產一筆，沒資料的那些會變成 no_data。
    # **那就是「讓洞看得見」的實作。**
    hours = [hour_summary(day, h, by_hour[h]) for h in range(24)]
    return hours, day_summary(day, hours, recs)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--全部" in sys.argv:
        days = sorted(p.stem for p in BIO.glob("2026-*.jsonl"))
    else:
        days = ([a for a in sys.argv[1:] if a.startswith("202")]
                or [datetime.now().strftime("%Y-%m-%d")])

    OUT.mkdir(parents=True, exist_ok=True)
    for day in days:
        hours, dsum = build(day)
        f = OUT / f"{day}.jsonl"
        with f.open("w", encoding="utf-8") as fh:
            for h in hours:
                fh.write(json.dumps(h, ensure_ascii=False) + "\n")
            fh.write(json.dumps(dsum, ensure_ascii=False) + "\n")

        src = BIO / f"{day}.jsonl"
        raw = src.stat().st_size if src.exists() else 0
        size = f.stat().st_size
        print(f"\n== {day} ==  原始 {raw/1024/1024:.1f} MB -> 摘要 {size/1024:.1f} KB"
              f"（{raw/max(size,1):.0f} 分之一）")
        print(f"  {dsum['verdict']}：{dsum['why']}")
        w = dsum["wear"]
        print(f"  最長連續配戴 {w['longest_run_h']} 小時"
              f"（超過 2 小時的有 {w['runs_over_2h']} 段）")
        for h in hours:
            c, hr = h["cover"], h.get("hr", {})
            line = (f"    {h['t'][11:16]} {BAR[h['verdict']]} {h['verdict']:8s}"
                    f" 戴{c['worn_minutes']:2d}分 心率{c['hr_samples']:5d}筆")
            if hr:
                line += f"  {hr['lo']}-{hr['p50']}-{hr['hi']}"
            if h.get("marks"):
                line += "  *" + "".join(m["tag"] for m in h["marks"])
            print(line)


if __name__ == "__main__":
    main()
