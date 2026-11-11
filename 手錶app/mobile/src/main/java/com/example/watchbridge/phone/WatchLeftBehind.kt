package com.example.watchbridge.phone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.util.Log
import com.google.android.gms.tasks.Tasks
import com.google.android.gms.wearable.Wearable
import org.json.JSONObject
import java.io.File
import java.io.RandomAccessFile
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 *
 * 時機：PhoneContext 看到手機離開已知地點的 Wi-Fi（home→away）。那一刻看兩件事：
 *   ① 錶有沒有藍牙連著（Wearable NodeClient connectedNodes）——錶留在家裡，出門幾十公尺就斷。
 *   ② 錶最後一筆資料（手錶資料/<日>.jsonl 最後一行）是不是 charging=true 或 on_body=off、而且是 30 分鐘內的。
 *
 * 不做：不叫錶、不走雲端（錶根本不在身邊）、不重複（同一次離家只提醒一次，由 PhoneContext 的 away 轉換觸發）。
 * 省電：只在轉換那一瞬間跑一次，背景執行緒，查節點最多等 3 秒。
 */
object WatchLeftBehind {
    private const val TAG = "WatchBridgePhone"
    private const val CH = "watch_left_behind_v2"
    private const val NOTIF_ID = 7788
    private const val CONFIRM_MS = 120_000L

    fun onLeftKnownPlace(ctx: Context) {
        val app = ctx.applicationContext
        thread(isDaemon = true, name = "watch-left-behind") {
            Thread.sleep(CONFIRM_MS)
            if (!PhoneContext.awayNow()) { Log.i(TAG, "出門沒帶錶：${CONFIRM_MS / 1000} 秒內回到已知 Wi-Fi，只是斷線，不提醒"); return@thread }
            runCatching { check(app) }.onFailure { Log.w(TAG, "出門沒帶錶：檢查失敗", it) }
        }
    }

    private fun check(ctx: Context) {
        // 藍牙問三次、隔 10 秒，三次都沒連上才算沒連（一次抖動不算）
        var connected = false
        for (i in 0 until 3) {
            connected = runCatching {
                Tasks.await(Wearable.getNodeClient(ctx).connectedNodes, 3, TimeUnit.SECONDS).isNotEmpty()
            }.getOrDefault(false)
            if (connected) break
            if (i < 2) Thread.sleep(10_000)
        }
        if (!PhoneContext.awayNow()) { Log.i(TAG, "出門沒帶錶：查的時候已經回到已知 Wi-Fi，不提醒"); return }
        val last = lastWatchRecord(ctx)
        val ageMin = last?.optLong("_age_ms")?.let { it / 60_000 } ?: -1L
        val charging = last?.optBoolean("charging") == true
        val offBody = last?.optString("on_body") == "off"
        val fresh = last != null && ageMin in 0..30
        Log.i(TAG, "出門沒帶錶檢查：藍牙連著=$connected 最後一筆 ${ageMin} 分前 charging=$charging off=$offBody")

        val reason = when {
            fresh && charging -> "錶最後一筆（${ageMin} 分鐘前）還在充電"
            fresh && offBody -> "錶最後一筆（${ageMin} 分鐘前）是脫下來的"
            !connected -> "錶跟手機的藍牙沒連上"
            else -> null
        } ?: return
        PhoneContext.record(ctx, "watch_left", JSONObject().put("reason", reason).put("bt", connected))
        notify(ctx, "⌚ 錶好像沒帶出門", "$reason。還來得及回去拿嗎？")
    }

    /** 今天（或昨天）手錶資料檔的最後一行，另附 _age_ms。只讀尾端 8 KB，不載整檔。 */
    internal fun lastWatchRecord(ctx: Context): JSONObject? {
        val dir = File(ctx.filesDir, "手錶資料")
        val fmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)
        val days = listOf(Date(), Date(System.currentTimeMillis() - 86_400_000L)).map { fmt.format(it) }
        for (d in days) {
            val f = File(dir, "$d.jsonl")
            if (!f.exists() || f.length() == 0L) continue
            val tail = runCatching {
                RandomAccessFile(f, "r").use { raf ->
                    val n = minOf(8192L, raf.length()); raf.seek(raf.length() - n)
                    val buf = ByteArray(n.toInt()); raf.readFully(buf); String(buf, Charsets.UTF_8)
                }
            }.getOrNull() ?: continue
            val line = tail.lines().lastOrNull { it.startsWith("{\"t\":\"") } ?: continue
            val o = runCatching { JSONObject(line) }.getOrNull() ?: continue
            val t = runCatching {
                SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).parse(o.optString("t").take(19))?.time
            }.getOrNull() ?: continue
            o.put("_age_ms", System.currentTimeMillis() - t)
            return o
        }
        return null
    }

    private fun notify(ctx: Context, title: String, text: String) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        if (!nm.areNotificationsEnabled()) { Log.w(TAG, "出門沒帶錶：通知被關了，沒辦法提醒"); return }
        val alarmAttrs = android.media.AudioAttributes.Builder()
            .setUsage(android.media.AudioAttributes.USAGE_ALARM)
            .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
        val alarmUri = android.media.RingtoneManager.getDefaultUri(android.media.RingtoneManager.TYPE_ALARM)
            ?: android.media.RingtoneManager.getDefaultUri(android.media.RingtoneManager.TYPE_NOTIFICATION)
        nm.createNotificationChannel(NotificationChannel(CH, "出門沒帶錶", NotificationManager.IMPORTANCE_HIGH)
            .apply {
                description = "離開常去地點時錶不在身邊（走鬧鐘音軌，靜音也會響）"
                setSound(alarmUri, alarmAttrs)
                enableVibration(true); vibrationPattern = longArrayOf(0, 400, 200, 400, 200, 600)
                setBypassDnd(false)
            })
        // 再自己響 6 秒：有些機型頻道聲音在靜音下仍被壓掉，鬧鐘音軌的 Ringtone 不會
        runCatching {
            val r = android.media.RingtoneManager.getRingtone(ctx, alarmUri)
            r.audioAttributes = alarmAttrs
            r.play()
            thread(isDaemon = true) { Thread.sleep(6_000); runCatching { r.stop() } }
        }.onFailure { Log.w(TAG, "出門沒帶錶：鬧鐘聲放不出來", it) }
        runCatching {
            val vm = ctx.getSystemService(android.os.VibratorManager::class.java)
            vm.defaultVibrator.vibrate(android.os.VibrationEffect.createWaveform(longArrayOf(0, 400, 200, 400, 200, 600), -1))
        }
        val n = Notification.Builder(ctx, CH)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle(title).setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .build()
        nm.notify(NOTIF_ID, n)
    }
}
