#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
watch_puller.py —— 把手錶橋接的資料拉回來，**而且真的去問 triage 該不該擔心**。

## 這支為什麼存在（2026-08-17，5090 那台的阿寶審查之後）

外部審查抓到一件我自己沒看見的事：

> **`triage.py` 的 `assess()` 在整個 repo 裡沒有任何呼叫者。它是一座沒有橋的島。**

我自己驗了，成立：
```
grep -rn "import triage|from triage|triage\." --include=*.py .   → 零筆
啟動收資料.bat 的全部內容 → 只有 hc_puller.py
```

也就是說：**2026-08-16 那一晚我花時間修好「紅燈物理上不可能亮」那個 bug，
修完之後那個警報器從來沒有接上電源。**

而這正是這個專案反覆在對付的同一隻病 —— 「東西看起來做好了，但沒接在產品上」。
手冊裡記著它已經發作過七次。**這是第八次，而且是我自己犯的。**

## ⚠ 但審查給的接法是錯的，而那個錯只有跑過程式才看得出來

審查建議「把 `assess()` 接進 `hc_puller` 的迴圈」。實際查證：

```
hc_puller.py  → port 8787 → HC Webhook → Samsung Health → Health Connect
手錶橋接      → port 8788 → 我們自己的原始資料
```

**那是兩條不同的管線。** hc_puller 拿到的是三星整理過的心率／睡眠／步數，
而 `triage.assess()` 吃的是我們自己的格式（`on_body` 三態、`mv`、`trust`、
`motion`、`hr_ibi`）—— 那些欄位在 8787 那條裡根本不存在。

接錯的話不會報錯，只會**永遠回綠燈**，因為它找不到任何它認得的記錄。
**又是一個安靜的失敗。**

所以缺的不是一條接線，是這整支。

## 它做什麼

```
每 N 分鐘：
  ① 找到手機（記住上次的 IP，換網段就掃）
  ② 抓今天的 JSONL 回來存檔
  ③ **餵給 triage.assess()，把判斷寫進日誌**
  ④ 判斷變差（綠→黃／黃→紅）就出聲
```

## 影子模式（預設開著）

第 ④ 步預設**只寫日誌不通知**。理由是 使用者 定的規矩：
新裝到任何一個人身上，前兩週一律只觀察不出聲，用真人照護當對照組驗誤報率。

要開通知：`--notify`。**但在跑滿兩週之前不要開。**
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage  # noqa: E402  —— 這一行就是那座橋

CANARY = Path(__file__).resolve().parent / "canary.py"
CANARY_EVERY_H = 6
"""多久跑一次金絲雀（正控制）。

## 為什麼影子模式**一定**要有這個

外部意見書（5090 那台的阿寶，2026-08-17）指出：

> 沒有正控制，「零誤報」跟「系統根本沒跑」在數學上分不出來。
> 一個一年才該叫一次的系統，跟一個永遠不叫的系統，兩週交出的成績單一模一樣。

而這個專案**已經被這件事咬過一次**：08-16 之前 triage 拿「最後一筆資料的時間」
當「現在」，紅燈物理上不可能亮 —— 而影子模式那個「零誤報」看起來很漂亮。

所以現在每 6 小時餵一次保證會觸發紅燈的假資料進 `assess()`，斷言它真的回紅。
**不紅就在日誌裡大聲說「這兩週的資料不能當數」。**"""

PORT = 8788
ROOT = Path(__file__).resolve().parent.parent / "生理資料" / "手錶橋接"
CONFIG = ROOT / "puller_config.json"


def _token() -> str:
    """存取權杖 —— **從設定檔讀，不寫在程式裡。**

    ⚠ 2026-08-27 改。原本是 `TOKEN = "<舊的寫死權杖，2026-08-27 已廢除>"` 寫死在這裡，
      而且早就進了 git 歷史（08-18 推上去的），掃描器每次都列成事故。

      拖到今天才改的理由是我記憶裡寫著「家人正在戴，改它會斷資料」——
      **那是 08-19 的狀態。** 使用者 2026-08-27 說：「我家人現在沒有戴啊，
      一直都是我在用，因為我們還沒測試完。」
      **過期的顧慮跟真的顧慮長得一模一樣，差別只在有沒有去問。**

    手機端第一次啟動時會隨機產生一串存進 SharedPreferences，
    並在 logcat 印一次。把那一串寫進：
        生理資料/手錶橋接/puller_config.json　→  {"token": "……"}
    （`生理資料/` 整個在 .gitignore 裡，不會進版控。）
    """
    import json as _json
    for f in (CONFIG,
              Path(__file__).resolve().parent.parent / "生理資料" / "puller_config.json"):
        if f.exists():
            try:
                t = _json.loads(f.read_text(encoding="utf-8")).get("token")
                if t:
                    return t
            except Exception:
                pass
    # ⚠ 找不到就**大聲說**，不要回一個猜的值。
    #   回猜的值會讓「設定檔沒設好」看起來像「手機拒絕連線」。
    raise SystemExit(
        "找不到存取權杖。手機端第一次啟動時會在 logcat 印一次，\n"
        "  抓法：adb -s <手機> logcat -d | grep 首次產生存取權杖\n"
        f"  然後寫進 {CONFIG}　格式：{{\"token\": \"……\"}}"
    )
VERDICT_LOG = ROOT / "判斷日誌.jsonl"

# 判斷的嚴重度順序。用來偵測「變差了」——只有變差才值得出聲。
RANK = {"green": 0, "unknown": 1, "yellow": 2, "red": 3}


def _get(ip: str, path: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(
        # ip 可以是「192.168.0.X」也可以是「http://127.0.0.1:18788」（USB 隧道）。
        (ip + path) if str(ip).startswith("http") else f"http://{ip}:{PORT}{path}",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def find_phone() -> str | None:
    """找手機。順序：上次成功的 IP → 掃同網段。

    ⚠ 掃網段是必要的，不是防禦性程式碼：使用者 會搬電腦、會換網路，
      2026-08-16 一天之內就從 192.168.0.x 換到 192.168.0.x。
      寫死 IP 的話那天整天都會是「連不上」。
    """
    if CONFIG.exists():
        last = json.loads(CONFIG.read_text(encoding="utf-8")).get("ip")
        if last and _alive(last):
            return last

    # ⚠ 這裡本來是用 `s.connect(("8.8.8.8", 80))` 問「我自己是哪個 IP」。
    #   **那個寫法在 VPN 開著的時候是錯的** —— 它回的是 VPN 的位址
    #   （使用者 的機器上是 10.0.0.X），於是就跑去掃 VPN 的網段，永遠找不到手機。
    #
    #   2026-08-16 下午我用同一個寫法掃過一次，掃了 254 個位址、零結果，
    #   查了才發現真實網段是 192.168.0.x。**同一天寫的程式又犯一次。**
    #
    #   正解：把**所有**本機的私網位址都拿出來，每個網段都掃。
    #   多掃幾個網段的成本是幾秒鐘，掃錯網段的成本是整套系統靜靜失聯。
    for prefix in _local_prefixes():
        for i in range(1, 255):
            ip = f"{prefix}.{i}"
            if _alive(ip, 0.35):
                ROOT.mkdir(parents=True, exist_ok=True)
                # 🔴 **只改 ip，其他欄位一個都不准動。**（2026-09-01 事故，09-02 修）
                #   原本寫的是 json.dumps({"ip": ip}) —— 整份覆蓋，
                #   **把同一個檔案裡的 token 洗掉了**。09-01 01:53 發生，
                #   隔天四條管路全部 401，而錯誤訊息只說「鑰匙不對」。
                #
                #   ⚠ 這是**同一種病的第三次**：
                #     08-27 我把橋接鑰匙寫進共用欄位 → 蓋掉 HC 的
                #     08-30 我把 HC 鑰匙寫回去      → 蓋掉橋接的
                #     09-01 快取 IP 整份覆蓋        → 又蓋掉橋接的
                #   **共同點都是「寫的人只想著自己那個欄位」。**
                #   規則：改設定檔一律先讀進來、改一個鍵、再寫回去。
                try:
                    舊 = json.loads(CONFIG.read_text(encoding="utf-8"))
                except Exception:
                    舊 = {}
                舊["ip"] = ip
                CONFIG.write_text(json.dumps(舊, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
                return ip
    return None


def _local_prefixes() -> list[str]:
    """本機所有私網位址的網段，去重。VPN 的網段排在最後。"""
    out, vpn = [], []
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ip = info[4][0]
        if ip.startswith("127."):
            continue
        prefix = ".".join(ip.split(".")[:3])
        # 10.x 通常是 VPN／公司網路，家裡的裝置多半在 192.168.x
        (vpn if ip.startswith("10.") else out).append(prefix)
    seen, ordered = set(), []
    for p in out + vpn:
        if p not in seen:
            seen.add(p); ordered.append(p)
    return ordered


def _alive(ip: str, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, PORT), timeout):
            return True
    except OSError:
        return False


def _valid_jsonl_bytes(data: bytes) -> bool:
    if not data or not data.endswith(b"\n"):
        return False
    try:
        for line in data.splitlines():
            if line.strip():
                json.loads(line)
        return True
    except (ValueError, UnicodeError):
        return False


def pull_day(ip: str, day: str, allow_valid_refresh: bool = False) -> Path | None:
    """抓一天的資料存檔。回存到哪。"""
    ROOT.mkdir(parents=True, exist_ok=True)
    out = ROOT / f"{day}.jsonl"
    try:
        data = _get(ip, f"/day?d={day}", timeout=240)
    except Exception as e:
        print(f"  抓不到 {day}：{e}")
        return None
    if out.exists():
        old = out.read_bytes()
        if old == data:
            if allow_valid_refresh:
                os.utime(out, None)  # 已逐位元組核對，避免下輪重抓同一個同大小檔
            return out
        if old.startswith(data):
            # 手機仍在補送時，不能把已從手錶直接核對、較完整的
            # 本機副本覆蓋成短版。10-01 的尾段曾差 2,752 bytes。
            print(f"  WARN {day} 手機副本比本機短 {len(old) - len(data)} bytes；保留完整本機檔")
            return out
        if not data.startswith(old):
            if (allow_valid_refresh and len(data) == len(old)
                    and _valid_jsonl_bytes(data) and not _valid_jsonl_bytes(old)):
                # 手機可能已在原位補上較早的藍牙塊，檔案長度卻沒變。
                # 9/25 實測：電腦留 136,464 bytes NUL，手機同長但已修好。
                backup_dir = ROOT.parent / "備份" / "同大小修補"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / out.name
                if backup.exists() and backup.read_bytes() != old:
                    print(f"  WARN {day} 同大小修補備份衝突，未覆蓋")
                    return None
                if not backup.exists():
                    backup.write_bytes(old)
                temporary = out.with_name(out.name + ".refreshing")
                if temporary.exists():
                    print(f"  WARN {day} 尚有未核對的暫存檔，未覆蓋")
                    return None
                temporary.write_bytes(data)
                os.replace(temporary, out)
                print(f"  {day} 同大小空洞已由手機完整 JSONL 修補；原檔已備份")
                return out
            # 同一日的檔案應是逐位元組前綴關係；分歧需人工對帳。
            print(f"  WARN {day} 手機與本機內容分歧，不覆蓋任何原始檔")
            return None
    out.write_bytes(data)
    return out


def catch_up(ip: str) -> int:
    """把手機上有、但電腦上沒有（或不完整）的日子補回來。回補了幾天。

    ## 為什麼需要（使用者 2026-08-28）

    > 「假設我出去旅行了呢，那這些記憶我能存多久？我有時候會出去一個禮拜。」

    查證的結果分三層：
    ```
    手錶　　461 天的空間，而且整套程式沒有任何一行會刪掉舊的生理資料
    手錶→手機　兩台都跟著她走，出門照樣同步
    手機→電腦　**只拉今天** ← 洞在這裡
    ```
    她出門一週回來，中間六天留在手機上，**永遠不會有人去拿**。
    而症狀是安靜的：電腦上就是少了那幾天，不報錯、不提醒。

    ⚠ **比大小不能只比「檔案在不在」**：當天的檔一直在長，
      昨天拉過不代表拉完了 —— 她昨天 23:00 拉過，那天最後一小時就會缺。
    """
    try:
        st = json.loads(_get(ip, "/status", timeout=30))
    except Exception as e:
        # ⚠ 問不到就明說。**不要靜靜地跳過** ——
        #   那會讓「補送壞了」跟「沒有東西要補」長得一樣。
        print(f"  ⚠ 問不到手機上有哪些天（{e}）—— 這次不補，**不代表沒有東西要補**")
        return 0
    ROOT.mkdir(parents=True, exist_ok=True)
    todo = []
    today = datetime.now().strftime("%Y-%m-%d")
    # 手機可能保留超過 30 天；不能只看最後 30 個檔案。
    # 也要查同大小但手機較晚修改的日子：亂序塊填空洞不會增長檔案。
    clock_skew_ms = (st.get("now", time.time() * 1000) - time.time() * 1000)
    for f in sorted(st.get("files", []), key=lambda x: x.get("day", "")):
        day, remote = f.get("day"), f.get("bytes", 0)
        if not day or day == today or remote <= 0:
            continue          # 今天的走原本那條路（順便會做判讀）
        local = ROOT / f"{day}.jsonl"
        have = local.stat().st_size if local.exists() else 0
        remote_modified_local = float(f.get("modified") or 0) - clock_skew_ms
        newer_same_size = (have == remote and local.exists()
                           and remote_modified_local > local.stat().st_mtime * 1000 + 1000)
        if have < remote or newer_same_size:
            todo.append((day, have, remote, newer_same_size))
    if not todo:
        return 0
    print(f"  ⏳ 有 {len(todo)} 天在手機上、電腦這邊缺或不完整，補回來：")
    done = 0
    for day, have, remote, newer_same_size in todo:
        p = pull_day(ip, day, allow_valid_refresh=newer_same_size)
        if p:
            done += 1
            print(f"     {day}  {have/1024/1024:.1f} → {p.stat().st_size/1024/1024:.1f} MB")
        else:
            print(f"     {day}  ✗ 補失敗（下次再試）")
    return done


def judge(path: Path, notify: bool) -> triage.Verdict:
    """**這裡就是那座橋。** 把資料餵給 triage，把判斷寫下來。"""
    records = triage._load(path)
    # ⚠ 生理期標記要**跨檔**找：她按一次就沒了，而這裡每次只讀當天的檔。
    #   只看當天的話，那顆按鈕在第二天就等於沒被按過。
    v = triage.assess(records, period_start=triage.find_period_start(ROOT))

    ROOT.mkdir(parents=True, exist_ok=True)
    with VERDICT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "t": datetime.now().isoformat(timespec="seconds"),
            "kind": "verdict",
            "level": v.level,
            "reason": v.reason,
            "evidence": v.evidence,
            "records": len(records),
            # ⚠ 一定要記「有沒有真的通知」。
            #   不記的話，日後看日誌會分不出「它判斷了但沒說」跟「它根本沒判斷」——
            #   而那兩件事在事後檢討時差非常多。
            "notified": bool(notify),
        }, ensure_ascii=False) + "\n")
    return v


def run_canary() -> tuple[bool, str]:
    """跑金絲雀。回（過了沒, 訊息）。"""
    import os
    import subprocess
    try:
        r = subprocess.run(
            [sys.executable, str(CANARY)],
            capture_output=True, text=True, timeout=120,
            cwd=str(CANARY.parent), encoding="utf-8", errors="replace",
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        tail = (r.stdout or "").strip().splitlines()
        return r.returncode == 0, (tail[-1] if tail else "（沒有輸出）")
    except Exception as e:
        return False, f"金絲雀自己跑不起來：{e}"


def last_level() -> str | None:
    if not VERDICT_LOG.exists():
        return None
    lines = VERDICT_LOG.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines):
        try:
            return json.loads(line)["level"]
        except Exception:
            continue
    return None


def main() -> None:
    # ⚠ 2026-08-20：**這一行是在修一個會讓影子模式整個停掉的 bug。**
    #
    #   Windows 主控台預設 cp950。金絲雀的輸出裡只要出現一個 cp950 印不出來的字
    #   （例如轉錄或子行程回來的 U+FFFD），這裡的 print 就丟 UnicodeEncodeError，
    #   **整支 puller 當場結束** —— 而它是影子模式唯一在判斷的東西。
    #
    #   最惡劣的地方是失敗的樣子：她不會看到「影子模式掛了」，
    #   她會看到「判斷日誌停在某個時間」，而那跟「一切正常沒事發生」長得一樣。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=10, help="幾分鐘拉一次")
    ap.add_argument("--once", action="store_true", help="只跑一次")
    ap.add_argument(
        "--notify", action="store_true",
        help="判斷變差時真的出聲。**影子模式期間不要開** —— "
             "使用者 定的規矩是新裝到任何人身上前兩週只觀察不通知。",
    )
    args = ap.parse_args()

    last_canary = 0.0

    while True:
        # ── 正控制：定期證明警報還會響 ───────────────────────
        # 放在迴圈**最前面**，所以第一輪就會跑一次 ——
        # 影子模式開始的那一刻就要有一筆「當時是好的」的證據。
        if time.time() - last_canary > CANARY_EVERY_H * 3600:
            ok, msg = run_canary()
            last_canary = time.time()
            stamp = datetime.now().strftime("%H:%M:%S")
            print(f"{stamp}  金絲雀：{'OK' if ok else 'X'}  {msg}")
            ROOT.mkdir(parents=True, exist_ok=True)
            with VERDICT_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "t": datetime.now().isoformat(timespec="seconds"),
                    "kind": "canary",
                    "pass": ok,
                    "msg": msg,
                }, ensure_ascii=False) + "\n")
            if not ok:
                print("  ⚠⚠ 金絲雀死了 —— **在修好之前，這段期間的影子模式資料不能當數**")

        stamp = datetime.now().strftime("%H:%M:%S")
        ip = find_phone()
        if not ip:
            # 🔴 Wi-Fi 找不到不代表手機不在（2026-08-31 立）。
            #   那天證據很清楚：手機服務在跑、自己連自己回 401、沒進 Doze、
            #   IP 沒變、跟電腦同一個 SSID —— 而電腦連得到路由器、連不到手機。
            #   **放行閘道、擋掉區網其他機器**＝VPN kill switch 的典型長相。
            #   叫她關 VPN 是把問題丟回給人，所以改成自己走 USB 隧道繞過去。
            from 手機在哪 import 找手機, 診斷
            base, how = 找手機(PORT)
            if base:
                ip = base
                print(f"{stamp}  Wi-Fi 打不通，改走 {how}")
        if not ip:
            from 手機在哪 import 診斷
            print(f"{stamp}  找不到手機（8788 沒人應）")
            print(f"     {診斷()}")
        else:
            # ★★ 2026-08-28：**這裡本來只拉「今天」，而那會在她出門的時候掉資料。**
            #
            #   她問：「假設我出去旅行了呢，那這些記憶我能存多久？我有時候會出去一個禮拜。」
            #
            #   查下去發現：手錶跟手機都好好的（461 天的空間、而且**整套程式裡
            #   沒有任何一行會刪掉舊的生理資料**，我逐檔 grep 過），
            #   但**電腦端每次只拉今天** —— 所以她出門一週回來，
            #   中間那六天的資料留在手機上，**永遠不會有人去拿**。
            #
            #   🔴 又是同一個形狀：東西好好地存著，只是沒有人去接。
            #     而症狀是安靜的 —— 電腦上就是少了那幾天，不會報錯。
            #
            #   改成：問手機有哪些天（/status 會回檔名跟大小），
            #   **本機沒有、或本機比手機小的，全部補回來。**
            #   比大小是必要的：當天的檔還在長，昨天拉過不代表拉完了。
            catch_up(ip)
            day = datetime.now().strftime("%Y-%m-%d")
            p = pull_day(ip, day)
            if p:
                prev = last_level()
                v = judge(p, args.notify)
                mb = p.stat().st_size / 1024 / 1024
                print(f"{stamp}  {ip}  {mb:.1f} MB  →  {v.說法.splitlines()[0]}")

                # 只有**變差**才值得出聲。一直維持在黃色不需要每十分鐘叫一次 ——
                # 那正是把人訓練成忽略警報的做法。
                worse = prev is not None and RANK.get(v.level, 1) > RANK.get(prev, 1)
                if worse:
                    msg = f"判斷從 {prev} 變成 {v.level}：{v.reason}"
                    if args.notify:
                        print(f"  ⚠ {msg}")
                        # TODO 這裡要接 NotifyBridge 送到手錶／LINE。
                        #      **還沒接** —— 標在這裡是為了不要有人以為它會通知。
                    else:
                        print(f"  （影子模式，只記不叫）{msg}")

        if args.once:
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
