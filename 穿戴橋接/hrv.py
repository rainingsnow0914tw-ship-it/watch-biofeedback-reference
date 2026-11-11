#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""hrv.py —— 從**真正相鄰**的心跳間隔算 HRV。


舊版的 API 是 `rmssd(ibis: list[int])` —— 收一串心跳間隔就算。
那個介面**本身就是 bug 的來源**，因為它讓呼叫端可以把
而函式無從得知哪兩拍其實不相鄰。

    偏差中位數 **+15.3%**　p95 **+50.6%**　最壞 **+440%**

刪掉一拍之後，它的前後兩拍在 list 裡變成相鄰 ——
掉封包的情況下內插反而更糟 —— 所以正解是**切斷**，不是補。

實際上 JSONL 裡**一個封包只有一個純量 status**。
zip 一個純量會截斷成一拍，或直接 TypeError ——
**那道濾網從來沒有真的在跑過。**


    rmssd_from_pairs([(651, 689), (689, 652), ...])

配對由 ibi_windows.py 產生，它保證每一對都是
**時間上真的相鄰、而且兩邊都不是雜訊**。

   同時段的基線比。不同長度的 RMSSD 不能互相比較"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_PAIRS = 30
"""⚠️ 注意這是**對數**不是拍數，而且真正的門檻是**時間長度**（見 ibi_windows.py
   的 WINDOW_S=300）。這裡只是最後一道保險。
   30 拍其實只有 17~30 秒，而且心率越高窗口越短：
   **恰好在最需要長窗口的時候變得最短**，方向完全相反。"""


@dataclass
class HrvResult:
    rmssd_ms: float
    pairs_used: int
    window_s: float
    artifact_pct: float
    confidence: float = 1.0
    trusted: bool = True         # 低可信＝在動或雜訊多，錶腦不准拿來下判斷
    unit_k: float = 1.0

    @property
    def 說法(self) -> str:
        """給人看的一句話。**一定要把窗口長度講出來** ——
        不同長度的 RMSSD 不能比，不寫等於埋一個以後會爆的雷。"""
        return (f"RMSSD {self.rmssd_ms:.1f} 毫秒"
                f"（{self.window_s:.0f} 秒窗口，{self.pairs_used} 對相鄰心跳，"
                f"雜訊 {self.artifact_pct:.1f}%，可信度 {self.confidence:.2f}"
                f"{'' if self.trusted else '・低可信，不拿來判斷'}）")


def rmssd_from_pairs(pairs: list[tuple[int, int]]) -> float | None:
    """
    從成對的相鄰心跳算 RMSSD。

        RMSSD = sqrt( Σ(後 − 前)² / n )

    每一對都必須是**時間上真的相鄰**的兩拍。
    這個函式不做任何過濾 —— 過濾是 ibi_windows 的事。
    """
    if len(pairs) < MIN_PAIRS:
        return None
    return math.sqrt(sum((b - a) ** 2 for a, b in pairs) / len(pairs))


def analyse(window) -> HrvResult | None:
    """吃一個 ibi_windows.Window，回一個結果。資料不足回 None。"""
    pairs = window.pairs()
    v = rmssd_from_pairs(pairs)
    if v is None:
        return None
    return HrvResult(
        rmssd_ms=v,
        pairs_used=len(pairs),
        window_s=window.duration_s,
        artifact_pct=window.artifact_pct,
        confidence=getattr(window, "confidence", 1.0),
        trusted=getattr(window, "trusted", True),
        unit_k=getattr(window, "unit_k", 1.0),
    )


# ── 舊介面：故意留著但會擋下來 ────────────────────────────────
def rmssd(ibis, statuses=None):
    """
    **舊介面，已停用。** 留著是為了讓還在呼叫它的程式直接壞掉、
    而不是安靜地算出灌水 15~440% 的數字。

    要算 HRV 請改成：
        from ibi_windows import windows_from_file
        from hrv import analyse
        for w in windows_from_file(路徑):
            print(analyse(w).說法)
    """
    raise NotImplementedError(
        "rmssd(ibis) 已停用：這個介面無法分辨哪兩拍其實不相鄰，"
        "實測會把 HRV 灌水 15~440%。請改用 ibi_windows.windows_from_file() + hrv.analyse()。"
    )


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from statistics import median

    from ibi_windows import windows_from_file

    p = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parent.parent / "生理資料" / "藍牙路徑" / "2026-08-15.jsonl"

    ws = windows_from_file(p)
    rs = [(w, analyse(w)) for w in ws]
    rs = [(w, r) for w, r in rs if r]
    print(f"{p.name}：{len(ws)} 個窗口，{len(rs)} 個算得出來\n")

    import datetime as dt
    for w, r in rs:
        t = dt.datetime.fromtimestamp(w.start_ms / 1000)
        print(f"  {t:%H:%M}  {r.rmssd_ms:6.1f} ms   {r.pairs_used:4} 對   雜訊 {r.artifact_pct:.2f}%")

    if rs:
        v = sorted(r.rmssd_ms for _, r in rs)
        print(f"\n  已量到的 5 分鐘窗口中位 {median(v):.1f} ms　"
              f"範圍 {v[0]:.1f} ~ {v[-1]:.1f} ms")
        print("  只代表上述可用窗口，不是全日 HRV；跨日比較還需同時段與同情境基線。")
