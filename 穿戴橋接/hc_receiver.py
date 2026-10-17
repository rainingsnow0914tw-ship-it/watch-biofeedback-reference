#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
hc_receiver.py —— Health Connect webhook 接收端（電腦這一側）

手機上的 HC Webhook app（com.hcwebhook.app）會定期把 Health Connect 裡的
生理資料 POST 過來，這支負責收下、去重、存成時間序列。

它是「收集層」。判讀層（比對基線、決定要不要震手錶）是另一支，
兩層刻意分開 —— 收集層一旦開始跑就不該再改，改壞了資料就斷了。

    python hc_receiver.py                 # 預設 0.0.0.0:8788
    python hc_receiver.py --port 9000
    python hc_receiver.py --stats         # 不開伺服器，只印目前累積狀況

資料放在 ..\生理資料\
    raw\YYYY-MM-DD.jsonl    原始 payload 逐筆存檔（永遠不動它，出事可重建）
    series\<type>.jsonl     正規化後的時間序列，一行一筆，已去重
    state.json              去重指紋 + 統計

為什麼原始檔要留：
    正規化的邏輯之後一定會改（欄位改名、單位換算、加新型別）。
    只要原始 payload 還在，重跑一次就能重建 series；反過來救不回來。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8788
DATA_DIR = Path(__file__).resolve().parent.parent / "生理資料"
RAW_DIR = DATA_DIR / "raw"
SERIES_DIR = DATA_DIR / "series"
STATE_FILE = DATA_DIR / "state.json"

# payload 裡除了這兩個 key，其餘都是資料型別陣列
META_KEYS = {"timestamp", "app_version"}

MAX_BODY = 32 * 1024 * 1024      # 32MB，首次同步 48 小時的心率可能不小


# ── 區網 IP ────────────────────────────────────────────────────────────
def lan_ips() -> list[str]:
    """
    列出本機所有可能的區網 IP，最可能的排最前面。

    跟 notify_bridge.py 同一套邏輯，原因也一樣：使用者 開著 Surfshark，
    「連 8.8.8.8 看來源位址」這招會回 VPN 的 10.x 位址，手機永遠連不到。
    所以不猜單一答案，列出全部候選並排序（192.168 優先、10.x 排最後）。
    """
    found: set[str] = set()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        found.add(s.getsockname()[0])
    except Exception:
        pass
    finally:
        s.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except Exception:
        pass

    def rank(ip: str) -> tuple[int, str]:
        if ip.startswith("192.168."):
            return (0, ip)
        if ip.startswith("172."):
            second = int(ip.split(".")[1]) if ip.split(".")[1].isdigit() else 0
            return (1 if 16 <= second <= 31 else 3, ip)
        if ip.startswith("10."):
            return (2, ip)          # VPN 常駐這段，往後排
        return (3, ip)

    ips = sorted((ip for ip in found
                  if not ip.startswith("127.") and not ip.startswith("169.254.")),
                 key=rank)
    return ips or ["127.0.0.1"]


# ── 儲存層 ─────────────────────────────────────────────────────────────
class Store:
    """
    負責寫檔與去重。所有寫入都在同一把鎖底下 —— app 可能同時打多個 webhook，
    ThreadingHTTPServer 會平行進來。
    """

    def __init__(self, root: Path = DATA_DIR):
        self.root = root
        self.raw_dir = root / "raw"
        self.series_dir = root / "series"
        self.state_file = root / "state.json"
        self.lock = threading.Lock()

        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.series_dir.mkdir(parents=True, exist_ok=True)

        self.state = self._load_state()
        # 去重指紋：只放在記憶體的 set，開機時從 state.json 讀回來
        self.seen: set[str] = set(self.state.get("seen", []))

    def _load_state(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[警告] state.json 讀不動（{e}），當成新的開始。"
                      f"raw\\ 還在，需要的話可以重建。")
        return {"seen": [], "counts": {}, "last_payload_at": None,
                "payloads_received": 0}

    def _save_state(self) -> None:
        self.state["seen"] = sorted(self.seen)
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.state_file)          # 原子換檔，中途斷電不會留半個檔

    @staticmethod
    def fingerprint(dtype: str, record: dict) -> str:
        """
        一筆紀錄的身分證。

        用整筆內容做雜湊而不是挑 time 當 key —— 因為同一個時間點可能有
        多筆（不同來源裝置），挑 time 會把真資料吃掉。
        Health Connect 的紀錄是不可變快照，同樣內容重送就是重複，這樣去重是對的。
        """
        blob = dtype + "|" + json.dumps(record, sort_keys=True,
                                        ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]

    def ingest(self, payload: dict, remote: str) -> dict:
        """
        收下一個 payload。回傳這次實際新增了什麼。

        回傳裡的 first_seen 是「這個型別史上第一次出現」——
        專門為了 2026-08-11 那個問題存在的：權限跟型別都設好了，
        但心率、睡眠會不會真的流過來，只有時間知道。
        與其之後靠人記得回頭查，不如讓它自己喊。
        """
        now = datetime.now(timezone.utc)
        added: dict[str, int] = {}
        first_seen: list[str] = []
        dupes = 0

        with self.lock:
            # 1) 原始存檔 —— 先寫這個，正規化爆掉也不會掉資料
            raw_line = json.dumps(
                {"received_at": now.isoformat(), "remote": remote, "payload": payload},
                ensure_ascii=False, separators=(",", ":"))
            raw_path = self.raw_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"
            with raw_path.open("a", encoding="utf-8") as f:
                f.write(raw_line + "\n")

            # 2) 逐型別正規化
            for key, value in payload.items():
                if key in META_KEYS or not isinstance(value, list):
                    continue
                fresh = []
                for record in value:
                    if not isinstance(record, dict):
                        continue
                    fp = self.fingerprint(key, record)
                    if fp in self.seen:
                        dupes += 1
                        continue
                    self.seen.add(fp)
                    fresh.append({**record, "_ingested_at": now.isoformat()})

                if fresh:
                    if key not in self.state.get("counts", {}):
                        first_seen.append(key)
                    path = self.series_dir / f"{key}.jsonl"
                    with path.open("a", encoding="utf-8") as f:
                        for record in fresh:
                            f.write(json.dumps(record, ensure_ascii=False,
                                               separators=(",", ":")) + "\n")
                    added[key] = len(fresh)
                    self.state.setdefault("counts", {})
                    self.state["counts"][key] = \
                        self.state["counts"].get(key, 0) + len(fresh)

            self.state["last_payload_at"] = now.isoformat()
            self.state["payloads_received"] = \
                self.state.get("payloads_received", 0) + 1
            self._save_state()

        return {"added": added, "duplicates": dupes, "first_seen": first_seen}

    def stats(self) -> dict:
        with self.lock:
            return {
                "payloads_received": self.state.get("payloads_received", 0),
                "last_payload_at": self.state.get("last_payload_at"),
                "counts": dict(self.state.get("counts", {})),
                "data_dir": str(self.root),
            }


# ── HTTP ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    store: Store = None          # 由 serve() 塞進來
    server_version = "hc-receiver/1.0"

    def _json(self, code: int, body: dict) -> None:
        blob = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/", "/ping"):
            self._json(200, {"status": "ok", "service": "hc-receiver"})
        elif path == "/stats":
            self._json(200, self.store.stats())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._json(400, {"error": "empty body"})
            return
        if length > MAX_BODY:
            self._json(413, {"error": "body too large"})
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._json(400, {"error": f"bad json: {e}"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"error": "payload must be a JSON object"})
            return

        remote = self.client_address[0]
        try:
            result = self.store.ingest(payload, remote)
        except Exception as e:
            # 回 500 讓 app 重試，資料不要掉
            self._json(500, {"error": str(e)})
            print(f"[錯誤] 存檔失敗：{e}", file=sys.stderr)
            return

        stamp = datetime.now().strftime("%H:%M:%S")
        if result["added"]:
            summary = "、".join(f"{k} {v} 筆" for k, v in result["added"].items())
        else:
            summary = "沒有新資料"
        print(f"[{stamp}] {remote} → {summary}"
              + (f"（重複 {result['duplicates']} 筆已跳過）"
                 if result["duplicates"] else ""))

        self._json(200, {"status": "ok", **result})

    def log_message(self, fmt, *args):
        pass          # 預設那行 access log 太吵，我們自己印有意義的


def serve(port: int = PORT) -> None:
    store = Store()
    Handler.store = store

    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    ips = lan_ips()

    print("\n  Health Connect 接收端已啟動")
    print(f"  資料存到　{DATA_DIR}\n")
    print("  手機 app 的 webhook 網址填這個：")
    print(f"      http://{ips[0]}:{port}/        ← 先試這個")
    if len(ips) > 1:
        print("\n  連不到就換下面這些：")
        for ip in ips[1:]:
            hint = "（VPN 位址，多半沒用）" if ip.startswith("10.") else ""
            print(f"      http://{ip}:{port}/    {hint}")
    print(f"\n  USB 接著的時候另外還有一條路（不受 VPN／換 Wi-Fi 影響）：")
    print(f"      adb reverse tcp:{port} tcp:{port}   然後 webhook 填 "
          f"http://127.0.0.1:{port}/")

    s = store.stats()
    if s["payloads_received"]:
        print(f"\n  目前累積：{s['payloads_received']} 個 payload，"
              f"最後一次 {s['last_payload_at']}")
        for k, v in sorted(s["counts"].items()):
            print(f"      {k:<28} {v} 筆")
    else:
        print("\n  還沒收到任何資料 —— 等手機第一次同步。")
    print("\n  Ctrl+C 停止\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  停止。資料都在 " + str(DATA_DIR))
        httpd.shutdown()


class Tee:
    """
    同時寫終端機和紀錄檔。

    為什麼不用 .bat 的 `>>` 轉向：開機那條路是 pythonw.exe（無主控台），
    cmd 的轉向在 `start /b` 後面會被吃掉，實測整行變成亂命令。
    紀錄檔由程式自己開最可靠 —— 不管誰怎麼啟動它都會有 log。
    """

    def __init__(self, path: Path, stream):
        self.stream = stream
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a", encoding="utf-8")

    def write(self, text: str) -> int:
        try:
            self.stream.write(text)
        except Exception:
            pass                      # pythonw 沒有真的 stdout，寫不進去不算錯
        self.file.write(text)
        self.file.flush()
        return len(text)

    def flush(self) -> None:
        try:
            self.stream.flush()
        except Exception:
            pass
        self.file.flush()


def main() -> None:
    ap = argparse.ArgumentParser(description="Health Connect webhook 接收端")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--stats", action="store_true",
                    help="只印目前累積狀況，不開伺服器")
    ap.add_argument("--log", action="store_true",
                    help="同時寫進 生理資料\\receiver.log（開機自動啟動時用）")
    args = ap.parse_args()

    if args.log:
        log_path = DATA_DIR / "receiver.log"
        sys.stdout = Tee(log_path, sys.stdout)
        sys.stderr = sys.stdout
        print(f"\n===== 啟動 {datetime.now().astimezone().isoformat()} =====")

    if args.stats:
        s = Store().stats()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return

    serve(args.port)


if __name__ == "__main__":
    main()
