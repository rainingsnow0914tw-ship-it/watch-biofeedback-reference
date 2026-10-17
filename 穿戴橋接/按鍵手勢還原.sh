#!/bin/sh
# 把 2026-08-20 動過的按鍵／手勢設定全部設回原樣。
#
# ── 2026-08-20 最終配置（使用者 實測通過的）──────────────────
#   紅色鍵連按兩下  →  記一筆（MarkActivity）      ← 給「精確時刻」
#   空中捏兩下      →  三星錄音                    ← 給「內容」
#
#   這個分工是她自己想出來的：
#   > 「現在鎖定的是你原本的阿寶生理資料，那我們把它改成錄音好了，
#   >   這樣子的話我就可以快速錄音告訴你我的情況」
#
#   ⚠ 而「捏兩下」在 Watch5 Pro（2022）上**是可以用的** —— 我原本查感測器
#     清單只看到 wrist_tilt_gesture，因此判斷「這台大概沒有」。**判斷錯了。**
#     她實際捏了一下就成功。
#     教訓：**感測器清單裡沒有，不等於功能不存在** —— 手勢可以由軟體從
#     加速度計推算，不需要專門的感測器條目。查不到就說查不到，不要說「沒有」。
#
# 動過的：
#   setting_home_key_double_press        lastapp                → 記一筆
#   setting_gesture_launch_app           fixed/open_exercise_list → 記一筆
#   setting_gesture_launch_on            0                      → 1
#   setting_gesture_launch_selected_app  （空）                  → 記一筆
#
# 為什麼要有這支：**改設定跟留退路是同一件事的兩半。**
# 她按下去發現不對，應該是一句話的事，不是一個功課。
ADB="${ADB:-/c/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe}"
W=$(sh "$(dirname "$0")/adb_target.sh" watch) || exit 1
"$ADB" -s "$W" shell "settings put global setting_home_key_double_press lastapp" < /dev/null
"$ADB" -s "$W" shell "settings put global setting_gesture_launch_app fixed/open_exercise_list" < /dev/null
"$ADB" -s "$W" shell "settings put global setting_gesture_launch_on 0" < /dev/null
"$ADB" -s "$W" shell "settings put global setting_gesture_launch_selected_app ''" < /dev/null
echo "全部設回原樣了。"
