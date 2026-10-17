#!/bin/sh
# 把 2026-08-20 關掉的那五個 app 全部打開。**一鍵還原，不留後遺症。**
#
# 關掉用的是 `pm disable-user`，不是移除 —— APK 還在原地，
# 只是不讓它啟動。所以還原就是 `pm enable`，資料設定全都在。
#
# 為什麼要有這支：她同意讓我關東西，但**同意不等於沒有退路**。
# 手錶上少了什麼、哪天想找回來，不應該變成她的功課。
#
# 用法：  sh 手錶瘦身還原.sh

ADB="${ADB:-/c/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe}"
W=$(sh "$(dirname "$0")/adb_target.sh" watch) || exit 1

for p in \
  com.samsung.android.samsungpay.gear \
  com.samsung.android.gallery.watch \
  com.google.android.apps.messaging \
  com.samsung.android.messaging \
  com.android.vending
do
  R=$("$ADB" -s "$W" shell "pm enable $p" < /dev/null 2>&1 | tr -d '\r')
  case "$R" in *enabled*) echo "  OK  打開  $p";; *) echo "  X   $p → $R";; esac
done
echo "全部還原完成。"
