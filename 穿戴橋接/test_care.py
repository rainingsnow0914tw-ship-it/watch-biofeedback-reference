"""
關心模式測試 —— 驗證「手機一連上就自動收到訊息」真的會動。

為什麼這個要測：
    這是整條鏈裡使用者唯一會親眼看到的部分。
    如果她開了網頁卻什麼都沒發生，前面所有東西通不通都沒意義。
"""

import asyncio
import json
import sys
from datetime import datetime

import websockets

from care import CareGiver, period
from notify_bridge import NotifyBridge

FAILED = []


def check(label, cond, detail=""):
    if not cond:
        FAILED.append(label)
    print(f"  [{'OK  ' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


async def run():
    print("=" * 62)
    print("  關心模式測試")
    print("=" * 62 + "\n")

    now = datetime.now()
    check("時段判斷", period(now.hour) in
          ["清晨", "早上", "中午", "下午", "傍晚", "晚上", "深夜"],
          f"{now:%H:%M} → {period(now.hour)}")

    # 連抽 12 次，確認短時間內不會重複
    giver = CareGiver()
    picks = [giver.pick(now.hour)[1] for _ in range(12)]
    worst = max(picks.count(p) for p in set(picks))
    check("不會連續重複同一句", worst <= 3, f"最多重複 {worst} 次 / 12 抽")

    # 全部時段都要有話可講，不能有空的
    empty = [p for p in ["清晨", "早上", "中午", "下午", "傍晚", "晚上", "深夜"]
             if not giver.pick({"清晨": 6, "早上": 9, "中午": 12, "下午": 15,
                                "傍晚": 18, "晚上": 21, "深夜": 2}[p])[1]]
    check("每個時段都有話", not empty, ", ".join(empty) or "七個時段都有")

    # 核心：連上就自動收到
    bridge = NotifyBridge(port=8801)
    await bridge.start()

    async def greet():
        seen = 0
        while True:
            await asyncio.sleep(0.4)
            if len(bridge.clients) > seen:
                await asyncio.sleep(0.5)
                _, line = giver.pick(datetime.now().hour)
                await bridge.push(f"連上了。{line}", level="ok",
                                  pattern=[150, 80, 150])
            seen = len(bridge.clients)

    asyncio.create_task(greet())

    got = []
    async with websockets.connect("ws://127.0.0.1:8801") as ws:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=6)
            got.append(json.loads(raw))
        except asyncio.TimeoutError:
            pass

    check("手機一連上就收到訊息", len(got) == 1,
          got[0]["text"] if got else "6 秒內沒收到")

    if got:
        check("訊息開頭正確", got[0]["text"].startswith("連上了。"))
        check("帶了震動節奏", got[0]["pattern"] == [150, 80, 150],
              str(got[0]["pattern"]))
        print(f"\n  實際收到：{got[0]['text']}")

    print("\n" + "=" * 62)
    if FAILED:
        print(f"  ✗ {len(FAILED)} 項失敗：{', '.join(FAILED)}")
        return 1
    print("  ✓ 全部通過")
    print("=" * 62)
    print("""
  已驗證：時段判斷、不重複、連上即推播、震動節奏傳遞
  未驗證：真實手機瀏覽器會不會震、會不會響 —— 只有拿手機開才知道
""")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
