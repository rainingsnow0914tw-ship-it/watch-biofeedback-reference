"""
推訊息到手錶 —— 透過 adb 發手機通知，Wear OS 會自動同步到錶上。

為什麼這條路能成立：
    Wear OS 的設計就是「手機的通知會鏡射到手錶」。
    所以我們不需要在手錶上寫任何 app，只要能讓手機產生一則通知，
    手錶就會震動並顯示。

    鏈路：  電腦 ──adb──► 手機（產生通知）──藍牙──► 手錶（震動顯示）

    零開發成本，代價是手機要能被 adb 連到。

跟 notify_bridge.py 的分工：
    notify_bridge  手機瀏覽器收，畫面要開著，但不需要 USB／adb
    watch_notify   直接震手錶，不用開任何畫面，但需要 adb 連線

    兩個可以並用 —— 重要的事震手錶，長篇的事推瀏覽器。

USB 線拔掉也能用：
    先跑一次 `python watch_notify.py --wifi`，它會把 adb 切到無線模式，
    之後手機和電腦在同一個 Wi-Fi 底下就能推，不用插線。

用法：
    python watch_notify.py "訊息內容"
    python watch_notify.py "標題" "內容"
    python watch_notify.py --wifi          # 切換成無線 adb
    python watch_notify.py --check         # 檢查連線狀態
"""

from __future__ import annotations

import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0  # 2026-09-23 不閃黑框（使用者 看到了）
import sys
from pathlib import Path

ADB = Path.home() / "AppData/Local/Android/Sdk/platform-tools/adb.exe"


def adb(*args: str, timeout: int = 30) -> tuple[int, str]:
    """執行 adb 指令，回傳 (return code, 輸出)。"""
    if not ADB.exists():
        return 1, f"找不到 adb：{ADB}\n（Android Studio 裝了就會有）"
    try:
        r = subprocess.run([str(ADB), *args], capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8",
                           errors="ignore", creationflags=_NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 1, "adb 指令逾時"


def devices() -> list[str]:
    """列出連線中的裝置序號。"""
    _, out = adb("devices")
    return [ln.split()[0] for ln in out.splitlines()[1:]
            if ln.strip() and "\tdevice" in ln]


def notify_phone(title: str, text: str, tag: str = "aibao") -> tuple[bool, str]:
    """
    發一則通知到「手機」。

    ⚠ 這條路到不了手錶。2026-08-11 實測確認：
        Galaxy Wearable 不把 com.android.shell 當作可管理的 app
        （在它的應用程式通知清單裡搜尋 shell 是「找不到結果」），
        所以 Wear OS 會直接過濾掉這個來源的通知。

        整條鏈其他部分都是好的 —— 通知確實進了手機的通知系統，
        手錶同步機制也正常（用時鐘 app 做對照實驗，手機手錶同時響）。
        唯一的問題是「發送者身份不被接受」。

    所以這支留著給「只想通知手機」的場合用，要震手錶請用 notify_watch()。
    """
    devs = devices()
    if not devs:
        return False, ("沒有連線中的裝置。\n"
                       "  · USB 線插著嗎？\n"
                       "  · 手機上的「允許 USB 偵錯」有沒有按同意？\n"
                       "  · 或先跑 python watch_notify.py --wifi 切成無線")

    code, out = adb("shell", "cmd", "notification", "post",
                    "-S", "bigtext", "-t", title, tag, text)
    if code != 0:
        return False, out.strip()
    return True, f"已送到手機（裝置 {devs[0]}）"


def notify_watch(text: str, delay: int = 2) -> tuple[bool, str]:
    """
    發一則訊息到「手錶」—— 借時鐘 app 的計時器當管道。

    為什麼要繞這一圈（這是今晚查出來的關鍵）：
        我們自己發的通知（com.android.shell）會被 Wear OS 過濾掉，
        但時鐘 app 是系統認可的正常 app，它的通知確定會鏡射到手錶
        （實測：計時器響的時候手機和手錶同時響）。

        而 SET_TIMER 這個 intent 支援帶自訂訊息 ——
        於是我們把想說的話塞進計時器的標籤裡，
        時間一到，那行字就會出現在手錶上並震動。

    代價（誠實列出）：
        · 會有鬧鐘聲，不是安靜的通知
        · 訊息前面會有計時器圖示
        · 有 delay 秒的延遲（不能瞬間送達）
        · 手機的時鐘 app 裡會留下一筆計時器紀錄

    所以它適合「需要確實被注意到」的訊息，不適合高頻的碎念。
    高頻的用 notify_bridge.py 推到瀏覽器比較不擾人。
    """
    devs = devices()
    if not devs:
        return False, "沒有連線中的裝置（先確認 USB 或無線 adb）"

    # 訊息一定要包單引號再送。
    #
    # 踩坑紀錄（2026-08-11）：
    #     adb shell 會把收到的參數在「裝置端」重新組成一行 shell 命令執行，
    #     所以裝置上的 shell 會再拆一次空格。
    #     訊息「阿寶說：現在幾點了 6」裡的那個空格，
    #     讓 "6" 變成獨立參數，被 am start 當成套件名 → pkg=6 → 整個指令失敗。
    #
    #     之前一直沒炸，純粹是因為中文不帶空格，運氣好而已。
    #     任何含空格、引號的訊息都會炸。
    #
    # 單引號裡的內容 shell 不做任何展開，最安全；
    # 訊息本身若含單引號，用 '\'' 這個標準寫法escape 掉。
    safe = "'" + text.replace("'", "'\\''") + "'"

    code, out = adb(
        "shell", "am", "start",
        "-a", "android.intent.action.SET_TIMER",
        "--ei", "android.intent.extra.alarm.LENGTH", str(max(1, delay)),
        "--es", "android.intent.extra.alarm.MESSAGE", safe,
        "--ez", "android.intent.extra.alarm.SKIP_UI", "true",
    )
    if code != 0 or "Error" in out:
        return False, out.strip()
    return True, f"已送到手錶（{delay} 秒後響，裝置 {devs[0]}）"


# 舊名稱保留，預設走「到得了手錶」的那條路
def notify(title: str, text: str, tag: str = "aibao") -> tuple[bool, str]:
    return notify_watch(text)


def go_wireless() -> None:
    """
    把 adb 切到無線模式，之後不用插 USB。

    做法是先透過 USB 叫手機開 5555 埠，再用 IP 連回去。
    手機重開機後要重來一次（Android 的安全設計）。
    """
    devs = devices()
    if not devs:
        print("✗ 先用 USB 連上手機再跑這個")
        return

    print("取得手機的 Wi-Fi IP…")
    _, out = adb("shell", "ip", "-f", "inet", "addr", "show", "wlan0")
    ip = ""
    for part in out.split():
        if part.startswith(("192.168.", "10.", "172.")) and "/" in part:
            ip = part.split("/")[0]
            break

    if not ip:
        print("✗ 抓不到手機的 Wi-Fi IP —— 手機有連 Wi-Fi 嗎？")
        return

    print(f"  手機 IP：{ip}")
    print("切換到無線模式…")
    adb("tcpip", "5555")

    import time
    time.sleep(2)

    code, out = adb("connect", f"{ip}:5555")
    if "connected" in out.lower():
        print(f"✓ 已連上 {ip}:5555 —— 現在可以拔掉 USB 線了")
        print("  （手機重開機後要重跑一次這個指令）")
    else:
        print(f"✗ 連線失敗：{out.strip()}")


def check() -> None:
    print("=" * 60)
    print("  連線狀態檢查")
    print("=" * 60)

    print(f"\n  adb 路徑：{'✓' if ADB.exists() else '✗'} {ADB}")

    devs = devices()
    if devs:
        print(f"  連線裝置：✓ {', '.join(devs)}")
    else:
        print("  連線裝置：✗ 沒有")
        _, out = adb("devices")
        print(f"\n{out}")
        return

    # 手錶連著沒——看藍牙裡有沒有 Watch
    _, out = adb("shell", "dumpsys", "bluetooth_manager")
    watch_bonded = "watch" in out.lower()
    print(f"  手錶已配對：{'✓' if watch_bonded else '✗ 找不到 Watch 字樣'}")

    print("\n  推一則測試：python watch_notify.py \"測試\"\n")


def main() -> int:
    if "--check" in sys.argv:
        check()
        return 0

    if "--wifi" in sys.argv:
        go_wireless()
        return 0

    # 逐一走過參數，把旗標「和它後面的值」一起挖掉，剩下的才是訊息。
    #
    # 踩坑紀錄（2026-08-11，今天第二次犯同一個錯）：
    #     原本用「不是 -- 開頭的就當訊息」來過濾，
    #     但 --delay 後面的數字也不是 -- 開頭，於是被當成訊息內容，
    #     跑出「阿寶說：… 6」這種字串，再加上空格問題直接讓指令失敗。
    #     morse.py 的 --wpm 是一模一樣的病，我修過一次還是又寫錯。
    #     教訓：旗標的「值」要用索引跳過，不能靠前綴判斷。
    argv = sys.argv[1:]
    delay = 2
    rest: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--delay" and i + 1 < len(argv):
            delay = int(argv[i + 1])
            i += 2
            continue
        if argv[i].startswith("--"):
            i += 1
            continue
        rest.append(argv[i])
        i += 1

    if not rest:
        print(__doc__)
        return 1

    text = " ".join(rest)

    # --phone 才走手機那條（到不了手錶），預設一律走手錶
    if "--phone" in sys.argv:
        ok, msg = notify_phone("阿寶", text)
    else:
        ok, msg = notify_watch(text, delay)

    print(("✓ " if ok else "✗ ") + msg)
    if ok:
        print(f"  內容：{text}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
