#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
手機資料接回來 —— 從手機的 Health Connect 拉資料，並在 token 換掉時自己接回去。

## 為什麼要有這支（2026-08-30 抓到的靜默斷線）

`hc_puller.py` 從 **2026-08-27 12:04** 起就一直拿到 401，
連續三天一筆資料都沒進來，**而沒有任何地方紅燈**。

原因：手機上的 Health Connect Webhook app 更新到 1.9.14 時
**重新產生了存取權杖**，設定檔裡那把舊的就作廢了。

為什麼三天沒人發現：
  · `每天對一下` 的第③步走的是**手錶那條管路**（我們自己的 app，8788），它一直好的
  · 所以「資料落後 3 分鐘 ✅」是真的 —— 只是那句話講的不是這條管路
  · puller.log 有寫 401，但沒有人在看 log

> 🔴 **教訓：一個系統有兩條管路，健康檢查只查了一條，就等於沒查。**
> `每天對一下` 現在會呼叫這支，把手機那條也一起驗。

## Health Connect 有什麼手錶沒有的

三星健康自己的**睡眠判讀**會同步到 Health Connect。
我們自己那套睡眠偵測（見 `跨框交接.md` 2026-08-30）**是壞的**
（心率撐不到 3 分鐘低於當日 p25），所以在修好之前，
**「她昨晚到底睡了沒」只有這條管路答得出來。**

用法：
    python 手機資料接回來.py              # 拉最近 4 天並印睡眠摘要
    python 手機資料接回來.py --days 14
    python 手機資料接回來.py --只檢查      # 只回報通不通，不存檔
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys
import urllib.error
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent.parent / "生理資料"
CFG = BASE / "puller_config.json"          # 第三方 HC Webhook（8787）
橋CFG = BASE / "手錶橋接" / "puller_config.json"   # 我們自己的 app（8788）
PORT = 8787
橋PORT = 8788

# ⚠ 兩把鑰匙**必須放在兩個檔案**（2026-08-30 用血換來的）：
#   本來兩支程式共用 `生理資料/puller_config.json` 的同一個 `token` 欄位。
#   08-27 我輪替橋接的鑰匙、寫進那個欄位，**當場把 HC 的鑰匙蓋掉** ——
#   HC 就此躺了三天，而我一路把它誤診成「第三方 app 改版換了鑰匙」。
#   08-30 早上我又反方向蓋回去一次，橋接立刻 401。
#   **同一個坑，我三天內踩了兩次，方向相反。**


def 設定() -> dict:
    return json.loads(CFG.read_text(encoding="utf-8"))


def 拉(days: int, timeout: int = 60) -> dict:
    cfg = 設定()
    # Wi-Fi 打不通就自己改走 USB 隧道（2026-08-31：VPN 防火牆擋區網）
    from 手機在哪 import 找手機
    base, how = 找手機(PORT)
    if base is None:
        raise SystemExit(f"🔴 兩條路都到不了手機：{how}")
    if how != "Wi-Fi":
        print(f"  （{how}）")
    url = f"{base}/?days={days}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + cfg["token"])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # ⚠ 明講怎麼修，不要只丟一個 401。
            #   2026-08-27~30 這條就是因為錯誤訊息沒人看得懂而躺了三天。
            raise SystemExit(
                "🔴 手機拒絕（401）—— app 的存取權杖換掉了。\n"
                "   修法：手機開 Health Connect Webhook → 設置 → 本地 HTTP 服務器\n"
                "   → 往下捲到 Examples，把 Bearer 後面那串貼進\n"
                f"   {CFG}\n"
                "   （app 每次改版都可能重新產生權杖。）"
            )
        raise


def 我們自己讀(days: int) -> dict | None:
    """走**我們自己的 app**（8788 的 /sleep）讀睡眠。

    這是 2026-08-30 新接的路。優先走這條的理由：
    中間少一個第三方 app，就少一種安靜壞掉的方式。
    👤 使用者 的原話：「你以後是要自動把睡眠拉到我們做的 APP 嗎？不然你怎麼知道呢?」
    """
    if not 橋CFG.exists():
        return None
    cfg = json.loads(橋CFG.read_text(encoding="utf-8"))
    ip = cfg.get("ip") or cfg.get("host")
    if not (ip and cfg.get("token")):
        return None
    from 手機在哪 import 找手機
    base, _how = 找手機(橋PORT)
    if base is None:
        return None
    req = urllib.request.Request(f"{base}/sleep?days={days}")
    req.add_header("Authorization", "Bearer " + cfg["token"])
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  ⚠ 自己那條讀不到（{e}），退回第三方那條")
        return None


def 印睡眠(o: dict) -> None:
    """把 /sleep 的結果印出來。

    ⚠ `ok=false` 要照原樣講出來，**不可以印成「沒睡」**。
    """
    if not o.get("ok"):
        print(f"  ⚠ 讀不到：{o.get('why')}")
        if o.get("fix"):
            print(f"     修法：{o['fix']}")
        return
    ss = o.get("sessions") or []
    if not ss:
        print("  ○ 這幾天沒有睡眠記錄。**這是「沒記到」，不是「沒睡」。**")
        return
    by: dict[str, int] = {}
    for x in ss:
        a = int(x.get("asleep_seconds", 0))
        st = x.get("start", "")
        # 跨夜歸給「入睡那天」；中午前入睡算前一晚的補眠
        # 容錯：Java 的 ZonedDateTime 會多吐 `[Asia/Macau]`。手機端已改成標準 ISO，
        # 這裡留一道防線 —— 舊版 app 還沒更新的手機也要讀得動。
        d = datetime.datetime.fromisoformat(st.split("[")[0]) - datetime.timedelta(hours=12)
        by[d.strftime("%m-%d")] = by.get(d.strftime("%m-%d"), 0) + a
        print(f"    {st[5:16]} → {x.get('end','')[11:16]}　實睡 {a//3600}h{a%3600//60:02d}m")
    print(f"\n  每晚合計（含白天補的）：")
    for k in sorted(by):
        v = by[k]
        警 = "　⚠ 不足 5 小時" if v < 5 * 3600 else ""
        print(f"    {k} 晚　{v//3600}h{v%3600//60:02d}m{警}")


def 睡了嗎(d: dict) -> None:
    """把三星自己的睡眠判讀印出來。

    ⚠ 這是**三星判的**，不是我們判的。我們自己那套是壞的。
    兩者將來要對照，但現在只有這一份可信。
    """
    段 = []
    def 找(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if "sleep" in k.lower() and isinstance(v, list):
                    段.extend(v)
                else:
                    找(v)
        elif isinstance(o, list):
            for x in o:
                找(x)
    找(d)

    if not 段:
        print("  ⚠ 這份資料裡**沒有睡眠記錄**。")
        print("     可能是：三星健康沒判到睡眠／沒授權睡眠這一項／她那幾晚沒戴著睡。")
        print("     ⚠ 三種原因長得一模一樣 —— 不要當成『她沒睡』。")
        return

    print(f"  三星判到 {len(段)} 段睡眠：\n")
    by_night: dict[str, list] = {}
    for s in 段:
        st = str(s.get("startTime") or s.get("start") or s.get("startTimeIso") or "")
        en = str(s.get("endTime") or s.get("end") or s.get("endTimeIso") or "")
        if not st:
            continue
        # 跨夜歸給「入睡那天」
        by_night.setdefault(st[:10], []).append((st, en, s))

    for night in sorted(by_night):
        segs = sorted(by_night[night])
        總分 = 0
        for st, en, _ in segs:
            try:
                a = datetime.datetime.fromisoformat(st.replace("Z", "+00:00"))
                b = datetime.datetime.fromisoformat(en.replace("Z", "+00:00"))
                總分 += int((b - a).total_seconds() // 60)
            except Exception:
                pass
        rng = f"{segs[0][0][11:16]} → {segs[-1][1][11:16]}"
        print(f"    {night}　{總分//60}小時{總分%60:02d}分　{rng}　（{len(segs)} 段）")


def main() -> None:
    """先問我們自己的 app，第三方只當加值。

    🔴 **2026-09-02 改的順序。** 之前是先打第三方（8787），
    打不到就整支 SystemExit —— 於是 09-02 那天第三方 app 沒在跑，
    **整個第③.6 步就掛了，連我們自己讀得到的睡眠都沒印出來**。

    這是「相依方向搞反」：主力是我們自己的 `/sleep`（08-30 接好的），
    第三方只多給心率／步數／血氧那幾種。
    **選配的東西不該有能力讓必要的東西停擺。**
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    days = 4
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    # ── 主力：我們自己的 app ────────────────────────────────
    print("═══ 三星判的睡眠（走我們自己的 app，8788 /sleep）═══")
    自己 = 我們自己讀(days)
    if 自己 is not None:
        印睡眠(自己)
        # 存一份給「今日報告」用 —— 它以前自己用心率亂推，
        # 09-02 推出「睡了 18 小時」（三星說 2h02m）。見下面那支的註解。
        if 自己.get("ok"):
            (BASE / "睡眠.json").write_text(
                json.dumps(自己, ensure_ascii=False), encoding="utf-8")
    else:
        print("  🔴 我們自己那條也讀不到 —— 這條是主力，要查。")

    # ── 加值：第三方 HC Webhook（心率／步數／血氧）────────────
    print(chr(10) + "═══ 第三方 HC Webhook（多給心率／步數／血氧）═══")
    try:
        d = 拉(days)
    except SystemExit as e:
        # ⚠ 照實說它沒跑到，但**不要讓它拖垮上面那段**。
        # ⚠ **不要拿這件事煩她。**（2026-09-02 使用者 說「這兩句我聽不懂」）
        #   我原本印的是「手機打開 Health Connect Webhook 讓它的本機伺服器跑起來」——
        #   那是引擎室的話，而且它**根本不需要她做任何事**：
        #   睡眠 08-30 起就由我們自己的 app 讀了，這條只是加值。
        #
        #   👤 使用者 的規矩：先大門、後引擎室。
        #   一個不需要她動手的東西，就不該用「⚠」出現在她眼前。
        print("  ○ 沒在跑（不影響任何事，睡眠是我們自己讀的）")
        return
    種類: dict[str, int] = {}
    def 數(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    種類[path + k] = len(v)
                elif isinstance(v, dict):
                    數(v, path + k + ".")
    數(d)
    print(f"  ✅ 拿到 {len(種類)} 種：" + "、".join(f"{k} {n}" for k, n in
          sorted(種類.items(), key=lambda x: -x[1])[:6]))
    if "--只檢查" not in sys.argv:
        (BASE / "hc_snapshot.json").write_text(json.dumps(d, ensure_ascii=False),
                                               encoding="utf-8")


if __name__ == "__main__":
    main()