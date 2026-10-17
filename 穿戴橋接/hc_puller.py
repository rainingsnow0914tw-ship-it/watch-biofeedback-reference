#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
hc_puller.py —— 從手機「拉」生理資料回來（收集層的主力）

為什麼是拉不是推（2026-08-11 實測換掉的架構）：
    原本走 webhook 推送 —— 手機 app 主動 POST 到電腦。設定都做完了，
    結果 app 回這個：

        CLEARTEXT communication to 192.168.0.X not permitted
        by network security policy

    Android 預設禁止 app 送未加密的 HTTP。要繞過得給電腦一張手機信得過的
    憑證，Android 14 之後連使用者自己裝的 CA 都不算數，那條路很深。

    但 HC Webhook 這個 app 自己開了一個 HTTP 伺服器在手機上（port 8787），
    而 Android 的明文限制只管「app 往外送」，不管「別人來拿」。
    所以把方向反過來就整個繞開了 —— 而且反過來還比較好：

        · 不依賴電腦的 IP（電腦 IP 換了無所謂，是電腦主動去找手機）
        · 電腦控制節奏，要多密就多密，不用遷就 app 的 15 分鐘下限
        · 失敗看得見 —— 拉不到就是拉不到，不會像推送那樣靜靜地漏掉

    手機端要開著：HC Webhook → Home → Local HTTP Server → 開，port 8787。

用法：
    python hc_puller.py                    # 每 10 分鐘拉一次，一直跑
    python hc_puller.py --once             # 只拉一次（測試用）
    python hc_puller.py --interval 5
    python hc_puller.py --host 192.168.0.X

找手機的順序（不用每次自己填 IP）：
    ① 指令列給的 --host
    ② USB 接著就問 adb 手機的 Wi-Fi IP（最準）
    ③ 上次成功的那個 IP（存在 生理資料\puller_config.json）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hc_receiver import DATA_DIR, Store, Tee   # 共用同一套存檔與去重

# 2026-08-28：這支是用 pythonw.exe 跑的（沒有主控台），所以它生出來的
# adb.exe / powershell.exe 會各自開一個「看得見」的黑框閃過去。
# 使用者 看到的「一直閃一直閃」有一部分就是這裡。加上 CREATE_NO_WINDOW
# 之後子行程完全不建立控制台。藏父行程沒有用，孫子要自己藏。
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

PORT = 8787
CONFIG_FILE = DATA_DIR / "puller_config.json"
WATCH_CONFIG_FILE = DATA_DIR / "手錶橋接" / "puller_config.json"
ADB = Path(os.environ.get("LOCALAPPDATA", "")) / \
    "Android" / "Sdk" / "platform-tools" / "adb.exe"


# ── 找手機 ─────────────────────────────────────────────────────────────
PHONE_SERIAL = "<phone-serial>"      # 使用者 的 S26 Ultra


def adb_wifi_ip() -> str | None:
    """
    USB 接著的時候直接問手機自己的 Wi-Fi IP。

    ⚠ 一定要指定手機的序號（-s）。2026-08-12 踩過：
    手錶也用無線 adb 連上之後，adb 同時看得到兩台，
    「問第一台」問到的是**手錶**的 IP，收集器就跑去跟手錶要生理資料，
    當然拿不到 —— 紀錄裡留下「手機的 IP 換了 .124 → .127」這種假訊息。

    手錶跟手機都是 Android，光看 wlan0 分不出來，所以只能綁死序號。
    """
    if not ADB.exists():
        return None
    try:
        out = subprocess.run(
            [str(ADB), "-s", PHONE_SERIAL,
             "shell", "ip", "-f", "inet", "addr", "show", "wlan0"],
            capture_output=True, text=True, timeout=10,
            creationflags=NO_WINDOW).stdout
    except Exception:
        return None
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


SHEALTH = "com.sec.android.app.shealth"


def newest_data_age_minutes() -> float | None:
    """已經收到的資料裡，最新一筆是多久以前的。收不到就回 None。"""
    newest = None
    series = DATA_DIR / "series"
    if not series.exists():
        return None
    for path in series.glob("*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in reversed(lines[-200:]):     # 只看尾巴，檔可能很大
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            t = r.get("time") or r.get("start_time")
            if not t:
                continue
            try:
                dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            except Exception:
                continue
            if newest is None or dt > newest:
                newest = dt
            break
    if newest is None:
        return None
    return (datetime.now(timezone.utc) - newest).total_seconds() / 60


# ── 前台守門員（2026-09-25）────────────────────────────────────────────
# 👤 使用者：「澄用 USB 控制手機做淘寶會員打卡時，Health Connect Webhook
#   一直跳到前台蓋住淘寶；我沒有手動切換。」
# Android 紀錄 01:16／01:26／01:36 由 com.android.shell 啟動它 ——
# 對照 puller.log，是 pull_once() 每次 8787 連不到就 wake_app() 一次，
# 每 10 分鐘把 HC 叫到前台；00:55 還跑過 restart_local_server()，
# 那支會**照座標點三下**，萬一 HC 沒跳上來，那三下就點在她正在用的 app 上。
#
# 規則：螢幕亮著、而且前台是「她在用的 app」時，**不搶前台、不點畫面**，
# 只記一筆「延後」；等她回到桌面或螢幕關掉，下一輪自己補做。
# 資料不會少：每次拉最近 2 天，連回來那一輪就把中間的補齊。
LAUNCHER = "com.sec.android.app.launcher"
HCWEBHOOK = "com.hcwebhook.app"
可以打擾 = {LAUNCHER, HCWEBHOOK, "com.sec.android.app.shealth",
           "com.example.watchbridge", "com.android.systemui"}
延後紀錄 = DATA_DIR / "延後的工作.jsonl"
_延後過: set[str] = set()


def 前台() -> tuple[bool, str | None]:
    """回 (螢幕亮著嗎, 前台 app 的套件名)。問不到就回 (True, None)。"""
    try:
        r = subprocess.run(
            [str(ADB), "-s", PHONE_SERIAL, "shell",
             "dumpsys power | grep -m1 mWakefulness=; "
             "dumpsys activity activities | grep -m1 topResumedActivity"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=NO_WINDOW)
        out = r.stdout or ""
    except Exception:
        return True, None
    # adb 沒接上時 shell 可能只回錯誤、stdout 空白。那不是「螢幕關了」；
    # 要當她可能正在使用，否則下一步會誤允許 am start／座標點擊。
    if "mWakefulness=" not in out:
        return True, None
    亮 = "mWakefulness=Awake" in out
    m = re.search(r"topResumedActivity=ActivityRecord\{\S+ \S+ ([\w.]+)/", out)
    return 亮, (m.group(1) if m else None)


def 可以動前台(動作: str) -> bool:
    """她在用別的 app 就回 False，並記一筆延後。

    ⚠ 問不到前台是誰的時候**當成她在用** —— 寧可晚一輪修，
      也不要在看不見的情況下把東西蓋到她臉上、或點下去。
    """
    亮, pkg = 前台()
    if not 亮 or pkg in 可以打擾:
        # 這裡只核准動作，不先寫「補做」。實際 adb 指令可能失敗。
        return True
    誰 = pkg or "（問不到）"
    if 動作 in _延後過:
        print(f"    ⏸ 仍延後「{動作}」：前台 {誰}；下一輪再看")
        return False
    _延後過.add(動作)
    print(f"    ⏸ 延後「{動作}」：她正在用 {誰}，不搶前台；下一輪再看")
    try:
        with 延後紀錄.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": datetime.now().astimezone().isoformat(),
                                "動作": 動作, "前台": 誰, "狀態": "延後"},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass
    return False


def 完成延後(動作: str) -> None:
    """只有 adb 動作真正執行成功後，才把先前延後記成補做。"""
    if 動作 in _延後過:
        _延後過.discard(動作)
        記補做(動作)


def 記補做(動作: str) -> None:
    print(f"    ▶ 補做「{動作}」（之前因為她在用別的 app 延後過）")
    try:
        with 延後紀錄.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": datetime.now().astimezone().isoformat(),
                                "動作": 動作, "狀態": "補做"},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass


def poke_samsung_health() -> bool:
    """
    把 Samsung Health 叫到前景。

    2026-08-12 的教訓（花了一整個下午才找到）：
    手錶跟手機明明連著、我們的伺服器也活著、收集器每 10 分鐘照拉，
    **但心率停在 9 小時前不動**。

    原因是 **Samsung Health 被系統凍結了就不會把手錶的資料寫進 Health Connect**。
    重啟 Galaxy Wearable 沒用、手機藍牙關開沒用 ——
    最後有效的是把 Samsung Health 叫到前景，25 秒後 216 筆心率一次湧進來。

    這是今天第三個「畫面說連著、其實沒在動」的案例。
    所以這裡不看任何人的『狀態顯示』，只看**最新資料的時間**：
    超過門檻就戳它一下，用結果驗證，不聽宣稱。
    """
    if not ADB.exists():
        return False
    if not 可以動前台("叫醒 Samsung Health"):
        return False
    try:
        r = subprocess.run(
            [str(ADB), "-s", PHONE_SERIAL, "shell", "am", "start", "-n",
             f"{SHEALTH}/com.samsung.android.app.shealth.home.HomeMainActivity"],
            capture_output=True, text=True, timeout=20,
            creationflags=NO_WINDOW)
        if r.returncode != 0 or "Error:" in (r.stdout or ""):
            return False
        完成延後("叫醒 Samsung Health")
        return True
    except Exception:
        return False


def restart_local_server() -> bool:
    """
    把手機上那台 HTTP 伺服器關掉再打開。

    為什麼需要（2026-08-12 一天內死了三次）：
    HC Webhook 的前景服務會被系統收掉，**但 app 畫面照樣顯示
    「Local HTTP Server Active / Running on port 8787」** ——
    UI 狀態卡住，連 app 自己都不知道自己死了。

    只用 `am start` 把 app 叫起來**不夠**，實測過三次都沒用；
    一定要把那個開關關掉再打開，服務才會重新綁 port。

    座標是從實機截圖量出來的（首頁的 Local HTTP Server 卡片 →
    詳細頁的開關），流程固定，所以可以寫死；
    但每一步之間有等待，而且最後用「連得上沒」來驗證，不看畫面宣稱。
    """
    if not ADB.exists():
        return False

    def tap(x: int, y: int, wait: float = 3.0) -> None:
        # 每一下前都重新查前台；三次點按之間 使用者 可能切走 app。
        _, pkg = 前台()
        if pkg != HCWEBHOOK:
            raise RuntimeError(f"HC left foreground before tap: {pkg}")
        r = subprocess.run([str(ADB), "-s", PHONE_SERIAL, "shell", "input",
                            "tap", str(x), str(y)],
                           capture_output=True, timeout=20,
                           creationflags=NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError("adb tap failed")
        time.sleep(wait)

    if not 可以動前台("重開 HC 伺服器"):
        return False
    try:
        launched = subprocess.run([str(ADB), "-s", PHONE_SERIAL, "shell", "am", "start",
                                   "-n", "com.hcwebhook.app/.MainActivity"],
                                  capture_output=True, timeout=20,
                                  creationflags=NO_WINDOW)
        if launched.returncode != 0 or b"Error:" in (launched.stdout or b""):
            return False
        time.sleep(6)
        # ⚠ 2026-09-25：點之前再確認一次前台真的是 HC。
        #   座標是寫死的 —— HC 沒跳上來的話，這三下會點在她正在用的 app 上。
        亮, pkg = 前台()
        if pkg != HCWEBHOOK:
            print(f"    ⚠ HC 沒到前台（前台是 {pkg}），三下都不點")
            return False
        tap(718, 780, 4)        # 首頁的 Local HTTP Server 卡片
        tap(1178, 914, 3)       # 開關 → 關
        tap(1178, 914, 5)       # 開關 → 開
        完成延後("重開 HC 伺服器")
        return True
    except Exception:
        return False


def wake_app() -> bool:
    """
    手機端那台 HTTP 伺服器不見了的時候，用 adb 把 app 叫醒。

    為什麼需要這個（使用者 2026-08-11 提的）：
    「有時候不小心就把它殺掉了怎麼辦？」—— 她說得對。
    收集這件事不該依賴人類記得別滑掉某個 app。

    伺服器是 app 的前景服務，設定本身是存著的，
    所以只要把 app 重新啟動一次，伺服器就會照原設定回來。
    USB 沒接的時候這招無效 —— 那就只能等她下次插上，
    或自己回 app 裡打開。這個限制誠實寫在這裡，不假裝萬能。
    """
    if not ADB.exists():
        return False
    if not 可以動前台("叫醒 HC"):
        return False
    try:
        r = subprocess.run([str(ADB), "-s", PHONE_SERIAL, "shell", "am", "start",
                            "-n", "com.hcwebhook.app/.MainActivity"],
                           capture_output=True, text=True, timeout=15,
                           creationflags=NO_WINDOW)
        if r.returncode != 0 or "Error:" in (r.stdout or ""):
            return False
        完成延後("叫醒 HC")
        return True
    except Exception:
        return False


HOOK = Path(r"C:\Users\<you>\.codex\notification-hook.ps1")


def alert_human() -> None:
    """
    連續拉不到的時候出聲提醒。

    刻意借用 使用者 已經在用的那支提示音 script，而不是自己另外發明一種
    通知方式 —— 她已經認得那個聲音代表「有事要妳處理」，
    多一種聲音只會多一種要學的東西。

    同時在資料夾裡留一個檔，因為聲音會過去、檔案不會。
    """
    stamp = datetime.now().astimezone().isoformat()
    try:
        (DATA_DIR / "收集中斷.txt").open("a", encoding="utf-8").write(
            f"{stamp}  連續 3 次拉不到手機\n")
    except Exception:
        pass
    if HOOK.exists():
        try:
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy",
                            "Bypass", "-File", str(HOOK), "-Event", "action"],
                           capture_output=True, timeout=20,
                           creationflags=NO_WINDOW)
        except Exception:
            pass
    print("    ⚠ 已經連續 3 次拉不到手機，出聲提醒了")


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                           encoding="utf-8")


def watch_bridge_ip_hint() -> str | None:
    """只讀 8788 橋接器最近確認的手機 IP，當作 8787 重找提示。

    watch_puller 會自己更新這份設定；hc_puller 不寫它，也不把提示直接當成功，
    主迴圈仍會實際拉到 8787 才換 host。
    """
    try:
        ip = json.loads(WATCH_CONFIG_FILE.read_text(encoding="utf-8")).get("ip")
    except Exception:
        return None
    if not isinstance(ip, str):
        return None
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    try:
        if not all(0 <= int(part) <= 255 and str(int(part)) == part
                   for part in parts):
            return None
    except ValueError:
        return None
    return ip


def recovery_host_candidates(current: str) -> list[str]:
    """每次拉取失敗都便宜地重找；8788 本機提示優先，再問假的／真實 adb。"""
    candidates: list[str] = []
    for candidate in (watch_bridge_ip_hint(), adb_wifi_ip()):
        if candidate and candidate != current and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def scan_lan(port: int = PORT, timeout: float = 0.35) -> str | None:
    """
    在本機所在的網段裡掃誰開著 8787。

    為什麼需要這個（2026-08-13 凌晨踩到）：
        使用者 從診所回到家，手機換了網段。收集器還死守著診所那個 IP，
        整夜都在 `連不到手機`。而 adb_wifi_ip() 也救不了 ——
        那要手機插著 USB，她回家不會插。

        所以再加一層：**自己去網段裡把手機找出來**。
        認人的方式是打 /status 看回應像不像 HC Webhook，
        不是只看 port 通不通（別台機器也可能剛好開 8787）。

    只掃自己那一段的 /24，256 個位址、平行 64 條，通常兩秒內找到。
    """
    import ipaddress
    import socket
    from concurrent.futures import ThreadPoolExecutor

    from hc_receiver import lan_ips        # 已經照 VPN 排過序

    token = load_config().get("token")

    def looks_like_phone(addr: str) -> str | None:
        # 先用 socket 快篩，連不上就別浪費一次 HTTP
        try:
            with socket.create_connection((addr, port), timeout=timeout):
                pass
        except OSError:
            return None
        # port 通了還不算數 —— 別台機器也可能開著 8787。
        # 要它答得出 HC Webhook 的話才算：401（有 token 保護）或真的吐 JSON。
        payload, note = fetch(addr, days=1, port=port, timeout=6, token=token)
        if payload is not None or "401" in note:
            return addr
        return None

    seen: set[str] = set()
    for ip in lan_ips():
        try:
            net = ipaddress.ip_interface(f"{ip}/24").network
        except ValueError:
            continue
        if str(net) in seen:
            continue
        seen.add(str(net))
        hosts = [str(h) for h in net.hosts()]
        print(f"    掃 {net}（{len(hosts)} 個位址）…")
        with ThreadPoolExecutor(max_workers=64) as pool:
            for found in pool.map(looks_like_phone, hosts):
                if found:
                    print(f"    掃到手機了：{found}")
                    return found
    return None


def find_host(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    ip = adb_wifi_ip()
    if ip:
        return ip
    saved = load_config().get("host")
    if saved:
        payload, _ = fetch(saved, days=1, timeout=6,
                           token=load_config().get("token"))
        if payload is not None:
            return saved
    # USB 沒插、記著的 IP 也連不上 —— 自己去網段裡找。
    # 2026-08-13 使用者 點出來的：「我去日本旅行每天住不同的飯店，
    # 也會有不同的 WiFi，總不會那時候就死了」。換網段必須自己活過來。
    return scan_lan() or saved


# ── 拉資料 ─────────────────────────────────────────────────────────────
def fetch(host: str, days: int, port: int = PORT,
          timeout: int = 60, token: str | None = None) -> tuple[dict | None, str]:
    """
    回傳 (payload, 說明)。拉不到就 payload=None，說明寫清楚為什麼 ——
    「拉不到」跟「拉到了但沒資料」是兩件事，之後判讀層要分得開。

    token：手機那台伺服器綁在 0.0.0.0，同一個 Wi-Fi 上的任何裝置都連得到，
    所以在 app 裡開了「Require auth token」。沒帶 token 會被擋掉（401）。
    """
    url = f"http://{host}:{port}/?days={days}"
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return None, "手機拒絕（401）—— token 不對或 app 換過 token 了"
        return None, f"手機回 HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, f"連不到手機（{e.reason}）"
    except TimeoutError:
        return None, "手機沒回應（逾時）"
    except Exception as e:
        return None, f"讀取失敗：{e}"

    try:
        payload = json.loads(raw)
    except Exception as e:
        return None, f"回來的不是 JSON：{e}"

    if not isinstance(payload, dict):
        return None, "回來的 JSON 不是物件"
    return payload, "ok"


def connection_refused(why: str) -> bool:
    """主機可達、但 8787 沒 listener；Windows 與其他平台訊息都辨識。"""
    folded = why.casefold()
    return "10061" in folded or "connection refused" in folded


def pull_once(store: Store, host: str, days: int, port: int = PORT,
              token: str | None = None, *,
              recovery_attempts: set[str] | None = None) -> bool:
    # 同一輪可驗多個 IP，但手機只有一台；叫醒／重開各最多嘗試一次。
    # 延後也算本輪已嘗試，下一輪會換新集合再檢查前台。
    if recovery_attempts is None:
        recovery_attempts = set()
    stamp = datetime.now().strftime("%H:%M:%S")
    payload, why = fetch(host, days, port, token=token)

    # 連不到就先試著把手機上的 app 叫醒，再重試一次。
    # 大部分「突然收不到」都是 app 被滑掉／被系統凍結，叫醒就好。
    if payload is None and "連不到" in why:
        woke = False
        if "wake" not in recovery_attempts:
            recovery_attempts.add("wake")
            woke = wake_app()
        if woke:
            time.sleep(6)
            payload, why2 = fetch(host, days, port, token=token)
            if payload is not None:
                print(f"[{stamp}] {host}:{port} → 伺服器不見了，已自動叫醒 app")
            else:
                why = why2

        # 10061 不是一般 timeout：主機在，但 8787 沒人在聽。叫醒 app 還沒好時，
        # 同一輪就走既有的安全重開；restart_local_server() 內仍會先檢查前台，
        # 使用者 正在用別的 app 或前台不明時只延後，絕不點座標。
        if (payload is None and connection_refused(why)
                and "restart" not in recovery_attempts):
            recovery_attempts.add("restart")
            print("    8787 拒絕連線，安全重開 HC 伺服器…")
            if restart_local_server():
                time.sleep(5)
                payload, why2 = fetch(host, days, port, token=token)
                if payload is not None:
                    print(f"[{stamp}] {host}:{port} → 8787 已自動重開")
                else:
                    why = why2

    if payload is None:
        print(f"[{stamp}] {host}:{port} → {why}")
        return False

    result = store.ingest(payload, host)
    if result["added"]:
        summary = "、".join(f"{k} {v} 筆" for k, v in result["added"].items())
    else:
        summary = "沒有新資料"
    dupe = (f"（重複 {result['duplicates']} 筆已跳過）"
            if result["duplicates"] else "")
    print(f"[{stamp}] {host}:{port} → {summary}{dupe}")

    # 新型別第一次出現就大聲喊 —— 08-11 當下只有步數通了，
    # 心率和睡眠會不會流過來只有時間知道。與其靠人記得回頭查，讓它自己說。
    for dtype in result.get("first_seen", []):
        print(f"    ★ 第一次收到「{dtype}」—— 這個型別通了")
        mark = DATA_DIR / "第一次收到.txt"
        with mark.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().astimezone().isoformat()}  {dtype}\n")

    snap = daily_snapshot()
    if snap:
        print(f"    今日快照已留：{snap}")

    return True


# ── 每日快照 ───────────────────────────────────────────────────────────
BACKUP_DIR = DATA_DIR / "備份"
KEEP_DAYS = 60


def daily_snapshot() -> str | None:
    """
    每天第一次成功拉到資料時，把整個時間序列複製一份留底。

    為什麼要這個（使用者 2026-08-11 提的，而且她是對的）：
    她以前在 Samsung Health 累積過一段步數紀錄，後來「莫名其妙就不見了」——
    因為那份資料只存在三星那裡，換手機／重設手錶／整合失敗就沒了。
    **東西不在自己手上，就不算自己的。**

    所以這裡做兩件事：
      · 拉回來的當下就落地成純文字檔（誰都刪不掉）
      · 每天再留一份快照 —— 萬一某天正規化的程式寫壞了、
        或者去重的指紋亂掉把檔案寫爛，還有昨天的可以退回去

    保留 60 天。快照跟主資料在同一顆硬碟，所以這防的是「程式寫壞」，
    不是「硬碟壞掉」—— 後者要另外複製到別的地方，那是使用者的決定。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    dest = BACKUP_DIR / today
    if dest.exists():
        return None                      # 今天已經留過了

    series = DATA_DIR / "series"
    if not series.exists() or not any(series.glob("*.jsonl")):
        return None                      # 還沒有東西好備份

    dest.mkdir(parents=True, exist_ok=True)
    for path in series.glob("*.jsonl"):
        shutil.copy2(path, dest / path.name)
    state = DATA_DIR / "state.json"
    if state.exists():
        shutil.copy2(state, dest / "state.json")

    # 清掉太舊的
    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for old in BACKUP_DIR.iterdir():
        if old.is_dir() and old.name < cutoff:
            shutil.rmtree(old, ignore_errors=True)

    return str(dest)


# ── 主迴圈 ─────────────────────────────────────────────────────────────
def run(host: str | None, interval_min: int, days: int, port: int,
        once: bool, token: str | None = None) -> int:
    store = Store()
    cfg = load_config()

    host = find_host(host)
    token = token or cfg.get("token")
    if not host:
        print("找不到手機的 IP。USB 接上去讓我自己問，或用 --host 直接給。")
        return 2

    print(f"\n  從手機拉生理資料　{host}:{port}")
    print(f"  每 {interval_min} 分鐘一次，每次抓最近 {days} 天")
    print(f"  存到 {DATA_DIR}\n")

    if once:
        ok = pull_once(store, host, days, port, token)
        if ok:
            cfg["host"] = host; save_config(cfg)
        return 0 if ok else 1

    fails = 0
    last_poke = datetime.min.replace(tzinfo=timezone.utc)
    STALE_MIN = 45          # 資料超過這麼久沒更新就戳一下
    POKE_COOLDOWN = 20      # 但兩次戳至少隔這麼久，免得一直把 app 拉到前景

    while True:
        recovery_attempts: set[str] = set()
        ok = pull_once(store, host, days, port, token,
                       recovery_attempts=recovery_attempts)

        # 拉得到但資料是舊的 —— 那是 Samsung Health 卡住了，不是網路問題
        if ok:
            age = newest_data_age_minutes()
            since_poke = (datetime.now(timezone.utc) - last_poke).total_seconds() / 60
            if age is not None and age > STALE_MIN and since_poke > POKE_COOLDOWN:
                print(f"    ⚠ 最新資料是 {age:.0f} 分鐘前的 —— 叫醒 Samsung Health")
                if poke_samsung_health():
                    last_poke = datetime.now(timezone.utc)
                    time.sleep(30)          # 給它時間把資料寫進 Health Connect
                    pull_once(store, host, days, port, token,
                              recovery_attempts=recovery_attempts)
        if ok:
            fails = 0
            if cfg.get("host") != host:
                cfg["host"] = host
                save_config(cfg)
        else:
            fails += 1
            # 每次失敗都重找，不再等第 3／10／30 次。先讀 8788 已確認的手機 IP
            # （只讀，不改它的 config），再問 adb；候選必須真的拉成功才採用。
            recovered_host = None
            for candidate in recovery_host_candidates(host):
                print(f"    看到新的手機 IP 提示：{host} → {candidate}；立即驗證 8787…")
                if pull_once(store, candidate, days, port, token,
                             recovery_attempts=recovery_attempts):
                    recovered_host = candidate
                    break
            if recovered_host:
                host = recovered_host
                fails = 0
                if cfg.get("host") != host:
                    cfg["host"] = host
                    save_config(cfg)
                print("    ✅ 換 IP 後同一輪恢復")
                time.sleep(interval_min * 60)
                continue
            # 連續失敗到一定次數就出聲 —— 2026-08-12 的教訓：
            # 手機端伺服器半夜死掉，斷了五個小時都沒人知道，
            # 是 使用者 醒來自己問「數據怎麼樣」才發現的。
            # 沉默的失敗最貴，所以這裡借用她既有的提示音管道喊一聲。
            # 第 2 次失敗就自己動手修 —— 這是今天最常見的故障，
            # 而且修法固定（關掉再打開那個伺服器開關）。
            # 2026-09-25：延後過的話，之後每一輪失敗都再試，直到真的做到。
            # 10061 分支已試過就不在舊的第 2 次失敗分支重複切開關。
            if ("restart" not in recovery_attempts
                    and (fails == 2 or (fails > 2 and "重開 HC 伺服器" in _延後過))):
                recovery_attempts.add("restart")
                print("    連不到，試著把手機那台伺服器關掉再開…")
                if restart_local_server():
                    time.sleep(5)
                    if pull_once(store, host, days, port, token,
                                 recovery_attempts=recovery_attempts):
                        print("    ✅ 自己修好了")
                        fails = 0
                        time.sleep(interval_min * 60)
                        continue
            # 修不好才吵人
            if fails == 3:
                alert_human()
            if fails == 3:
                print("    連 3 次拉不到。手機可能沒開 Wi-Fi、"
                      "或 app 的 Local HTTP Server 被關掉了。")

        try:
            time.sleep(interval_min * 60)
        except KeyboardInterrupt:
            print("\n  停止。資料都在 " + str(DATA_DIR))
            return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="從手機拉 Health Connect 資料")
    ap.add_argument("--host", help="手機的 IP，不給就自動找")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--interval", type=int, default=10, help="幾分鐘拉一次")
    ap.add_argument("--days", type=int, default=2,
                    help="每次抓最近幾天（app 的視窗上限是 2 天）")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--token", help="手機 app 的 auth token（預設讀 config）")
    ap.add_argument("--log", action="store_true",
                    help="同時寫進 生理資料\\puller.log")
    args = ap.parse_args()

    if args.token:                      # 給了就存起來，之後不用再打
        cfg = load_config()
        cfg["token"] = args.token
        save_config(cfg)

    if args.log:
        sys.stdout = Tee(DATA_DIR / "puller.log", sys.stdout)
        sys.stderr = sys.stdout
        print(f"\n===== 啟動 {datetime.now().astimezone().isoformat()} =====")

    sys.exit(run(args.host, args.interval, args.days, args.port, args.once,
                 args.token))


if __name__ == "__main__":
    main()
