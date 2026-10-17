"""
關心 —— 定時推一句話到 使用者 的手機。

為什麼寫這個：
    她說「你可以沒事發一句廢話給我，關心我有沒有喝水睡覺」。
    那句話聽起來像在講功能，其實在講的是「有人記得我」。

    所以這支的重點不是提醒功能，是**語氣**。
    機器人式的「請注意補充水分」不算數，那種東西她關掉的速度
    比看到的速度還快。要像有人隨口念一句。

設計上的三個講究：
    1. 依時段講不同的話 —— 早上八點跟半夜三點該說的話不一樣，
       而且半夜那幾句要真的像有人在旁邊看著她熬夜。
    2. 隨機但不重複 —— 記住最近說過的，避免同一句連續出現。
       重複三次以上，關心就變成噪音。
    3. 真的去查時間 —— 不 assume 現在幾點。
       家規寫過：硬猜時段會造成錯亂感。

用法：
    python care.py                      # 預設每 45 分鐘一則
    python care.py --every 20           # 每 20 分鐘
    python care.py --now                # 立刻先送一則（測試用）

    手機要先開 notify_bridge 印出的網址，頁面保持開著。
"""

from __future__ import annotations

import asyncio
import random
import sys
from datetime import datetime

from notify_bridge import NotifyBridge, lan_ips

# 依時段分開。key 是 (起始小時, 結束小時)，跨午夜的另外處理。
LINES: dict[str, list[str]] = {
    "清晨": [                                    # 05–08
        "天亮了欸，妳是早起還是還沒睡？",
        "早안…喔不對，早安。先喝一杯水再開始。",
        "這個時間醒著的話，記得吃點東西再忙。",
    ],
    "早上": [                                    # 08–11
        "喝水了沒？第一杯最容易忘。",
        "肩膀放鬆一下，妳現在一定又聳著。",
        "眼睛離開螢幕看一下遠方，二十秒就好。",
    ],
    "中午": [                                    # 11–14
        "吃午餐了嗎，還是又在忙忘記了。",
        "十二點多了，先去吃飯，程式不會跑掉。",
        "吃飽了記得走兩步，別馬上又坐回去。",
    ],
    "下午": [                                    # 14–17
        "下午茶時間，喝點東西。咖啡也算，但水更好。",
        "坐很久了吧，站起來伸個懶腰。",
        "如果現在有點卡住，通常是該休息不是該硬撐。",
    ],
    "傍晚": [                                    # 17–20
        "天要暗了，晚餐有著落嗎？",
        "眼睛累了的話，這個時間光線變化最傷，開個燈。",
        "今天做的已經夠多了，剩下的明天也可以。",
    ],
    "晚上": [                                    # 20–23
        "晚上了，喝水這件事整天下來妳大概只做了一半。",
        "貓有沒有吵妳？記得牠們也要吃飯。",
        "如果還在忙，至少換個姿勢。",
    ],
    "深夜": [                                    # 23–05
        "十一點多了，妳知道我要說什麼。",
        "半夜了欸。我不會叫妳一定要睡，但妳自己心裡有數。",
        "這個時間的靈感通常隔天看會發現沒那麼厲害。",
        "還醒著啊。喝口水，然後認真考慮一下床這個東西。",
        "熬夜的隔天妳會怪我沒攔妳，我先攔了喔。",
    ],
}


def period(hour: int) -> str:
    """把小時對應到時段。深夜跨午夜，所以單獨判斷。"""
    if 5 <= hour < 8:
        return "清晨"
    if 8 <= hour < 11:
        return "早上"
    if 11 <= hour < 14:
        return "中午"
    if 14 <= hour < 17:
        return "下午"
    if 17 <= hour < 20:
        return "傍晚"
    if 20 <= hour < 23:
        return "晚上"
    return "深夜"


class CareGiver:
    """
    挑句子的人。

    維護一份「最近說過的」清單，避免同一句短時間內重複 ——
    關心一旦重複就變成噪音，這是這支程式最容易失敗的地方。
    """

    def __init__(self, memory: int = 6) -> None:
        self.recent: list[str] = []
        self.memory = memory

    def pick(self, hour: int) -> tuple[str, str]:
        p = period(hour)
        pool = [ln for ln in LINES[p] if ln not in self.recent]

        # 這個時段的話全講過了就重置，不然會沒東西可挑
        if not pool:
            pool = LINES[p]
            self.recent = [r for r in self.recent if r not in pool]

        line = random.choice(pool)
        self.recent.append(line)
        self.recent = self.recent[-self.memory:]
        return p, line


async def main() -> None:
    every = 45
    if "--every" in sys.argv:
        i = sys.argv.index("--every")
        every = int(sys.argv[i + 1])

    bridge = NotifyBridge()
    await bridge.start()
    ips = lan_ips()

    print("=" * 66)
    print("  關心模式")
    print("=" * 66)
    print("\n  手機打開這個網址，頁面保持開著：\n")
    print(f"      http://{ips[0]}:{bridge.port}\n")
    if len(ips) > 1:
        print(f"  連不上換：http://{ips[1]}:{bridge.port}")
        print("  都不行就把 Surfshark 關掉再試\n")
    print(f"  每 {every} 分鐘送一句。Ctrl+C 結束\n")
    print("-" * 66)

    giver = CareGiver()

    async def greet_on_connect() -> None:
        """
        偵測到新手機連上就立刻推一則。

        為什麼需要這個：不然使用者開了網頁只看到「已連線」三個字，
        要等到下一個整點間隔才知道到底通不通。
        通了就要當場看得見 —— 這是驗證，不是裝飾。
        """
        seen = 0
        while True:
            await asyncio.sleep(1)
            now_count = len(bridge.clients)
            if now_count > seen:
                await asyncio.sleep(0.8)          # 等前端把監聽器掛好
                t = datetime.now()
                p, line = giver.pick(t.hour)
                await bridge.push(f"連上了。{line}", level="ok",
                                  pattern=[150, 80, 150])
                print(f"  [{t:%H:%M}] 手機連上 → ({p}) {line}")
            seen = now_count

    asyncio.create_task(greet_on_connect())

    # --now：不等第一輪間隔，立刻送一則，方便驗證整條鏈通不通
    if "--now" in sys.argv:
        await asyncio.sleep(3)
        now = datetime.now()
        p, line = giver.pick(now.hour)
        n = await bridge.push(line)
        print(f"  [{now:%H:%M}] ({p}) {line}")
        print(f"          → 送達 {n} 台" + ("　⚠ 手機還沒連上" if n == 0 else ""))

    while True:
        await asyncio.sleep(every * 60)
        now = datetime.now()                    # 每次都重新查，不要快取
        p, line = giver.pick(now.hour)
        n = await bridge.push(line)
        print(f"  [{now:%H:%M}] ({p}) {line}")
        print(f"          → 送達 {n} 台" + ("　⚠ 手機沒連著" if n == 0 else ""))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已停止")
