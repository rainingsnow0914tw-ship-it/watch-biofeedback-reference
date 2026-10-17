#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
triage.py —— 急／不急的判斷（使用者 說的「中轉站」）。

為什麼要有這一層（2026-08-13 使用者 原話）：
    「萬一它已經倒在地上涼了，過幾個小時才傳上雲端，不就完蛋了？
      好像，怎麼覺得好像開始做儀錶板啊？像 datadog 一樣的東西。
      我們應該有一個中轉站？先讓這個中轉站判斷急還是不急？」

她說得對。儀表板是**事後回顧**，安全系統是**當下反應**，兩者架構完全不同。

## 三條設計原則（都是她定的）

1. **判斷要在資料出生的地方做。**
   這份 Python 是原型，用真實資料驗證邏輯；**最終要移植到手錶上跑**，
   因為出事的時候最可能沒網路、沒手機、沒電腦。

2. **警報不能用 AI。**
   要網路、要好幾秒、每次答案還可能不一樣 —— 那三件事在「要不要叫人」
   這個問題上都是致命的。**警報要死板、可測試、離線也能跑。**
   AI 用在後面那一層：聽懂她說什麼、判斷她的狀態、溫暖地陪她。

3. **「沒有資料」不是一種資料。**
   2026-08-13 的教訓：我看到資料斷了就說「應該是脫下來充電」——
   錯的。要判斷「她脫了錶」，必須有裝置**主動說**「我不在手上」。
   所以本模組**永遠不從「沒收到」推論身體狀況**，只推論「我失去訊號了」。

## 血氧為什麼不在這裡

使用者：「心率的反應應該會在血氧之前吧，血氧就當輔助就好了」——文獻證實。
而且血氧要人不動三十秒才量得準，**最需要量的時候恰恰是最量不到的時候**。
所以血氧只在睡覺時量趨勢，永遠不進警報路徑。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# ── 門檻 ──────────────────────────────────────────────────────────
#
# 這些數字**現在都是暫定的**，要用 使用者 自己的基線校正。
# 研究值只拿來當上限參考，不是拿來直接抄：
#   健康的人睡覺時每 15–30 分鐘就會換一次姿勢，一晚 10–30 次大翻身。
#   → 完全不動超過 30–45 分鐘，即使睡覺也異常。
# 但 使用者 說自己「睡相很不好會一直動」，所以她的門檻可能要更嚴。

SIGNAL_LOST_MIN = 15      # 戴著卻這麼久量不到心率 → 紅
STILL_WARN_MIN = 45       # 完全不動這麼久 → 黃（先問，不直接報警）

PIPELINE_DEAD_MIN = 20
"""最新一筆資料比「現在」舊超過這麼久 → 管線本身死了。

2026-08-16 稽核抓到的洞：這支本來拿**最後一筆資料的時間**當「現在」，
所以整條管線死了七小時，它照樣印「看起來正常」——
**紅燈在物理上不可能亮**，因為「現在」是由資料自己定義的。

這正好違反本檔案原則三自己寫的那句：「沒有訊號 ≠ 沒有心跳」。
我把那句寫在檔頭，然後在程式裡犯了一模一樣的錯。"""

# 動作量門檻**依指標版本分開**。
#
# 為什麼不能只有一個數字（2026-08-15 換取樣方式時就埋下的雷）：
#   v1（三星 SDK 25 Hz，原始單位）與 v2（SensorManager 5 Hz，毫 m/s²）
#   的動作量**不是同一個尺度**。用同一個門檻去套，換了 APK 之後
#   「不動」這件事就再也偵測不到 —— 而且畫面上什麼都看不出來。
#
# v2 的 400 是這樣來的：MotionSampler 的靜止門檻是每一筆取樣 80 毫 m/s²，
# 而 activity 是整分鐘的平均相鄰差。實測她清醒靜坐時 activity 約 50~150、
# 走動時 200~1500、甩手那一分鐘 6573。取 400 當「幾乎沒動」的上界。
# ⚠️ 仍待用**睡覺那幾個小時**校正 —— 睡著的分鐘應該幾乎全部落在門檻以下。
STILL_ACTIVITY_BY_VERSION = {
    1: 50.0,      # 三星 25 Hz。**待校正**，而且往後不會再有新資料
    # v2 **刻意設成 None** —— 見下方 2026-08-16 的事故說明
    2: None,
    # v3 實際上不存在 —— 批次交付在 non-wakeUp 感測器上完全不送，只活了幾分鐘
    3: None,
    # v4 = 撤回 SensorManager，搬回三星 SDK 25 Hz（2026-08-16）。
    # **尺度與 v1 相同**，所以先沿用 v1 的 50.0。
    # ⚠ 這個 50 是 v1 時代「待校正」的暫定值，**還是沒有用真實睡眠資料校過**。
    #   收滿一晚 trust=True 的 v4 資料再回來重設。
    4: 50.0,
}
"""動作量的「靜止」門檻，依指標版本分開。**None 代表「這個版本還沒校準，不要判」。**

## 2026-08-16 的事故：這裡本來填了 400，那個數字是編的

當天我用睡眠資料「校」出門檻 105，報給 使用者 說「安靜段 89% 抓到、
清醒段只有 8% 誤判」。三個對抗式查證 agent 把它拆了，我自己重驗，全部成立：

**一、8% 是挑出來的。** 我只算了兩段清醒（3.7%、5.7%），
   沒算另外三段（37.1%、42.6%、59.5%）。**全部合計是 33.9%，是我報的四倍。**

**二、這個門檻分不出「睡著」跟「錶放在充電座上」。**
   離腕充電 219 分鐘：中位 42，**95.9% 落在門檻以下**
   睡眠段   319 分鐘：中位 48，89.3% 落在門檻以下
   —— **沒戴的錶比睡著的人還靜。**

   給家人用時這是最致命的失效方向：阿公把錶脫下來放床頭櫃 →
   系統回報「睡得很安穩」。**原理上分不出來，不是調門檻能救的。**

**三、底下的資料本身是廢的。** 安靜時每分鐘只收到 34 筆取樣（目標 300），
   87% 的分鐘不足 60 筆 —— 而動作量是「相鄰差的平均」，取樣率一低數值就被灌大。
   我拿那些被灌大的數字去校門檻。

所以現在的規矩：**沒有在該版本的真實資料上校準過，就填 None，讓它回 unknown。**
猜一個數字填進去，比空著危險得多 —— 因為它會安靜地給出看起來合理的答案。"""

MIN_TRUST_RATIO = 0.5
"""一小時內至少要有這麼高比例的 motion 記錄是 `trust=True`，才做不動判斷。

手錶端會在取樣不足時把 `trust` 標成 false（見 MotionSampler.MIN_TRUSTWORTHY_SAMPLES）。
**沒有這道閘門，2026-08-16 那種「87% 的分鐘都不可信」的資料會被當成正常資料用。**"""

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

    ⚠ 2026-08-16 手錶端把這個欄位從布林改成三態字串。改型別的當下，
      三個消費端全部會**安靜地壞掉**，而且方向還相反：

        Python  `if not r.get("on_body")` → 字串 "off" 是 truthy → 判成「戴著」
        Kotlin  `optBoolean("on_body")`   → 字串一律回 false    → 判成「沒戴」

      兩邊都不會報錯，只是從此判斷全錯。所以讀這個欄位一律走這支，
      **不要在別的地方自己 get**。

    舊資料是布林：True → "on"，False → "off"。
    完全沒有這個欄位 → "unknown"（**不是 "on"**）。
    這一條很重要：舊版預設 True 本身就是個 bug，不要把它繼承下來。
    """
    v = r.get("on_body")
    if isinstance(v, str):
        return v if v in ("on", "off", "inferred_on", "unknown") else "unknown"
    if isinstance(v, bool):
        return "on" if v else "off"
    return "unknown"


def is_worn(r: dict) -> bool:
    """這一筆記錄的時候，錶在不在人身上。

    ⚠ `inferred_on` **算戴著**。它的意思是「availability 沒在講話，但脈搏還在」——
      而脈搏是物理事實，比三星的推論更硬。

      使用者 2026-08-17 定的：
      > 「availability 過期只能表示很久沒更新，不能直接等於佩戴狀態 unknown。
      >   佩戴狀態該用多證據融合……如果生理訊號還在持續，就應該標 inferred on。」

      為什麼這條很重要：08-17 早上我把過期一律當 unknown，
      結果**一天 83.8% 的分鐘變成「不知道」**，triage 永遠回「說不準」，
      判斷層等於不存在。**把「不知道」設得太寬鬆，跟設得太嚴一樣糟。**

    ⚠ 但 `inferred_on` **比 `on` 弱**：它只保證「這支錶在某個活人身上」，
      不保證是誰、也不保證貼合良好。需要區分的地方要自己看原始值。
    """
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
"""按下「生理期」之後，往後幾天的判斷要帶上註記。

⚠ **這個數字沒有校準過。** 它是照一般常識取的（月經 3~7 天），
  不是從 使用者 自己的資料算出來的。所以它**只用來加註記，不用來改門檻** ——
  一個沒校準的數字拿去動判斷邏輯，就是 2026-08-16 那個
  「聽起來很漂亮、實際 34% 誤判」的門檻的翻版。

  等累積到至少兩個完整週期的資料，再談要不要讓它影響判斷。"""


def find_period_start(root: Path, now: datetime | None = None,
                      lookback_days: int = 40) -> datetime | None:
    """往回翻幾天的資料檔，找最近一次「生理期」標記。

    ⚠ **一定要跨檔找。** 標記是按一次就沒了，而 `assess()` 每次只拿到當天的資料 ——
      只看當天的話，她初一按的那一顆，初二就等於沒按過。
      這正是這個專案反覆在犯的那隻病的變形：東西存在，但用它的人看不到它。
    """
    now = now or datetime.now()

    # ── 手動更正檔優先 ───────────────────────────────────────
    #
    # ⚠ 2026-08-20 加，起因是一次我自己犯的錯：
    #   08-18 她說「我這兩天，**就是昨天吧**，生理期來了」——
    #   帶「吧」的那句是不確定，我卻把它寫成確定的 08-17。
    #   08-20 對不上的時候，她的第一反應是「**我按錯了嗎？**」
    #   她沒按錯，那顆按鈕 08-19 才裝上手錶 —— **08-17 世界上還沒有那顆鈕。**
    #
    #   所以要有一條「人可以直接更正」的路，而且**優先於按鈕**：
    #   按鈕記的是「她按的那一刻」，這個檔記的是「事後查清楚的事實」，
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
    """
    看最近一段資料，回一個判斷。

    刻意只回四種結果，而且**每一種都附證據** ——
    使用者 要能自己判斷 AI 對不對，不是叫她相信一個分數。

    `period_start` 是最近一次「生理期」標記的時間。傳進來的話，
    落在窗內的判斷會**多一行註記**，但**判斷本身完全不變** —— 見下面的說明。
    """
    v = _assess_core(records, now, hr_baseline)

    # ── 生理期註記 ───────────────────────────────────────────
    #
    # 使用者 2026-08-18 提議在手錶上加一顆「生理期」。她當成一個記事，
    # 但它其實是這套系統目前**最大的已知干擾源** —— 週期會同時把
    # 靜息心率推高、HRV 壓低、皮膚溫度抬高，而那三個正好是判斷用的訊號。
    #
    # ⚠ **但這裡刻意只加註記，不動任何門檻。**
    #
    #   要「聰明地」在經期放寬心率門檻很容易，而且聽起來很合理。
    #   但那等於拿文獻的平均值去猜她的身體 —— 而放寬門檻的失效方向是
    #   **漏報**：真的出事的那次，剛好被「喔她生理期」解釋掉。
    #
    #   註記的失效方向相反：最壞的情況是她看到一行沒用的字。
    #   在沒有校準資料之前，選失效方向溫和的那一邊。
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

    # ⚠ 2026-08-16 修的致命 bug：這裡本來是 `now = now or T(records[-1])`。
    #
    #     拿**最後一筆資料**當「現在」，等於讓資料自己定義時間。
    #     結果是：整條管線死掉七小時，這支照樣印「看起來正常」——
    #     因為在它眼裡「現在」就是資料停止的那一刻，一切都很新鮮。
    #     **紅燈在物理上不可能亮。**
    #
    #     影子模式那個「兩週零誤報」的漂亮成績，就是這樣來的。
    #     一個永遠不會響的警報器，誤報率當然是零。
    now = now or datetime.now()

    # 傳進來的 now 也要當真：晚於 now 的資料一律不看。
    # 不裁的話回放測試會偷看到未來，測出來的表現是假的。
    records = [r for r in records if T(r) <= now]
    if not records:
        return Verdict("unknown", "這個時間點之前沒有任何資料", ["連一筆都沒收到"])

    # ── 管線本身還活著嗎 ─────────────────────────────────────
    # 這一題必須在所有身體判斷**之前**問。
    # 「收不到資料」跟「身體沒事」長得一模一樣，而它們是完全相反的兩件事。
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

    # ── 先處理「不該判斷」的情況 ─────────────────────────────
    # 錶不在手上就沒有身體資料可判斷。這不是異常，是**沒有輸入**。
    ob = on_body_of(last)
    if ob == "unknown":
        # 「不知道戴著沒」→ 所有身體判斷都不能做。
        # 這一條刻意不回綠燈：離腕的錶比睡著的人還靜，
        # 不知道有沒有戴的時候給「正常」，正是最危險的假陰性。
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

    # ⚠ 2026-08-18 修：這裡本來拿**牆上的時鐘**去減最後一筆心率。
    #
    #   影子模式第一天就抓到了：11 次紅燈，全部是誤報，全部長一樣 ——
    #   「錶說在手上，但已經 15~20 分鐘量不到心跳」。
    #
    #   真正的原因不是沒有心跳，是**資料還在藍牙路上**。
    #   例如 15:12 判斷的那次，我手上最新的資料只到 14:55（落後 17 分鐘），
    #   而完整檔案裡 15:11 明明有心率 —— 那筆是後來才送到的。
    #
    #   兩個門檻之間剛好留了一個必然誤報的窗口：
    #       PIPELINE_DEAD_MIN = 20  → 落後 17 分鐘「還不算死」，放行
    #       SIGNAL_LOST_MIN   = 15  → 「15 分鐘沒心跳」，亮紅燈
    #   而藍牙送達延遲本來就常常落在 15~20 分鐘之間。
    #
    #   **正解：在資料內部量，不要拿外面的時鐘去減。**
    #   「我手上最新的一筆是什麼時候」減「我手上最後一筆心率是什麼時候」——
    #   這樣送達延遲會自動抵銷掉，因為兩邊延遲一樣。
    #
    #   而「資料整批太舊」是另一件事，前面 PIPELINE_DEAD_MIN 那關已經管了。
    #   **兩件事分開管，才不會互相製造窗口。**
    data_now = T(records[-1])
    if hrs:
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
        # ⚠ 2026-08-16 加：本來這裡沒有 else，動作量整條缺席就**直接往下走**，
        #   最後回綠燈。「這個通道壞了」跟「她一直在動所以沒事」在舊版裡
        #   長得一模一樣 —— 又是同一隻病。
        return Verdict(
            "unknown", "這一小時沒有任何動作量資料",
            [f"心率有 {len(hrs)} 筆，但 motion 一筆都沒有",
             "加速度計沒開、或這一段是舊版收的 —— 不動的判斷這段做不了"])

    # ── 閘門一：錶不在手上的分鐘，一律不參與不動判斷 ──────────
    #
    # 2026-08-16 實測：離腕放在充電座上的 219 分鐘，動作量中位 42、
    # **95.9% 落在任何合理的靜止門檻以下** —— 比睡著的人還靜。
    #
    # 所以「錶沒戴」跟「人沒動」在動作量上**原理上分不出來**。
    # 給家人用時這是最致命的失效方向：
    #     阿公把錶脫下來放床頭櫃 → 系統回報「睡得很安穩」。
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
    # 舊資料沒有這個欄位 —— 那些一律當**不可信**（2026-08-15/16 的資料
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
        # 沒有 mv 欄位的是 2026-08-15 以前收的舊資料，算 v1。
        thr = STILL_ACTIVITY_BY_VERSION.get(m.get("mv", 1))
        if thr is None:
            # **這個版本還沒在真實資料上校準過。**
            # 不要猜一個門檻硬判 —— 2026-08-16 就是這樣搞出一個
            # 「聽起來 89%／8%、實際 34% 誤判、而且分不出充電座」的門檻。
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
