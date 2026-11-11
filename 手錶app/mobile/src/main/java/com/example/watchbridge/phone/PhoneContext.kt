package com.example.watchbridge.phone

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.app.PendingIntent
import com.google.android.gms.location.ActivityRecognition
import com.google.android.gms.location.ActivityTransition
import com.google.android.gms.location.ActivityTransitionRequest
import com.google.android.gms.location.DetectedActivity
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 *
 *   ① 在家／出門 —— 手機連著家裡 Wi-Fi（自己的 IPv4 落在家裡那段）就是在家。
 *      所以直接找 **Wi-Fi 傳輸層**那條網路的位址（VPN 疊在上面不影響）。
 *
 * 每一次**狀態改變**才寫一行到 filesDir/手機情境/<日期>.jsonl（append-only）；
 * 另外搭看門狗的 15 分鐘鬧鐘補一筆 tick（電量的時間線靠這個）。
 *
 *    事件 kind=activity, state=still/walking/running/in_vehicle/on_bicycle；每一行現況也帶 activity 欄。
 *
 */
object PhoneContext {
    private const val TAG = "WatchBridgePhone"
    const val DIR = "手機情境"
    private const val PREF = "context"
    private const val DEFAULT_HOME_PREFIX = "192.168.0."

    @Volatile private var started = false
    @Volatile private var wifiIp: String? = null     // Wi-Fi 那條網路目前的 IPv4（沒連就 null）
    @Volatile private var lastHome: Boolean? = null
    fun awayNow(): Boolean = lastHome == false
    @Volatile private var lastScreen: Boolean? = null
    @Volatile private var lastCharging: Boolean? = null
    @Volatile private var activity: String? = null
    @Volatile private var activityRegistered = false

    fun homePrefix(ctx: Context): String =
        ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).getString("home_prefix", DEFAULT_HOME_PREFIX).orEmpty()

    fun configure(ctx: Context, homePrefix: String) {
        ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).edit().putString("home_prefix", homePrefix.trim()).apply()
        record(ctx, "config", JSONObject().put("home_prefix", homePrefix.trim()))
    }

    /** BridgeServer.onCreate 叫一次。重複叫不會重複註冊。 */
    @Synchronized
    fun start(ctx: Context) {
        if (started) return
        started = true
        val app = ctx.applicationContext

        // ② 螢幕、③ 充電：系統廣播（只能動態註冊，manifest 收不到 SCREEN_ON/OFF）
        val f = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON); addAction(Intent.ACTION_SCREEN_OFF); addAction(Intent.ACTION_USER_PRESENT)
            addAction(Intent.ACTION_POWER_CONNECTED); addAction(Intent.ACTION_POWER_DISCONNECTED)
        }
        val r = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) {
                val kind = when (i.action) {
                    Intent.ACTION_SCREEN_ON -> "screen_on"
                    Intent.ACTION_SCREEN_OFF -> "screen_off"
                    Intent.ACTION_USER_PRESENT -> "unlock"
                    Intent.ACTION_POWER_CONNECTED -> "charge_on"
                    Intent.ACTION_POWER_DISCONNECTED -> "charge_off"
                    else -> return
                }
                record(app, kind)
                runCatching { PhoneSpot.onEvent(app, kind) }
            }
        }
        runCatching {
            if (Build.VERSION.SDK_INT >= 33) app.registerReceiver(r, f, Context.RECEIVER_NOT_EXPORTED)   // 系統廣播照收
            else app.registerReceiver(r, f)
        }.onFailure { Log.e(TAG, "情境：螢幕／充電廣播註冊失敗", it) }

        runCatching {
            val cm = app.getSystemService(ConnectivityManager::class.java)
            val req = NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build()
            cm.registerNetworkCallback(req, object : ConnectivityManager.NetworkCallback() {
                override fun onLinkPropertiesChanged(n: Network, lp: LinkProperties) { onWifi(app, ipv4Of(lp)) }
                override fun onAvailable(n: Network) { onWifi(app, ipv4Of(cm.getLinkProperties(n))) }
                override fun onLost(n: Network) { onWifi(app, null) }
            })
            // 開機當下的狀態（callback 對已連著的網路也會補一次 onAvailable，但先讀一次比較穩）
            wifiIp = currentWifiIp(cm)
        }.onFailure { Log.e(TAG, "情境：Wi-Fi 回呼註冊失敗", it) }

        lastHome = isHome(app); lastScreen = screenOn(app); lastCharging = charging(app)
        startActivity(app)   // ④ 有權限才會真的註冊
        record(app, "start")
        runCatching { PhoneSpot.start(app) }
        Log.i(TAG, "情境偵測開了：home=$lastHome screen=$lastScreen charging=$lastCharging ip=$wifiIp")
    }

    private fun onWifi(ctx: Context, ip: String?) {
        if (ip == wifiIp) return
        wifiIp = ip
        val home = isHome(ctx)
        if (home != lastHome) {
            val wasHome = lastHome
            lastHome = home
            record(ctx, if (home) "home" else "away")
            if (wasHome == true && !home) WatchLeftBehind.onLeftKnownPlace(ctx)
        } else record(ctx, "wifi")
    }

    private fun ipv4Of(lp: LinkProperties?): String? =
        lp?.linkAddresses?.map { it.address }?.firstOrNull { it is java.net.Inet4Address }?.hostAddress

    private fun currentWifiIp(cm: ConnectivityManager): String? =
        cm.allNetworks.firstOrNull { n ->
            val c = cm.getNetworkCapabilities(n) ?: return@firstOrNull false
            c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) && !c.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
        }?.let { ipv4Of(cm.getLinkProperties(it)) }

    /*
      */
    fun currentIp(): String? = wifiIp

    fun isHome(ctx: Context): Boolean {
        val ip = wifiIp ?: return false
        return homePrefix(ctx).split(",").map { it.trim() }.filter { it.isNotEmpty() }.any { ip.startsWith(it) }
    }
    fun screenOn(ctx: Context): Boolean = runCatching { ctx.getSystemService(PowerManager::class.java).isInteractive }.getOrDefault(false)

    private fun batteryIntent(ctx: Context): Intent? =
        runCatching { ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) }.getOrNull()
    fun battery(ctx: Context): Int {
        val i = batteryIntent(ctx) ?: return -1
        val level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1); val scale = i.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
        return if (level < 0 || scale <= 0) -1 else level * 100 / scale
    }
    fun charging(ctx: Context): Boolean = (batteryIntent(ctx)?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0

    /** 現況一包。GET /context/now 回這個；每一行紀錄也都帶著它（之後讀的人不用自己重建狀態）。 */
    fun snapshot(ctx: Context): JSONObject = JSONObject()
        .put("t", System.currentTimeMillis())
        .put("home", isHome(ctx))
        .put("wifi", wifiIp != null)
        .put("ip", wifiIp ?: JSONObject.NULL)
        .put("screen", screenOn(ctx))
        .put("battery", battery(ctx))
        .put("charging", charging(ctx))
        .put("activity", activity ?: JSONObject.NULL)
        .put("activity_on", activityRegistered)
        .put("spot", PhoneSpot.last.name.lowercase())
        .put("spot_age_s", if (PhoneSpot.lastAt == 0L) -1 else (System.currentTimeMillis() - PhoneSpot.lastAt) / 1000)

    /** 看門狗每 15 分鐘叫一次：補一筆 tick，順便校正（廣播偶爾漏，狀態不一致就補一筆變化）。 */
    fun tick(ctx: Context) {
        if (!started) return
        val s = screenOn(ctx); val c = charging(ctx)
        if (lastScreen != null && s != lastScreen) record(ctx, if (s) "screen_on" else "screen_off", JSONObject().put("missed", true))
        if (lastCharging != null && c != lastCharging) record(ctx, if (c) "charge_on" else "charge_off", JSONObject().put("missed", true))
        runCatching { ctx.getSystemService(ConnectivityManager::class.java) }.getOrNull()?.let { onWifi(ctx, currentWifiIp(it)) }
        if (!activityRegistered) startActivity(ctx)
        record(ctx, "tick")
        runCatching { PhoneSpot.onEvent(ctx, "tick") }
    }

    // ── ④ 活動辨識 ────────────────────────────────────────────
    fun hasActivityPermission(ctx: Context): Boolean =
        ctx.checkSelfPermission(android.Manifest.permission.ACTIVITY_RECOGNITION) == PackageManager.PERMISSION_GRANTED

    fun activityName(type: Int): String = when (type) {
        DetectedActivity.STILL -> "still"
        DetectedActivity.WALKING -> "walking"
        DetectedActivity.RUNNING -> "running"
        DetectedActivity.ON_FOOT -> "on_foot"
        DetectedActivity.IN_VEHICLE -> "in_vehicle"
        DetectedActivity.ON_BICYCLE -> "on_bicycle"
        else -> "unknown"
    }

    @Synchronized
    fun startActivity(ctx: Context): Boolean {
        if (activityRegistered) return true
        if (!hasActivityPermission(ctx)) return false
        val app = ctx.applicationContext
        return runCatching {
            val types = listOf(DetectedActivity.STILL, DetectedActivity.WALKING, DetectedActivity.RUNNING,
                               DetectedActivity.IN_VEHICLE, DetectedActivity.ON_BICYCLE)
            val transitions = types.flatMap { t ->
                listOf(ActivityTransition.ACTIVITY_TRANSITION_ENTER, ActivityTransition.ACTIVITY_TRANSITION_EXIT).map { tr ->
                    ActivityTransition.Builder().setActivityType(t).setActivityTransition(tr).build()
                }
            }
            val pi = PendingIntent.getBroadcast(
                app, 14, Intent(app, ActivityReceiver::class.java).setAction("com.example.watchbridge.ACTIVITY"),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,   // Play 服務要往裡面塞結果 → 必須 MUTABLE
            )
            ActivityRecognition.getClient(app)
                .requestActivityTransitionUpdates(ActivityTransitionRequest(transitions), pi)
                .addOnSuccessListener { Log.i(TAG, "活動辨識登記成功"); record(app, "activity_on") }
                .addOnFailureListener { Log.w(TAG, "活動辨識登記失敗", it); activityRegistered = false }
            activityRegistered = true
            true
        }.onFailure { Log.w(TAG, "活動辨識登記例外", it) }.getOrDefault(false)
    }

    /** ActivityReceiver 叫。只有狀態真的變了才寫一行。 */
    fun onActivity(ctx: Context, name: String, enter: Boolean) {
        val next: String? = if (enter) name else (if (activity == name) null else activity)
        if (next == activity) return
        activity = next
        record(ctx, "activity", JSONObject().put("state", next ?: JSONObject.NULL).put("transition", if (enter) "enter" else "exit").put("what", name))
        runCatching { PhoneSpot.onEvent(ctx, "activity") }
    }

    @Synchronized
    fun record(ctx: Context, kind: String, extra: JSONObject? = null) {
        runCatching {
            val snap = snapshot(ctx).put("kind", kind)
            extra?.keys()?.forEach { k -> snap.put(k, extra.get(k)) }
            lastScreen = snap.getBoolean("screen"); lastCharging = snap.getBoolean("charging")
            val day = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
            val dir = File(ctx.filesDir, DIR).apply { mkdirs() }
            File(dir, "$day.jsonl").appendText(snap.toString() + "\n", Charsets.UTF_8)
        }.onFailure { Log.w(TAG, "情境寫檔失敗：$kind", it) }
    }

    fun dayFile(ctx: Context, day: String): File = File(File(ctx.filesDir, DIR), "$day.jsonl")
}
