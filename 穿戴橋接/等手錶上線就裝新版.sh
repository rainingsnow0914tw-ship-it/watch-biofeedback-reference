#!/bin/sh
# 手錶一開無線偵錯就自動裝新版。**不需要她做第二件事。**
#
# 為什麼要有這支：08-18 的 One UI 8 更新重開了手錶，而
# **無線偵錯每次重開機都會關掉**（08-17 實測，所有權限都活著只有它死）。
# 所以新版 APK 編好了也裝不上去。
#
# 她唯一要做的：手錶 → 設定 → 開發人員選項 → 無線偵錯 ✅
# 剩下的這支全包：發現 → 連線 → 安裝 → 授權 → 驗證。
#
# 用法：  sh 等手錶上線就裝新版.sh [等幾分鐘，預設 90]

ADB="${ADB:-/c/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe}"
APK="$(cd "$(dirname "$0")/../手錶app/wear/build/outputs/apk/debug" && pwd)/wear-debug.apk"
WAIT_MIN="${1:-90}"
PKG=com.example.watchbridge

[ -f "$APK" ] || { echo "找不到 APK：$APK"; exit 1; }
echo "等手錶上線，最多 $WAIT_MIN 分鐘。APK：$(ls -l "$APK" | awk '{print $6,$7,$8}')"

END=$(( $(date +%s) + WAIT_MIN * 60 ))
while [ "$(date +%s)" -lt "$END" ]; do
  # ① 已經連著（USB 或先前連過的無線）就直接用
  W=$(sh "$(dirname "$0")/adb_target.sh" watch 2>/dev/null) && [ -n "$W" ] && break

  # ② mDNS 找無線偵錯。adb 自己會宣告 _adb-tls-connect._tcp
  SVC=$("$ADB" mdns services 2>/dev/null | tr -d '\r' | grep "_adb-tls-connect" | head -1)
  if [ -n "$SVC" ]; then
    ADDR=$(echo "$SVC" | awk '{print $NF}')
    echo "看到裝置：$ADDR，連連看"
    "$ADB" connect "$ADDR" >/dev/null 2>&1
    sleep 3
    W=$(sh "$(dirname "$0")/adb_target.sh" watch 2>/dev/null) && [ -n "$W" ] && break
  fi
  sleep 20
done

W=$(sh "$(dirname "$0")/adb_target.sh" watch) || {
  echo "時間到，手錶還是沒上線。無線偵錯打開之後再跑一次這支就好。"; exit 1; }

echo "找到手錶：$W"
echo "舊版：$("$ADB" -s "$W" shell dumpsys package $PKG < /dev/null 2>/dev/null | grep versionCode | head -1 | tr -d '\r')"

"$ADB" -s "$W" install -r "$APK" || { echo "X 安裝失敗，舊版還在，沒有動到任何東西"; exit 1; }

# ⚠ 通知監聽權限重裝之後可能掉，補一次不花錢
"$ADB" -s "$W" shell cmd notification allow_listener $PKG/$PKG.FallWatcher < /dev/null >/dev/null 2>&1

echo "新版：$("$ADB" -s "$W" shell dumpsys package $PKG < /dev/null 2>/dev/null | grep versionCode | head -1 | tr -d '\r')"

# ⚠ **不要只看「安裝成功」就宣布完成。** 這個專案已經被
#   「綠燈但實際是壞的」咬過至少八次。要看服務真的在跑、資料真的有新的一筆。
echo "--- 等服務起來（最多 3 分鐘）---"
for i in 1 2 3 4 5 6; do
  sleep 30
  LAST=$("$ADB" -s "$W" shell "run-as $PKG tail -1 files/\$(date +%Y-%m-%d).jsonl" < /dev/null 2>/dev/null | tr -d '\r')
  case "$LAST" in
    *chg=*|*on_body*) echo "OK 服務在寫資料了："; echo "  $(echo "$LAST" | cut -c1-160)"; exit 0 ;;
  esac
  echo "  還沒（第 $i 次）"
done
echo "⚠ 裝好了，但 3 分鐘內沒看到新資料 —— 要人去確認一下，不要當成功"
exit 2
