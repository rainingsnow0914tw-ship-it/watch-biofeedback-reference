package com.example.watchbridge

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.util.Log

/**
 * 用 Android 的 SensorManager 取加速度，取樣率自己指定。
 *
 * 為什麼不用三星 SDK 的 ACCELEROMETER_CONTINUOUS（2026-08-15 換掉的理由）：
 *     那個 API **沒有速率參數**，只能吃它的預設 —— 實測是 25 Hz
 *     （每分鐘 1490 個取樣）。
 *     而我們拿這 1490 個取樣，只為了算出**一個**每分鐘的動作量數字。
 *
 *     實測代價：加上加速度計之後，耗電從 3%/小時 跳到 5.5%/小時，
 *     滿電只能撐約 18 小時 —— 一天充不完一次。
 *
 * 為什麼是 5 Hz：
 *     翻個身要一到兩秒，5 Hz 會取到 5~10 個點，足夠判斷「有沒有動」。
 *     實測降到 5 Hz 之後，動作量數值變成原本的 **1.36 倍** ——
 *     那是**固定的縮放**，不影響「動 vs 不動」的分辨力。
 *
 * ⚠️ 但這代表 **5 Hz 之後的動作量，不能跟 25 Hz 時期的數值直接比。**
 *    跟 HRV 不同窗口不能比是同一件事。
 *    所以資料裡要記下取樣率（見 CollectorService 寫入的 hz 欄位），
 *    之後分析時才知道哪些能比。
 *
 * 為什麼現在換：動作量才收兩天，基線還沒養起來。再拖只會丟掉更多資料。
 */
class MotionSampler(
    private val ctx: Context,
    private val hz: Int = 5,
) : SensorEventListener {

    private val sm = ctx.getSystemService(SensorManager::class.java)
    private val meter = MotionMeter(stillThreshold = STILL_THRESHOLD)
    private var sensor: Sensor? = null
    private var onWindow: ((MotionMeter.Window, Int) -> Unit)? = null

    fun start(onWindow: (MotionMeter.Window, Int) -> Unit): Boolean {
        this.onWindow = onWindow
        sensor = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        val s = sensor ?: run {
            Log.e(TAG, "沒有加速度計"); return false
        }
        // 單位是微秒。5 Hz = 每 200,000 微秒一筆。
        // ⚠ 這是「建議值」不是保證值 —— 系統可能給得更快或更慢，
        //   所以真正的取樣數要看 Window.samples，不要假設等於 hz*60。
        val periodUs = 1_000_000 / hz

        // ⚠ 2026-08-16 修的重大 bug：本來是 `registerListener(this, s, periodUs)`，
        //   **沒有第四個參數**。
        //
        //   後果：交付綁在 CPU 醒著的時候。人一不動，手錶就讓 CPU 睡，
        //   感測器事件跟著被節流 —— 實測**安靜時每分鐘只收到 34 筆**（目標 300），
        //   87% 的分鐘不足 60 筆。而同一支錶**放在充電座上卻收滿 374 筆**（CPU 沒睡）。
        //
        //   也就是說：**最需要精度的時候，取樣最爛。**
        //   而動作量是「相鄰差的平均」，取樣率一變數值尺度就變 ——
        //   所以那些數字連互相比較都不能，等於整晚的睡眠資料是廢的。
        //
        //   這是我 2026-08-15 為了省電從三星 SDK 換過來時製造的
        //   （三星那條穩定給 1490 筆／分鐘，沒有這個問題）。
        //
        // 正解：maxReportLatencyUs。感測器晶片自己把取樣攢在硬體佇列裡，
        //   **CPU 睡著它照樣以指定速率取樣**，等攢滿或到時間才一次交付叫醒 CPU。
        //   附帶好處：CPU 醒來的次數變少，**比原本更省電**。
        //
        // ⚠ 但這要硬體有 FIFO 才成立。沒有 FIFO 的話系統會安靜地退回非批次模式 ——
        //   **裝完一定要實測 samples 有沒有回到 300 左右，不能假設它生效了。**
        val batchUs = BATCH_LATENCY_S * 1_000_000
        val ok = sm.registerListener(this, s, periodUs, batchUs)
        Log.i(TAG, if (ok) "加速度計已開，目標 $hz Hz，批次延遲 ${BATCH_LATENCY_S}s"
                   else "加速度計註冊失敗")
        return ok
    }

    fun stop() {
        runCatching { sm.unregisterListener(this) }
        onWindow = null
    }

    override fun onSensorChanged(e: SensorEvent) {
        // SensorEvent.values 是 m/s²（浮點），而 MotionMeter 收整數。
        // 乘 1000 轉成毫 m/s² —— 保留三位有效小數，同時沿用既有的整數介面。
        val w = meter.feed(
            (e.values[0] * 1000).toInt(),
            (e.values[1] * 1000).toInt(),
            (e.values[2] * 1000).toInt(),
            e.timestamp / 1_000_000,          // 奈秒 → 毫秒
        ) ?: return
        onWindow?.invoke(w, hz)
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    companion object {
        const val TAG = "WatchBridge"

        /**
         * 「靜止」的門檻，單位毫 m/s²（重力 = 9810，實測 9759）。
         *
         * 80 ≈ 重力的 0.8%。這個值是從 2026-08-15 錄的一段真實波形挑的
         * （5 Hz 降取樣後，相鄰差的第 80 百分位在 84.6）。
         *
         * ⚠️ 這是**起始值不是定案**。真正該拿來校的是**睡覺那幾個小時** ——
         *    睡著時靜止比例應該接近 1，醒著坐著應該明顯低一截。
         *    收滿一晚之後回來重設這個數字。
         */
        const val STILL_THRESHOLD = 80.0

        /**
         * 批次交付的最大延遲（秒）。
         *
         * 60 秒 = 剛好一個窗口。感測器晶片攢滿一分鐘才叫醒 CPU 一次。
         * 太長沒有好處（窗口是一分鐘），太短就失去省電與抗節流的效果。
         *
         * ⚠️ 硬體 FIFO 裝得下多少要實測。5 Hz × 60 秒 = 300 筆，
         *    一般手錶的加速度計 FIFO 有幾百到幾千筆，應該夠 ——
         *    **但「應該」不算，裝完看 samples 有沒有回到 300 左右。**
         */
        const val BATCH_LATENCY_S = 60

        /**
         * 一分鐘至少要收到這麼多筆，那一分鐘的動作量才可信。
         *
         * 150 = 目標 300 的一半。低於這個代表交付被節流了，
         * 而**取樣率一低，動作量的數值就會被灌大**（相鄰兩筆隔更久、差更大）——
         * 實測 11 Hz→5 Hz 就放大 1.36 倍，掉到 0.5 Hz 會放大幾倍不知道。
         *
         * 這種時候**寧可說「這一分鐘我不確定」，也不要給一個看起來正常的數字**。
         * 2026-08-16 的教訓：安靜時 87% 的分鐘不足 60 筆，
         * 而那些數字被我拿去校門檻、拿去判睡眠，全部是沙上建塔。
         */
        const val MIN_TRUSTWORTHY_SAMPLES = 150

        /**
         * 指標版本。換取樣率、換門檻、換交付方式就要 +1 ——
         * 不同版本的動作量**不能互相比較**。
         *
         * v1: 三星 SDK 25 Hz，三星原始單位
         * v2: SensorManager 5 Hz，毫 m/s²　**⚠ 有節流 bug，安靜時的數值不可信**
         * v3: 同上 + 硬體批次交付（2026-08-16）
         */
        const val METRIC_VERSION = 3
    }
}
