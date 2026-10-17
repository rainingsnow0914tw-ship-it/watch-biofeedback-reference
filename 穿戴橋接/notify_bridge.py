"""
本地通知橋 —— 電腦推訊息到手機，完全不經過雲端。

為什麼是這個做法：
    要「電腦通知手機」，一般人的第一反應是找推播服務
    （Firebase、LINE、Telegram）。但那些都要把訊息送出網際網路，
    繞一圈再回到同一個房間裡的手機。

    這支不繞。電腦開一個 WebSocket server，手機用瀏覽器連進來，
    訊息直接在區網內走完。沒有帳號、沒有金鑰、沒有第三方伺服器，
    而且**手機不用裝任何 app**。

    代價：網頁要開著。手機鎖屏久了瀏覽器可能被系統凍結，
    連線會斷（程式有自動重連，但螢幕全黑時不保證）。
    要背景推播就得換 ntfy 自架那條路，那需要裝 app。

用法：
    python notify_bridge.py                    # 啟動，印出手機該開的網址
    python notify_bridge.py --test             # 啟動後 5 秒自動送一則測試訊息

    當成模組用（給 biofeedback 之類的呼叫）：
        from notify_bridge import NotifyBridge
        bridge = NotifyBridge()
        await bridge.start()
        await bridge.push("心率到 120 了", level="warn")

【重要】使用者 的電腦開著 Surfshark VPN。
        VPN 會改寫路由，手機可能連不到電腦的區網 IP。
        連不上的話第一件事是把 VPN 關掉再試。
        （2026-06-23 連印表機時就是同一個坑。）
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from datetime import datetime

import websockets
from websockets.http11 import Response
from websockets.datastructures import Headers

PORT = 8765

# 手機端的頁面。內嵌成字串是刻意的——整個橋只有一個檔案，
# 沒有額外的靜態檔要管，複製到別台機器就能跑。
PAGE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>通知橋</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; min-height: 100vh; background: #161315; color: #EDE7E8;
    font-family: -apple-system, "PingFang TC", "Noto Sans TC", sans-serif;
    display: flex; flex-direction: column;
  }
  header {
    padding: 18px 20px; border-bottom: 1px solid #322A2E;
    display: flex; align-items: center; gap: 10px;
  }
  #dot { width: 9px; height: 9px; border-radius: 50%; background: #8A6; flex: none; }
  #dot.off { background: #A55; }
  #status { font-size: 13px; color: #A99EA1; }
  #enable {
    margin: 20px; padding: 15px; border: 0; border-radius: 6px;
    background: #C0574E; color: #fff; font-size: 15px; font-weight: 600;
  }
  #list { flex: 1; overflow-y: auto; padding: 12px 16px 32px; }
  .msg {
    background: #211D20; border-left: 3px solid #C0574E; border-radius: 4px;
    padding: 12px 14px; margin-bottom: 10px;
  }
  .msg.warn { border-left-color: #D9AE55; }
  .msg.ok   { border-left-color: #6BB6A1; }
  .msg p { margin: 0; font-size: 15px; line-height: 1.5; }
  .msg time { display: block; font-size: 11px; color: #7A6E71; margin-top: 5px;
              font-variant-numeric: tabular-nums; }
  .empty { color: #7A6E71; font-size: 14px; text-align: center; padding: 40px 20px; }
</style>
</head>
<body>
<header>
  <span id="dot" class="off"></span>
  <span id="status">連線中…</span>
</header>
<button id="enable">點一下啟用震動與提示音</button>
<div id="list"><p class="empty">等待訊息</p></div>

<script>
let armed = false, ctx = null;

// 手機瀏覽器規定：震動和聲音必須由使用者手勢觸發過一次才准用。
// 所以先擺一顆按鈕，按過之後才 arm。
document.getElementById('enable').onclick = () => {
  armed = true;
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (navigator.vibrate) navigator.vibrate(60);
  document.getElementById('enable').remove();
};

function beep() {
  if (!ctx) return;
  const o = ctx.createOscillator(), g = ctx.createGain();
  o.connect(g); g.connect(ctx.destination);
  o.frequency.value = 880; o.type = 'sine';
  g.gain.setValueAtTime(0.0001, ctx.currentTime);
  g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + 0.01);
  g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35);
  o.start(); o.stop(ctx.currentTime + 0.36);
}

function show(m) {
  const list = document.getElementById('list');
  const empty = list.querySelector('.empty');
  if (empty) empty.remove();
  const d = document.createElement('div');
  d.className = 'msg ' + (m.level || '');
  const p = document.createElement('p');
  p.textContent = m.text;                       // textContent 不是 innerHTML，避免注入
  const t = document.createElement('time');
  t.textContent = new Date().toLocaleTimeString();
  d.append(p, t);
  list.prepend(d);
  if (armed) {
    if (navigator.vibrate) navigator.vibrate(m.pattern || [120, 60, 120]);
    beep();
  }
}

// 自動重連：手機切背景、Wi-Fi 抖動都會斷線,斷了就每 2 秒試一次
function connect() {
  const ws = new WebSocket('ws://' + location.host);
  const dot = document.getElementById('dot'), st = document.getElementById('status');

  ws.onopen = () => { dot.classList.remove('off'); st.textContent = '已連線'; };
  ws.onmessage = e => { try { show(JSON.parse(e.data)); } catch (_) {} };
  ws.onclose = () => {
    dot.classList.add('off'); st.textContent = '斷線，重連中…';
    setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();
}
connect();
</script>
</body>
</html>"""


def lan_ips() -> list[str]:
    """
    列出本機所有可能的區網 IP，最可能的排最前面。

    踩坑紀錄（2026-08-08 首次實跑，而且是我自己預警過的坑）：
        原本只用「開 UDP socket 假裝連 8.8.8.8，看作業系統選哪個來源位址」
        這招取單一 IP。它在乾淨的機器上是對的，但 使用者 開著 Surfshark ——
        路由到 8.8.8.8 當然走 VPN，於是拿到 10.0.0.X（VPN 內部位址）。
        手機連那個永遠連不到。

        更糟的是這種錯誤「看起來成功」：程式正常啟動、印出一個像樣的網址，
        只有真的拿手機去連才會發現是死的。

    改法：不猜單一答案，列出全部候選並排序 ——
        192.168.x.x  家用路由器最常見，優先
        172.16–31.x  企業／Docker 網段，次之
        10.x.x.x     VPN 最愛用這段，排最後
    並且全部印給使用者看，猜錯了她還有別的可以試。
    """
    found: set[str] = set()

    # 路由法：拿得到就收下，它在沒有 VPN 時是最準的
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        found.add(s.getsockname()[0])
    except Exception:
        pass
    finally:
        s.close()

    # 主機名法：會把所有介面的位址都撈出來，包含路由法看不到的
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


def lan_ip() -> str:
    """最可能的那一個。仍然保留，給只要一個答案的呼叫端用。"""
    return lan_ips()[0]


class NotifyBridge:
    """WebSocket 通知伺服器。可獨立跑，也可以被別的程式 import。"""

    def __init__(self, port: int = PORT):
        self.port = port
        self.clients: set = set()
        self._server = None

    async def _handler(self, ws) -> None:
        self.clients.add(ws)
        print(f"  [{datetime.now():%H:%M:%S}] 手機已連線"
              f"（目前 {len(self.clients)} 台）")
        try:
            await ws.wait_closed()
        finally:
            self.clients.discard(ws)
            print(f"  [{datetime.now():%H:%M:%S}] 手機斷線"
                  f"（剩 {len(self.clients)} 台）")

    async def _http(self, connection, request):
        """
        非 WebSocket 的請求就回傳網頁。

        這樣 HTTP 跟 WebSocket 共用同一個 port——手機只要記一個網址，
        不用分「網頁在 8765、WebSocket 在 8766」那種容易記錯的設定。
        """
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None                     # 交給 websockets 正常處理
        body = PAGE.encode()
        return Response(200, "OK", Headers({
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(body)),
        }), body)

    async def start(self) -> str:
        self._server = await websockets.serve(
            self._handler, "0.0.0.0", self.port, process_request=self._http
        )
        return f"http://{lan_ip()}:{self.port}"

    async def push(self, text: str, level: str = "",
                   pattern: list[int] | None = None) -> int:
        """
        推一則訊息給所有連著的手機。回傳送達幾台。

        level: "" / "warn" / "ok" —— 決定左側色條
        pattern: 震動節奏，毫秒陣列，例如 [200,100,200]
        """
        if not self.clients:
            return 0
        payload = json.dumps({"text": text, "level": level, "pattern": pattern})
        dead = set()
        for ws in self.clients:
            try:
                await ws.send(payload)
            except Exception:
                dead.add(ws)
        self.clients -= dead
        return len(self.clients)


async def main() -> None:
    bridge = NotifyBridge()
    await bridge.start()
    ips = lan_ips()

    print("=" * 66)
    print("  本地通知橋　—　完全區網內，不經過雲端")
    print("=" * 66)
    print("\n  手機瀏覽器打開這個網址：\n")
    print(f"      http://{ips[0]}:{bridge.port}        ← 先試這個\n")

    if len(ips) > 1:
        print("  連不上的話換這些試：\n")
        for ip in ips[1:]:
            hint = "（VPN 位址，多半沒用）" if ip.startswith("10.") else ""
            print(f"      http://{ip}:{bridge.port}    {hint}")
        print()

    print("  開啟後點一下「啟用震動與提示音」，頁面保持開著即可。")
    print("\n  ⚠ 全部都連不上 → 把 VPN 關掉再試一次。")
    print("     Surfshark 會改寫路由，手機找不到這台電腦。")
    print("\n  Ctrl+C 結束\n")
    print("-" * 66)

    if "--test" in sys.argv:
        await asyncio.sleep(5)
        n = await bridge.push("這是一則測試訊息 —— 收到代表整條鏈通了",
                              level="ok", pattern=[200, 100, 200])
        print(f"  [測試] 已送出，送達 {n} 台")

    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已停止")
