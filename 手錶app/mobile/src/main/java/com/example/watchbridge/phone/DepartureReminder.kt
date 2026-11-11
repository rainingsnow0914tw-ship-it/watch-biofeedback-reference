package com.example.watchbridge.phone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 *
 * 看門狗每 15 分鐘叫一次 check()：
 *   1. 一天學一次：讀手機自己存的 手機情境/<日>.jsonl 近 15 天 → 通勤 → 時段（存 prefs，GET /context/now 看得到）。
 *   2. 現在人在哪（PhoneContext 目前的 Wi-Fi 網段）＋錶最後一筆（WatchLeftBehind.lastWatchRecord）→ 每個時段問 Policy。
 * 學不到時段就什麼都不做（資料不夠不瞎猜）。
 */
object DepartureReminder {
    private const val TAG = "WatchBridgePhone"
    private const val PREF = "departure"
    private const val CH = "departure_remind_v1"
    private const val NOTIF_ID = 7790
    private val TZ: TimeZone = TimeZone.getTimeZone(java.util.TimeZone.getDefault().id)

    @Volatile private var learnedDay: String? = null
    @Volatile var slots: List<DeparturePolicy.Slot> = emptyList(); private set

    fun check(ctx: Context) {
        val app = ctx.applicationContext
        val now = System.currentTimeMillis()
        val prefixes = PhoneContext.homePrefix(app).split(",").map { it.trim() }.filter { it.isNotEmpty() }
        val today = DeparturePolicy.dayKey(now, TZ)
        if (learnedDay != today) {
            slots = runCatching { DeparturePolicy.learn(DeparturePolicy.departures(readEvents(app, 15), prefixes), now, TZ) }
                .onFailure { Log.w(TAG, "出門前提醒：學時段失敗", it) }.getOrDefault(emptyList())
            learnedDay = today
            app.getSharedPreferences(PREF, Context.MODE_PRIVATE).edit().putString("slots", slotsJson().toString()).apply()
            Log.i(TAG, "出門前提醒：學到 ${slots.size} 個時段 ${slots.map { "${it.from}→${it.to}@${DeparturePolicy.hhmm(it.remindMin)}" }}")
        }
        if (slots.isEmpty()) return

        val here = DeparturePolicy.placeOf(PhoneContext.currentIp(), prefixes)
        val w = WatchLeftBehind.lastWatchRecord(app)?.let {
            DeparturePolicy.Watch(it.optLong("_age_ms", -60_000) / 60_000, it.optBoolean("charging"), it.optString("on_body") == "off")
        }
        val prefs = app.getSharedPreferences(PREF, Context.MODE_PRIVATE)
        val sent = prefs.getStringSet("sent", emptySet()).orEmpty()
        for (s in slots) {
            val key = DeparturePolicy.shouldRemind(s, now, TZ, here, w, sent) ?: continue
            val why = if (w?.charging == true) "錶還在充電座上喔" else "錶還沒戴上喔"
            val dest = if (s.to == prefixes.getOrNull(1)) "去第二個常去地點" else if (s.to == prefixes.getOrNull(0)) "回家" else "出門"
            notify(app, "⌚ $why", "平常這時候（${DeparturePolicy.hhmm(s.medianMin)} 左右）妳會$dest，等下出門記得戴。")
            PhoneContext.record(app, "departure_remind", JSONObject().put("key", key).put("slot", DeparturePolicy.hhmm(s.medianMin))
                .put("to", s.to).put("watch_age_min", w?.ageMin ?: -1).put("watch_charging", w?.charging ?: false))
            // 只留最近 30 個 key
            prefs.edit().putStringSet("sent", (sent + key).toList().takeLast(30).toSet()).apply()
            return
        }
    }

    fun slotsJson(): JSONArray = JSONArray().apply {
        slots.forEach { s ->
            put(JSONObject().put("from", s.from).put("to", s.to).put("usual", DeparturePolicy.hhmm(s.medianMin))
                .put("remind", DeparturePolicy.hhmm(s.remindMin)).put("earliest", DeparturePolicy.hhmm(s.earliestMin))
                .put("latest", DeparturePolicy.hhmm(s.latestMin)).put("days", s.days).put("n", s.n))
        }
    }

    private fun readEvents(ctx: Context, days: Int): List<DeparturePolicy.Ev> {
        val fmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)
        val out = ArrayList<DeparturePolicy.Ev>()
        for (i in days downTo 0) {
            val f = PhoneContext.dayFile(ctx, fmt.format(Date(System.currentTimeMillis() - i * 86_400_000L)))
            if (!f.exists()) continue
            f.forEachLine(Charsets.UTF_8) { l ->
                val o = runCatching { JSONObject(l) }.getOrNull() ?: return@forEachLine
                val kind = o.optString("kind")
                if (kind == "spot") return@forEachLine   // 手機在哪的紀錄很多，跟出門無關
                out += DeparturePolicy.Ev(o.optLong("t"), kind, if (o.isNull("ip")) null else o.optString("ip"))
            }
        }
        return out.sortedBy { it.t }
    }

    private fun notify(ctx: Context, title: String, text: String) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        if (!nm.areNotificationsEnabled()) { Log.w(TAG, "出門前提醒：通知被關了"); return }
        nm.createNotificationChannel(NotificationChannel(CH, "出門前提醒帶錶", NotificationManager.IMPORTANCE_HIGH)
            .apply { description = "平常出門前 10 分鐘，錶還在充電／沒戴就提醒一次" })
        nm.notify(NOTIF_ID, Notification.Builder(ctx, CH)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title).setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setAutoCancel(true).build())
    }
}
