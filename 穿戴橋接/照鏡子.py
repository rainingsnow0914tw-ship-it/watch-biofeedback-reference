
#

# 兩隻哨兵搶麥克風各燒 155 分鐘 CPU）。voxpress 與念稿本來就有這條，所以那天它們沒事。
#
# 做法：Windows 具名互斥鎖（CreateMutexW）。程式死掉、被殺、當機，系統都會自動放鎖，
# 不用 pid 檔、不會留殭屍鎖。venv 的 pythonw 啟動器會多開一層同名父程序，但它不跑 Python 碼，不受影響。
#
# 用法（放在長跑迴圈開始之前；一次性的 --once 模式不要鎖）：
#     import 照鏡子; 照鏡子.獨占("hc_puller")
# 正本在 家族看門狗/，穿戴橋接/ 與 aibao-wake/ 各放一份同內容副本（搬資料夾時三份一起改）。
import ctypes, sys, time
from pathlib import Path

_HANDLES = []            # 握著不放，程式活著鎖就在
ERROR_ALREADY_EXISTS = 183


def 獨占(名字: str, 退出=True) -> bool:
    """拿到鎖回 True。拿不到：退出=True 就寫一行日誌後 sys.exit(0)；否則回 False。"""
    k32 = ctypes.windll.kernel32
    h = k32.CreateMutexW(None, False, f"Local\aibao-family-{名字}")
    已有人 = (k32.GetLastError() == ERROR_ALREADY_EXISTS)
    if h and not 已有人:
        _HANDLES.append(h)
        return True
    try:
        log = Path(__file__).resolve().parent / "照鏡子.log"
        with log.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {名字}：已經有一個在跑，我退下（argv={sys.argv[1:]}）\n")
    except Exception:
        pass
    if 退出:
        sys.exit(0)
    return False
