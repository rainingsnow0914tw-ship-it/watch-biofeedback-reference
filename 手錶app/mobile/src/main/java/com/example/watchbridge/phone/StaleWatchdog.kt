package com.example.watchbridge.phone

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 看門狗：手錶的資料停了就講出來。
 *
 * ## 這支存在的理由，是因為我先前假裝它已經存在
 *
 * 這支就是把那句話補成真的。
 *
 * ## 為什麼寫在手機不寫在電腦
 *
 *
 * ## 為什麼用 AlarmManager 而不是服務裡的計時器
 *
 * 計時器跟著服務一起死。而**服務死掉正是要偵測的情況之一** ——
 * 用會一起死的東西去監測死亡，等於沒有監測。
 * AlarmManager 的鬧鐘由系統保管，app 行程沒了它照樣會響，
 *
 * 用 `setAndAllowWhileIdle` 不用 `setExact`：
 *   而我們只是每十幾分鐘看一次，早幾分鐘晚幾分鐘無所謂。
 *   ⚠️ 但要知道它在 Doze 底下**大約九分鐘才准響一次**，所以實際間隔會比設定值長。
 *
 *
 * 通知的文字必須把這件事講死 —— 這跟 triage.py 檔頭原則三是同一條：
 * **沒有訊號 ≠ 沒有心跳。**
 *
 * ## 它會留下證據
 *
 */
class StaleWatchdog : BroadcastReceiver() {

    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != ACTION) return
        // BroadcastReceiver runs on the app's main thread. Tasks.await below
        // must run off it; otherwise a connected watch can be reported as
        // "不見了" when the query fails or blocks the receiver.
        val pending = goAsync()
        val appCtx = ctx.applicationContext
        Thread({
            try {
                runCatching { check(appCtx) }.onFailure { Log.e(TAG, "看門狗自己出錯", it) }

                // 順手重算今天摘要；只在原始資料更新時才重算。
                // 摘要失敗不可拖垮看門狗。
                runCatching { rebuildSummary(appCtx) }
                    .onFailure { Log.e(TAG, "重算摘要失敗（不影響看門狗）", it) }
            } finally {
                try {
                    schedule(appCtx) // 檢查失敗也要排下一次
                } finally {
                    pending.finish()
                }
            }
        }, "watchdog-check").start()
    }

    private fun check(ctx: Context) {
        val dir = File(ctx.filesDir, "手錶資料")
        val now = System.currentTimeMillis()
        runCatching { PhoneContext.tick(ctx) }
        runCatching { DepartureReminder.check(ctx) }

        //   我們要偵測的是**傳輸管線**有沒有在動，不是身體資料新不新。
        //   而且解析 18 MB 的 JSONL 只為了看最後一行，在手機上太貴。
        val newest = dir.listFiles().orEmpty()
            .filter { it.name.endsWith(".jsonl") }
            .maxByOrNull { it.lastModified() }

        val ageMin = if (newest == null) -1L else (now - newest.lastModified()) / 60_000

        //
        //   當天實測：藍牙送達會成批延遲，正常情況最久到 62 分鐘 ——
        //   而舊版 25 分鐘就叫、60 分鐘就大聲叫。
        //   實際上手錶一整天收了 97,432 筆、一筆都沒掉。
        //
        //   誤報會殺死整個系統：叫幾次以後人就不理它了。
        //
        //   所以：手錶連著 = 資料只是還在路上，不叫。
        //         手錶不見了 = 那才是真的要講的事（沒電、太遠、app 死了）。
        val watchConnected = nodeConnected(ctx)

        val level = when {
            newest == null -> "no-data"
            // 手錶還在旁邊 → 資料只是還沒送到，不是壞了。
            // 但拖過 STUCK_MIN 就算連著也該講一聲 —— 那時候不是延遲，是卡住了。
            watchConnected == true && ageMin < STUCK_MIN -> "ok"
            ageMin >= LOUD_MIN -> "loud"
            ageMin >= QUIET_MIN -> "quiet"
            else -> "ok"
        }

        // 服務掛了就順手把它帶回來。
        // 這一條同時補掉稽核發現的另一個洞：BridgeServer.onTimeout 自己 stopSelf()
        // 之後 **START_STICKY 不會把它帶回來**（自己停的服務系統視為正常結束）。
        // 那個洞原本沒有任何東西接得住。
        var restarted = false
        if (!serverAlive(ctx)) {
            runCatching { BridgeServer.start(ctx) }
                .onSuccess { restarted = true }
                .onFailure { Log.e(TAG, "橋接服務叫不起來", it) }
        }

        when (level) {
            "loud" -> notify(
                ctx, NotificationManager.IMPORTANCE_HIGH,
                when (watchConnected) {
                    true -> "手錶連著，但資料卡住 ${ageMin} 分鐘"
                    false -> "手錶不見了，資料停了 ${ageMin} 分鐘"
                    null -> "手錶連線狀態查不到，資料停了 ${ageMin} 分鐘"
                },
                "這代表我現在對身體狀況一無所知 —— 不代表沒事，也不代表出事。" +
                    "檢查手錶有沒有電、藍牙有沒有連上。",
            )
            "quiet" -> notify(
                ctx, NotificationManager.IMPORTANCE_LOW,
                "手錶資料停了 ${ageMin} 分鐘",
                "還在觀察，超過 ${LOUD_MIN} 分鐘會再提醒一次。",
            )
            "no-data" -> notify(
                ctx, NotificationManager.IMPORTANCE_LOW,
                "還沒有收到任何手錶資料",
                "手錶上的 app 可能沒開起來。",
            )
            else -> getNm(ctx).cancel(NOTIF_ID)   // 恢復了就把通知收掉
        }

        log(ctx, "check age=${ageMin}min level=$level watch=${when (watchConnected) { true -> "連著"; false -> "不見了"; null -> "未知" }} " +
            "file=${newest?.name ?: "無"} restarted=$restarted")
    }

    /**
     * 手錶還連著嗎。
     *
     * 資料有沒有到只說明送達，說明不了手錶在不在。
     *
     */
    private fun nodeConnected(ctx: Context): Boolean? = runCatching {
        com.google.android.gms.tasks.Tasks.await(
            com.google.android.gms.wearable.Wearable.getNodeClient(ctx).connectedNodes,
            3, java.util.concurrent.TimeUnit.SECONDS,
        ).isNotEmpty()
    }.onFailure { Log.w(TAG, "手錶節點查詢失敗；本次連線狀態未知", it) }.getOrNull()

    /** 橋接服務還活著嗎。用系統的執行中服務清單，不用自己記旗標（旗標會跟著行程一起沒）。 */
    private fun serverAlive(ctx: Context): Boolean = runCatching {
        @Suppress("DEPRECATION")
        ctx.getSystemService(android.app.ActivityManager::class.java)
            .getRunningServices(Int.MAX_VALUE)
            .any { it.service.className == BridgeServer::class.java.name }
    }.getOrDefault(true)   // 查不到就假設活著 —— 寧可不叫，也不要一直亂重啟

    private fun getNm(ctx: Context) = ctx.getSystemService(NotificationManager::class.java)

    private fun notify(ctx: Context, importance: Int, title: String, text: String) {
        val nm = getNm(ctx)
        // 兩個頻道分開建：**通知頻道的重要性建立之後就改不了**，
        val ch = if (importance >= NotificationManager.IMPORTANCE_HIGH) CH_LOUD else CH_QUIET
        nm.createNotificationChannel(
            NotificationChannel(
                ch,
                if (ch == CH_LOUD) "手錶資料中斷（需要注意）" else "手錶資料中斷（觀察中）",
                importance,
            )
        )
        nm.notify(
            NOTIF_ID,
            android.app.Notification.Builder(ctx, ch)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(android.app.Notification.BigTextStyle().bigText(text))
                .setSmallIcon(android.R.drawable.stat_notify_error)
                .setAutoCancel(true)
                .build(),
        )
        Log.w(TAG, "$title / $text")
    }

    /** 每次檢查都留一行。**沒有證據的監看等於沒有監看。** */
    private fun log(ctx: Context, msg: String) {
        runCatching {
            val f = File(ctx.filesDir, LOG)
            // 只留最後 200 行 —— 這是給人看的軌跡，不是資料
            val old = if (f.exists()) f.readLines().takeLast(199) else emptyList()
            val stamp = SimpleDateFormat("MM-dd HH:mm:ss", Locale.US).format(Date())
            f.writeText((old + "$stamp  $msg").joinToString("\n") + "\n")
        }.onFailure { Log.e(TAG, "看門狗日誌寫不了", it) }
    }

    companion object {
        const val TAG = "WatchBridgePhone"
        const val ACTION = "com.example.watchbridge.WATCHDOG"
        const val LOG = "watchdog.log"

        /** 今天的原始資料比摘要新的話，重算一次。 */
        private fun rebuildSummary(ctx: Context) {
            val day = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US)
                .format(java.util.Date())
            val src = File(File(ctx.filesDir, "手錶資料"), "$day.jsonl")
            if (!src.exists()) return
            val out = File(File(ctx.filesDir, Summary.DIR), "$day.jsonl")
            if (out.exists() && out.lastModified() >= src.lastModified()) return
            val t0 = System.currentTimeMillis()
            Summary.build(ctx.filesDir, day)
            // ⚠ 記耗時。摘要如果慢到影響看門狗的節奏，要看得出來 ——
            Log.i(TAG, "摘要重算 $day 花了 ${System.currentTimeMillis() - t0} ms")
        }
        private const val NOTIF_ID = 77
        private const val CH_QUIET = "stale_quiet"
        private const val CH_LOUD = "stale_loud"

        /** 每隔多久看一次。資料是每 5 分鐘一塊，所以 15 分鐘足夠靈敏。 */
        private const val EVERY_MIN = 15L

        /**
         *
         * 25 分鐘 ≈ 連續漏掉五塊。單次藍牙抖動不會這麼久，
         * 但手錶脫下來放桌上**不會**觸發（服務照跑、動作量照寫）——
         */
        private const val QUIET_MIN = 25L
        private const val LOUD_MIN = 60L

        /**
         * 手錶連著、但資料還是卡這麼久 → 就算連著也要講。
         *
         * 那是**正常**的（資料一筆沒掉）。留 1.5 倍的餘裕。
         * 超過這個就不是 Doze 攢批次了，是傳送真的卡住 ——
         * 那種情況今天也發生過（Tasks.await 沒逾時，整條傳送靜靜停擺）。
         */
        private const val STUCK_MIN = 90L

        fun schedule(ctx: Context) {
            val am = ctx.getSystemService(AlarmManager::class.java)
            val pi = PendingIntent.getBroadcast(
                ctx, 0,
                Intent(ctx, StaleWatchdog::class.java).setAction(ACTION),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            runCatching {
                am.setAndAllowWhileIdle(
                    AlarmManager.RTC_WAKEUP,
                    System.currentTimeMillis() + EVERY_MIN * 60_000,
                    pi,
                )
            }.onFailure { Log.e(TAG, "排不了看門狗鬧鐘", it) }
        }
    }
}
