"""合成金絲雀 —— 定期證明 triage 的警報還會響。

## 為什麼要有這支

這個專案反覆發作同一隻病：**東西看起來在動，其實安靜地不做事。**
手冊第五區乙自己記著「同一隻病已經在這個專案發作過至少七次」，
而 2026-08-16 抓到的第三個洞是它最純粹的形態 ——
triage 拿最後一筆資料當「現在」，於是紅燈在物理上不可能亮，
影子模式那個「兩週零誤報」因此是假的。

修掉那一次，只解決了那一次。**下一個安靜不動作的東西還沒被發現，
因為沒有東西在找它。**

這支就是那個「在找它的東西」：餵保證會觸發的假資料進 `triage.assess()`，
斷言它真的照約定回應。不照約定就用非零離開碼喊出來。

    每一個「應該在壞掉時出聲」的東西，都要定期被證明它還會出聲。

## 🔴 這支自己的設計約束

**金絲雀壞掉的時候要炸得很大聲，不能靜靜通過。**

所以這裡刻意**沒有任何 try/except**。`import triage` 失敗、`assess` 簽章變了、
`Verdict` 欄位改名 —— 一律讓它整支炸掉，因為「金絲雀安靜地跳過檢查」
跟「警報器安靜地不響」是同一隻病，而那正是這支要防的東西。

## 怎麼跑

    python canary.py            # 全部檢查，全過回 0，任何一條沒過回 1
    python canary.py -v         # 連過了的也印出來

建議接在開機或每日排程上，失敗就出聲。

—— 5090 那台的阿寶寫，2026-08-17
   （應 使用者 那台的阿寶之邀：他對每個元件都有「我知道它應該怎樣」的偏見，
     所以由沒碰過硬體、只讀過 code 的外部視角來寫這些斷言）
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage  # noqa: E402  —— 不包 try/except 是刻意的，見檔頭

NOW = datetime(2026, 8, 17, 12, 0, 0)
"""固定時間，讓每次跑的結果都一樣。

用真實時鐘的話，測試會在某些時刻通過、某些時刻失敗，
而那種不穩定最後一定會被人用「重跑一次就好」帶過去。"""

FAILED: list[str] = []
SEEN_LEVELS: set[str] = set()


def rec(minutes_ago: float, type_: str, **kw) -> dict:
    """做一筆假資料。時間一律相對 NOW 往回算。"""
    return {"t": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
            "type": type_, **kw}


def check(name: str, records: list[dict], want_level: str,
          want_reason: str, why: str, verbose: bool = False) -> None:
    """跑一個案例，斷言**等級與理由都對**。

    ⚠ 為什麼理由也要比對，不能只看等級：

        「管線死了」跟「完全沒有資料」都回 unknown。只斷言 unknown 的話，
        一個把資料整包弄丟的 bug 會讓這條檢查**用錯誤的理由通過**。
        那就變成一個測到別的東西的測試 —— 又是同一隻病，只是換成長在測試裡。
    """
    v = triage.assess(records, now=NOW)
    SEEN_LEVELS.add(v.level)

    ok_level = v.level == want_level
    ok_reason = want_reason in v.reason
    if ok_level and ok_reason:
        if verbose:
            print(f"  OK   {name}\n         → [{v.level}] {v.reason}")
        return

    FAILED.append(name)
    print(f"  FAIL {name}")
    print(f"         應該回：[{want_level}] …{want_reason}…")
    print(f"         實際回：[{v.level}] {v.reason}")
    if not ok_level and ok_reason:
        print("         （理由對、等級不對 —— 多半是分級改了）")
    if ok_level and not ok_reason:
        print("         （等級對、理由不對 —— **可能是用錯誤的原因通過**，比整條失敗更危險）")
    print(f"         為什麼要有這條：{why}")


# ══════════════════════════════════════════════════════════════════
# 第一關：triage 這支的形狀還是我們以為的那個嗎
#
# 這一關在所有案例之前。形狀變了而沒人發現的話，
# 下面每一條斷言都會變成在測一個不存在的約定。
# ══════════════════════════════════════════════════════════════════

def check_shape() -> None:
    print("[1/4] 檢查 triage 的形狀")

    for attr in ("assess", "Verdict", "on_body_of", "SIGNAL_LOST_MIN",
                 "PIPELINE_DEAD_MIN", "STILL_WARN_MIN", "MIN_TRUST_RATIO",
                 "STILL_ACTIVITY_BY_VERSION"):
        if not hasattr(triage, attr):
            FAILED.append(f"triage 少了 {attr}")
            print(f"  FAIL triage 沒有 {attr} —— 這支金絲雀的假設已經過期了")

    v = triage.assess([], now=NOW)
    for attr in ("level", "reason", "evidence"):
        if not hasattr(v, attr):
            FAILED.append(f"Verdict 少了 {attr}")
            print(f"  FAIL Verdict 沒有 .{attr}")


# ══════════════════════════════════════════════════════════════════
# 第二關：門檻之間的關係
#
# 這一關來自這個家族 2026-08-11 的教訓：
#     「一個逾時單獨看永遠是對的，錯只存在於它跟外層／內層的關係裡。」
#
# 每個門檻各自看都很合理，但它們**互相會把對方關掉**，
# 而那種失效不會有任何錯誤訊息。
# ══════════════════════════════════════════════════════════════════

def check_thresholds() -> None:
    print("[2/4] 檢查門檻之間的關係")

    # 🔴 最重要的一條關係
    #
    # 「戴著但量不到心跳 → 紅燈」這條路，需要**管線還活著**才走得到 ——
    # 因為 assess() 會先問「還有沒有新資料進來」，管線死了就直接回 unknown。
    #
    # 所以如果 SIGNAL_LOST_MIN >= PIPELINE_DEAD_MIN，在「只剩心率這條通道
    # 停掉」的情境下，紅燈永遠等不到就被 unknown 攔截。
    # 兩個數字各自看都很合理，錯只存在於它們的關係裡。
    if triage.SIGNAL_LOST_MIN >= triage.PIPELINE_DEAD_MIN:
        FAILED.append("SIGNAL_LOST_MIN >= PIPELINE_DEAD_MIN")
        print(f"  FAIL SIGNAL_LOST_MIN({triage.SIGNAL_LOST_MIN}) "
              f">= PIPELINE_DEAD_MIN({triage.PIPELINE_DEAD_MIN})")
        print("         → 心率單獨停掉時，紅燈會被『管線死了』搶先攔截。")
        print("         這兩個數字各自看都合理，錯在它們的關係。")

    # 門檻被調成沒有意義的值。註解裡寫著這些全部「待校正」，
    # 所以它們**一定會被人改**，而改壞了不會有任何症狀。
    for name, lo, hi in (("SIGNAL_LOST_MIN", 5, 60),
                         ("PIPELINE_DEAD_MIN", 5, 120),
                         ("STILL_WARN_MIN", 10, 240)):
        val = getattr(triage, name)
        if not (lo <= val <= hi):
            FAILED.append(f"{name} 超出合理範圍")
            print(f"  FAIL {name} = {val}，不在 {lo}–{hi} 之間")
            print("         太小會一直誤報、太大等於關掉。兩種都不會報錯。")

    if not (0 < triage.MIN_TRUST_RATIO <= 1):
        FAILED.append("MIN_TRUST_RATIO 超出範圍")
        print(f"  FAIL MIN_TRUST_RATIO = {triage.MIN_TRUST_RATIO}，應該在 0–1 之間")

    # 🔴 如果每一個指標版本的靜止門檻都是 None，
    #    「不動太久」這件事就**整個偵測不到了**，而且是安靜的 ——
    #    assess() 會老實回 unknown，日誌看起來很正常，
    #    但沒有人會注意到那條路從此再也不會亮黃燈。
    calibrated = [k for k, t in triage.STILL_ACTIVITY_BY_VERSION.items()
                  if t is not None]
    if not calibrated:
        FAILED.append("沒有任何一個 mv 版本有校準過的門檻")
        print("  FAIL STILL_ACTIVITY_BY_VERSION 裡每一個版本都是 None")
        print("         → 不動偵測整條關閉，而且不會有任何症狀。")

    # 🔴 光看「有沒有任何一個版本有校準」不夠 ——
    #
    #    手錶現在正在產出的是**最新那個版本**。如果最新版本沒校準，
    #    不動偵測對「現在正在收的資料」就是關閉的，
    #    而舊版本還校準著，所以上面那條檢查會開開心心地通過。
    #
    #    突變測試實證（2026-08-17）：把 v4 從 50.0 改成 None，
    #    第一版金絲雀**全部通過** —— 因為造假資料的那支函式
    #    「挑一個有校準的版本」，於是它默默退回去測 v1。
    #    **金絲雀自己適應了故障，而不是報告故障。**
    if triage.STILL_ACTIVITY_BY_VERSION:
        newest = max(triage.STILL_ACTIVITY_BY_VERSION)
        if triage.STILL_ACTIVITY_BY_VERSION[newest] is None:
            FAILED.append(f"最新的指標版本 v{newest} 沒有校準門檻")
            print(f"  FAIL 最新的指標版本 v{newest} 的靜止門檻是 None")
            print("         → 不動偵測對**現在正在收的資料**是關閉的。")
            print("         舊版本還校準著，所以整體看起來很正常。")
            print("         （如果 v%d 是刻意還沒校準，這條就該一直紅著，"
                  "直到收滿真實睡眠資料校完為止。）" % newest)


# ══════════════════════════════════════════════════════════════════
# 第三關：警報真的會響嗎
# ══════════════════════════════════════════════════════════════════

def _calibrated_mv() -> tuple[int, float]:
    """挑一個有校準過門檻的指標版本來造假資料。

    不寫死 mv=4，是因為版本會往前走（v1→v2→v3→v4 已經換過四次）。
    寫死的話，換到 v5 的那天這支會安靜地繼續測一個沒人在用的版本。
    """
    ok = {k: t for k, t in triage.STILL_ACTIVITY_BY_VERSION.items() if t is not None}
    mv = max(ok)
    return mv, ok[mv]


def check_alarms(verbose: bool) -> None:
    print("[3/4] 檢查警報真的會響")
    mv, thr = _calibrated_mv()
    quiet = triage.SIGNAL_LOST_MIN + 3      # 久到足以觸發紅燈
    dead = triage.PIPELINE_DEAD_MIN + 10    # 久到足以判定管線死掉
    still = triage.STILL_WARN_MIN + 10      # 久到足以觸發不動警告

    def hr_stream(start: float, end: float, **kw) -> list[dict]:
        return [rec(m, "hr", bpm=70, on_body="on", **kw)
                for m in range(int(start), int(end), -1)]

    def motion_stream(start: float, end: float, activity: float,
                      trust: bool = True, **kw) -> list[dict]:
        return [rec(m, "motion", activity=activity, mv=mv, trust=trust,
                    on_body="on", **kw)
                for m in range(int(start), int(end), -1)]

    # ── 陽性對照 ────────────────────────────────────────────────
    #
    # 🔴 這兩條是整支金絲雀的地基，而且是我（外部視角）加的重點。
    #
    # 底下那些「不准回綠燈」的斷言有一個致命的共同弱點：
    # **如果 assess() 壞成「什麼都回 unknown」，它們會全部通過。**
    # 一支永遠通過的金絲雀，跟一個永遠不會響的警報器是同一個東西。
    #
    # 所以必須先證明綠燈**還回得出來**，那些「不准綠」才有意義。

    check("陽性對照：一切正常時回得出綠燈",
          hr_stream(59, 0) + motion_stream(59, 0, activity=thr * 20),
          "green", "看起來正常",
          "如果這條掛了，底下所有『不准回綠燈』的斷言都會變成永遠通過的空話",
          verbose)

    check("陽性對照：離腕充電是綠燈",
          hr_stream(59, 5) + [rec(1, "hr", bpm=0, on_body="off", charging=True, batt=88)],
          "green", "充電",
          "充電被誤判成異常的話會一直誤報，而誤報會把 LINE 那條通道燒掉",
          verbose)

    # ── 紅燈 ────────────────────────────────────────────────────
    #
    # 注意這個情境的形狀：**動作量還在送、只有心率停了。**
    # 整條管線一起死掉的話 assess() 會先回 unknown（那是對的，見下一條），
    # 所以紅燈的唯一入口就是「資料還在流，但心率量不到」。
    check("紅燈：戴著卻量不到心跳",
          hr_stream(59, quiet) + motion_stream(quiet - 1, 0, activity=thr * 20),
          "red", "量不到心跳",
          "這是唯一會叫人的那條路。它不響，整個系統就只是一台記錄器",
          verbose)

    # ── 管線死掉不准說「正常」 ──────────────────────────────────
    check("管線死掉 → 說不準，不是正常",
          hr_stream(dead + 60, dead) + motion_stream(dead + 60, dead, activity=thr * 20),
          "unknown", "沒有任何新資料進來",
          "8/16 那個洞的本體：管線死七小時，它照樣說『看起來正常』",
          verbose)

    check("完全沒有資料 → 說不準",
          [], "unknown", "完全沒有資料",
          "空輸入必須明確說不知道，不能落進任何一條判斷路徑",
          verbose)

    # ── 不知道戴沒戴，不准判身體 ────────────────────────────────
    check("on_body=unknown → 不准判身體",
          hr_stream(59, 1) + [rec(0.5, "hr", bpm=70, on_body="unknown")],
          "unknown", "不確定錶在不在手上",
          "沒戴的錶比睡著的人還靜 —— 不知道戴沒戴時說『正常』是最危險的假陰性",
          verbose)

    # ⚠ 這條測的是「**欄位完全缺席**」，跟上面那條 on_body="unknown" 不一樣。
    #
    #   triage 的註解特別強調：沒有 on_body 這個欄位時要回 "unknown"，
    #   **不是 "on"** ——「舊版預設 True 本身就是個 bug，不要把它繼承下來」。
    #
    #   突變測試實證（2026-08-17）：把 on_body_of 最後那個
    #   `return "unknown"` 改成 `return "on"`，第一版金絲雀全部通過 ——
    #   因為我造的每一筆假資料都乖乖帶了 on_body 欄位，
    #   那條「欄位不存在」的路從來沒有被走到過。
    check("完全沒有 on_body 欄位 → 不准當成戴著",
          [rec(m, "hr", bpm=70) for m in range(59, 0, -1)],
          "unknown", "不確定錶在不在手上",
          "舊資料沒有這個欄位。預設成『戴著』會讓離腕的錶被當成人在睡覺",
          verbose)

    check("錶脫下來且沒充電 → 黃燈",
          hr_stream(59, 5) + [rec(1, "hr", bpm=0, on_body="off", charging=False)],
          "yellow", "脫下來",
          "脫下來沒充電是預期外的狀態，要留痕但還不到警報",
          verbose)

    # ── 資料品質閘門 ────────────────────────────────────────────
    check("動作量取樣不可信 → 不准拿來判斷",
          hr_stream(59, 0)
          + motion_stream(59, 10, activity=1.0, trust=False)
          + motion_stream(9, 0, activity=1.0, trust=True)
          + [rec(0.2, "hr", bpm=70, on_body="on")],
          "unknown", "取樣不足",
          "取樣一低數值會被灌大。8/16 就是拿被灌大的數字校出一個假門檻",
          verbose)

    check("這一小時沒有動作量 → 說不準，不是正常",
          hr_stream(59, 0),
          "unknown", "沒有任何動作量資料",
          "『通道壞了』跟『她一直在動所以沒事』在舊版裡長得一模一樣",
          verbose)

    # ⚠ 這條的假資料有個陷阱，我第一次就造錯了：
    #   assess() 先看**最後一筆**的 on_body。如果最後一筆是離腕的動作量，
    #   會在「錶脫下來了」那條就回黃燈，根本走不到動作量的閘門。
    #   要測的是「戴著，但這一小時的動作量全部是離腕時收的」，
    #   所以最後一筆必須是 on_body="on" 的心率。
    check("錶整小時都不在手上 → 不准做不動判斷",
          hr_stream(59, 0)
          + [rec(m, "motion", activity=1.0, mv=mv, trust=True, on_body="off")
             for m in range(59, 1, -1)]
          + [rec(0.2, "hr", bpm=70, on_body="on")],
          "unknown", "錶都不在手上",
          "阿公把錶放床頭櫃 → 系統回報『睡得很安穩』，這是最致命的失效方向",
          verbose)

    # ── 未校準的指標版本 ────────────────────────────────────────
    uncal = [k for k, t in triage.STILL_ACTIVITY_BY_VERSION.items() if t is None]
    if uncal:
        check(f"指標版本 v{uncal[0]} 沒校準過 → 說不準，不准猜",
              hr_stream(59, 0)
              + [rec(m, "motion", activity=1.0, mv=uncal[0], trust=True, on_body="on")
                 for m in range(59, 0, -1)],
              "unknown", "還沒校準",
              "猜一個門檻填進去，會安靜地給出看起來合理的錯答案",
              verbose)

    # ── 不動偵測 ────────────────────────────────────────────────
    check("連續不動太久 → 黃燈",
          hr_stream(still + 20, 0)
          + motion_stream(still + 20, 0, activity=thr / 10),
          "yellow", "沒動",
          "不動偵測是唯一在人昏倒但心跳還在時會發現的路徑",
          verbose)


def check_default_now(verbose: bool) -> None:
    """不傳 now 的那條路 —— 專門守 2026-08-16 那個 bug 的本體。

    🔴 **這一條是這支金絲雀第一版漏掉的，而且漏得很諷刺。**

    為了每次跑的結果一樣，上面每個案例都固定傳 `now=NOW`。
    但 8/16 那個 bug 的本體正是「**沒有傳 now 的時候，拿什麼當現在**」——
    `now = now or T(records[-1])`，讓資料自己定義時間。

    固定傳 now，就等於永遠不會執行到那個 `or` 後面。
    於是整支金絲雀對它要防的那個 bug 完全瞎掉。

    實測（2026-08-17，寫完第一版之後做的突變測試）：
        把 `now = now or T(records[-1])` 原樣塞回 triage.py，
        第一版金絲雀 12 條**全部通過、離開碼 0**。

    所以這一條刻意用**真實時鐘**：餵一批明顯過期的資料、不傳 now，
    要求它回「管線死了」。如果有人把預設值改回讓資料自己定義時間，
    這批過期資料在它眼裡就會變成新鮮的，於是回綠燈 —— 這條就會叫。

    代價是這一條不像其他條那樣完全確定（它依賴真實時鐘），
    但這是唯一測得到那條路的方法。
    """
    print("[4/4] 檢查「沒傳 now」時拿什麼當現在")
    mv, thr = _calibrated_mv()
    real_now = datetime.now()
    stale_from = triage.PIPELINE_DEAD_MIN + 90    # 遠比「管線死了」的門檻還舊
    stale_to = triage.PIPELINE_DEAD_MIN + 30

    def old(minutes_ago: float, type_: str, **kw) -> dict:
        return {"t": (real_now - timedelta(minutes=minutes_ago)).isoformat(),
                "type": type_, **kw}

    records = [old(m, "hr", bpm=70, on_body="on")
               for m in range(stale_from, stale_to, -1)]
    records += [old(m, "motion", activity=thr * 20, mv=mv, trust=True, on_body="on")
                for m in range(stale_from, stale_to, -1)]

    v = triage.assess(records)      # ← 刻意不傳 now，這就是重點
    SEEN_LEVELS.add(v.level)

    if v.level == "unknown" and "沒有任何新資料進來" in v.reason:
        if verbose:
            print(f"  OK   不傳 now 時用真實時鐘\n         → [{v.level}] {v.reason}")
        else:
            print("  OK   不傳 now 時用真實時鐘")
        return

    FAILED.append("不傳 now 時沒有用真實時鐘")
    print("  FAIL 餵了一批 30–110 分鐘前的舊資料、不傳 now")
    print(f"         應該回：[unknown] …沒有任何新資料進來…")
    print(f"         實際回：[{v.level}] {v.reason}")
    print("         → 「現在」很可能又是由資料自己定義的（8/16 那個 bug 的本體）。")
    print("         那會讓管線死掉時，判斷層照樣回報一切正常。")


def main() -> int:
    ap = argparse.ArgumentParser(description="triage 合成金絲雀")
    ap.add_argument("-v", "--verbose", action="store_true", help="連過了的也印出來")
    args = ap.parse_args()

    print(f"合成金絲雀 —— 基準時間 {NOW:%Y-%m-%d %H:%M}\n")
    check_shape()
    check_thresholds()
    check_alarms(args.verbose)
    check_default_now(args.verbose)

    # 🔴 最後一道，也是最容易被忽略的一道。
    #
    # 上面每一條都可能「各自通過」而整體其實是死的：
    # 只要 assess() 退化成什麼都回 unknown，所有「不准回綠燈」的斷言
    # 都會開開心心地通過。
    #
    # 所以這裡直接要求：四種等級在這一輪裡**全部真的出現過**。
    # 這一條掛掉的意思是「判斷層塌成一種答案了」，而它塌掉不會有錯誤訊息。
    print("\n[終] 四種等級都真的出現過嗎")
    missing = {"green", "yellow", "red", "unknown"} - SEEN_LEVELS
    if missing:
        FAILED.append("等級覆蓋不全")
        print(f"  FAIL 這一輪從來沒出現過：{'、'.join(sorted(missing))}")
        print("         判斷層可能已經塌成單一答案 —— 那種壞法不會有錯誤訊息。")
    else:
        print("  OK   green / yellow / red / unknown 全部出現過")

    # 用 OK / X 純文字標記，不用 ✓✗ 或 emoji ——
    # 這台的主控台是 cp950，✗ 直接讓整支 UnicodeEncodeError 炸掉。
    # （家規早就寫過這條，我第一版還是踩了。）
    print()
    if FAILED:
        print(f"X 金絲雀死了 —— {len(FAILED)} 條沒過：")
        for f in FAILED:
            print(f"    - {f}")
        print("\n在修好之前，triage 的判斷不能當數。")
        return 1

    print("OK 金絲雀活著 —— 警報路徑全部確認可達")
    return 0


if __name__ == "__main__":
    sys.exit(main())
