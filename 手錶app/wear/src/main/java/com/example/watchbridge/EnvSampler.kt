package com.example.watchbridge

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.util.Log

/**
 *
 * 兩顆都是手錶本來就開著的感測器（氣壓計給海拔、光感給自動亮度），我們只是多一個聽眾。
 *
 *   不帶的話人一靜 CPU 就睡，事件被節流，最需要資料的安靜時段反而收不到。
 *   帶了之後感測器晶片自己攢一分鐘才叫醒 CPU 一次，比不帶更省電。
 *
 * 氣壓：1 Hz 連續。一層樓約 0.35～0.4 hPa（3 公尺），天氣一小時變不到 1 hPa，所以
 * 光：on-change 感測器，亮度沒變就不會有事件。這裡記這一分鐘最後一筆／最小／最大，
 */
class EnvSampler(ctx: Context) : SensorEventListener {

    data class Window(
        val hpaMed: Double, val hpaMin: Double, val hpaMax: Double, val hpaN: Int,
        val lux: Double, val luxMin: Double, val luxMax: Double, val luxN: Int,
    )

    private val sm = ctx.getSystemService(SensorManager::class.java)
    private var baro: Sensor? = null
    private var light: Sensor? = null
    private var onWindow: ((Window) -> Unit)? = null

    private var windowStart = 0L
    private val hpa = ArrayList<Double>(90)
    private var luxLast = -1.0; private var luxMin = Double.MAX_VALUE; private var luxMax = -1.0; private var luxN = 0

    fun start(onWindow: (Window) -> Unit): Boolean {
        this.onWindow = onWindow
        baro = sm.getDefaultSensor(Sensor.TYPE_PRESSURE)
        light = sm.getDefaultSensor(Sensor.TYPE_LIGHT)
        val batchUs = 60 * 1_000_000
        val okB = baro?.let { sm.registerListener(this, it, 1_000_000, batchUs) } ?: false
        val okL = light?.let { sm.registerListener(this, it, 1_000_000, batchUs) } ?: false
        Log.i(TAG, "環境：氣壓=${if (okB) "開" else "沒有／失敗"} 光=${if (okL) "開" else "沒有／失敗"}")
        return okB || okL
    }

    fun stop() {
        runCatching { sm.unregisterListener(this) }
        onWindow = null
    }

    override fun onSensorChanged(e: SensorEvent) {
        val tMs = e.timestamp / 1_000_000
        if (windowStart == 0L) windowStart = tMs
        when (e.sensor.type) {
            Sensor.TYPE_PRESSURE -> hpa.add(e.values[0].toDouble())
            Sensor.TYPE_LIGHT -> {
                val v = e.values[0].toDouble()
                luxLast = v; luxN++
                if (v < luxMin) luxMin = v
                if (v > luxMax) luxMax = v
            }
        }
        if (tMs - windowStart < 60_000) return
        val sorted = hpa.sorted()
        val w = Window(
            hpaMed = if (sorted.isEmpty()) -1.0 else sorted[sorted.size / 2],
            hpaMin = sorted.firstOrNull() ?: -1.0, hpaMax = sorted.lastOrNull() ?: -1.0, hpaN = sorted.size,
            lux = luxLast, luxMin = if (luxN > 0) luxMin else luxLast, luxMax = if (luxN > 0) luxMax else luxLast, luxN = luxN,
        )
        windowStart = tMs
        hpa.clear(); luxMin = Double.MAX_VALUE; luxMax = -1.0; luxN = 0
        onWindow?.invoke(w)
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    companion object { const val TAG = "WatchBridge" }
}
