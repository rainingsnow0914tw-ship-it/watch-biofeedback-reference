package com.example.watchbridge

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log

/**
 * 監聽三星原生的跌倒偵測。
 *
 *
 *
 *
 * 在手錶上挖出來的事實：
 *   · 套件 `com.samsung.android.watch.safety_assistance` 就是跌倒偵測／SOS
 *   · 它**有通知頻道**：`mId='8280', mName=SOS`
 *   · 它**已經發過 12 則通知**（`numPostedByApp=12`）
 *   · 這台錶**允許第三方監聽通知**（已經有兩個監聽器在跑）
 *
 * 而通知是**推播不是輪詢** —— 系統直接回呼 `onNotificationPosted`，
 * 延遲是毫秒級。**所以只要它發通知，我們就是即時拿到。**
 *
 *    所以這一支刻意**把每一則通知都記下來**（不只是跌倒的），
 *    哪個套件、什麼時候、標題寫什麼、跟撞擊差了幾毫秒。
 *
 *    不要因為程式碼存在就以為功能成立。
 *
 * ## 怎麼授權
 *
 * ```
 * adb shell cmd notification allow_listener \
 *     com.example.watchbridge/com.example.watchbridge.FallWatcher
 * ```
 * ⚠️ **恢復原廠或重新配對之後要重下一次。** 這條跟三星開發者模式一樣，
 */
class FallWatcher : NotificationListenerService() {

    override fun onListenerConnected() {
        Log.i(TAG, "通知監聽已連上")
        SamsungSensors.onRecord?.invoke("fall_watcher") { put("state", "connected") }
    }

    override fun onListenerDisconnected() {
        // ⚠ 這一筆很重要。監聽器被系統斷開之後**不會自己回來**，
        //   而斷開的時候畫面上什麼都看不到 —— 又是一個靜默失敗。
        Log.w(TAG, "通知監聽斷開了")
        SamsungSensors.onRecord?.invoke("fall_watcher") { put("state", "disconnected") }
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val pkg = sbn.packageName ?: return

        // ⚠ 寧可寬一點 —— 現階段的目的是**搞清楚跌倒到底會發什麼**，
        //   而不是精準過濾。等實測看到真正的那則長什麼樣再收窄。
        //
        //   另外 3 次**前後五分鐘一則通知都沒有** —— 但那不代表沒有通知，
        //   **是代表發通知的那個 app 不在我的白名單裡，我根本沒看。**
        //
        //   而我用一份猜出來的白名單，先把大部分證據丟掉了。
        //
        //   成本算過：通知一天大約幾十到幾百則，每則記幾十 bytes；
        //   相比之下 hr_ibi 一天 56,000 筆。**通知全記的成本是零頭。**
        //
        //   ⚠ 隱私：只記套件名、標題、內文前段。**訊息類 app 的內文不記**（見下）。
        val interesting = INTERESTING.any { pkg.contains(it, ignoreCase = true) }
        val shadowMode = true

        val ex = sbn.notification?.extras
        val title = ex?.getCharSequence("android.title")?.toString().orEmpty()
        //   非緊急類只留套件名、標題長度與時間，那已經足夠做時間對照。
        val text = if (interesting)
            ex?.getCharSequence("android.text")?.toString().orEmpty() else ""

        Log.w(TAG, "緊急類通知：$pkg / $title / $text")

        if (!interesting && !shadowMode) return

        SamsungSensors.onRecord?.invoke("notif") {
            put("pkg", pkg)
            // 非緊急類不記標題原文，只記長度 —— 時間對照用不到內容
            put("title", if (interesting) title else "(${title.length}字)")
            put("watched", interesting)
            put("text", text)
            put("channel", sbn.notification?.channelId ?: "")
            put("post_t", sbn.postTime)
        }

        //
        //
        //   —— **是我們的問題。**
        //
        //   真相：`safety_assistance` **會例行發標題 "SOS"、內文空白的通知**
        //   的常駐指示，跟有沒有人跌倒完全無關。
        //
        //   要做完假摔測試、看到真的那一則，才有辦法分辨。
        //
        //   在那之前，正確的行為是：**只記錄，不動作。**
        //   一個會為了常駐指示放火警的警報器，比沒有警報器更糟 ——
        //
        //   要恢復動作的條件（三個都要）：
        //     ① 做過假摔測試，看到真的跌倒通知
        //     ② 能用 title / text / channel 把它跟這些常駐 SOS 分開
        //     ③ 那個判別式在影子模式跑過，確認不會誤觸
        if (pkg.contains("safety_assistance", ignoreCase = true)) {
            Log.w(TAG, "三星 SOS 通知（title=$title）—— **只記錄，不震**，" +
                "因為還分不出這是常駐指示還是真的跌倒")
        }
    }

    companion object {
        const val TAG = "WatchBridge"

        /**
         * 會被記錄下來的套件關鍵字。
         *
         * safety_assistance 是主角；其他幾個放進來是因為**我還不確定
         * 跌倒的通知是從哪一個套件出來的** —— 有可能是 shealth、
         * 也有可能是系統的 emergency 元件。實測之後再收窄。
         */
        private val INTERESTING = listOf(
            "safety_assistance",
            "emergency",
            "shealth",
            "safetyhub",
            "sos",
        )
    }
}
