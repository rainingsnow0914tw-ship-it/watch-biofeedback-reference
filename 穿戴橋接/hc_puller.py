#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""原本走 webhook 推送 —— 手機 app 主動 POST 到電腦。設定都做完了，
    結果 app 回這個：

        CLEARTEXT communication to 192.168.0.X not permitted
        by network security policy

    Android 預設禁止 app 送未加密的 HTTP。要繞過得給電腦一張手機信得過的

    但 HC Webhook 這個 app 自己開了一個 HTTP 伺服器在手機上（port 8787），
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
    ③ 上次成功的那個 IP（存在 生理資料\puller_config.json）"""

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


# 之後子行程完全不建立控制台。藏父行程沒有用，孫子要自己藏。
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

PORT = 8787
CONFIG_FILE = DATA_DIR / "puller_config.json"
WATCH_CONFIG_FILE = DATA_DIR / "手錶橋接" / "puller_config.json"
ADB = Path(os.environ.get("LOCALAPPDATA", "")) / \
    "Android" / "Sdk" / "platform-tools" / "adb.exe"


# ── 找手機 ─────────────────────────────────────────────────────────────
PHONE_SERIAL = "<phone-serial>"

_目標快取: dict[str, object] = {"t": 0.0, "v": PHONE_SERIAL}


def adb_target() -> str:
    """adb 的 -s 該填什麼：USB 接著就是序號本人；只有 Wi-Fi adb 時找 ro.serialno 對得上的 ip:port。"""
    import time as _t
    if _t.time() - float(_目標快取["t"]) < 60:
        return str(_目標快取["v"])
    target = PHONE_SERIAL
    try:
        out = subprocess.run([str(ADB), "devices"], capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW).stdout
        names = [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip() and ln.split()[-1] == "device"]
        if PHONE_SERIAL not in names:
            for n in names:
                if ":" not in n or n.startswith("adb-"):
                    continue
                sn = subprocess.run([str(ADB), "-s", n, "shell", "getprop", "ro.serialno"], capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW).stdout.strip()
                if sn == PHONE_SERIAL:
                    target = n; break
    except Exception:
        pass
    _目標快取.update(t=_t.time(), v=target)
    return target


def adb_wifi_ip() -> str | None:
    """USB 接著的時候直接問手機自己的 Wi-Fi IP。

    手錶也用無線 adb 連上之後，adb 同時看得到兩台，

    手錶跟手機都是 Android，光看 wlan0 分不出來，所以只能綁死序號。"""
    if not ADB.exists():
        return None
    try:
        out = subprocess.run(
            [str(ADB), "-s", adb_target(),
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


# 對照 puller.log，是 pull_once() 每次 8787 連不到就 wake_app() 一次，


#


# 資料不會少：每次拉最近 2 天，連回來那一輪就把中間的補齊。
LAUNCHER = "com.sec.android.app.launcher"
HCWEBHOOK = "com.hcwebhook.app"
可以打擾 = {LAUNCHER, HCWEBHOOK, "com.sec.android.app.shealth",
           "com.example.watchbridge", "com.android.systemui"}
延後紀錄 = DATA_DIR / "延後的工作.jsonl"
_延後過: set[str] = set()
_延後起: dict[str, float] = {}      # 動作 → 第一次被延後的時間
_延後喊過: dict[str, float] = {}    # 動作 → 上次喊救命的時間
延後喊救命秒 = 2 * 3600


def 喊救命(動作: str, 誰: str) -> None:
    """ """
    import time as _t
    now = _t.time()
    起 = _延後起.setdefault(動作, now)
    if now - 起 < 延後喊救命秒 or now - _延後喊過.get(動作, 0.0) < 延後喊救命秒:
        return
    _延後喊過[動作] = now
    行 = f"[{datetime.now():%H:%M:%S}] ⚠ HC puller「{動作}」已延後 {(now - 起) / 3600:.1f} 小時（前台 {誰}）——三星健康資料可能沒在拉，去看 puller.log" + chr(10)
    print("    " + 行.strip())
    try:
        with (DATA_DIR / "哨兵.log").open("a", encoding="utf-8") as f:
            f.write(行)
    except Exception:
        pass


def 前台() -> tuple[bool, str | None]:
    """回 (螢幕亮著嗎, 前台 app 的套件名)。問不到就回 (True, None)。"""
    try:
        r = subprocess.run(
            [str(ADB), "-s", adb_target(), "shell",
             "dumpsys power | grep -m1 mWakefulness=; "
             "dumpsys activity activities | grep -m1 topResumedActivity; "
             "dumpsys window | grep -m1 mFocusedApp"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=NO_WINDOW)
        out = r.stdout or ""
    except Exception:
        return True, None


    if "mWakefulness=" not in out:
        return True, None
    亮 = "mWakefulness=Awake" in out
    m = (re.search(r"topResumedActivity=ActivityRecord\{\S+ \S+ ([\w.]+)/", out)
         or re.search(r"mFocusedApp=ActivityRecord\{\S+ \S+ ([\w.]+)/", out))
    return 亮, (m.group(1) if m else None)


def 可以動前台(動作: str) -> bool:
    """ """
    亮, pkg = 前台()
    if not 亮 or pkg in 可以打擾:

        return True
    誰 = pkg or "（問不到）"
    喊救命(動作, 誰)
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
    _延後起.pop(動作, None); _延後喊過.pop(動作, None)
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
    """把 Samsung Health 叫到前景。

    手錶跟手機明明連著、我們的伺服器也活著、收集器每 10 分鐘照拉，
    **但心率停在 9 小時前不動**。

    原因是 **Samsung Health 被系統凍結了就不會把手錶的資料寫進 Health Connect**。
    重啟 Galaxy Wearable 沒用、手機藍牙關開沒用 ——
    最後有效的是把 Samsung Health 叫到前景，25 秒後 216 筆心率一次湧進來。

    所以這裡不看任何人的『狀態顯示』，只看**最新資料的時間**：
    超過門檻就戳它一下，用結果驗證，不聽宣稱。"""
    if not ADB.exists():
        return False
    if not 可以動前台("叫醒 Samsung Health"):
        return False
    try:
        r = subprocess.run(
            [str(ADB), "-s", adb_target(), "shell", "am", "start", "-n",
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
    """把手機上那台 HTTP 伺服器關掉再打開。

    HC Webhook 的前景服務會被系統收掉，**但 app 畫面照樣顯示
    UI 狀態卡住，連 app 自己都不知道自己死了。

    只用 `am start` 把 app 叫起來**不夠**，實測過三次都沒用；
    一定要把那個開關關掉再打開，服務才會重新綁 port。

    座標是從實機截圖量出來的（首頁的 Local HTTP Server 卡片 →
    詳細頁的開關），流程固定，所以可以寫死；"""
    if not ADB.exists():
        return False

    def tap(x: int, y: int, wait: float = 3.0) -> None:

        _, pkg = 前台()
        if pkg != HCWEBHOOK:
            raise RuntimeError(f"HC left foreground before tap: {pkg}")
        r = subprocess.run([str(ADB), "-s", adb_target(), "shell", "input",
                            "tap", str(x), str(y)],
                           capture_output=True, timeout=20,
                           creationflags=NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError("adb tap failed")
        time.sleep(wait)


    try:
        fs = subprocess.run([str(ADB), "-s", adb_target(), "shell", "am", "force-stop", "com.hcwebhook.app"],
                            capture_output=True, timeout=20, creationflags=NO_WINDOW)

        # 這裡以前照樣等 2 秒＋輪詢 18 秒，再退回點畫面那條又打一次 adb。連不到就是連不到，直接收手。
        if fs.returncode != 0:
            print("    force-stop 失敗（adb 連不到手機），這輪不重開")
            return False
        time.sleep(2)
        subprocess.run([str(ADB), "-s", adb_target(), "shell", "am", "start", "-n", "com.hcwebhook.app/.MainActivity"],
                       capture_output=True, timeout=20, creationflags=NO_WINDOW)
        for _ in range(6):
            time.sleep(3)
            ss = subprocess.run([str(ADB), "-s", adb_target(), "shell", "ss -ltn"], capture_output=True, text=True,
                                timeout=15, creationflags=NO_WINDOW).stdout or ""
            if isinstance(ss, bytes):
                ss = ss.decode("utf-8", errors="replace")
            if re.search(rf":{PORT}\s", ss + " "):
                print("    ▶ force-stop＋start 之後 8787 回來了（沒點畫面）")
                完成延後("重開 HC 伺服器")
                return True
        print("    force-stop＋start 之後 8787 還是沒在聽，退回點畫面那條")
    except Exception as e:
        print(f"    force-stop 那條例外：{e}")
    if not 可以動前台("重開 HC 伺服器"):
        return False
    try:
        launched = subprocess.run([str(ADB), "-s", adb_target(), "shell", "am", "start",
                                   "-n", "com.hcwebhook.app/.MainActivity"],
                                  capture_output=True, timeout=20,
                                  creationflags=NO_WINDOW)
        if launched.returncode != 0 or b"Error:" in (launched.stdout or b""):
            return False
        time.sleep(6)


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
    """手機端那台 HTTP 伺服器不見了的時候，用 adb 把 app 叫醒。

    收集這件事不該依賴人類記得別滑掉某個 app。

    伺服器是 app 的前景服務，設定本身是存著的，
    所以只要把 app 重新啟動一次，伺服器就會照原設定回來。
    或自己回 app 裡打開。這個限制誠實寫在這裡，不假裝萬能。"""
    if not ADB.exists():
        return False
    if not 可以動前台("叫醒 HC"):
        return False
    try:
        r = subprocess.run([str(ADB), "-s", adb_target(), "shell", "am", "start",
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
    """連續拉不到的時候出聲提醒。

    多一種聲音只會多一種要學的東西。

    同時在資料夾裡留一個檔，因為聲音會過去、檔案不會。"""
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


TUNNEL_PORT = PORT + 10000     # 跟 手機在哪.py 同一套：本機埠 = 手機埠 + 10000


def open_tunnel(port: int = PORT) -> bool:
    """Wi-Fi 打不通 8787 時，改走 adb 隧道（adb forward tcp:18787 → 手機 8787）。

    手機 8787／8788 都在 LISTEN、ping 得到、adb 5555 也連得上，
    **偏偏電腦直連 8787 一律逾時**。手錶線早就會改走隧道所以照收，
    HC 線只會換 IP、不會走隧道 → 一離開家裡就整條卡死、6 小時沒拉到。"""
    if not ADB.exists():
        return False
    def 找得到手機() -> str | None:
        _目標快取["t"] = 0.0
        t = adb_target()
        try:
            out = subprocess.run([str(ADB), "devices"], capture_output=True, text=True,
                                 timeout=10, creationflags=NO_WINDOW).stdout
        except Exception:
            return None
        ok = [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip().endswith("device")]
        return t if t in ok else None
    target = 找得到手機()
    if not target:

        try:
            from 手機在哪 import 手機位址候選
            候選 = 手機位址候選()
        except Exception:
            候選 = []
        for ip in dict.fromkeys(filter(None, [*候選, watch_bridge_ip_hint(), load_config().get("host")])):
            try:
                subprocess.run([str(ADB), "connect", f"{ip}:5555"], capture_output=True,
                               text=True, timeout=10, creationflags=NO_WINDOW)
            except Exception:
                continue
            target = 找得到手機()
            if target:
                break
    if not target:
        return False
    try:
        r = subprocess.run([str(ADB), "-s", target, "forward", f"tcp:{TUNNEL_PORT}", f"tcp:{port}"],
                           capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def scan_lan(port: int = PORT, timeout: float = 0.35) -> str | None:
    """在本機所在的網段裡掃誰開著 8787。

        整夜都在 `連不到手機`。而 adb_wifi_ip() 也救不了 ——

        所以再加一層：**自己去網段裡把手機找出來**。
        認人的方式是打 /status 看回應像不像 HC Webhook，
        不是只看 port 通不通（別台機器也可能剛好開 8787）。

    只掃自己那一段的 /24，256 個位址、平行 64 條，通常兩秒內找到。"""
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


    return scan_lan() or saved


# ── 拉資料 ─────────────────────────────────────────────────────────────
def fetch(host: str, days: int, port: int = PORT,
          timeout: int = 60, token: str | None = None) -> tuple[dict | None, str]:
    """回傳 (payload, 說明)。拉不到就 payload=None，說明寫清楚為什麼 ——

    token：手機那台伺服器綁在 0.0.0.0，同一個 Wi-Fi 上的任何裝置都連得到，"""
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
    """每天第一次成功拉到資料時，把整個時間序列複製一份留底。

    因為那份資料只存在三星那裡，換手機／重設手錶／整合失敗就沒了。
    **東西不在自己手上，就不算自己的。**

    所以這裡做兩件事：
      · 拉回來的當下就落地成純文字檔（誰都刪不掉）
      · 每天再留一份快照 —— 萬一某天正規化的程式寫壞了、
        或者去重的指紋亂掉把檔案寫爛，還有昨天的可以退回去"""
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

    via_tunnel = False
    while True:
        recovery_attempts: set[str] = set()
        if via_tunnel:
            ok = open_tunnel(port) and pull_once(store, "127.0.0.1", days, TUNNEL_PORT, token,
                                                 recovery_attempts=recovery_attempts)
            if not ok:
                via_tunnel = False
                print("    隧道斷了，改回 Wi-Fi 直連")
                ok = pull_once(store, host, days, port, token,
                               recovery_attempts=recovery_attempts)
        else:
            ok = pull_once(store, host, days, port, token,
                           recovery_attempts=recovery_attempts)
        cur_host, cur_port = ("127.0.0.1", TUNNEL_PORT) if via_tunnel else (host, port)

        # 拉得到但資料是舊的 —— 那是 Samsung Health 卡住了，不是網路問題
        if ok:
            age = newest_data_age_minutes()
            since_poke = (datetime.now(timezone.utc) - last_poke).total_seconds() / 60
            if age is not None and age > STALE_MIN and since_poke > POKE_COOLDOWN:
                print(f"    ⚠ 最新資料是 {age:.0f} 分鐘前的 —— 叫醒 Samsung Health")
                if poke_samsung_health():
                    last_poke = datetime.now(timezone.utc)
                    time.sleep(30)          # 給它時間把資料寫進 Health Connect
                    pull_once(store, cur_host, days, cur_port, token,
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
            # 換 IP 也不行 → 走 adb 隧道（手機在、只是區網直連被擋的情況）
            if open_tunnel(port):
                print(f"    Wi-Fi 打不通，改走 adb 隧道 127.0.0.1:{TUNNEL_PORT}…")
                if pull_once(store, "127.0.0.1", days, TUNNEL_PORT, token,
                             recovery_attempts=recovery_attempts):
                    via_tunnel = True
                    fails = 0
                    print("    ✅ 走隧道恢復")
                    time.sleep(interval_min * 60)
                    continue

            # 手機端伺服器半夜死掉，斷了五個小時都沒人知道，


            # 第 2 次失敗就自己動手修 —— 這是今天最常見的故障，
            # 而且修法固定（關掉再打開那個伺服器開關）。

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

    if not args.once:
        import 照鏡子; 照鏡子.獨占("hc_puller")

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
