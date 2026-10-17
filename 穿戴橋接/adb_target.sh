#!/bin/sh
# 找指定裝置的 adb 序號。**找不到就回非零、絕不回空字串。**
#
# 用法：  W=$(sh adb_target.sh watch) || exit 1
#         W=$(sh adb_target.sh phone) || exit 1
#
# ── 這支存在的理由：2026-08-18 的兩次事故 ──────────────────────
#
# ⚠ 事故一：空序號會落到錯的裝置
#     W=$(adb devices | grep ... | cut -f1)
#     adb -s "$W" shell ...
#   手錶掉線時 $W 是空字串，而 **`adb -s ""` 不是「沒有目標」，
#   是「隨便挑唯一那台」** —— 結果我把「關閉自動系統更新」
#   改到 使用者 的主力手機上，而且還「驗證通過」了
#   （那台真的照著改了，我只驗了「改成功」沒驗「改在對的機器上」）。
#
# ⚠ 事故二：這支腳本的第一版自己也有兩個 bug
#   ① **`adb shell` 會吃掉 stdin** —— 放在 `while read` 迴圈裡，
#      它會把管線剩下的行全部吞光，所以迴圈只跑第一台就結束。
#      修法：`< /dev/null`。
#   ② **adb devices 的輸出是 CRLF** —— `cut -f1` 拿到的序號尾巴黏著 \r，
#      拿去 `adb -s` 會比對不到。修法：`tr -d '\r'`。
#
#   這兩個 bug 合起來的症狀是「手錶明明連著卻說找不到」——
#   而第一版我寫完沒測就用了。
ADB="${ADB:-/c/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe}"
WANT="${1:-watch}"

FOUND=""
# 先把清單抓進變數，不要邊跑迴圈邊讀管線
LIST=$("$ADB" devices 2>/dev/null | tail -n +2 | tr -d '\r' | awk -F'\t' '$2=="device"{print $1}')

for s in $LIST; do
  m=$("$ADB" -s "$s" shell "getprop ro.product.model" < /dev/null 2>/dev/null | tr -d '\r\n')
  case "$m" in
    # ⚠ 2026-08-19：**型號有時候是底線不是連字號。** 她的主力手機
    #   `ro.product.model` 回的是 `SM_S9480`（底線），只比對 `SM-S*` 會漏掉，
    #   症狀是「手機明明插著 USB 卻說找不到 phone」。兩種都收。
    SM-R*|SM_R*)                         [ "$WANT" = "watch" ] && FOUND="$s" ;;   # Galaxy Watch
    SM-S*|SM_S*|SM-N*|SM_N*|SM-G*|SM_G*) [ "$WANT" = "phone" ] && FOUND="$s" ;;  # Galaxy 手機
  esac
  [ -n "$FOUND" ] && break
done

if [ -z "$FOUND" ]; then
  echo "找不到 $WANT（手錶=SM-R*、手機=SM-S*）" >&2
  exit 1
fi
echo "$FOUND"
