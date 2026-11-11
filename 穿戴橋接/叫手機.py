# -*- coding: utf-8 -*-
"""從電腦透過 8788 叫自己的手機；設定檔全程只讀。"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


PORT = 8788
CONFIG = Path(__file__).resolve().parent.parent / "生理資料" / "手錶橋接" / "puller_config.json"


# 每句都繞雲端中轉站（慢 1～3 秒、偶爾斷）。現在：電腦先看自己在哪個網段——
#   跟設定檔同網段 → 照舊；不同 → 用上次在這個網段找到的手機位址（記在 生理資料/手機位址.json）；
#   那個也不通 → 在這個網段 254 台裡找開著 8788、而且帶我們 token 回得出 /said 的那台（約 2～4 秒，10 分鐘最多掃一次）。
# 找不到就回設定檔的位址，呼叫端照舊退回中轉站，行為跟以前一樣。
import socket as _socket
import time as _time
import concurrent.futures as _cf

_ADDR_BOOK = CONFIG.parent.parent / "手機位址.json"
_RESOLVED = {"t": 0.0, "prefix": "", "host": ""}
_LAST_SCAN = {"t": 0.0}


def _my_prefixes() -> list[str]:
    """這台電腦現在的區網網段（192.168.x.），排除 VPN／Tailscale。"""
    out = []
    try:
        for info in _socket.getaddrinfo(_socket.gethostname(), None, _socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("192.168.") and ip.rsplit(".", 1)[0] + "." not in out:
                out.append(ip.rsplit(".", 1)[0] + ".")
    except OSError:
        pass
    try:   # 主要那張卡（往外連時用的來源位址）
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM); s.connect(("192.168.0.X", 9)); ip = s.getsockname()[0]; s.close()
        if ip.startswith("192.168.") and ip.rsplit(".", 1)[0] + "." not in out:
            out.insert(0, ip.rsplit(".", 1)[0] + ".")
    except OSError:
        pass
    return out


def _port_open(host: str, timeout: float = 0.4) -> bool:
    try:
        with _socket.create_connection((host, PORT), timeout=timeout):
            return True
    except OSError:
        return False


def _is_our_phone(host: str, token: str) -> bool:
    """敲 /said 帶 token：回得出 JSON 才算我們的手機（別人的 8788 不會有我們的 token）。"""
    try:
        req = urllib.request.Request(f"http://{host}:{PORT}/said?since={int(_time.time() * 1000)}",
                                     headers={"Authorization": f"Bearer {token}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=1.5) as r:
            return isinstance(json.loads(r.read().decode("utf-8")), dict)
    except Exception:
        return False


def _book() -> dict:
    try:
        return json.loads(_ADDR_BOOK.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_book(b: dict) -> None:
    try:
        _ADDR_BOOK.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def resolve_host(config_host: str, token: str) -> str:
    """回這次該敲的手機位址。60 秒內重用上次結果。"""
    now = _time.time()

    #   以前任何位址都會被換掉 → 測試的 192.0.2.123 被換成真手機 192.168.0.X（test_call_phone 3 項 FAIL），
    #   以後設定檔改填 Tailscale 之類的位址也會被誤換。
    if not config_host.startswith("192.168."):
        return config_host
    mine = _my_prefixes()
    if not mine or any(config_host.startswith(p) for p in mine):
        return config_host   # 電腦跟設定檔同網段（在家）→ 照舊
    prefix = mine[0]
    if _RESOLVED["prefix"] == prefix and now - _RESOLVED["t"] < 60:
        return _RESOLVED["host"]
    book = _book()
    cand = book.get(prefix)
    if cand and _port_open(cand) and _is_our_phone(cand, token):
        _RESOLVED.update(t=now, prefix=prefix, host=cand)
        return cand
    if now - _LAST_SCAN["t"] < 600:
        return cand or config_host
    _LAST_SCAN["t"] = now
    hosts = [f"{prefix}{i}" for i in range(1, 255)]
    with _cf.ThreadPoolExecutor(64) as ex:
        opened = [h for h, ok in zip(hosts, ex.map(_port_open, hosts)) if ok]
    found = next((h for h in opened if _is_our_phone(h, token)), None)
    if found:
        book[prefix] = found; book["_更新"] = _time.strftime("%Y-%m-%d %H:%M"); _save_book(book)
        _RESOLVED.update(t=now, prefix=prefix, host=found)
        print(f"[{_time.strftime('%H:%M:%S')}] 手機在這個網路的位址：{found}（電腦網段 {prefix}，已記下）", file=sys.stderr)
        return found
    return cand or config_host


_TUNNEL = {"t": 0.0, "base": None}


def _tunnel_if_blocked(config_host: str, host: str) -> str | None:
    """直連不通就借 手機在哪.找手機（會自己 adb connect 手機自報的位址、開 adb forward 18788）。
    只管設定檔是區網位址的情況（測試的 192.0.2.x 不碰）；結果快取 60 秒，哨兵常呼叫也不會每次卡幾秒。"""
    if not config_host.startswith("192.168."):
        return None
    now = _time.time()
    if now - _TUNNEL["t"] < 60:
        return _TUNNEL["base"]
    base = None
    if not _port_open(host, 0.8):
        try:
            from 手機在哪 import 找手機
            b, _how = 找手機(PORT)
            if b and b.startswith("http://127.0.0.1"):
                base = b
        except Exception:
            base = None
    _TUNNEL.update(t=now, base=base)
    return base


def load_config() -> tuple[str, str]:
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"讀不到設定檔 {CONFIG}: {exc}") from exc

    if not isinstance(config, dict):
        raise RuntimeError("設定檔必須是 JSON 物件")
    ip = config.get("ip")
    token = config.get("token")
    if not isinstance(ip, str) or not isinstance(token, str):
        raise RuntimeError("設定檔的 ip 與 token 必須是文字")
    ip, token = ip.strip(), token.strip()
    if not ip:
        raise RuntimeError(f"設定檔缺少 ip：{CONFIG}")
    if not token:
        raise RuntimeError(f"設定檔缺少 token：{CONFIG}")

    if any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise RuntimeError("token 含有無效控制字元")
    try:
        address = urllib.parse.urlsplit(ip if "://" in ip else f"http://{ip}")
        if (address.scheme not in {"http", "https"} or not address.hostname
                or address.username is not None or address.password is not None
                or address.path not in {"", "/"} or address.query or address.fragment
                or address.port not in {None, PORT}):
            raise ValueError("invalid bridge address")
        host = resolve_host(address.hostname, token)
        tunnel = _tunnel_if_blocked(address.hostname, host)
        if tunnel:
            return tunnel, token
        if ":" in host:
            host = f"[{host}]"
        base = f"{address.scheme}://{host}:{PORT}"
    except ValueError as exc:
        raise RuntimeError("ip 必須是手機位址，且只能使用 8788；不能帶帳密、路徑或其他連接埠") from exc
    return base, token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 不把 Bearer token 跟著轉址交出去。


def send(path: str, payload: dict[str, object]) -> dict[str, object]:
    base, token = load_config()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=6) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"手機拒絕請求（HTTP {exc.code}）：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"連不到手機 8788：{exc.reason}；請確認同 Wi-Fi，並開啟阿寶橋接、允許附近裝置") from exc
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("手機連線逾時或回應無效") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("手機回應不是有效 JSON") from exc
    if not isinstance(result, dict) or result.get("ok") is not True or result.get("action") != path.lstrip("/"):
        raise RuntimeError("手機沒有確認執行這個指令")
    return result


def said_since(since_ms: int) -> dict[str, object]:
    """ """
    base, token = load_config()
    request = urllib.request.Request(f"{base}/said?since={int(since_ms)}",
                                     headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"連不到手機 8788：{exc.reason}") from exc


DIAG_READ = ("status", "log")
DIAG_ACTION = ("restart_collector", "wifi_on", "wifi_off", "adb_on", "adb_keep_on", "adb_keep_off", "send_now")


def watch_diag(cmd: str) -> dict[str, object]:
    """ """
    base, token = load_config()
    request = urllib.request.Request(f"{base}/watch_diag?cmd={cmd}", data=b"" if cmd in DIAG_ACTION else None,
                                     method="POST" if cmd in DIAG_ACTION else "GET",
                                     headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(detail)   # 409 錶沒連／504 錶沒回，內容本身就是診斷
        except json.JSONDecodeError:
            raise RuntimeError(f"手機拒絕（HTTP {exc.code}）：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"連不到手機 8788：{exc.reason}") from exc


def ring_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("秒數必須是 1～30 的整數") from exc
    if not 1 <= seconds <= 30:
        raise argparse.ArgumentTypeError("秒數必須在 1～30 之間")
    return seconds


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="透過自己的 8788 橋接叫手機")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--ring", type=ring_seconds, metavar="SECONDS", help="用鬧鐘音量響鈴並震動")
    action.add_argument("--notify", nargs=2, metavar=("TITLE", "TEXT"), help="送出通知標題與內文")
    action.add_argument("--say", metavar="TEXT", help="2026-10-17：一句話轉給手錶唸出來（錶不在旁邊回 409）")
    action.add_argument("--said", nargs="?", const="0", metavar="SINCE_MS", help="列出她在錶上說過的話（可帶 since 毫秒）")
    action.add_argument("--watch-diag", choices=DIAG_READ + DIAG_ACTION, metavar="CMD",
                        help="2026-11-05 不用 adb 問錶：status（版本／佩戴／Wi-Fi）、log、restart_collector、wifi_on、wifi_off")
    parser.add_argument("--reply", action="store_true", help="在通知上標示需要 使用者 回覆（只可搭配 --notify）")
    parser.add_argument("--quiet", action="store_true", help="搭配 --say：錶只震一下＋顯示字，不唸（她想聽再按錶上的 🔊）")
    args = parser.parse_args(argv)
    if args.reply and not args.notify:
        parser.error("--reply 只能搭配 --notify")
    if args.notify:
        title, text = (value.strip() for value in args.notify)
        if not title or not text or len(title) > 100 or len(text) > 2000:
            parser.error("通知標題需 1～100 字，內文需 1～2000 字")
        args.notify = [title, text]
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        if args.ring is not None:
            result = send("/ring", {"seconds": args.ring})
        elif args.say is not None:
            text = args.say.strip()
            if not text or len(text) > 300:
                print("失敗：--say 需 1～300 字", file=sys.stderr); return 1
            result = send("/say", {"text": text, "speak": not args.quiet})
        elif args.said is not None:
            result = said_since(int(args.said))
        elif args.watch_diag is not None:
            result = watch_diag(args.watch_diag)
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 0 if result.get("ok") else 2
        else:
            title, text = args.notify
            result = send("/notify", {"title": title, "text": text, "reply_required": args.reply})
    except RuntimeError as exc:
        print(f"失敗：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
