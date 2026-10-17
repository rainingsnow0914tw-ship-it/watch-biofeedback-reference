#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ibi_windows.py —— 把手錶送來的心跳封包，切成「真的可以拿來算 HRV」的窗口。

為什麼要單獨一支（2026-08-15，三個 agent + 十一次交叉驗證查出來的）：

    HRV 量的是**相鄰兩下心跳差多少**。所以「相鄰」這件事必須是真的。
    而我原本的 hrv.py 把一整個小時、來自幾百個封包的心跳間隔串在一起算，
    等於在每個封包接縫上憑空製造出一個假的「相鄰差」。

    在她真實資料上實測（30 拍窗口，也就是我當時的實際規格）：
        偏差中位數 +15.3%　p95 +50.6%　最壞 +440%

    所以切窗口不是工程細節，**它就是這個指標的定義本身**。

## 這支遵守的五條規則（每一條都有她自己的資料當證據）

1. **只把「時間對得上」的封包接起來。**
   判準：後一包的 IBI 總和，要能對上兩包 sensor_t 的差（容許 JOIN_TOL_MS）。
   對不上就是中間掉了心跳 —— 那裡必須切斷，不能橋接。
   （她的資料裡，非空封包之間只有 64.1% 的時間真的有心跳送到，
     698 個接縫有 202 個缺超過 2 秒，最糟一個缺 7,961 秒。）

2. **窗口用 5 分鐘。**
   在她資料上量：60 秒窗口對 5 分鐘的誤差中位數 40.6%、120 秒 22.1%、180 秒 12.6%。
   Task Force 1996 的標準短時窗口本來就是 5 分鐘。

3. **每段連續資料的前 60 秒丟掉。**
   量測剛開始時感測器還在穩定。實測：ICC 0.649→0.772，誤差中位數 15.4%→11.1%。

4. **絕不把幾個小窗口的 RMSSD 平均起來。**
   數學上必輸（Jensen 不等式：mean(√x) ≤ √(mean x)）。
   實測：平均六個 10 秒窗口 r=0.792，直接用同一批拍算一個 60 秒窗口 r=0.784 ——
   沒有變好，還多了 −2.1% 的系統性偏差。
   **有 N 秒的乾淨連續資料，就把它們全部合起來算一個值。**

5. **雜訊超過 5% 的窗口整個丟掉。**
   Kubios 的預設接受門檻。而且她的資料主要問題是**掉封包**不是零星雜訊，
   掉封包的情況下內插反而更糟 —— 所以這裡只做「標記 + 丟窗口」，不做內插。

⚠️ 這支**不算 HRV**，只負責切。算的事情交給 hrv.py。
   兩件事分開，才不會又像上次那樣混在一起錯。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# ── 參數（每一個都附出處，不要憑感覺調） ──────────────────────

JOIN_TOL_MS = 1000
"""兩個封包能不能接起來的容差。
IBI 總和跟 sensor_t 差距在這個範圍內才算「中間沒掉拍」。
1 秒 ≈ 一到兩下心跳，比這個大就一定漏了東西。"""

WINDOW_S = 300
"""Task Force 1996 的標準短時窗口。她的資料實測也支持這個長度。"""

WARMUP_S = 60
"""每段連續資料開頭丟掉的秒數 —— 感測器剛咬上時最不穩。"""

MAX_ARTIFACT_PCT = 5.0
"""窗口裡被標記為雜訊的比例上限。Kubios 的預設接受門檻。"""

IBI_MIN_MS, IBI_MAX_MS = 300, 2000
"""生理上可能的範圍。300ms=200bpm，2000ms=30bpm。"""

ECTOPIC_RATIO = 0.20
"""Malik 的 20% 規則：跟局部中位數差超過兩成，視為早搏或漏拍。"""


@dataclass
class Beat:
    ms: int          # 這一拍跟前一拍相隔幾毫秒
    t_ms: int        # 這一拍發生的絕對時間（epoch ms，從 sensor_t 回推）
    artifact: bool   # 是不是雜訊


@dataclass
class Window:
    beats: list[Beat]
    start_ms: int
    end_ms: int

    @property
    def duration_s(self) -> float:
        return (self.end_ms - self.start_ms) / 1000

    @property
    def artifact_pct(self) -> float:
        return 100.0 * sum(1 for b in self.beats if b.artifact) / max(1, len(self.beats))

    @property
    def clean(self) -> list[int]:
        """乾淨的那些拍。**注意：這些拍不保證彼此相鄰** ——
        算 RMSSD 時要用 pairs()，不要直接對這個列表做相鄰差。"""
        return [b.ms for b in self.beats if not b.artifact]

    def pairs(self) -> list[tuple[int, int]]:
        """
        真正相鄰、而且兩邊都乾淨的成對心跳。**RMSSD 只能用這個算。**

        這是整支程式的重點：雜訊拍不是「刪掉」，是**把它兩側的配對一起作廢**。
        刪掉會讓它的前後兩拍變成假的相鄰 —— 那正是舊版 +440% 的來源。
        """
        out = []
        for a, b in zip(self.beats, self.beats[1:]):
            if not a.artifact and not b.artifact:
                out.append((a.ms, b.ms))
        return out


def _load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        # 2026-10-03：離腕 PPG 曾在 status=1 時仍吐出看似合理的 IBI。
        # HRV 比心率更依賴真實相鄰拍；明確 off、unknown 或無效狀態
        # 都不可進連續段，空窗要靠下面的 sensor_t 閘門切開。
        if (r.get("type") == "hr_ibi" and r.get("ibi") and r.get("sensor_t")
                and str(r.get("on_body")) in ("on", "inferred_on", "True")
                and r.get("status") == 1):
            out.append(r)
    return out


def contiguous_runs(records: list[dict]) -> list[list[Beat]]:
    """
    把封包串成「時間真的接得上」的連續段。

    ## sensor_t 是封包的**第一拍**（2026-08-16 實測定案）

    這件事我原本猜錯了，寫成「最後一拍」，所以每一拍的絕對時間都往回推 ——
    大包最多把時間放早 378 秒。

    怎麼確定的：用寫檔當下的牆上時間 `t` 去對 `sensor_t`。
    小包（1~2 拍）量出兩者是同一個時鐘（差 +120 ms）。
    然後看大包：`t - sensor_t` 全部都 **≥ 整包時長**，而不是接近零。
        · 若 sensor_t 是最後一拍 → t - sensor_t 應該只有幾十毫秒的送達延遲
        · 若 sensor_t 是第一拍   → t - sensor_t 應該 ≈ 整包時長（或更多）
    實測 156 個大包全部符合後者。

    ⚠️ **但這個錯不影響 RMSSD 的數值。** A/B 實測：
        舊（往回推）中位 31.7 ms，新（往後推）中位 31.8 ms。
        因為大包裡那幾百拍本來就真的相鄰，錨點放哪裡不改變「相鄰差」。
        錯的是**窗口的時間標籤**（差幾分鐘），不是心率變異度本身。
        —— 記在這裡，是為了下次不要再被同一份稽核報告嚇到而推翻對的結論。

    ## 資料長什麼樣（實測 2026-08-15）

    三星送兩種形狀的封包，差非常多：
        小包：1~2 拍，每秒一個
        大包：一次幾百拍（最大 622 拍 ≈ 378 秒），中間隔幾分鐘才來一次
    **92% 的心跳在大包裡**（69,239 / 75,030）。所以真正撐起 HRV 的是大包，
    而大包內部的相鄰性是可信的。

    ## 接合判準

    前一包的**結尾**要接得上這一包的**開頭**：
        prev_end = prev_sensor_t + sum(prev_ibis)
        接得上 ⟺ |這一包的 sensor_t − prev_end| ≤ JOIN_TOL_MS

    ⚠️ 舊版拿「這一包的總和」去對兩包 sensor_t 的差 —— 那是「最後一拍」
    假設下的正確寫法。改了錨點就必須跟著改，兩者是一組的。
    """
    records = sorted(records, key=lambda r: r["sensor_t"])
    runs: list[list[Beat]] = []
    cur: list[Beat] = []
    prev_end: int | None = None

    for r in records:
        ibis = [int(x) for x in r["ibi"]]
        if not ibis:
            continue
        t0 = int(r["sensor_t"])

        # 上一包的結尾接不上這一包的開頭 → 中間掉了心跳，必須切斷
        if prev_end is not None and abs(t0 - prev_end) > JOIN_TOL_MS:
            if cur:
                runs.append(cur)
            cur = []

        # 每一拍的絕對時間從封包**開頭**往後推。
        # 第 i 拍的 t_ms 記的是「這一拍發生的時刻」＝ 前面所有間隔累加。
        t = t0
        for ms in ibis:
            t += ms
            cur.append(Beat(ms=ms, t_ms=t, artifact=False))
        prev_end = t

    if cur:
        runs.append(cur)
    return runs


def mark_artifacts(beats: list[Beat]) -> None:
    """
    就地標記雜訊，**不刪除**。

    兩道：
      ① 生理上不可能的值
      ② Malik 20% 規則 —— 跟**局部中位數**比，不是跟前一拍比。
         跟前一拍比會連鎖誤判：一個雜訊會把它後面那個正常拍也拖下水。
    """
    n = len(beats)
    for i, b in enumerate(beats):
        if not (IBI_MIN_MS <= b.ms <= IBI_MAX_MS):
            b.artifact = True

    # 局部中位數用前後各 5 拍（只看非離群的），避開被雜訊帶歪
    for i, b in enumerate(beats):
        if b.artifact:
            continue
        lo, hi = max(0, i - 5), min(n, i + 6)
        nb = sorted(x.ms for j, x in enumerate(beats[lo:hi], lo)
                    if j != i and IBI_MIN_MS <= x.ms <= IBI_MAX_MS)
        if len(nb) < 3:
            continue
        med = nb[len(nb) // 2]
        if abs(b.ms - med) > med * ECTOPIC_RATIO:
            b.artifact = True


def extract_windows(
    runs: list[list[Beat]],
    window_s: int = WINDOW_S,
    warmup_s: int = WARMUP_S,
    max_artifact_pct: float = MAX_ARTIFACT_PCT,
) -> list[Window]:
    """
    從連續段裡切出可用的窗口。

    窗口彼此**不重疊** —— 重疊的窗口會讓同一批心跳被算好幾次，
    之後做統計時會低估變異、高估信心。
    """
    out: list[Window] = []
    for run in runs:
        if len(run) < 10:
            continue
        mark_artifacts(run)

        t0 = run[0].t_ms + warmup_s * 1000     # 前 60 秒丟掉
        cur: list[Beat] = []
        w_start: int | None = None

        for b in run:
            if b.t_ms < t0:
                continue
            if w_start is None:
                w_start = b.t_ms
            cur.append(b)
            if b.t_ms - w_start >= window_s * 1000:
                w = Window(beats=cur, start_ms=w_start, end_ms=b.t_ms)
                if w.artifact_pct <= max_artifact_pct and len(w.pairs()) >= 30:
                    out.append(w)
                cur, w_start = [], None
        # 尾巴不足一個窗口就丟掉 —— 不同長度的 RMSSD 不能互相比較
    return out


def windows_from_file(path: str | Path, **kw) -> list[Window]:
    return extract_windows(contiguous_runs(_load(Path(path))), **kw)


if __name__ == "__main__":
    import sys
    from statistics import median

    p = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parent.parent / "生理資料" / "藍牙路徑" / "2026-08-15.jsonl"
    recs = _load(p)
    runs = contiguous_runs(recs)
    print(f"檔案 {p.name}")
    print(f"  有 IBI 的封包 {len(recs)} 個")
    print(f"  切出連續段 {len(runs)} 段")
    lens = sorted((sum(b.ms for b in r) / 1000) for r in runs)
    if lens:
        print(f"  連續段長度：最短 {lens[0]:.0f} 秒　中位 {median(lens):.0f} 秒　最長 {lens[-1]:.0f} 秒")
        print(f"  ≥5 分鐘的連續段：{sum(1 for x in lens if x >= 300)} 段")
    ws = extract_windows(runs)
    print(f"  可用的 5 分鐘窗口：{len(ws)} 個")
    if ws:
        print(f"  雜訊比例中位數：{median(w.artifact_pct for w in ws):.2f}%")
