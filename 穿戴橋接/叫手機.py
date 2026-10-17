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
        host = address.hostname
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
        with opener.open(request, timeout=15) as response:
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
    """GET /said?since=<ms>：她在錶上按「跟阿寶說」講過的話。"""
    base, token = load_config()
    request = urllib.request.Request(f"{base}/said?since={int(since_ms)}",
                                     headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
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
