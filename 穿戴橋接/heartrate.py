"""
心率讀取 —— 把身體的訊號變成輸入。

為什麼做這個：
    使用者 看到「心率越高震越強」的時候說「有點妙」。
    妙在哪？因為那是一個閉環：

        心率高 → 震得強 → 更興奮 → 心率更高 → 震得更強

    一般的震動是「妳操作它，它輸出」。
    心率同步是「它讀妳，妳反應，它再讀」——
    中間沒有「妳決定」這一步。妳不是在控制它，
    妳是在跟自己的生理反應對打。而且沒辦法作弊，
    想讓它停下來就得真的冷靜下來。

技術上怎麼讀到心率：
    藍牙聯盟定義了標準的 Heart Rate Service（UUID 0x180D），
    任何遵守標準的裝置都用同一套格式廣播心率。
    所以不需要三星的 SDK、不需要官方 APP，
    只要它有開這個服務就讀得到。

    2026-08-08 掃描時，使用者 的 Galaxy Fit3 確實廣播了 0x180D。

    但「有這個服務」跟「讓陌生裝置讀」是兩件事 ——
    很多廠商的手環要求先配對綁定，或只讓自家 APP 讀。
    所以這支會誠實回報到底讀不讀得到，不會假裝成功。

用法：
    python heartrate.py                    # 掃描有心率服務的裝置
    python heartrate.py --read <MAC>       # 連線並持續讀心率
    python heartrate.py --demo             # 沒有裝置也能看效果（模擬心率）
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime

from bleak import BleakClient, BleakScanner

# 藍牙聯盟標準定義，所有廠商共用
HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


def parse_hr(data: bytearray) -> int:
    """
    解析心率量測封包。

    格式（藍牙 GATT 規範）：
        byte 0 = 旗標。最低位元 0 → 心率是 1 byte；1 → 是 2 bytes
        後面才是心率值

    這個「看旗標決定長度」的設計很容易寫錯 ——
    直接讀 data[1] 在高心率（>255，理論上不會發生但有些裝置會用 16-bit）
    或某些裝置上會拿到垃圾值。
    """
    if not data:
        return 0
    flags = data[0]
    if flags & 0x01:
        return int.from_bytes(data[1:3], "little")
    return data[1]


async def scan_hr_devices(seconds: int = 12) -> list:
    """找出附近有心率服務的裝置。"""
    print(f"掃描有心率服務的裝置（{seconds} 秒）…\n")
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)

    hits = []
    for addr, (dev, adv) in found.items():
        uuids = {u.lower() for u in (adv.service_uuids or [])}
        uuids |= {u.lower() for u in adv.service_data.keys()}
        if HR_SERVICE in uuids or "180d" in " ".join(uuids):
            name = adv.local_name or dev.name or "(無名稱)"
            hits.append((addr, name, adv.rssi))
            print(f"  ✓ {name:<24} {addr}   {adv.rssi} dBm")

    if not hits:
        print("  ✗ 沒有裝置在廣播心率服務。")
        print("    手環通常只在「運動模式」或「連續心率監測開啟」時才廣播。")
    return hits


async def read_hr(address: str, duration: int = 60) -> None:
    """
    連上裝置並持續讀心率。

    最常見的失敗是「配對要求」—— 很多手環只讓已綁定的主機讀，
    或只讓自家 APP 讀。這種情況會拿到 authentication 相關的錯誤，
    要誠實回報，不要假裝在讀。
    """
    print(f"連線到 {address}…")

    try:
        async with BleakClient(address, timeout=20.0) as client:
            print("已連線。檢查服務…\n")

            services = client.services
            has_hr = any(s.uuid.lower() == HR_SERVICE for s in services)

            if not has_hr:
                print("✗ 這台裝置連上後沒有提供心率服務。")
                print("  它廣播了 0x180D，但實際上沒開放讀取 ——")
                print("  代表它把心率鎖在自家 APP 的私有協議裡。")
                print("\n  可用的服務：")
                for s in services:
                    print(f"    {s.uuid}  {s.description}")
                return

            history: list[int] = []

            def on_data(_, data: bytearray) -> None:
                bpm = parse_hr(data)
                if bpm <= 0:
                    return
                history.append(bpm)
                avg = sum(history[-10:]) / len(history[-10:])

                # 把心率映射到 0~1 的強度：60 bpm → 0、140 bpm → 1
                # 這個範圍是靜息心率到中高強度運動的常見區間。
                intensity = max(0.0, min(1.0, (bpm - 60) / 80))
                bar = "█" * int(intensity * 40)

                print(f"  {datetime.now():%H:%M:%S}  {bpm:>3} bpm  "
                      f"(平均 {avg:>5.1f})  →  強度 {intensity:>5.1%} |{bar:<40}|")

            await client.start_notify(HR_MEASUREMENT, on_data)
            print(f"讀取中（{duration} 秒，Ctrl+C 結束）…\n")
            await asyncio.sleep(duration)
            await client.stop_notify(HR_MEASUREMENT)

            if history:
                print(f"\n讀到 {len(history)} 筆。"
                      f"範圍 {min(history)}–{max(history)} bpm，"
                      f"平均 {sum(history)/len(history):.1f}")
            else:
                print("\n✗ 連上了，但一筆資料都沒收到。")
                print("  手環可能需要開啟「連續心率監測」才會持續推送。")

    except Exception as e:
        print(f"\n✗ 連線失敗：{type(e).__name__}")
        print(f"  {e}\n")
        print("  常見原因：")
        print("    1. 手環要求先在系統設定裡配對綁定")
        print("    2. 手環只允許自家 APP 連線（三星/小米/華為都常這樣）")
        print("    3. 手機 APP 正連著它 —— BLE 一次只能被一個主機佔用")


async def _run_loop(start_bpm: float, steps: int, label: str) -> float:
    """跑一段閉環模擬，回傳結束時的心率。"""
    import math

    print(f"\n  【{label}】起始 {int(start_bpm)} bpm\n")
    bpm = start_bpm

    for t in range(steps):
        intensity = max(0.0, min(1.0, (bpm - 60) / 80))

        # 閉環方程式：
        #   +intensity * 2.2  → 震動推高心率（正回饋）
        #   -0.4              → 身體自然回落（負回饋，恆定性）
        #   +sin(...)         → 自然波動
        #
        # 兩股力量拉鋸。當 intensity * 2.2 < 0.4 時衰減勝出，
        # 系統自己滑回靜息；反之正回饋勝出，一路衝到飽和。
        # 臨界點就在 intensity ≈ 0.18，也就是約 74 bpm。
        bpm += intensity * 2.2 + math.sin(t / 3) * 0.8 - 0.4
        bpm = max(58.0, min(155.0, bpm))

        bar = "█" * int(intensity * 44)
        print(f"  {int(bpm):>3} bpm  →  {intensity:>5.1%}  |{bar:<44}|")
        await asyncio.sleep(0.09)

    return bpm


async def demo() -> None:
    """
    展示閉環的真實行為 —— 而它不是「一路爬到頂」那麼簡單。

    2026-08-08 第一次跑這個 demo 時，我以為會看到失控爬升，
    結果它在低檔來回震盪。查了參數才發現這不是寫錯，
    是正回饋系統的真實特性：

        它有臨界點。

    低於臨界點，身體的自然回落勝過震動的推力，系統自己滑回靜息。
    越過臨界點，推力勝過回落，就一路衝到飽和回不來。

    這在控制理論上叫「雙穩態」——同一個系統有兩個穩定狀態，
    中間隔著一道門檻。心臟、神經、荷爾蒙、雪崩、疫情擴散、
    甚至輿論引爆，全都是同一個數學。

    所以這個 demo 跑兩次，讓兩種命運並排看。
    """
    print("=" * 70)
    print("  心率同步閉環（模擬）")
    print("=" * 70)
    print("  規則：心率越高 → 震得越強 → 心率又更高")
    print("  但身體同時在自然回落。兩股力量拉鋸，臨界點在 74 bpm 附近。")

    end1 = await _run_loop(68.0, 26, "情境一：起始低於臨界點")
    print(f"\n  → 收在 {int(end1)} bpm。推力打不過身體的回落，系統自己穩住了。")

    end2 = await _run_loop(80.0, 30, "情境二：起始高於臨界點")
    print(f"\n  → 衝到 {int(end2)} bpm 飽和。同一套規則，完全不同的命運。")

    print("\n" + "=" * 70)
    print("  這就是為什麼它妙。")
    print("  不是「越震越強」那麼單純 —— 是系統有兩個穩定狀態，")
    print("  中間隔著一道門檻。越過之前，妳可以隨時抽身；")
    print("  越過之後，它自己就會走完全程，不需要任何人操作。")
    print("\n  同一個數學也長在雪崩、疫情擴散、神經放電、輿論引爆上。")
    print("=" * 70 + "\n")


async def main() -> None:
    args = sys.argv[1:]

    if "--demo" in args:
        await demo()
    elif "--read" in args:
        i = args.index("--read")
        addr = args[i + 1] if i + 1 < len(args) else None
        if not addr:
            print("用法：python heartrate.py --read <MAC位址>")
            return
        await read_hr(addr)
    else:
        print("=" * 70)
        print("  心率裝置掃描")
        print("=" * 70)
        hits = await scan_hr_devices()
        if hits:
            print(f"\n  讀取心率：python heartrate.py --read {hits[0][0]}")
        print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已停止")
