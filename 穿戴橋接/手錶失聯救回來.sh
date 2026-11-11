#!/bin/sh
#
# ── 那天發生什麼（6 小時 20 分的資料卡在手錶裡）──────────────
#
# 症狀：手錶正常顯示、心率照收、電池正常，**但資料六個小時沒進手機**。
#      兩邊：  ACL BR/EDR:N（沒連線），但**配對紀錄都還在、藍牙都開著**
#
#   ③ 重開手錶、兩邊藍牙關開、重開 Galaxy Wearable —— **全部沒用**
#
#
#   這個失敗**在手錶上完全看不出來** —— 錶面正常、心率正常、電量正常。
#   而 Galaxy Wearable **不會自己重連，也不會主動通知**，它只是安靜地待在
#   **六小時裡唯一發現不對的，是我們自己寫的看門狗。**
#
# 用法：  sh 手錶失聯救回來.sh

ADB="${ADB:-/c/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe}"
P=$(sh "$(dirname "$0")/adb_target.sh" phone) || exit 1
PKG=com.samsung.android.app.watchmanager
# 你的錶在 dumpsys bluetooth_manager 裡那一行的藍牙位址片段（例：XX:XX(Public），放環境變數，不寫死
BT="${WATCH_BLUETOOTH_MATCH:?請先設定 WATCH_BLUETOOTH_MATCH（見 config/environment.example）}"

echo "① 現在連著嗎"
S=$("$ADB" -s "$P" shell "dumpsys bluetooth_manager 2>/dev/null | grep -m1 '$BT'" < /dev/null 2>&1 | tr -d '\r' | grep -oE "ACL BR/EDR:.")
echo "   $S"
case "$S" in *:Y*) echo "   已經連著，不用救。"; exit 0;; esac

echo "② 打開 Galaxy Wearable"
"$ADB" -s "$P" shell "monkey -p $PKG 1" < /dev/null >/dev/null 2>&1
sleep 6

# ⚠ **不要用記憶中的座標。** 每次都重新截圖確認那顆鈕在哪 ——
#   版面會隨版本與字級改變，照舊座標點下去可能點到別的東西。
echo "③ 截圖找「連接」鈕"
TMP="${TMPDIR:-/tmp}/gw_$$.png"
"$ADB" -s "$P" exec-out screencap -p > "$TMP" 2>/dev/null
echo "   截圖存在 $TMP —— **請看一眼確認畫面上真的有「連接」鈕再繼續**"
echo "   （1440x3120 的機器上，那顆鈕大約在 718,1103）"

echo "④ 按下去"
"$ADB" -s "$P" shell "input tap 718 1103" < /dev/null 2>&1 | tr -d '\r'

echo "⑤ 等連線（最多 2 分鐘）"
i=0
while [ $i -lt 8 ]; do
  sleep 15; i=$((i+1))
  S=$("$ADB" -s "$P" shell "dumpsys bluetooth_manager 2>/dev/null | grep -m1 '$BT'" < /dev/null 2>&1 | tr -d '\r' | grep -oE "ACL BR/EDR:.")
  echo "   $((i*15))s：${S:-未出現}"
  case "$S" in *:Y*) echo "   OK 連上了。佇列會自己補送，看檔案變大就是在補。"; exit 0;; esac
done
echo "   X 兩分鐘還沒連上 —— 截圖看一下畫面，那顆鈕可能不在預期的位置"
exit 1
