#!/bin/sh
#
#
# ⚠ 停掉的代價要講清楚：**連安全性更新也收不到了。**
#   哪天要收，跑這支就回來。
#
# 用法：  sh 更新鎖還原.sh
ADB="${ADB:-/c/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe}"
W=$(sh "$(dirname "$0")/adb_target.sh" watch) || exit 1
for p in com.wssyncmldm com.sec.android.soagent; do
  R=$("$ADB" -s "$W" shell "pm enable $p" < /dev/null 2>&1 | tr -d '\r')
  case "$R" in *enabled*) echo "  OK 打開 $p";; *) echo "  X  $p → $R";; esac
done
"$ADB" -s "$W" shell "settings put global setting_show_badge_for_fota 1" < /dev/null
echo "更新提示徽章也開回來了。"
echo
echo "⚠ 另外兩道鎖**沒有動**（08-18 設的，本來就該留著）："
echo "   galaxy_system_update_block   = 1   （擋自動安裝）"
echo "   ota_disable_automatic_update = 1"
echo "   要收更新的話這兩個也要設回 0。"
