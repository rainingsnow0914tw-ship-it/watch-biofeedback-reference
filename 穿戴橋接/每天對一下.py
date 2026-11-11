#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""沒有對，那些紀錄只是一堆躺在手錶裡的位元組。

而**這件事不能靠我每次記得做**——我每一框都是新的，會忘。
所以要變成一個指令。

## 順序是有理由的，不要調

```
① 震動史      ← 最先做。dumpsys 只留最近一段，**過了就沒了**
② 語音筆記    ← 手錶上的錄音，撈回來 + 本機 Whisper 轉錄
③ 生理資料    ← 從手機拉（手錶→藍牙→手機→這裡）
④ 判讀        ← 餵給 triage，寫進判斷日誌
```

**①放第一是因為它是唯一會消失的東西。** 其他四樣都在硬碟上，晚一分鐘做沒差；

**那 5 次全部發生在之後，等我想去查的時候已經被輪掉了。**

用法：
    python 每天對一下.py              # 今天"""

from __future__ import annotations

import json
import statistics
import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
import sys
from datetime import datetime, timedelta
from pathlib import Path

from 覆蓋率 import classify, headline

HERE = Path(__file__).resolve().parent
BIO = HERE.parent / "生理資料" / "手錶橋接"
VOICE = HERE.parent / "生理資料" / "語音筆記"
BUZZ = HERE.parent / "生理資料" / "震動史"


def step(n: str) -> None:
    print(f"\n{'─' * 58}\n{n}\n{'─' * 58}")


def run(script: str, *args: str) -> int:

    #
    #   子行程是直接寫終端機的，而父行程的 print 還卡在緩衝區裡 ——
    #   結果子行程的輸出會**跑到父行程的標題前面**，

    #   實際發生過：⑥ 那一節的進度條印在 ⑤ 的中間，我還以為 script 壞了。
    sys.stdout.flush()
    r = subprocess.run([sys.executable, str(HERE / script), *args],
                       cwd=str(HERE), encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    # ⚠ 而且**失敗要講出來**。原本這裡只是 return，


    if r.returncode != 0:
        print(f"  ⚠ {script} 離開碼 {r.returncode} —— **這一步沒跑完，上面缺的東西不代表沒事**")
        sys.stdout.flush()
    return r.returncode


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


def T(r: dict) -> datetime:
    return datetime.fromisoformat(r["t"])


def reconcile(day: str) -> None:
    """ """
    recs = load(day)
    if not recs:
        print(f"  ⚠ {day} 的生理資料還沒到電腦上 —— 對照做不了")
        return

    cover = classify(recs, day)
    states = cover["states"]
    print("  " + headline(cover, day))

    def valid_record(r: dict) -> bool:
        stamp = r.get("t", "")
        if not stamp.startswith(day + "T") or len(stamp) < 16:
            return False
        index = int(stamp[11:13]) * 60 + int(stamp[14:16])
        return (index < len(states) and states[index] == "valid"
                and r.get("on_body") in ("on", "inferred_on"))

    marks = [r for r in recs if r.get("type") == "mark"]
    notifs = [r for r in recs if r.get("type") == "notif"]
    hr = [(T(r), r["hr"]) for r in recs
          if r.get("type") == "hr_ibi" and (r.get("hr") or 0) > 0
          and r.get("status") == 1 and valid_record(r)]
    mo = [(T(r), r) for r in recs if r.get("type") == "motion" and valid_record(r)]

    # 震動史（本機存檔，可能有好幾次抓取，去重）
    buzz = []
    bf = BUZZ / f"{day}.jsonl"
    if bf.exists():
        seen = set()
        for line in bf.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                b = json.loads(line)
            except Exception:
                continue
            k = (b["t"], b["ms"], b["pkg"])
            if k not in seen:
                seen.add(k); buzz.append(b)

    # 語音筆記
    voice = []
    vf = VOICE / f"{day}.jsonl"
    if vf.exists():
        for line in vf.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                voice.append(json.loads(line))
            except Exception:
                continue

    print(f"  資料 {len(recs)} 筆 / 按鈕 {len(marks)} 次 / 語音 {len(voice)} 段 / "
          f"通知 {len(notifs)} 則 / 震動史 {len(buzz)} 筆")

    if not marks and not voice:
        print("  這一天沒有按鈕也沒有語音 —— 沒有東西可以對")
        return

    def around(t: datetime, lo: int, hi: int):
        a, b = t + timedelta(minutes=lo), t + timedelta(minutes=hi)
        span = hi - lo
        valid = 0
        for i in range(span):
            moment = a + timedelta(minutes=i)
            index = moment.hour * 60 + moment.minute
            if moment.strftime("%Y-%m-%d") == day and index < len(states) and states[index] == "valid":
                valid += 1
        if span <= 0 or valid / span < 0.70:
            return [], [], valid, span
        h = [v for tt, v in hr if a <= tt <= b]
        m = [r for tt, r in mo if a <= tt <= b]
        act = [x.get("activity") or 0 for x in m if x.get("trust") is True]
        return h, act, valid, span

    for m in sorted(marks, key=T):
        t = T(m)
        print(f"\n  ■ {t:%H:%M:%S}  {m.get('label')}")
        for lo, hi, name in [(-10, 0, "前 10 分"), (0, 10, "後 10 分")]:
            h, act, valid, span = around(t, lo, hi)
            line = f"     {name}（有效 {valid}/{span} 分）："
            if span <= 0 or valid / span < 0.70:
                line += "資料不足，不能作生理對照"
            else:
                line += f"心率中位 {statistics.median(h):.0f}" if h else "心率無資料"
                if act:
                    line += f"　動作中位 {statistics.median(act):.0f}　最大 {max(act):.0f}"
            print(line)


        #


        #

        #   · 往後看是浪費 —— 之後發生的事不可能是原因

        #     一分鐘只是**下限**，愣一下就三分鐘了
        #


        back, fwd = (300, 60) if m.get("tag") == "buzz" else (300, 300)
        near_n = [n for n in notifs
                  if -back <= (T(n) - t).total_seconds() <= fwd]
        for n in near_n[:4]:
            d = (T(n) - t).total_seconds()
            print(f"     通知 {d:+.0f}s　{n.get('pkg', '').split('.')[-1]}"
                  f" / {n.get('title', '')[:24]}")
        # 震動史：±3 分鐘
        near_b = []
        for b in buzz:
            try:
                bt = datetime.strptime(f"{t.year}-{b['t']}", "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if -back <= (bt - t).total_seconds() <= fwd:
                near_b.append((bt, b))
        near_b.sort()
        for bt, b in near_b[:6]:
            mine = "  ★ 我們的確認震（她按下去那一刻）"                 if "watchbridge" in b["pkg"] else ""
            print(f"     震動 {(bt - t).total_seconds():+.0f}s　{b['ms']}ms"
                  f"　{b['pkg'].split('.')[-1]}{mine}")


        #


        #


        if m.get("tag") == "buzz" and near_b:
            ours = [x for x in near_b if "watchbridge" in x[1]["pkg"]]
            others = [x for x in near_b if "watchbridge" not in x[1]["pkg"]]
            if ours and others:
                press = ours[-1][0]
                cause = [x for x in others if x[0] < press]
                if cause:
                    gap = (press - cause[-1][0]).total_seconds()
                    print(f"     ⏱ **量到的反應時間 {gap:.0f} 秒**"
                          f"（{cause[-1][1]['pkg'].split('.')[-1]} 震 → 她按下去）")

        if m.get("tag") == "buzz" and not near_n and not near_b:

            print("     ⚠ 前後沒有通知也沒有震動記錄 —— "
                  "**這代表我沒抓到，不代表沒震**（震動史會被輪掉、通知白名單可能漏）")

    for v in voice:
        t = datetime.fromisoformat(v["t"])
        print(f"\n  🎤 {t:%H:%M:%S}　「{v.get('text', '')}」")
        for lo, hi, name in [(-90, 0, "前 90 分"), (0, 30, "後 30 分")]:
            h, act, valid, span = around(t, lo, hi)
            line = f"     {name}（有效 {valid}/{span} 分）："
            if span <= 0 or valid / span < 0.70:
                line += "資料不足，不能作生理對照"
            else:
                line += f"心率中位 {statistics.median(h):.0f}" if h else "心率無資料"
                if act:
                    line += f"　動作中位 {statistics.median(act):.0f}"
            print(line)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    day = next((a for a in sys.argv[1:] if a.startswith("202")),
               datetime.now().strftime("%Y-%m-%d"))
    print(f"═══ 對一下：{day} ═══")

    step("⓪ 先確認手錶跟手機還連著（斷了就自己救）")

    #   那天所有東西看起來都正常 —— 錶面、心率、電量 —— 只有資料沒進手機。

    #   如果不先修這一條，後面拉回來的都是舊資料，而報告會看起來很正常。


    #
    #   真正的答案在第③步之後才知道：**拉回來的資料新不新。**

    #   看不到就明講看不到，把判斷留給資料本身。
    r = subprocess.run(["sh", str(HERE / "手錶失聯救回來.sh")],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(HERE), creationflags=_NO_WINDOW)
    txt = (r.stdout or "") + (r.stderr or "")
    if "找不到 phone" in txt:
        print("  手機沒接在電腦上 —— **這一步跳過，不代表斷線**")
        print("  （真正的答案看第③步拉回來的資料新不新）")
    else:
        print(txt.strip() or "（沒有輸出）")

    step("① 震動史（最先做 —— 它是唯一會消失的東西）")
    run("震動溯源.py")

    step("② 語音筆記：撈回來 + 本機轉錄")
    run("語音筆記撈回來.py")

    step("③ 生理資料：從手機拉回來 + 判讀")
    run("watch_puller.py", "--once")

    step("③.5 資料到底新不新（這才是連線好不好的真答案）")
    recs = load(day)
    if not recs:
        print("  今天還沒有任何資料")
    else:
        newest = max(T(r) for r in recs)
        age = (datetime.now() - newest).total_seconds() / 60
        mark = "OK" if age < 30 else ("⚠" if age < 120 else "🔴")
        print(f"  {mark} 最新一筆 {newest:%m-%d %H:%M}，落後 {age:.0f} 分鐘")
        if age >= 30:
            print("     （手錶每 5 分鐘送一次、藍牙有排隊，落後 15~20 分鐘算正常；"
                  "超過一小時就要看連線）")


    #    **連續三天一筆沒進來，而這支腳本從頭到尾都是綠的** ——
    #    因為第③步走的是手錶那條管路（我們自己的 app），它一直好的。
    #    一個系統有兩條管路，健康檢查只查一條，就等於沒查。
    step("③.6 手機那條管路（三星的睡眠判讀走這條，跟手錶是兩條線）")
    if run("手機資料接回來.py", "--days", "3"):
        print("  ⚠ 手機那條沒通 —— 上面有寫怎麼修。")
        print("     ⚠ 沒通的時候『她昨晚睡了沒』就沒有答案，")
        print("       因為我們自己的睡眠偵測是壞的（見 跨框交接.md 2026-08-30）。")
    step("④ 今天怎麼樣")


    #   舊的逐筆版留在 reconcile()，要細節的時候還叫得到。
    run("摘要層.py", day)
    import 今日報告
    print(今日報告.report(day))


    #


    #
    #   ④ 是體檢表（有沒有事），⑤ 是敘事（這跟我有什麼關係）。


    #
    #   我確實撈到了、也轉錄了 —— 但那兩段是**昨天下午錄的**，


    #


    #
    #   所以這一節看的是**檔案的修改時間**（最近撈到的），不是錄音的日期。
    step("④.5 最近撈到的語音（不管它屬於哪一天）")
    fresh = []
    for f in sorted(VOICE.glob("2026-*.jsonl")):
        if datetime.now().timestamp() - f.stat().st_mtime > 36 * 3600:
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                fresh.append((f.stem, json.loads(line)))
            except Exception:
                continue
    if not fresh:
        print("  最近 36 小時沒有新撈到的語音")
        print("  ⚠ 但那是「我沒撈到」——**手錶／手機連不上的時候也長這樣**")
    else:
        print(f"  最近撈到 {len(fresh)} 段：")
        for day, v in fresh:
            t = v.get("t", "")[11:19]
            print(f"\n    {day} {t}")
            print(f"    「{v.get('text', '')}」")

    step("⑤ 妳的一天")
    import 今天的故事
    print(今天的故事.故事(day))


    #   答案是會，但**這件事有期限**。


    step("⑥ 還差多少")
    run("校準進度.py")


    #   查證結果：不是我（逐字稿裡我只執行過把它關掉的指令）。
    #   **但它確實自己從 0 變回 1 了，而我不知道是什麼做的。**

    #   下次它再變，變化前後發生什麼就查得到。


    step("⑥.5 四條管路（今天到底哪一段不對）")
    run("四條管路.py")

    step("⑦ 設定有沒有自己變")
    run("誰動了設定.py")

    step("完成")
    print("  ⚠ 上面每一行「沒查到」都要當成『我沒看到』，不是『沒發生』。")


if __name__ == "__main__":
    main()
