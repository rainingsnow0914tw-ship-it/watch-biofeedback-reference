package com.example.watchbridge

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.util.Log
import java.io.BufferedWriter
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 跌倒測試錄影機 —— 把慣性感測器的原始波形整段錄下來。
 *
 * 為什麼是「全錄」而不是「偵測到才錄」（2026-08-13 定的）：
 *     使用者 要請家人把她推倒在床上做各種角度的假摔。
 *     **那種測試不容易做第二次。**
 *     如果我先設一個門檻、只在超過門檻時才錄，而門檻設錯了，
 *     整場測試就白做 —— 而且事後補不回來。
 *     所以測試階段全部錄下來，門檻等看過真實波形再定。
 *
 * 為什麼不用三星 Health SDK 而用 Android 的 SensorManager：
 *     三星 SDK 的加速度計**只給 x/y/z 三個整數**，沒有陀螺儀、沒有重力方向。
 *     而跌倒偵測的研究共識是要三個東西一起看：
 *         ① 加速度大小  ② 角速度大小  ③ 垂直角度
 *     SensorManager 三個全有，還多了氣壓計，而且**加速度計和陀螺儀免權限**。
 *
 *     兩層並存：三星 SDK 繼續收生理資料，這一層專門做慣性。
 *
 * 為什麼挑 accelerometer_32g：
 *     一般加速度計量到 ±2g~±16g 就飽和了。三星另外裝了一顆量到 32g 的，
 *     **那顆的存在理由就是捕捉撞擊瞬間** —— 撞太大力反而測不出來是這類系統
 *     最典型的失敗方式。
 *
 * 用法（全部從電腦下指令，她只要專心摔）：
 *     adb shell am broadcast -a com.example.watchbridge.REC_START --es tag "站姿假摔"
 *     adb shell am broadcast -a com.example.watchbridge.REC_STOP
 */
class FallRecorder(private val ctx: Context) : SensorEventListener {

    private val sm = ctx.getSystemService(SensorManager::class.java)
    private var writer: BufferedWriter? = null
    private var n = 0
    private var t0Wall = 0L
    private var t0Nanos = 0L

    /**
     * 要錄哪幾種。順序就是重要性順序。
     * 32G 那顆拿不到就退回一般加速度計 —— 退化要明說，不要靜靜地少一軌。
     */
    private fun pick(): List<Pair<String, Sensor>> {
        val out = mutableListOf<Pair<String, Sensor>>()
        val all = sm.getSensorList(Sensor.TYPE_ALL)

        // 2026-08-13 實測：32G 那顆**用不了**。
        // dumpsys 顯示它要 com.samsung.permission.SSENSOR —— 那是三星簽章級權限，
        // 只有系統／三星自簽的 app 拿得到，我們申請不到。
        // 症狀很陰險：registerListener **成功**，但一筆資料都不送。
        // （所以「註冊成功」不等於「收得到」，這跟前一晚「服務活著但不收資料」同一種病。）
        //
        // 一般的 android.sensor.accelerometer 是 perm: n/a，免權限。
        // 代價是量程比較小，撞擊夠大時會飽和 —— 所以把量程記進表頭，
        // 之後看到數值頂到天花板就知道是飽和，不是真的只有那麼大。
        sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let { out += "acc" to it }

        sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.let { out += "gyro" to it }
        sm.getDefaultSensor(Sensor.TYPE_GRAVITY)?.let { out += "grav" to it }
        sm.getDefaultSensor(Sensor.TYPE_LINEAR_ACCELERATION)?.let { out += "lin" to it }
        sm.getDefaultSensor(Sensor.TYPE_PRESSURE)?.let { out += "baro" to it }
        // 「人摔」跟「錶掉在地上」的差別就在這一軌
        all.firstOrNull { it.stringType == "android.sensor.low_latency_offbody_detect" }
            ?.let { out += "onbody" to it }
        return out
    }

    private val nameOf = mutableMapOf<Sensor, String>()

    fun start(tag: String): String {
        stop()
        val dir = File(ctx.filesDir, "跌倒測試").apply { mkdirs() }
        val stamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val f = File(dir, "${stamp}_$tag.jsonl")

        val sensors = pick()
        if (sensors.isEmpty()) return "沒有可用的感測器"

        t0Wall = System.currentTimeMillis()
        t0Nanos = 0L
        n = 0
        writer = f.bufferedWriter()

        // 表頭：真實時間、開機時間、每一軌的量程與解析度。
        // 量程一定要記 —— 之後看到數值頂到量程上限，那是**飽和**，
        // 不是「撞擊只有那麼大」。沒記的話這兩件事分不出來。
        val meta = sensors.joinToString(",") { (n, s) ->
            """{"n":"$n","max":${"%.2f".format(s.maximumRange)},"res":${"%.4f".format(s.resolution)}}"""
        }
        writer?.appendLine(
            """{"header":true,"tag":"$tag","wall_ms":$t0Wall,""" +
            """"boot_ns":${android.os.SystemClock.elapsedRealtimeNanos()},""" +
            """"sensors":[$meta]}"""
        )

        for ((name, s) in sensors) {
            nameOf[s] = name
            // SENSOR_DELAY_FASTEST：測試時要最高解析度。
            // 研究說 20Hz 就夠判斷，但**錄的時候寧可多**，篩掉容易、補不回來。
            sm.registerListener(this, s, SensorManager.SENSOR_DELAY_FASTEST)
        }
        Log.i(TAG, "開始錄：${f.name}　軌道 ${sensors.map { it.first }}")
        return f.name
    }

    fun stop(): Int {
        if (writer == null) return 0
        sm.unregisterListener(this)
        runCatching { writer?.flush(); writer?.close() }
        writer = null
        nameOf.clear()
        Log.i(TAG, "停止錄影，共 $n 筆")
        return n
    }

    override fun onSensorChanged(e: SensorEvent) {
        val w = writer ?: return
        val name = nameOf[e.sensor] ?: return
        // 2026-08-13 實測：**不同感測器的時間戳基準不一樣**。
        // offbody 那顆先送了一筆，我拿它當起點，結果陀螺儀的相對時間
        // 變成 24842606 毫秒（六小時多）—— 因為兩顆用不同的時鐘。
        //
        // 跌倒偵測要把加速度和角速度**對齊到同一條時間軸**上看，
        // 對錯了整個判斷就毀了。所以這裡**存原始奈秒不做正規化**，
        // 對齊交給電腦端做（那裡看得到全部軌道，才有辦法發現基準不同）。
        val v = e.values.joinToString(",") { "%.4f".format(it) }
        runCatching {
            w.appendLine("""{"ns":${e.timestamp},"s":"$name","v":[$v]}""")
            n++
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    companion object {
        const val TAG = "WatchBridge"
        const val ACTION_START = "com.example.watchbridge.REC_START"
        const val ACTION_STOP = "com.example.watchbridge.REC_STOP"
    }
}
