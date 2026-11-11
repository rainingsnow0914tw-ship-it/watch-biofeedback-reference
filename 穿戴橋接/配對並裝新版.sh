#!/bin/sh
#
# 用法：  sh 配對並裝新版.sh 123456
#
#
# 而且：
#     ping        通（71~899 ms）
#     TCP 32853   通
#     同一個網段（電腦 192.168.0.X / 手錶同網段）
#     adb connect **failed**
#
# 網路層全部通、只有 adb 連不上 → **不是網路問題，是這台電腦的 adb 金鑰
#
#   （不只是把開關關掉 —— 開關開回來也還是連不上，要重新配對）。
#   而配對碼只顯示在錶面上，遠端一定要有人在旁邊念。
#

ADB="${ADB:-/c/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe}"
HERE="$(dirname "$0")"
APK="$(cd "$HERE/../手錶app/wear/build/outputs/apk/debug" && pwd)/wear-debug.apk"
PKG=com.example.watchbridge
CODE="$1"

[ -n "$CODE" ] || { echo "用法：sh 配對並裝新版.sh <六位數配對碼>"; exit 1; }
[ -f "$APK" ] || { echo "找不到 APK：$APK"; exit 1; }

echo "① 找配對服務（配對畫面要開著，最多等 2 分鐘）"
PAIR=""
i=0
while [ $i -lt 24 ]; do
  PAIR=$("$ADB" mdns services 2>/dev/null | tr -d '\r' | grep "_adb-tls-pairing" | awk '{print $NF}' | head -1)
  [ -n "$PAIR" ] && break
  i=$((i+1)); sleep 5
done
[ -n "$PAIR" ] || { echo "X 沒看到配對服務 —— 錶上的『使用配對碼配對裝置』畫面要開著"; exit 1; }
echo "   找到 $PAIR"

echo "② 配對"
"$ADB" pair "$PAIR" "$CODE" 2>&1 | tr -d '\r' || { echo "X 配對失敗（碼可能過期，畫面重開一次會給新的）"; exit 1; }

echo "③ 連線"
CONN=$("$ADB" mdns services 2>/dev/null | tr -d '\r' | grep "_adb-tls-connect" | awk '{print $NF}' | head -1)
[ -n "$CONN" ] && "$ADB" connect "$CONN" 2>&1 | tr -d '\r'
sleep 3

W=$(sh "$HERE/adb_target.sh" watch) || { echo "X 配對過了但還是找不到手錶"; exit 1; }
echo "   手錶：$W"
echo "   舊版：$("$ADB" -s "$W" shell dumpsys package $PKG < /dev/null 2>/dev/null | grep versionCode | head -1 | tr -d '\r')"

echo "④ 安裝"
"$ADB" -s "$W" install -r "$APK" 2>&1 | tr -d '\r' | tail -3 || { echo "X 安裝失敗，舊版還在"; exit 1; }
"$ADB" -s "$W" shell cmd notification allow_listener $PKG/$PKG.FallWatcher < /dev/null >/dev/null 2>&1
echo "   新版：$("$ADB" -s "$W" shell dumpsys package $PKG < /dev/null 2>/dev/null | grep versionCode | head -1 | tr -d '\r')"

echo "⑤ 驗證：服務要真的寫出新資料（最多 3 分鐘）"
for i in 1 2 3 4 5 6; do
  sleep 30
  LAST=$("$ADB" -s "$W" shell "run-as $PKG tail -1 files/\$(date +%Y-%m-%d).jsonl" < /dev/null 2>/dev/null | tr -d '\r')
  case "$LAST" in
    *on_body*) echo "OK 在寫了："; echo "   $(echo "$LAST" | cut -c1-170)"; exit 0 ;;
  esac
  echo "   還沒（第 $i 次）"
done
echo "⚠ 裝好了但 3 分鐘內沒看到新資料 —— 不當成功，要再查"
exit 2
