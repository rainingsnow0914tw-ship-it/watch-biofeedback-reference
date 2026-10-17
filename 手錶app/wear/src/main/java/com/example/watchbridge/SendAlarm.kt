package com.example.watchbridge

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * 用系統鬧鐘叫傳送器，取代原本的 java.util.Timer。
 *
 * ## 為什麼換掉（2026-08-16 深夜查到的根因）
 *
 * 原本 CollectorService 裡是：
 * ```
 * sendTimer.scheduleAtFixedRate(..., 60_000L, 5 * 60_000L)
 * ```
 * **`java.util.Timer` 在 Doze 底下會被凍住。** 它底層是 `Object.wait()`，
 * 系統一進休眠那條執行緒就停在那裡，時間到了也不會醒。
 *
 * 實測證據：
 * ```
 * 23:29:29.760  送出 179981 bytes
 * 23:29:29.818  送出 179975 bytes     ← 三次全擠在同一秒
 * 23:29:29.928  送出  73198 bytes
 * 23:55:00      —— 25 分鐘沒有再送過
 * mWakefulness = Dozing
 * light_idle_to = +1h0m0s0ms
 * ```
 * 「三次擠在同一秒」就是計時器被凍住之後、醒來時把積欠的執行一次補完。
 * 而 `light_idle_to` 上限一小時，**正好對上整天看到的 60–75 分鐘鋸齒**。
 *
 * 症狀很陰險：**收資料完全正常**（那是感測器回呼，不靠計時器），
 * 服務也活著、前景通知也在，**只有「送」這件事間歇性停擺**，
 * 而且每次都會自己恢復 —— 所以看起來像網路不穩，其實是排程被凍。
 *
 * ## 為什麼 AlarmManager 可以
 *
 * `setAndAllowWhileIdle` 的鬧鐘由系統保管，**Doze 底下照樣會響**
 * （深度休眠時大約九分鐘才准響一次，這對我們完全夠用）。
 * 這跟手機端 StaleWatchdog 用的是同一招 ——
 * 諷刺的是那支的註解裡就寫著「計時器跟著服務一起死」，
 * **而我在手錶端自己用了計時器。**
 *
 * 不用 `setExact`：Android 12 以上要 SCHEDULE_EXACT_ALARM 權限（要使用者手動開），
 * 而傳送早幾分鐘晚幾分鐘沒有差別。
 *
 * ## 為什麼一次醒來要送好幾塊
 *
 * 深度休眠時鬧鐘大約九分鐘才准響一次，而手錶每分鐘產生約 10–12 KB。
 * 一塊 180 KB ÷ 12 KB/min ≈ 15 分鐘的量 —— 單塊其實追得上，
 * 但**積壓的舊資料就永遠追不完**。所以醒來一次就多送幾塊，
 * 直到送完或碰到預算上限。
 */
class SendAlarm : BroadcastReceiver() {

    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != ACTION) return
        // 診斷用：沒有這一行就分不出「receiver 沒被叫到」和「叫到了但沒動作」。
        Log.i(TAG, "鬧鐘響了，去叫收集服務")

        // ⚠ 2026-08-17 02:07 立刻踩到的坑：**這裡不可以直接呼叫傳送器。**
        //
        //     `onReceive` 跑在**主執行緒**上，而 `Tasks.await()` 明文禁止在主執行緒呼叫：
        //         IllegalStateException: Must not be called on the main application thread
        //
        //     實測日誌（第一次手動觸發就六連發）：
        //         02:07:30.660 W 送不出去（IllegalStateException: Must not be called...）
        //         02:07:31.082 W 送不出去（IllegalStateException: Must not be called...）
        //
        //     **這個 bug 比它要修的那個更嚴重**：失敗不推進 offset，
        //     所以資料會一直累積、**永遠送不出去**，而 app 看起來完全正常
        //     （收資料照常、服務活著、前景通知在）。
        //     如果沒有手動測這一次，它會安靜地跑好幾天。
        //
        // 為什麼不用 goAsync()：
        //     那個有時限（約十秒），而我們一塊就可能等到 45 秒。
        //     超時的話系統會直接砍掉，比現在更難查。
        //
        // 正解：**叫已經在跑的前景服務去做。**
        //     它是前景服務，沒有時限；它本來就有自己的背景執行緒；
        //     而且它活著本身就是這套東西能運作的前提。
        runCatching {
            ctx.startForegroundService(
                Intent(ctx, CollectorService::class.java).setAction(ACTION)
            )
        }.onFailure { Log.e(TAG, "叫不動收集服務", it) }

        // ⚠ 一定要在最後**無條件**重排下一次。
        //   放進 runCatching 裡面的話，只要上面那段丟一次例外，
        //   鬧鐘就從此再也不會響 —— 而且沒有人會發現。
        //   （這條跟手機端看門狗是同一個教訓。）
        schedule(ctx)
    }

    companion object {
        const val TAG = "WatchBridge"
        const val ACTION = "com.example.watchbridge.SEND_NOW"

        /** 幾分鐘叫一次。深度休眠底下實際會比這個久，那是系統的下限，不是 bug。 */
        private const val EVERY_MIN = 10L

        fun schedule(ctx: Context) {
            val am = ctx.getSystemService(AlarmManager::class.java)
            val pi = PendingIntent.getBroadcast(
                ctx, 0,
                Intent(ctx, SendAlarm::class.java).setAction(ACTION),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            runCatching {
                am.setAndAllowWhileIdle(
                    AlarmManager.RTC_WAKEUP,
                    System.currentTimeMillis() + EVERY_MIN * 60_000,
                    pi,
                )
            }.onFailure { Log.e(TAG, "排不了傳送鬧鐘", it) }
        }
    }
}
