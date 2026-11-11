#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r""">   **讓你這些數據能夠跟我的生活有連結，而不是死板板的數據。**


```
【系統】盲區 4/24 小時
```


## 這支跟 `今日報告.py` 的分工

```
今日報告.py   一天的體檢表 —— 睡多久、心率多少、系統有沒有瞎
```


## 一條寫死的規矩：**只講看到的，不講推論的**

每一則的格式固定是：
```
身體當時在做什麼           ← 這也是事實，感測器量到的
兩者對不對得上             ← **這一行才是判斷，而且要能被否證**
```

是 `Reconcile` 那三條規矩的同一條。


用法：
    python 今天的故事.py                # 今天"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

from 覆蓋率 import classify, headline

HERE = Path(__file__).resolve().parent
BIO = HERE.parent / "生理資料" / "手錶橋接"
VOICE = HERE.parent / "生理資料" / "語音筆記"

TAG_NAME = {
    "unwell": "不舒服", "buzz": "震了", "wake": "起床", "sleep": "睡覺",
    "exercise": "運動", "medicine": "吃藥", "period": "生理期",
    "off_wrist": "脫錶", "other": "其他",
}


# **這是唯一有實測依據的一把尺**，不要憑感覺加新的級距。
LADDER = [(19, "幾乎沒動"), (79, "動作很少"), (192, "輕微活動"),
          (641, "走路"), (10**9, "比走路更激烈")]


def _load(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def T(r: dict) -> datetime:
    return datetime.fromisoformat(r["t"])


def 像什麼(act: float) -> str:
    for hi, name in LADDER:
        if act <= hi:
            return name
    return "?"


def 窗內(recs: list[dict], a: datetime, b: datetime, kind: str) -> list[dict]:
    return [r for r in recs if r.get("type") == kind and a <= T(r) < b]


def 身體怎麼樣(recs: list[dict], a: datetime, b: datetime,
             skip: datetime | None = None,
             states: list[str] | None = None, day: str | None = None) -> dict:
    """回那個時間窗裡身體在做什麼。

    ⚠ `skip` 是**錄音當下那一分鐘** —— 要排除掉。"""
    day = day or a.strftime("%Y-%m-%d")
    span = max(0, round((b - a).total_seconds() / 60))
    valid_minutes = {
        (a + timedelta(minutes=i)).strftime("%H:%M")
        for i in range(span)
        if states is not None
        and (a + timedelta(minutes=i)).strftime("%Y-%m-%d") == day
        and (a + timedelta(minutes=i)).hour * 60 + (a + timedelta(minutes=i)).minute < len(states)
        and states[(a + timedelta(minutes=i)).hour * 60 + (a + timedelta(minutes=i)).minute] == "valid"
    }

    def can_use(r: dict) -> bool:
        return (r.get("on_body") in ("on", "inferred_on")
                and (states is None or r["t"][11:16] in valid_minutes))

    hr = [r["hr"] for r in 窗內(recs, a, b, "hr_ibi")
          if can_use(r) and r.get("status") == 1 and (r.get("hr") or 0) > 40]
    mo = [r for r in 窗內(recs, a, b, "motion")
          if can_use(r) and r.get("trust") is True]
    if skip:
        mo = [r for r in mo if abs((T(r) - skip).total_seconds()) > 70]
    acts = [r.get("activity") or 0 for r in mo]
    worn = valid_minutes if states is not None else {
        r["t"][:16] for r in recs if a <= T(r) < b and can_use(r)}
    return {
        "hr": statistics.median(hr) if hr else None,
        "hr_hi": max(hr) if hr else None,
        "hr_n": len(hr),
        "act": statistics.median(acts) if acts else None,


        #
        #   **一天裡最值得看的事情，往往正是最短的那幾分鐘。**

        #   突然站起來、短暫的心悸這一整類事件。
        "act_hi": max(acts) if acts else None,
        "act_n": len(acts),
        "worn": len(worn),
        "分鐘": (b - a).total_seconds() / 60,
        "valid_minutes": len(valid_minutes) if states is not None else None,
    }


def 一句話(前: dict, 後: dict, tag: str | None) -> str:
    """**這一行是判斷，所以要能被否證。** 講不出來就老實說講不出來。"""
    if 後.get("valid_minutes") is not None and (
            後["分鐘"] <= 0 or 後["valid_minutes"] / 後["分鐘"] < 0.70):
        return (f"這段有效資料只有 {後['valid_minutes']:.0f}/{後['分鐘']:.0f} 分鐘；"
                "她說的事保留，但身體反應資料不足，不能對照")
    前段不足 = (前.get("valid_minutes") is not None and
              (前["分鐘"] <= 0 or 前["valid_minutes"] / 前["分鐘"] < 0.70))
    if 前段不足:
        前 = {**前, "act": None, "hr": None}
    if 後["hr_n"] == 0 and 後["act_n"] == 0:
        return "接下來沒有任何資料 —— 我不知道那段發生什麼"
    bits = []
    if 後["act"] is not None:
        s = f"動作 {像什麼(後['act'])}"

        #
        # ⚠ 但**不可以只看有沒有跨級距**。第一版只比 `像什麼()` 的結果，

        #   中位 18.9、尖峰 19.4，**差 0.5 卻剛好跨過級距線**，
        #   於是一個什麼都沒發生的窗口被講成有事發生。
        #
        #   級距線是為了讓數字讀得懂，**不是事件偵測器**。

        if (後["act_hi"] is not None and 後["act"] is not None
                and 像什麼(後["act_hi"]) != 像什麼(後["act"])
                and 後["act_hi"] >= max(後["act"] * 2, 40)):
            s += f"，但中間有一下到「{像什麼(後['act_hi'])}」（尖峰 {後['act_hi']:.0f}）"
        else:
            s += f"（{後['act']:.0f}）"
        if 前["act"] is not None and 前["act"] > 0:
            r = 後["act"] / 前["act"]
            if r >= 2:
                s += f"，比前面高 {r:.1f} 倍"
            elif r <= 0.5:
                s += f"，掉到前面的 {r:.0%}"
        bits.append(s)
    if 後["hr"] is not None:
        s = f"心率 {後['hr']:.0f}"
        if 後["hr_hi"] is not None and 後["hr_hi"] - 後["hr"] >= 15:
            s += f"（最高衝到 {後['hr_hi']:.0f}）"
        if 前["hr"] is not None:
            d = 後["hr"] - 前["hr"]
            if abs(d) >= 8:
                s += f"　{'↑' if d > 0 else '↓'}{abs(d):.0f}"
        bits.append(s)
    if tag == "off_wrist" and 後["worn"] == 0:
        bits.append("**之後就沒戴了 —— 資料同意妳**")
    elif tag == "off_wrist" and 後["worn"] > 3:
        bits.append("⚠ **但資料還說戴著 —— 這是個 bug，要查**")
    result = "　".join(bits)
    return ("前段資料不足，只描述後段：" + result) if 前段不足 else result


def 故事(day: str) -> str:
    recs = _load(BIO / f"{day}.jsonl")
    voice = _load(VOICE / f"{day}.jsonl")
    L: list[str] = []

    def line(s: str = "") -> None:
        L.append(s)

    line(f"═══ {day}　妳的一天 ═══")
    cover = classify(recs, day)
    line("  " + headline(cover, day))
    if cover["quality"] not in ("sufficient", "partial"):
        line("  ⚠ 全日覆蓋不足；以下只保留有證據的局部事件，不能當整天狀態。")
    if not recs:
        line("  這一天一筆資料都沒有 —— **不是沒事，是不知道**")
        return "\n".join(L)


    events: list[tuple[datetime, str, str, str | None, str | None]] = []
    for m in recs:
        if m.get("type") == "mark":
            events.append((T(m), "按鈕", TAG_NAME.get(m.get("tag"), m.get("label", "?")),
                           m.get("tag"), None))
    for v in voice:
        events.append((datetime.fromisoformat(v["t"]), "語音",
                       (v.get("text") or "").strip(), None, v.get("t")))
    events.sort(key=lambda x: x[0])

    if not events:
        line("  今天沒有按鈕、也沒有語音 ——")
        line("  **那這一天只有生理資料，沒有辦法跟妳的生活對起來。**")
        return "\n".join(L)

    line(f"  {len(events)} 件事　（{sum(1 for e in events if e[1]=='語音')} 段語音、"
         f"{sum(1 for e in events if e[1]=='按鈕')} 次按鈕）")
    line()

    for t, kind, text, tag, v_t in events:
        前 = 身體怎麼樣(recs, t - timedelta(minutes=10), t, skip=t,
                     states=cover["states"], day=day)
        後 = 身體怎麼樣(recs, t, t + timedelta(minutes=20), skip=t,
                     states=cover["states"], day=day)
        mark = "🎤" if kind == "語音" else "▸ "
        line(f"  {t:%H:%M}  {mark} " + (f"「{text}」" if kind == "語音" else text))


        if kind == "語音":
            import 聽懂她說什麼 as 聽
            tags, notes = 聽.抽標籤(text, v_t)
            讀過 = any("阿寶親自讀過" in n for n in notes)
            # 三種狀態要**分得出來**，不可以長一樣：
            #   ① 我讀過，有標籤          → 印標籤

            #   ③ 我還沒讀                → 明講還沒讀，並附上關鍵字的猜測當提示


            if 讀過 and tags:
                line(f"         我讀成：{'、'.join(聽.NAME.get(x, x) for x in tags)}"
                     "　（我理解錯了跟我說）")
            elif 讀過:
                line("         我讀過了 —— 這句沒有對應的事件標籤，只當背景")
            else:
                for n in notes:
                    if n.startswith("🔸"):
                        line(f"         {n}")
                        break
            for sk in notes:
                if not sk.startswith("🔸") and "阿寶親自讀過" not in sk:
                    line(f"         ⊘ {sk}")
        line(f"         {一句話(前, 後, tag)}")
        line()

    # 沒有事件覆蓋到的大塊時間 —— **要講出來，不然看起來像一整天都對上了**
    covered = set()
    for t, *_ in events:
        for m in range(-10, 21):
            covered.add((t + timedelta(minutes=m)).strftime("%H:%M"))
    worn_min = [f"{i // 60:02d}:{i % 60:02d}"
                for i, state in enumerate(cover["states"]) if state == "valid"]
    gap = [m for m in worn_min if m not in covered]
    if gap:
        line(f"  ── 另外有 {len(gap)/60:.1f} 小時戴著、但沒有任何一句話對應 ──")
        line(f"     （{gap[0]} ~ {gap[-1]} 之間）")
        line("     **那段的數字只能自己看，接不到妳的生活。**")
    return "\n".join(L)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    day = next((x for x in sys.argv[1:] if x.startswith("202")),
               datetime.now().strftime("%Y-%m-%d"))
    print(故事(day))


if __name__ == "__main__":
    main()
