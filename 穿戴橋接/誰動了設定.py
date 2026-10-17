#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
誰動了設定 —— 記錄手錶設定的每一次變化，讓「它自己變了」變成看得見的事。

## 為什麼要有這支（使用者 2026-08-27）

> 「剛才手錶又變成了會轉來轉去，**是你有碰到什麼嗎？**
>   因為本來不會的，是你連完無線偵錯之後就變這樣。
>   我懷疑是不是跟那個手機一樣，因為你們每次用完手機之後，
>   手機也會開螢幕旋轉 —— 是不是這樣你們比較好看啊？」

**她的懷疑很合理，而且值得被認真查。**

查證結果（翻整份逐字稿的工具呼叫紀錄）：
```
settings put system accelerometer_rotation 0    ← 我執行過，1 次（關掉）
settings put system accelerometer_rotation 1    ← **一次都沒執行過**
                                                   （grep 撈到的 3 次是我訊息裡
                                                     寫的「要還原的話…」說明文字）
```
→ **不是我。但它確實從 0 變回 1 了，而我不知道是什麼做的。**

## 所以這支存在的理由

**「我不知道」不是答案的終點，是下一步的起點。**
與其下次再猜一輪，不如現在就開始記 —— 每次跑都存一份快照，
下次它再變，**變化前後發生了什麼就查得到**。

⚠ 這支跟 `更新前存證.py` 的分工：
```
更新前存證.py   手動、一次性、為了系統更新前後比對，會存 APK 跟原始資料
誰動了設定.py   常態、輕量、每次「對一下」都跑，只存設定值 + 時間
```

用法：
    python 誰動了設定.py          # 記一筆，並跟上一筆比對
    python 誰動了設定.py --歷史   # 看所有變化過的項目
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import 裝置  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "生理資料" / "設定變化.jsonl"

# 只盯這些 —— 全部設定有一千多項，噪音太大會沒人看。
# **選的標準：改了會影響她的使用體驗，或影響我們收資料的能力。**
WATCH = {
    "system": [
        "accelerometer_rotation",     # 08-27 她抱怨「螢幕轉來轉去」的元兇
        "user_rotation",
        "screen_brightness",
        "screen_off_timeout",
    ],
    "global": [
        "adb_enabled",
        "development_settings_enabled",
        "stay_on_while_plugged_in",   # 開發者選項，開著會比較耗電
        "sos_fall_detection_on",      # 跌倒偵測 —— 這個被關掉會很嚴重
        "galaxy_system_update_block",
        "ota_disable_automatic_update",
        "setting_home_key_double_press",
        "setting_gesture_launch_app",
        "setting_gesture_launch_on",
        # ⚠ 2026-08-28 加。這一項是她「半夜被震」的真兇：
        #   `com.google.android.apps.wearable.settings` 震了 135 次、每次約 2.1 秒，
        #   **其中 82 次（61%）在凌晨兩點到六點之間**。
        #   那是「手錶跟手機斷線」的提醒 —— 而每一次長震的當下，
        #   我們的資料都還在流（12/12 驗證過），因為資料是手錶自己在收。
        #   **所以那個提醒對她一點用都沒有，只是在半夜吵她。**
        "disconnection_alert_on",
        "restrict_disconnection_alert",
        "setting_disconnection_alert_at_home_disallowed",
    ],
    "secure": [
        "enabled_notification_listeners",   # 掉了 FallWatcher 就聾了
    ],
}


def 讀一份(w: str) -> dict:
    out = {}
    for ns, keys in WATCH.items():
        for k in keys:
            v = 裝置.sh(w, f"settings get {ns} {k}", 容許失敗=True).strip()
            out[f"{ns}.{k}"] = v
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if "--歷史" in sys.argv:
        if not OUT.exists():
            print("還沒有任何快照")
            return
        rows = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"═══ {len(rows)} 份快照，{rows[0]['t'][:16]} ~ {rows[-1]['t'][:16]} ═══\n")
        changes = 0
        for a, b in zip(rows, rows[1:]):
            diff = {k: (a["v"].get(k), v) for k, v in b["v"].items() if a["v"].get(k) != v}
            if not diff:
                continue
            changes += 1
            print(f"  {b['t'][:19]}")
            for k, (o, n) in diff.items():
                print(f"    {k}\n       {o} → {n}")
        if not changes:
            print("  所有快照之間**沒有任何變化** —— 設定是穩的")
        return

    try:
        w = 裝置.找("watch")
    except Exception as e:
        # ⚠ 連不上就是連不上。**不要寫一份空的快照** ——
        #   那會讓「我沒看到」跟「全部變成空字串」混在一起。
        print(f"  手錶連不上，這次不記：{e}")
        return

    now = 讀一份(w)
    prev = None
    if OUT.exists():
        rows = [l for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
        if rows:
            prev = json.loads(rows[-1])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"), "v": now},
                            ensure_ascii=False) + "\n")

    if prev is None:
        print(f"  第一份快照，記了 {len(now)} 項設定（以後每次都會跟上一份比）")
        return
    diff = {k: (prev["v"].get(k), v) for k, v in now.items() if prev["v"].get(k) != v}
    if not diff:
        print(f"  設定沒變（跟 {prev['t'][:16]} 那份比，{len(now)} 項全同）")
        return
    print(f"  ⚠ 跟 {prev['t'][:16]} 那份比，**有 {len(diff)} 項變了**：")
    for k, (o, n) in diff.items():
        print(f"    {k}")
        print(f"       {o} → {n}")
    print("  （不是妳改的話，跟我說 —— 我會去查那段時間發生什麼）")


if __name__ == "__main__":
    main()
