package com.example.watchbridge

import android.app.Activity
import android.content.Context
import android.graphics.Color
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.widget.TextView

/**
 *
 * Wear OS 給 App 的正式做法：requestNetwork(TRANSPORT_WIFI)——有 App 說需要 Wi-Fi，系統就把它開著。
 * 點一下＝開著不關（minutes=0 沒有時限），再點一下＝放掉回省電。程序靠 CollectorService 前景服務活著，所以放在 object 裡。
 */
object WifiKeeper {
    private const val TAG = "WifiKeeper"
    private val h = Handler(Looper.getMainLooper())
    private var cb: ConnectivityManager.NetworkCallback? = null
    var until = 0L; private set
    val holding get() = cb != null
    var onWifi: ((Boolean) -> Unit)? = null

    fun hold(ctx: Context, minutes: Int) {
        val cm = ctx.applicationContext.getSystemService(ConnectivityManager::class.java)
        release(ctx)
        val c = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(n: Network) { gotAt = System.currentTimeMillis(); Log.i(TAG, "Wi-Fi 到手"); h.post { onWifi?.invoke(true) } }
            override fun onLost(n: Network) { lostAt = System.currentTimeMillis(); Log.i(TAG, "Wi-Fi 掉了"); h.post { onWifi?.invoke(false) } }
        }
        val req = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        runCatching { cm.requestNetwork(req, c) }.onFailure { Log.w(TAG, "requestNetwork 失敗：$it"); return }
        cb = c
        until = System.currentTimeMillis() + minutes * 60_000L
        h.removeCallbacksAndMessages(null)
        if (minutes > 0) h.postDelayed({ release(ctx) }, minutes * 60_000L) else until = Long.MAX_VALUE
        Log.i(TAG, if (minutes > 0) "保持 Wi-Fi $minutes 分鐘" else "保持 Wi-Fi：開著，直到再點一次")
    }

    @Volatile var gotAt = 0L
    @Volatile var lostAt = 0L

    fun pauseForBluetooth(ctx: Context, ms: Long) {
        if (!holding) return
        release(ctx)
        Log.i(TAG, "辨識要網路：暫時放掉 Wi-Fi 走藍牙，${ms / 1000} 秒後接回")
        h.postDelayed({ if (remembered(ctx) && !holding) hold(ctx, 0) }, ms)
    }

    private const val PREFS = "keep_wifi"
    fun remembered(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("on", false)
    fun setOn(ctx: Context, on: Boolean) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("on", on).apply()
        if (on) hold(ctx, 0) else release(ctx)
    }
    /** CollectorService.onCreate 呼叫：上次是開的就再開。 */
    fun restore(ctx: Context) { if (remembered(ctx) && !holding) { Log.i(TAG, "恢復上次的「保持Wi-Fi：開」"); hold(ctx, 0) } }

    fun release(ctx: Context) {
        val c = cb ?: return
        runCatching { ctx.applicationContext.getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(c) }
        cb = null; until = 0L
        Log.i(TAG, "放掉 Wi-Fi，回到省電")
    }
}

/**
 * 只碰 adb_wifi_enabled 這一個設定、只會寫 1。沒給 WRITE_SECURE_SETTINGS 就什麼都不做。
 */
object AdbKeeper {
    private const val TAG = "AdbKeeper"
    private const val PREFS = "adb_keep"
    private const val KEY = "adb_wifi_enabled"

    fun wanted(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("on", true)
    fun setWanted(ctx: Context, on: Boolean) =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("on", on).apply()
    fun canWrite(ctx: Context) =
        ctx.checkSelfPermission(android.Manifest.permission.WRITE_SECURE_SETTINGS) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED
    fun state(ctx: Context) = android.provider.Settings.Global.getInt(ctx.contentResolver, KEY, -1)
    var lastFix = 0L; private set
    var fixes = 0; private set

    private fun wifiUp(ctx: Context): Boolean {
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        return cm.allNetworks.any { cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
    }

    /** 回一句：turned_on／already_on／not_wanted／keep_wifi_off／no_permission／no_wifi／failed:… */
    fun ensure(ctx: Context, manual: Boolean = false): String {
        if (!manual && !wanted(ctx)) return "not_wanted"
        if (!manual && !WifiKeeper.holding) return "keep_wifi_off"
        if (!canWrite(ctx)) return "no_permission"
        if (state(ctx) == 1) return "already_on"
        if (!wifiUp(ctx)) return "no_wifi"
        return runCatching {
            android.provider.Settings.Global.putInt(ctx.contentResolver, KEY, 1)
            lastFix = System.currentTimeMillis(); fixes++
            Log.i(TAG, "無線偵錯被關掉了 → 寫回 1（第 $fixes 次）")
            "turned_on"
        }.getOrElse { Log.w(TAG, "寫不回去：$it"); "failed: $it" }
    }
}

class KeepWifiActivity : Activity() {
    private lateinit var msg: TextView

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        msg = TextView(this).apply {
            text = "📶 正在叫醒 Wi-Fi…"
            textSize = 17f
            setTextColor(Color.parseColor("#C9B6F2"))
            gravity = Gravity.CENTER
            setPadding(30, 30, 30, 30)
            setBackgroundColor(Color.BLACK)
        }
        setContentView(msg)
        if (WifiKeeper.holding) {
            WifiKeeper.setOn(this, false)
            msg.text = "📶 保持 Wi-Fi：關\n\n回到省電了 🌙"
            msg.postDelayed({ finish() }, 2000)
            return
        }
        WifiKeeper.onWifi = { ok ->
            msg.text = if (ok) "📶 保持 Wi-Fi：開\n\n現在可以去開「無線偵錯」\n不用關手機藍牙\n\n用完再點一次就關掉"
                       else "Wi-Fi 還沒連上\n確認錶的 Wi-Fi 有連到網路"
        }
        WifiKeeper.setOn(this, true)
    }

    override fun onDestroy() { WifiKeeper.onWifi = null; super.onDestroy() }
}
