#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""好像，怎麼覺得好像開始做儀錶板啊？像 datadog 一樣的東西。


1. **判斷要在資料出生的地方做。**
   這份 Python 是原型，用真實資料驗證邏輯；**最終要移植到手錶上跑**，

2. **警報不能用 AI。**
   這個問題上都是致命的。**警報要死板、可測試、離線也能跑。**


## 血氧為什麼不在這裡

而且血氧要人不動三十秒才量得準，**最需要量的時候恰恰是最量不到的時候**。
所以血氧只在睡覺時量趨勢，永遠不進警報路徑。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# ── 門檻 ──────────────────────────────────────────────────────────
#

# 研究值只拿來當上限參考，不是拿來直接抄：
#   健康的人睡覺時每 15–30 分鐘就會換一次姿勢，一晚 10–30 次大翻身。
#   → 完全不動超過 30–45 分鐘，即使睡覺也異常。


SIGNAL_LOST_MIN = 15      # 戴著卻這麼久量不到心率 → 紅
HC_HR_MAX_AGE_H = 6
HC_HR_SERIES = Path(__file__).resolve().parent.parent / "生理資料" / "series" / "heart_rate.jsonl"


def hc_latest_hr(now: datetime | None = None, path: Path | None = None) -> tuple[int, datetime] | None:
    """三星健康（手機 Health Connect → puller → series/heart_rate.jsonl）最新一筆心率 → (bpm, 本地時間)。
    只讀檔尾 64 KB（整檔 9 MB）。"""
    path = path or HC_HR_SERIES
    try:
        with path.open("rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", "replace").splitlines()[1:]
    except OSError:
        return None
    best = None
    for line in tail:
        try:
            r = json.loads(line)
            bpm = int(r.get("bpm") or 0)
            t = datetime.fromisoformat(str(r["time"]).replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        except Exception:
            continue
        if bpm > 0 and (now is None or t <= now) and (best is None or t > best[1]):
            best = (bpm, t)
    return best


def hr_channel_dead(records: list[dict]) -> bool:
    """ """
    return bool(records) and not any(r.get("type") == "hr" and r.get("bpm", 0) > 0 for r in records)
STILL_WARN_MIN = 45       # 完全不動這麼久 → 黃（先問，不直接報警）

PIPELINE_DEAD_MIN = 20
"""我把那句寫在檔頭，然後在程式裡犯了一模一樣的錯。"""

# 動作量門檻**依指標版本分開**。
#

#   v1（三星 SDK 25 Hz，原始單位）與 v2（SensorManager 5 Hz，毫 m/s²）
#   的動作量**不是同一個尺度**。用同一個門檻去套，換了 APK 之後

#
# v2 的 400 是這樣來的：MotionSampler 的靜止門檻是每一筆取樣 80 毫 m/s²，


# ⚠️ 仍待用**睡覺那幾個小時**校正 —— 睡著的分鐘應該幾乎全部落在門檻以下。
STILL_ACTIVITY_BY_VERSION = {
    1: 50.0,      # 三星 25 Hz。**待校正**，而且往後不會再有新資料

    2: None,
    # v3 實際上不存在 —— 批次交付在 non-wakeUp 感測器上完全不送，只活了幾分鐘
    3: None,

    # **尺度與 v1 相同**，所以先沿用 v1 的 50.0。

    #   收滿一晚 trust=True 的 v4 資料再回來重設。
    4: 50.0,
}
"""**一、8% 是挑出來的。** 我只算了兩段清醒（3.7%、5.7%），
   沒算另外三段（37.1%、42.6%、59.5%）。**全部合計是 33.9%，是我報的四倍。**

   離腕充電 219 分鐘：中位 42，**95.9% 落在門檻以下**
   睡眠段   319 分鐘：中位 48，89.3% 落在門檻以下
   —— **沒戴的錶比睡著的人還靜。**


**三、底下的資料本身是廢的。** 安靜時每分鐘只收到 34 筆取樣（目標 300），
   我拿那些被灌大的數字去校門檻。

所以現在的規矩：**沒有在該版本的真實資料上校準過，就填 None，讓它回 unknown。**
猜一個數字填進去，比空著危險得多 —— 因為它會安靜地給出看起來合理的答案。"""

MIN_TRUST_RATIO = 0.5
"""一小時內至少要有這麼高比例的 motion 記錄是 `trust=True`，才做不動判斷。

手錶端會在取樣不足時把 `trust` 標成 false（見 MotionSampler.MIN_TRUSTWORTHY_SAMPLES）。"""

# 心率偏離基線多少算異常。用倍數不用絕對值，因為每個人基線不同。
HR_HIGH_RATIO = 1.35
HR_LOW_RATIO = 0.70


@dataclass
class Verdict:
    level: str                      # green / yellow / red / unknown
    reason: str
    evidence: list[str] = field(default_factory=list)

    @property
    def 說法(self) -> str:
        tag = {"green": "正常", "yellow": "有點不對", "red": "要立刻確認",
               "unknown": "說不準"}[self.level]
        s = f"[{tag}] {self.reason}"
        for e in self.evidence:
            s += f"\n    · {e}"
        return s


def on_body_of(r: dict) -> str:
    """讀 `on_body`，**同時吃得下新舊兩種格式**，回 "on" / "off" / "unknown"。

      三個消費端全部會**安靜地壞掉**，而且方向還相反：


      兩邊都不會報錯，只是從此判斷全錯。所以讀這個欄位一律走這支，
      **不要在別的地方自己 get**。

    舊資料是布林：True → "on"，False → "off"。
    完全沒有這個欄位 → "unknown"（**不是 "on"**）。
    這一條很重要：舊版預設 True 本身就是個 bug，不要把它繼承下來。"""
    v = r.get("on_body")
    if isinstance(v, str):
        return v if v in ("on", "off", "inferred_on", "unknown") else "unknown"
    if isinstance(v, bool):
        return "on" if v else "off"
    return "unknown"


def is_worn(r: dict) -> bool:
    """這一筆記錄的時候，錶在不在人身上。

      而脈搏是物理事實，比三星的推論更硬。


      不保證是誰、也不保證貼合良好。需要區分的地方要自己看原始值。"""
    return on_body_of(r) in ("on", "inferred_on")


def _load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


PERIOD_WINDOW_DAYS = 7
"""等累積到至少兩個完整週期的資料，再談要不要讓它影響判斷。"""


def find_period_start(root: Path, now: datetime | None = None,
                      lookback_days: int = 40) -> datetime | None:
    """⚠ **一定要跨檔找。** 標記是按一次就沒了，而 `assess()` 每次只拿到當天的資料 ——"""
    now = now or datetime.now()

    # ── 手動更正檔優先 ───────────────────────────────────────
    #


    #


    #   而事實會贏。
    manual = root.parent / "生理期起算.txt"
    if manual.exists():
        days = []
        for line in manual.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                try:
                    days.append(datetime.strptime(line, "%Y-%m-%d"))
                except ValueError:
                    continue
        if days:
            return max(days)

    for back in range(lookback_days + 1):
        d = (now - timedelta(days=back)).strftime("%Y-%m-%d")
        f = root / f"{d}.jsonl"
        if not f.exists():
            continue
        hit = None
        for r in _load(f):
            if r.get("type") == "mark" and r.get("tag") == "period":
                hit = datetime.fromisoformat(r["t"])
        if hit:
            return hit
    return None


def assess(records: list[dict], now: datetime | None = None,
           hr_baseline: float | None = None,
           period_start: datetime | None = None) -> Verdict:
    """看最近一段資料，回一個判斷。

    刻意只回四種結果，而且**每一種都附證據** ——

    落在窗內的判斷會**多一行註記**，但**判斷本身完全不變** —— 見下面的說明。"""
    v = _assess_core(records, now, hr_baseline)


    #

    # 但它其實是這套系統目前**最大的已知干擾源** —— 週期會同時把
    # 靜息心率推高、HRV 壓低、皮膚溫度抬高，而那三個正好是判斷用的訊號。
    #
    # ⚠ **但這裡刻意只加註記，不動任何門檻。**
    #


    #

    #   在沒有校準資料之前，選失效方向溫和的那一邊。
    if hr_channel_dead(records):
        hc = hc_latest_hr(now)
        now_ = now or datetime.now()
        if hc and (now_ - hc[1]) <= timedelta(hours=HC_HR_MAX_AGE_H):
            v.evidence.append(f"錶的心率通道停了（這份資料沒有任何心率），不是身體問題；三星健康最新心率 {hc[0]}"
                              f"（{hc[1]:%H:%M}，延遲 {int((now_ - hc[1]).total_seconds() // 60)} 分鐘，不是即時）")
    if period_start is not None:
        now_ = now or datetime.now()
        days = (now_ - period_start).total_seconds() / 86400
        if 0 <= days <= PERIOD_WINDOW_DAYS:
            v.evidence.append(
                f"（生理期第 {days:.0f} 天 —— 心率會偏高、HRV 會偏低，"
                f"**這是註記不是解釋**，不因此放寬任何門檻）")
    return v


def _assess_core(records: list[dict], now: datetime | None = None,
                 hr_baseline: float | None = None) -> Verdict:
    if not records:
        return Verdict("unknown", "完全沒有資料", ["連一筆都沒收到"])

    def T(r): return datetime.fromisoformat(r["t"])
    records = sorted(records, key=T)


    #


    #     **紅燈在物理上不可能亮。**
    #

    #     一個永遠不會響的警報器，誤報率當然是零。
    now = now or datetime.now()

    # 傳進來的 now 也要當真：晚於 now 的資料一律不看。
    # 不裁的話回放測試會偷看到未來，測出來的表現是假的。
    records = [r for r in records if T(r) <= now]
    if not records:
        return Verdict("unknown", "這個時間點之前沒有任何資料", ["連一筆都沒收到"])

    # ── 管線本身還活著嗎 ─────────────────────────────────────
    # 這一題必須在所有身體判斷**之前**問。

    age = (now - T(records[-1])).total_seconds() / 60
    if age >= PIPELINE_DEAD_MIN:
        return Verdict(
            "unknown", f"已經 {age:.0f} 分鐘沒有任何新資料進來",
            [f"最後一筆：{T(records[-1]):%m-%d %H:%M}",
             "**這是管線的問題，不是身體的判斷** —— 手錶沒電、app 掛了、"
             "藍牙斷了都會這樣",
             "在修好之前，這段時間發生什麼事我一概不知道"])

    recent = [r for r in records if T(r) > now - timedelta(minutes=60)]
    if not recent:
        return Verdict("unknown", "最近一小時沒有資料",
                       [f"最後一筆是 {T(records[-1]):%H:%M}"])

    last = recent[-1]


    # 錶不在手上就沒有身體資料可判斷。這不是異常，是**沒有輸入**。
    ob = on_body_of(last)
    if ob == "unknown":

        # 這一條刻意不回綠燈：離腕的錶比睡著的人還靜，

        return Verdict(
            "unknown", "不確定錶在不在手上",
            [f"{T(last):%H:%M} 的 on_body 是 unknown",
             "可能是感測器還在找訊號、或那條通道停了",
             "**沒戴的錶比睡著的人還靜**，所以不知道戴沒戴就不能判身體"])
    if ob == "off":
        if last.get("charging"):
            return Verdict("green", "錶拿下來充電中",
                           [f"{T(last):%H:%M} 不在手上、充電中 {last.get('batt')}%"])
        # 脫了但沒充電 —— 這個值得記一筆，但還不到警報
        return Verdict("yellow", "錶脫下來了，而且沒在充電",
                       [f"{T(last):%H:%M} 不在手上、沒充電",
                        "如果這是預期外的，值得問一句"])

    # ── 紅：戴著，但量不到心跳 ───────────────────────────────
    hrs = [r for r in recent if r["type"] == "hr" and r.get("bpm", 0) > 0]


    #
    #   影子模式第一天就抓到了：11 次紅燈，全部是誤報，全部長一樣 ——

    #
    #   真正的原因不是沒有心跳，是**資料還在藍牙路上**。


    #
    #   兩個門檻之間剛好留了一個必然誤報的窗口：


    #   而藍牙送達延遲本來就常常落在 15~20 分鐘之間。
    #
    #   **正解：在資料內部量，不要拿外面的時鐘去減。**

    #   這樣送達延遲會自動抵銷掉，因為兩邊延遲一樣。
    #

    #   **兩件事分開管，才不會互相製造窗口。**
    data_now = T(records[-1])

    #   三星健康也沒有 → 照舊亮紅（不能自己解釋掉，原則不變）。註記由 assess() 加。
    hc = hc_latest_hr(now) if hr_channel_dead(records) else None
    channel_dead_ok = hc is not None and (now - hc[1]) <= timedelta(hours=HC_HR_MAX_AGE_H)
    if channel_dead_ok:
        quiet = 0
    elif hrs:
        quiet = (data_now - T(hrs[-1])).total_seconds() / 60
    else:
        quiet = (data_now - T(recent[0])).total_seconds() / 60
    if quiet >= SIGNAL_LOST_MIN:
        return Verdict(
            "red", f"錶說在手上，但已經 {quiet:.0f} 分鐘量不到心跳",
            [f"最後一筆有效心率：{T(hrs[-1]):%H:%M}" if hrs else "這一小時沒有任何有效心率",
             f"（拿資料裡最新的 {data_now:%H:%M} 去比，不是拿現在的時鐘 —— "
             f"送達延遲不算沒心跳）",
             "感測器貼合不良也會這樣 —— 但這種情況必須當面確認，不能自己解釋掉"])

    # ── 黃：完全不動太久 ─────────────────────────────────────
    motions = [r for r in recent if r["type"] == "motion"]
    if not motions:


        return Verdict(
            "unknown", "這一小時沒有任何動作量資料",
            [f"心率有 {len(hrs)} 筆，但 motion 一筆都沒有",
             "加速度計沒開、或這一段是舊版收的 —— 不動的判斷這段做不了"])

    # ── 閘門一：錶不在手上的分鐘，一律不參與不動判斷 ──────────
    #

    # **95.9% 落在任何合理的靜止門檻以下** —— 比睡著的人還靜。
    #


    # 唯一的解法就是這道硬閘門：**沒戴就不判，不是判成正常。**
    on_body_motions = [m for m in motions if is_worn(m)]
    if not on_body_motions:
        return Verdict(
            "unknown", "這一小時錶都不在手上",
            [f"{len(motions)} 筆動作量全部是離腕狀態",
             "**不動的判斷這段做不了** —— 沒戴的錶比睡著的人還靜，分不出來"])

    # ── 閘門二：取樣不足的分鐘不可信 ─────────────────────────
    #
    # 手錶端會在每分鐘取樣數不足時把 trust 標成 false。

    # 正是在節流 bug 之下收的，安靜時 87% 的分鐘不足 60 筆）。
    trusted = [m for m in on_body_motions if m.get("trust") is True]
    ratio = len(trusted) / len(on_body_motions)
    if ratio < MIN_TRUST_RATIO:
        return Verdict(
            "unknown", f"動作量取樣不足，只有 {ratio:.0%} 的分鐘可信",
            [f"戴著的 {len(on_body_motions)} 分鐘裡，只有 {len(trusted)} 分鐘取樣夠",
             "取樣一低，動作量的數值會被灌大 —— 這種數字不能拿來判斷",
             "（沒有 trust 欄位的舊資料一律當不可信）"])

    still = []
    for m in reversed(trusted):
        # 動作量門檻要跟著**那一筆自己的指標版本**走。

        thr = STILL_ACTIVITY_BY_VERSION.get(m.get("mv", 1))
        if thr is None:
            # **這個版本還沒在真實資料上校準過。**


            return Verdict(
                "unknown", f"動作量 v{m.get('mv', 1)} 還沒校準過門檻",
                ["這個指標版本沒有對應的靜止門檻，不動的判斷這段做不了",
                 "要先收滿一段 trust=True 的真實睡眠資料才能校",
                 "**寧可說不知道，也不要猜一個數字** —— 猜的門檻會安靜地給出合理的錯答案"])
        if m.get("activity", 999999) < thr:
            still.append(m)
        else:
            break
    if still:
        span = (T(still[0]) - T(still[-1])).total_seconds() / 60
        if span >= STILL_WARN_MIN:
            thr = STILL_ACTIVITY_BY_VERSION[still[0].get("mv", 1)]
            return Verdict(
                "yellow", f"連續 {span:.0f} 分鐘幾乎完全沒動",
                [f"最近動作量 {still[0].get('activity'):.0f}"
                 f"（v{still[0].get('mv', 1)} 門檻 {thr:.0f}）",
                 "研究值：健康的人睡覺每 15–30 分鐘就會換姿勢",
                 "先震一下問問，不要直接叫人"])

    # ── 黃：心率偏離基線 ─────────────────────────────────────
    if hrs and hr_baseline:
        cur = sorted(r["bpm"] for r in hrs[-30:])[len(hrs[-30:]) // 2]
        if cur > hr_baseline * HR_HIGH_RATIO:
            return Verdict("yellow", f"心率 {cur} 比平常高很多",
                           [f"同時段基線約 {hr_baseline:.0f}",
                            "運動、發燒、緊張都會這樣 —— 要問，不要猜"])
        if cur < hr_baseline * HR_LOW_RATIO:
            return Verdict("yellow", f"心率 {cur} 比平常低很多",
                           [f"同時段基線約 {hr_baseline:.0f}"])

    n_hr = len(hrs)
    cur = sorted(r["bpm"] for r in hrs)[n_hr // 2] if hrs else 0
    return Verdict("green", "看起來正常",
                   [f"最近一小時 {n_hr} 筆心率，中位 {cur}",
                    f"最後一筆 {T(last):%H:%M}"])


if __name__ == "__main__":
    import sys
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parent.parent / "生理資料" / "手錶" / \
        f"{datetime.now():%Y-%m-%d}.jsonl"
    if not p.exists():
        print(f"找不到 {p}")
        raise SystemExit(1)
    recs = _load(p)
    print(f"讀了 {len(recs)} 筆：{p.name}\n")
    print(assess(recs).說法)
