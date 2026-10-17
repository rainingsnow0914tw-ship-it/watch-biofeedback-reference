"""
BLE 深度診斷 —— 連上裝置，把它開放的東西全部列出來。

跟 heartrate.py 的差別：
    heartrate.py 只問一件事：「有沒有心率服務？」
    這支問的是：「你到底開放了什麼？」

    這個差別很重要。2026-08-08 我對 Fit 3 只檢查心率服務，
    確認存在、訂閱成功、然後零資料，接著我就推論說
    「手環大概沒在量測」——那是猜的，我沒有查證。

    真正該做的是把所有 GATT 服務、特徵值、屬性、
    以及每個可讀值的實際內容全部倒出來看。答案通常就在裡面：
    可能是手錶只開放了裝置資訊沒開心率、
    可能是特徵值需要配對加密、也可能是真的沒在量測。
    列出來就不用猜。

順便找標準服務裡的驚喜：
    藍牙聯盟定義了一票標準服務，廠商只要有實作，
    任何程式都能直接讀，不需要官方 SDK：
        0x180D 心率　　0x180F 電池　　0x180A 裝置資訊
        0x1809 體溫計　0x1822 脈搏血氧　0x181D 體重
    如果 Watch 5 Pro 開了 0x1809，體溫就不用寫 app 了。

用法：
    python ble_probe.py                     # 掃描附近所有裝置
    python ble_probe.py <MAC>               # 深度診斷某台
    python ble_probe.py <MAC> --listen 30   # 診斷後訂閱所有通知 30 秒
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime

from bleak import BleakClient, BleakScanner

# 藍牙聯盟標準服務（16-bit 短碼會被展開成完整 UUID）
KNOWN_SERVICES = {
    "1800": "通用存取",
    "1801": "通用屬性",
    "1805": "目前時間",
    "1809": "體溫計 ← 能直接讀體溫！",
    "180a": "裝置資訊",
    "180d": "心率 ← 能直接讀心率！",
    "180f": "電池電量",
    "1810": "血壓",
    "1812": "人機介面 HID",
    "1814": "跑步速度",
    "1816": "自行車速度",
    "181b": "身體組成",
    "181c": "使用者資料",
    "181d": "體重量測",
    "1822": "脈搏血氧 ← 能直接讀血氧！",
    "1826": "健身機器",
    "fef5": "裝置韌體更新",
}

KNOWN_CHARS = {
    "2a19": "電池電量",
    "2a37": "心率量測",
    "2a38": "感測器位置",
    "2a1c": "體溫量測",
    "2a1e": "即時體溫",
    "2a24": "型號",
    "2a25": "序號",
    "2a26": "韌體版本",
    "2a27": "硬體版本",
    "2a29": "製造商",
    "2a2b": "目前時間",
    "2a5f": "脈搏血氧連續量測",
    "2a98": "體重",
}


def short(uuid: str) -> str:
    """從完整 UUID 取出 16-bit 短碼（標準服務才有意義）。"""
    u = uuid.lower()
    if u.endswith("-0000-1000-8000-00805f9b34fb"):
        return u[4:8]
    return ""


def label(uuid: str, table: dict) -> str:
    s = short(uuid)
    return table.get(s, "")


async def scan(seconds: int = 12) -> None:
    print(f"掃描 {seconds} 秒…\n")
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)

    rows = []
    for addr, (dev, adv) in found.items():
        name = adv.local_name or dev.name or "(無名稱)"
        uuids = list(adv.service_uuids or [])
        marks = [KNOWN_SERVICES[short(u)] for u in uuids
                 if short(u) in KNOWN_SERVICES]
        rows.append((adv.rssi, name, addr, marks))

    for rssi, name, addr, marks in sorted(rows, reverse=True):
        print(f"  {rssi:>4} dBm  {name:<26} {addr}")
        for m in marks:
            print(f"              └ {m}")

    print(f"\n  共 {len(rows)} 台。深度診斷：python ble_probe.py <MAC>\n")


async def probe(target, listen: int = 0) -> None:
    """
    target 可以是 MAC 字串，也可以是掃描拿到的 BLEDevice 物件。

    對用隨機化位址的裝置（Wear OS 手錶就是）一定要傳物件 ——
    傳字串的話位址早就輪換過，必然 DeviceNotFound。
    """
    shown = getattr(target, "address", target)
    print("=" * 72)
    print(f"  深度診斷　{shown}")
    print("=" * 72)
    print("\n連線中…（若手錶要求配對，請在錶上按同意）")

    try:
        async with BleakClient(target, timeout=25.0) as client:
            print("✓ 已連線\n")

            notifiable = []
            total_chars = 0

            for svc in client.services:
                tag = label(svc.uuid, KNOWN_SERVICES)
                head = f"服務 {short(svc.uuid) or svc.uuid[:8]}"
                print(f"─ {head}　{tag or svc.description}")

                for ch in svc.characteristics:
                    total_chars += 1
                    props = ",".join(ch.properties)
                    cname = label(ch.uuid, KNOWN_CHARS) or ch.description
                    print(f"    {short(ch.uuid) or ch.uuid[:8]}  "
                          f"{cname:<22} [{props}]")

                    # 可讀的就真的讀一次——很多有用的東西（型號、韌體、
                    # 電量）就在這裡，而且讀了才知道權限夠不夠
                    if "read" in ch.properties:
                        try:
                            val = await client.read_gatt_char(ch.uuid)
                            try:
                                txt = val.decode("utf-8").strip()
                                shown = txt if txt.isprintable() and txt else val.hex()
                            except UnicodeDecodeError:
                                shown = val.hex()
                            if shown:
                                print(f"        → {shown[:80]}")
                        except Exception as e:
                            print(f"        → 讀取被拒（{type(e).__name__}）")

                    if "notify" in ch.properties or "indicate" in ch.properties:
                        notifiable.append((ch.uuid, cname))
                print()

            print("─" * 72)
            print(f"  共 {total_chars} 個特徵值，其中 {len(notifiable)} 個可訂閱通知\n")

            if listen and notifiable:
                print(f"訂閱全部並監聽 {listen} 秒…")
                print("（如果手錶正在量測，資料會出現在下面）\n")

                got = {}

                def make_cb(name):
                    def cb(_, data: bytearray):
                        got[name] = got.get(name, 0) + 1
                        print(f"  [{datetime.now():%H:%M:%S}] {name}: {data.hex()}")
                    return cb

                subscribed = []
                for uuid, cname in notifiable:
                    try:
                        await client.start_notify(uuid, make_cb(cname))
                        subscribed.append(uuid)
                    except Exception:
                        pass

                print(f"  成功訂閱 {len(subscribed)} 個\n")
                await asyncio.sleep(listen)

                for uuid in subscribed:
                    try:
                        await client.stop_notify(uuid)
                    except Exception:
                        pass

                print()
                if got:
                    print("  收到的資料：")
                    for k, v in got.items():
                        print(f"    {k}: {v} 筆")
                else:
                    print("  ✗ 一筆都沒收到。")
                    print("    這代表裝置有開放通道，但當下沒有在推送資料 ——")
                    print("    通常要在裝置上開始量測（運動模式／連續監測）才會推。")

    except Exception as e:
        print(f"\n✗ 連線失敗：{type(e).__name__}")
        print(f"  {e}\n")
        print("  常見原因：")
        print("    1. 裝置需要先在 Windows 藍牙設定裡配對")
        print("    2. 手機 APP 正連著它（BLE 一次只能被一個主機佔用）")
        print("    3. 裝置不在範圍內或沒開機")


async def find_watch(seconds: int = 15):
    """
    自動找出附近的三星穿戴裝置，回傳 BLEDevice 物件（不是字串）。

    為什麼一定要回傳物件而不是 MAC 字串（2026-08-11 踩到）：
        Wear OS 手錶用「隨機化位址」廣播（52: 開頭那種），
        位址每隔幾十秒就換一次，這是藍牙的隱私設計。

        所以「掃描 → 記下 MAC → 之後拿 MAC 去連」這條路
        必然失敗，錯誤訊息還是 DeviceNotFound，
        看起來像手錶消失了，其實是它換了門牌。

        正確做法是掃描當下就抓住 BLEDevice 物件，
        bleak 內部握著那次掃描的連線資訊，直接拿它去連。

    順帶一提，這也是為什麼自動搜尋比人工抄 MAC 好——
    對隨機位址的裝置，人工抄下來的 MAC 本來就活不久。
    """
    print(f"搜尋穿戴裝置（{seconds} 秒）…\n")
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)

    candidates = []
    for addr, (dev, adv) in found.items():
        name = adv.local_name or dev.name or ""
        low = name.lower()
        # 三星手錶廣播名長這樣：「Galaxy Watch5 Pro (XXXX)」
        if any(k in low for k in ("galaxy watch", "watch5", "watch 5",
                                  "galaxy fit", "fit3", "fit 3")):
            uuids = {short(u) for u in (adv.service_uuids or [])}
            candidates.append({"rssi": adv.rssi, "name": name, "addr": addr,
                               "uuids": uuids, "dev": dev})

    if not candidates:
        print("  ✗ 沒找到穿戴裝置。\n")
        print("    檢查：")
        print("      1. 手錶開機了嗎（長按側鍵）")
        print("      2. 手錶藍牙有沒有開")
        print("      3. 離電腦夠近嗎")
        print("      4. 手機正在配對／同步中的話，手錶可能暫時不對外廣播")
        return None

    # 不能用 tuple 排序——BLEDevice 物件不支援比較，會 TypeError
    candidates.sort(key=lambda c: -c["rssi"])

    print("  找到：\n")
    for c in candidates:
        hits = [KNOWN_SERVICES[u] for u in c["uuids"] if u in KNOWN_SERVICES]
        print(f"    {c['rssi']:>4} dBm  {c['name']:<28} {c['addr']}")
        for h in hits:
            print(f"                └ {h}")
    print()

    return candidates[0]["dev"]


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    listen = 0
    if "--listen" in sys.argv:
        i = sys.argv.index("--listen")
        listen = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) else 30

    if "--auto" in sys.argv:
        addr = await find_watch()
        if addr:
            print()
            await probe(addr, listen or 30)
    elif args:
        await probe(args[0], listen)
    else:
        await scan()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已停止")
