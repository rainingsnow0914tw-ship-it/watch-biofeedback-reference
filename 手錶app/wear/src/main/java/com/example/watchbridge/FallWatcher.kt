package com.example.watchbridge

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log

/**
 * 監聽三星原生的跌倒偵測。
 *
 * ## 為什麼走通知這條路（2026-08-17 凌晨查出來的）
 *
 * 使用者 問了一個關鍵問題：
 * > 「我們能夠即時拿到那條三星的摔倒警報嗎？要是不是即時就白費咧？」
 *
 * 她問對了。如果拿不到、或要等幾分鐘才拿得到，整條路就沒有意義。
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
 * ⚠️ **但「它跌倒時到底會不會發那則通知」，只有真的摔一次才知道。**
 *    所以這一支刻意**把每一則通知都記下來**（不只是跌倒的），
 *    這樣 使用者 假摔的時候，我可以直接從資料裡看到：
 *    哪個套件、什麼時候、標題寫什麼、跟撞擊差了幾毫秒。
 *
 *    **在那次實測之前，這支的價值是「收集證據」，不是「已經能偵測跌倒」。**
 *    不要因為程式碼存在就以為功能成立。
 *
 * ## 怎麼授權
 *
 * 通知監聽是特權，使用者要手動開；手錶上那個設定頁很難找，所以用 adb：
 * ```
 * adb shell cmd notification allow_listener \
 *     com.example.watchbridge/com.example.watchbridge.FallWatcher
 * ```
 * ⚠️ **恢復原廠或重新配對之後要重下一次。** 這條跟三星開發者模式一樣，
 *    是家人版部署時必須現場做完的步驟之一。
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

        // 只記「可能跟緊急有關」的，不然一天幾百則通知會把資料檔灌爆。
        // ⚠ 寧可寬一點 —— 現階段的目的是**搞清楚跌倒到底會發什麼**，
        //   而不是精準過濾。等實測看到真正的那則長什麼樣再收窄。
        // ⚠ 2026-08-20 改成**影子模式期間全部記**，理由是一次真實的失敗：
        //
        //   她 08-19 按了 5 次「震了」。事後去對，只有 1 次對得上
        //   （17:30:14 按的，17:29:56 三星健康發了「身體能量指數良好」—— 差 17 秒）。
        //   另外 3 次**前後五分鐘一則通知都沒有** —— 但那不代表沒有通知，
        //   **是代表發通知的那個 app 不在我的白名單裡，我根本沒看。**
        //
        //   影子模式的整個目的就是搞清楚「她感覺到的」跟「系統做的」對不對得上。
        //   而我用一份猜出來的白名單，先把大部分證據丟掉了。
        //
        //   成本算過：通知一天大約幾十到幾百則，每則記幾十 bytes；
        //   相比之下 hr_ibi 一天 56,000 筆。**通知全記的成本是零頭。**
        //
        //   ⚠ 隱私：只記套件名、標題、內文前段。**訊息類 app 的內文不記**（見下）。
        val interesting = INTERESTING.any { pkg.contains(it, ignoreCase = true) }
        // 影子模式期間全收；等找出誰在震她之後再收窄回 INTERESTING。
        val shadowMode = true

        val ex = sbn.notification?.extras
        val title = ex?.getCharSequence("android.title")?.toString().orEmpty()
        // ⚠ 內文只在「緊急類」才記。**別人傳給她的訊息內容不該進我們的資料檔** ——
        //   我們要的是「有沒有東西在震她」，不是「誰跟她說了什麼」。
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
            // ⚠ 用系統給的 postTime，不要用「我收到的當下」。
            //   要量「跌倒到我收到隔了多久」，兩個時間都必須是真的。
            put("post_t", sbn.postTime)
        }

        // ⚠⚠ 2026-08-18：**這裡本來會直接放 T-3 火警震動，已經關掉。**
        //
        //   原本的邏輯是「看到 safety_assistance 的通知就震」，理由寫著
        //   「三星那個門檻是它在幾百萬支錶上調出來的」。**那太天真了。**
        //
        //   實測後果：使用者 被無故震了至少 8 次（08-17 09:37、10:10、15:43，
        //   08-18 00:37、01:03、01:39 連三次）。她的原話是
        //   「他手錶的時候會震動，不知道是提醒？還是我們系統的問題？」
        //   —— **是我們的問題。**
        //
        //   真相：`safety_assistance` **會例行發標題 "SOS"、內文空白的通知**
        //   （頻道 8280，importance 只有 LOW）。那八成是「SOS 功能已啟用」
        //   的常駐指示，跟有沒有人跌倒完全無關。
        //
        //   **而我到現在還不知道「真的跌倒」的那則通知長什麼樣** ——
        //   要做完假摔測試、看到真的那一則，才有辦法分辨。
        //
        //   在那之前，正確的行為是：**只記錄，不動作。**
        //   一個會為了常駐指示放火警的警報器，比沒有警報器更糟 ——
        //   它會在真的出事之前，先把人訓練成忽略它。
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
