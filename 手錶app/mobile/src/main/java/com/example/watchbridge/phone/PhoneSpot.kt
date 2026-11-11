package com.example.watchbridge.phone

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.hardware.TriggerEvent
import android.hardware.TriggerEventListener
import android.media.AudioManager
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.Log
import org.json.JSONObject
import kotlin.math.sqrt

/**
 *
 *   通話開始／結束、看門狗每 15 分鐘 tick。每次開 光感＋距離＋加速度 2.5 秒，判完立刻關。
 *   距離感測常開會一直點紅外線，所以絕不常開。
 *   估算：一天亮滅屏幾百次 × 2.5 秒 ≈ 每天 10～20 分鐘的低功耗感測器時間，而且那些時刻手機本來就醒著。
 * ⑨ 撿起手勢：系統有 "android.sensor.pick_up_gesture"（one-shot、感測器晶片自己判）就掛上，觸發後記一筆＋看一眼，再重新掛。
 *   沒有這顆（或拿不到）就安靜跳過。
 *
 * PhoneContext.snapshot() 也會帶 spot 跟它幾秒前量的。
 */
object PhoneSpot {
    private const val TAG = "WatchBridgePhone"
    private const val SAMPLE_MS = 2_500L
    private const val AFTER_OFF_MS = 8_000L
    private const val MIN_GAP_MS = 4_000L      // 兩次取樣至少隔 4 秒（亮滅屏連發時不要疊）

    @Volatile var last: PhoneSpotPolicy.Spot = PhoneSpotPolicy.Spot.UNKNOWN; private set
    @Volatile var lastAt: Long = 0L; private set
    @Volatile private var busyUntil = 0L
    @Volatile var pickupOn = false; private set

    private val thread by lazy { HandlerThread("phone-spot").apply { start() } }
    private val h by lazy { Handler(thread.looper) }

    fun start(ctx: Context) {
        val app = ctx.applicationContext
        runCatching { armPickup(app) }.onFailure { Log.w(TAG, "手機在哪：撿起手勢掛不上", it) }
        runCatching {
            if (android.os.Build.VERSION.SDK_INT >= 31) {
                app.getSystemService(AudioManager::class.java).addOnModeChangedListener({ it.run() }) { mode ->
                    look(app, if (mode == AudioManager.MODE_IN_CALL || mode == AudioManager.MODE_IN_COMMUNICATION) "call_on" else "call_off")
                }
            }
        }.onFailure { Log.w(TAG, "手機在哪：通話狀態監聽掛不上", it) }
        look(app, "start")
    }

    /** PhoneContext 的事件轉進來。screen_off 等 8 秒再看（收進口袋要一點時間）。 */
    fun onEvent(ctx: Context, kind: String) {
        when (kind) {
            "screen_off" -> h.postDelayed({ look(ctx.applicationContext, "screen_off+8s") }, AFTER_OFF_MS)
            "screen_on", "unlock", "activity", "tick" -> look(ctx.applicationContext, kind)
        }
    }

    fun look(ctx: Context, trig: String) {
        val now = SystemClock.elapsedRealtime()
        if (now < busyUntil) return
        busyUntil = now + SAMPLE_MS + MIN_GAP_MS
        h.post { runCatching { sample(ctx, trig) }.onFailure { Log.w(TAG, "手機在哪：取樣失敗 $trig", it) } }
    }

    private fun sample(ctx: Context, trig: String) {
        val sm = ctx.getSystemService(SensorManager::class.java)
        val acc = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        val light = sm.getDefaultSensor(Sensor.TYPE_LIGHT)
        val prox = sm.getDefaultSensor(Sensor.TYPE_PROXIMITY)
        var lux: Float? = null; var near: Boolean? = null
        var sx = 0.0; var sy = 0.0; var sz = 0.0; var n = 0
        val mags = ArrayList<Float>(64)
        val l = object : SensorEventListener {
            override fun onSensorChanged(e: SensorEvent) {
                when (e.sensor.type) {
                    Sensor.TYPE_ACCELEROMETER -> {
                        val (x, y, z) = Triple(e.values[0], e.values[1], e.values[2])
                        sx += x; sy += y; sz += z; n += 1; mags += sqrt(x * x + y * y + z * z)
                    }
                    Sensor.TYPE_LIGHT -> lux = e.values[0]
                    Sensor.TYPE_PROXIMITY -> near = e.values[0] < (prox?.maximumRange ?: 5f).coerceAtMost(5f)
                }
            }
            override fun onAccuracyChanged(s: Sensor?, a: Int) {}
        }
        acc?.let { sm.registerListener(l, it, SensorManager.SENSOR_DELAY_NORMAL, h) }
        light?.let { sm.registerListener(l, it, SensorManager.SENSOR_DELAY_NORMAL, h) }
        prox?.let { sm.registerListener(l, it, SensorManager.SENSOR_DELAY_NORMAL, h) }
        // 2.5 秒後收：用 postDelayed 而不是 sleep，事件才進得來（listener 跑在同一個 handler 上）
        h.postDelayed({
            sm.unregisterListener(l)
            val std = if (mags.size < 2) 0f else mags.average().let { m -> sqrt(mags.sumOf { ((it - m) * (it - m)).toDouble() } / mags.size).toFloat() }
            val inCall = runCatching { ctx.getSystemService(AudioManager::class.java).mode }.getOrNull()
                .let { it == AudioManager.MODE_IN_CALL || it == AudioManager.MODE_IN_COMMUNICATION }
            val r = PhoneSpotPolicy.Reading(
                near, lux,
                if (n > 0) (sx / n).toFloat() else 0f, if (n > 0) (sy / n).toFloat() else 0f, if (n > 0) (sz / n).toFloat() else 0f,
                std, n, inCall, PhoneContext.screenOn(ctx),
            )
            val res = PhoneSpotPolicy.classify(r)
            val changed = res.spot != last
            last = res.spot; lastAt = System.currentTimeMillis()
            if (changed || trig == "screen_on" || trig == "pickup") {
                PhoneContext.record(ctx, "spot", JSONObject()
                    .put("spot", res.spot.name.lowercase()).put("spot_zh", res.spot.zh).put("why", res.why).put("trig", trig)
                    .put("lux", lux?.toDouble() ?: JSONObject.NULL).put("near", near ?: JSONObject.NULL)
                    .put("tilt", res.tiltDeg ?: JSONObject.NULL).put("std", (std * 1000).toInt() / 1000.0).put("n", n))
            }
        }, SAMPLE_MS)
    }

    // ── ⑨ 撿起手勢（系統感測器，one-shot）────────────────────
    private fun armPickup(ctx: Context) {
        val sm = ctx.getSystemService(SensorManager::class.java)
        val s = sm.getSensorList(Sensor.TYPE_ALL).firstOrNull { it.stringType == "android.sensor.pick_up_gesture" } ?: run {
            Log.i(TAG, "手機在哪：這支手機沒有開放撿起手勢感測器，跳過"); return
        }
        pickupOn = sm.requestTriggerSensor(object : TriggerEventListener() {
            override fun onTrigger(e: TriggerEvent) {
                pickupOn = false
                look(ctx, "pickup")
                h.postDelayed({ runCatching { armPickup(ctx) } }, 5_000)   // one-shot：觸發後要重掛
            }
        }, s)
        Log.i(TAG, "手機在哪：撿起手勢 ${if (pickupOn) "掛上了" else "掛不上"}")
    }
}
