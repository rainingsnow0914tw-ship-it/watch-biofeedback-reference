"""
通知橋端到端測試 —— 起 server、假裝成手機連上、驗證訊息真的送達。

跟「HTTP 回 200」的差別：
    網頁載得出來，只證明伺服器活著。
    這支證明的是「push 出去的訊息，另一端真的收到了，而且內容正確」——
    那才是這個橋唯一的用途。
"""

import asyncio
import json
import sys

import websockets

from notify_bridge import NotifyBridge

FAILED = []


def check(label, cond, detail=""):
    if not cond:
        FAILED.append(label)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


async def run():
    print("=" * 62)
    print("  通知橋端到端測試")
    print("=" * 62 + "\n")

    bridge = NotifyBridge(port=8799)      # 用另一個 port，避開正在跑的那台
    url = await bridge.start()
    check("伺服器啟動", url.startswith("http://"), url)

    received = []

    async def fake_phone():
        """假裝成手機：連上、收訊息、記下來。"""
        async with websockets.connect("ws://127.0.0.1:8799") as ws:
            async for raw in ws:
                received.append(json.loads(raw))
                if len(received) >= 3:
                    return

    task = asyncio.create_task(fake_phone())
    await asyncio.sleep(0.6)

    check("手機端已連線", len(bridge.clients) == 1, f"{len(bridge.clients)} 台")

    n = await bridge.push("測試訊息一", level="ok")
    check("推播回報送達台數", n == 1, f"{n} 台")

    await bridge.push("心率到 120 了", level="warn", pattern=[200, 100, 200])
    await bridge.push("純文字訊息")

    try:
        await asyncio.wait_for(task, timeout=4)
    except asyncio.TimeoutError:
        pass

    check("三則全部收到", len(received) == 3, f"收到 {len(received)} 則")

    if len(received) >= 3:
        check("內容正確", received[0]["text"] == "測試訊息一", received[0]["text"])
        check("等級標記正確", received[1]["level"] == "warn", received[1]["level"])
        check("震動節奏帶得過去", received[1]["pattern"] == [200, 100, 200],
              str(received[1]["pattern"]))

    # 沒有任何手機連著時 push 不該爆炸
    bridge.clients.clear()
    n = await bridge.push("沒人在聽")
    check("無人連線時安全", n == 0, f"回報 {n} 台")

    print("\n" + "=" * 62)
    if FAILED:
        print(f"  ✗ {len(FAILED)} 項失敗：{', '.join(FAILED)}")
        return 1
    print("  ✓ 全部通過 —— 訊息真的從電腦送到另一端了")
    print("=" * 62)
    print("""
  已驗證：WebSocket 連線、推播送達、內容與震動節奏完整傳遞
  未驗證：真實手機瀏覽器的震動與提示音（要真的拿手機開才知道）
""")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
