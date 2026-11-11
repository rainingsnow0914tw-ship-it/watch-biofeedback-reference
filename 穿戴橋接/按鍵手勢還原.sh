#!/bin/sh
#
#
#
#     教訓：**感測器清單裡沒有，不等於功能不存在** —— 手勢可以由軟體從
#
# 動過的：
#   setting_home_key_double_press        lastapp                → 記一筆
#   setting_gesture_launch_app           fixed/open_exercise_list → 記一筆
#   setting_gesture_launch_on            0                      → 1
#   setting_gesture_launch_selected_app  （空）                  → 記一筆
#
# 為什麼要有這支：**改設定跟留退路是同一件事的兩半。**
ADB="${ADB:-/c/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe}"
W=$(sh "$(dirname "$0")/adb_target.sh" watch) || exit 1
"$ADB" -s "$W" shell "settings put global setting_home_key_double_press lastapp" < /dev/null
"$ADB" -s "$W" shell "settings put global setting_gesture_launch_app fixed/open_exercise_list" < /dev/null
"$ADB" -s "$W" shell "settings put global setting_gesture_launch_on 0" < /dev/null
"$ADB" -s "$W" shell "settings put global setting_gesture_launch_selected_app ''" < /dev/null
echo "全部設回原樣了。"
