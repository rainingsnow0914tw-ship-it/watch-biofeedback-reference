package com.example.watchbridge

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import com.google.android.gms.tasks.Tasks
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.WearableListenerService
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 *
 * 想知道錶的狀態不一定要 adb：電腦 叫手機.py → 手機 BridgeServer /watch_diag → MessageClient /diag → 這裡
 * → 回 /diag_reply 給手機 → 手機把 JSON 交回電腦。走的是本來就通的藍牙資料層，不用 Wi-Fi、不用無線偵錯。
 *
 * ⚠ 白名單，刻意很小：只讀（status、log）＋三個可逆的小動作（restart_collector、wifi_on、wifi_off）。
 *   沒有安裝、沒有刪除、沒有任意 shell —— 這條路沒有 adb 那種密碼配對，不能開大門。
 */
class DiagService : WearableListenerService() {
    override fun onMessageReceived(event: MessageEvent) {
        if (event.path != Diag.REQ_PATH) return
        // WearableListenerService 的回呼本來就在背景執行緒，可以 Tasks.await。
        val req = runCatching { JSONObject(String(event.data, Charsets.UTF_8)) }.getOrNull() ?: return
        val id = req.optString("id")
        val cmd = req.optString("cmd")
        val out = runCatching { Diag.run(applicationContext, cmd) }
            .getOrElse { JSONObject().put("ok", false).put("error", it.toString()) }
        out.put("id", id).put("cmd", cmd).put("t_watch", System.currentTimeMillis())
        Log.i(Diag.TAG, "診斷 $cmd → ok=${out.optBoolean("ok")}")
        runCatching {
            Tasks.await(Wearable.getMessageClient(applicationContext)
                .sendMessage(event.sourceNodeId, Diag.REPLY_PATH, out.toString().toByteArray(Charsets.UTF_8)))
        }.onFailure { Log.w(Diag.TAG, "診斷回覆送不出去", it) }
    }
}

object Diag {
    const val TAG = "WatchDiag"
    const val REQ_PATH = "/diag"
    const val REPLY_PATH = "/diag_reply"
    val COMMANDS = setOf("status", "log", "restart_collector", "wifi_on", "wifi_off", "adb_on", "adb_keep_on", "adb_keep_off", "send_now")

    fun run(ctx: Context, cmd: String): JSONObject = when (cmd) {
        "status" -> status(ctx)
        "log" -> JSONObject().put("ok", true).put("lines", recentLog())
        "restart_collector" -> {
            val svc = CollectorService.live
            if (svc != null) JSONObject().put("ok", true).put("result", svc.kickSensors())
            else {
                // 服務不在：試著叫起來。背景啟動前景服務可能被系統擋，擋了就照實回報。
                val r = runCatching { CollectorService.start(ctx); "started" }.getOrElse { "start_blocked: $it" }
                JSONObject().put("ok", r == "started").put("result", r)
            }
        }
        "wifi_on" -> { WifiKeeper.setOn(ctx, true); JSONObject().put("ok", true).put("keep_wifi", true) }
        "wifi_off" -> { WifiKeeper.setOn(ctx, false); JSONObject().put("ok", true).put("keep_wifi", false) }
        "adb_on" -> AdbKeeper.ensure(ctx, manual = true).let {
            JSONObject().put("ok", it == "turned_on" || it == "already_on").put("result", it)
        }
        "adb_keep_on" -> { AdbKeeper.setWanted(ctx, true); JSONObject().put("ok", true).put("adb_keep", true).put("result", AdbKeeper.ensure(ctx)) }
        "send_now" -> {
            val r = CollectorService.live?.sendNow() ?: "collector_not_running"
            JSONObject().put("ok", r == "sent").put("result", r)
        }
        "adb_keep_off" -> { AdbKeeper.setWanted(ctx, false); JSONObject().put("ok", true).put("adb_keep", false) }
        else -> JSONObject().put("ok", false).put("error", "unknown_cmd")
    }

    private fun status(ctx: Context): JSONObject {
        val pi = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
        val j = JSONObject().put("ok", true)
            .put("version_code", pi.longVersionCode).put("version_name", pi.versionName)
            .put("uptime_min", SystemClock.elapsedRealtime() / 60_000)
        // Wi-Fi 與 adb：這兩個決定電腦能不能直接 adb 連進來。
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        var wifiIp: String? = null
        runCatching {
            for (n in cm.allNetworks) {
                val caps = cm.getNetworkCapabilities(n) ?: continue
                if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) continue
                wifiIp = cm.getLinkProperties(n)?.linkAddresses
                    ?.firstOrNull { it.address is java.net.Inet4Address }?.address?.hostAddress
            }
        }
        j.put("wifi_ip", wifiIp ?: JSONObject.NULL)
            .put("keep_wifi_holding", WifiKeeper.holding)
            .put("keep_wifi_remembered", WifiKeeper.remembered(ctx))
            .put("wifi_radio_on", runCatching { ctx.getSystemService(android.net.wifi.WifiManager::class.java).isWifiEnabled }.getOrNull() ?: JSONObject.NULL)
            .put("keep_wifi_got_age_s", if (WifiKeeper.gotAt > 0) (System.currentTimeMillis() - WifiKeeper.gotAt) / 1000 else -1)
            .put("keep_wifi_lost_age_s", if (WifiKeeper.lostAt > 0) (System.currentTimeMillis() - WifiKeeper.lostAt) / 1000 else -1)
            .put("adb_keep_wanted", AdbKeeper.wanted(ctx))
            .put("adb_write_permission", AdbKeeper.canWrite(ctx))
            .put("adb_fixes", AdbKeeper.fixes)
            .put("adb_last_fix_age_s", if (AdbKeeper.lastFix > 0) (System.currentTimeMillis() - AdbKeeper.lastFix) / 1000 else -1)
            .put("adb_wifi_enabled", runCatching { Settings.Global.getString(ctx.contentResolver, "adb_wifi_enabled") }.getOrNull() ?: JSONObject.NULL)
        // 收集服務的佩戴融合判斷
        val svc = CollectorService.live
        j.put("collector", svc?.diagSnapshot() ?: JSONObject().put("running", false))
        j.put("offbody_sensor", offbodyNow(ctx))
        return j
    }

    /** 跟 Android 直接要一筆 LOW_LATENCY_OFFBODY_DETECT（on-change 感測器註冊時會先送目前狀態）。1＝在手上、0＝離開。 */
    private fun offbodyNow(ctx: Context): JSONObject {
        val sm = ctx.getSystemService(SensorManager::class.java)
        val s = sm.getDefaultSensor(Sensor.TYPE_LOW_LATENCY_OFFBODY_DETECT, true)
            ?: sm.getDefaultSensor(Sensor.TYPE_LOW_LATENCY_OFFBODY_DETECT)
            ?: return JSONObject().put("present", false)
        val latch = CountDownLatch(1)
        var value = Float.NaN
        var ts = 0L
        val l = object : SensorEventListener {
            override fun onSensorChanged(e: SensorEvent) { value = e.values[0]; ts = e.timestamp; latch.countDown() }
            override fun onAccuracyChanged(s: Sensor?, a: Int) {}
        }
        val ht = HandlerThread("diag-offbody").apply { start() }
        val reg = sm.registerListener(l, s, SensorManager.SENSOR_DELAY_NORMAL, Handler(ht.looper))
        val got = reg && latch.await(3, TimeUnit.SECONDS)
        sm.unregisterListener(l)
        ht.quitSafely()
        val j = JSONObject().put("present", true).put("name", s.name).put("registered", reg).put("got_event", got)
        if (got) {
            j.put("value", value.toDouble())
            j.put("state", if (value >= 0.5f) "on_body" else "off_body")
            j.put("age_s", (SystemClock.elapsedRealtimeNanos() - ts) / 1_000_000_000L)
        }
        return j
    }

    /** 這個 App 自己的最近 log（App 只讀得到自己的），挑我們的標籤，最多 40 行。 */
    private fun recentLog(): JSONArray {
        val arr = JSONArray()
        runCatching {
            val p = ProcessBuilder("logcat", "-d", "-t", "600", "-v", "time").redirectErrorStream(true).start()
            val lines = p.inputStream.bufferedReader().readLines()
            p.waitFor(3, TimeUnit.SECONDS)
            lines.filter { l -> TAGS.any { l.contains(it) } }.takeLast(40).forEach { arr.put(it.take(240)) }
        }.onFailure { arr.put("logcat 讀不到：$it") }
        return arr
    }

    private val TAGS = listOf("WatchBridge", "WifiKeeper", "WatchDiag", "SamsungSensors", "FallWatcher")
}
