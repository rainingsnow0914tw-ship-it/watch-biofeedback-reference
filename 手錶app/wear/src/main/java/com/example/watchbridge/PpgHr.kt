package com.example.watchbridge

import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 *
 * 權限、重掛、重開機都試過。所以心率算法那層壞了，我們就自己從原始波形算。
 *
 * 做法（純函式，不碰 Android，離線測試在 wear/src/test PpgHrTest）：
 *   1. 依時間戳重取樣成固定 25 Hz（三星的時間戳會抖，批次送達）
 *   2. 高通：減掉 1.5 秒的移動平均（拿掉呼吸、手動造成的基線漂移與巨大 DC）
 *   3. 低通：5 點移動平均做兩次（拿掉高頻雜訊）
 *   4. 自相關找 40～200 bpm 範圍內最強的週期；
 *      若一半的延遲（＝兩倍心率）那裡也有夠強的峰，取它（避免把每兩拍當一拍、算成一半）
 *   5. 拋物線內插求小數延遲 → bpm；自相關值就是品質（0～1），低於 MIN_QUALITY 不算數
 *
 */
object PpgHr {
    const val FS = 25.0
    const val MIN_BPM = 40.0
    const val MAX_BPM = 200.0
    const val MIN_SECONDS = 10.0
    const val MIN_QUALITY = 0.5

    data class Est(val bpm: Double, val quality: Double, val seconds: Double, val n: Int) {
        val ok: Boolean get() = quality >= MIN_QUALITY && bpm in MIN_BPM..MAX_BPM
    }

    /** t＝毫秒時間戳（遞增），v＝綠光原始值。資料不夠回 null；算得出來但品質差回 Est(ok=false)。 */
    fun estimate(t: LongArray, v: DoubleArray): Est? {
        if (t.size != v.size || t.size < 50) return null
        // 批次送達會讓時間戳倒退：照時間排序，只丟完全同一毫秒的重複點。
        val order = t.indices.sortedBy { t[it] }
        val tt = ArrayList<Long>(t.size); val vv = ArrayList<Double>(v.size)
        for (i in order) if (tt.isEmpty() || t[i] > tt.last()) { tt += t[i]; vv += v[i] }
        if (tt.size < 50) return null
        val seconds = (tt.last() - tt.first()) / 1000.0
        if (seconds < MIN_SECONDS) return null

        val n = floor(seconds * FS).toInt() + 1
        val x = DoubleArray(n)
        var j = 0
        for (i in 0 until n) {
            val ti = tt[0] + i * 1000.0 / FS
            while (j < tt.size - 2 && tt[j + 1] < ti) j++
            val t0 = tt[j].toDouble(); val t1 = tt[j + 1].toDouble()
            val f = if (t1 > t0) ((ti - t0) / (t1 - t0)).coerceIn(0.0, 1.0) else 0.0
            x[i] = vv[j] + (vv[j + 1] - vv[j]) * f
        }
        // code104：兩種前處理都算，取品質高的那個。
        //    移動平均高通擋不住，自相關被拉到 40 bpm 邊界（44／44／49，答案是 86／87）。
        //    差分本身就是高通，跳點變成單一尖刺再被削掉 → 真實資料 3/3 對（87.0 vs 86、83.6 vs 83、90.3 vs 87）。
        val d = DoubleArray(n) { if (it == 0) 0.0 else x[it] - x[it - 1] }
        val mad = d.map { kotlin.math.abs(it) }.sorted()[n / 2].takeIf { it > 0 } ?: 1.0
        val ya = movingAvg(movingAvg(DoubleArray(n) { d[it].coerceIn(-6 * mad, 6 * mad) }, 3), 3)
        val w = (1.5 * FS).roundToInt()
        val b1 = movingAvg(x, w)
        val h1 = DoubleArray(n) { x[it] - b1[it] }
        val b2 = movingAvg(h1, w)
        val yb = movingAvg(movingAvg(DoubleArray(n) { h1[it] - b2[it] }, 5), 5)
        val ea = fromSignal(ya, seconds, tt.size) ?: return null
        val eb = fromSignal(yb, seconds, tt.size) ?: return ea
        return if (eb.quality > ea.quality) eb else ea
    }

    /** 一條前處理過的訊號 → 自相關找週期。訊號太短回 null。 */
    private fun fromSignal(y: DoubleArray, seconds: Double, count: Int): Est? {
        val n = y.size
        val minLag = ceil(60.0 * FS / MAX_BPM).toInt()
        val maxLag = floor(60.0 * FS / MIN_BPM).toInt()
        if (maxLag + 2 >= n / 2) return null
        val r = DoubleArray(maxLag + 2)
        for (k in minLag - 1..maxLag + 1) r[k] = corr(y, k)

        var best = -1
        for (k in minLag..maxLag) {
            if (r[k] >= r[k - 1] && r[k] >= r[k + 1] && (best < 0 || r[k] > r[best])) best = k
        }
        if (best < 0) return Est(0.0, 0.0, seconds, count)
        // 倍數陷阱：週期 P 的訊號在 2P、3P、4P 的自相關一樣高（測試抓到：150 bpm 被算成 50）。
        var k = best
        for (c in minLag..best) {
            if (r[c] >= r[c - 1] && r[c] >= r[c + 1] && r[c] >= 0.85 * r[best]) { k = c; break }
        }
        val denom = r[k - 1] - 2 * r[k] + r[k + 1]
        val shift = if (denom != 0.0) (0.5 * (r[k - 1] - r[k + 1]) / denom).coerceIn(-0.5, 0.5) else 0.0
        val lag = k + shift
        return Est(60.0 * FS / lag, r[k].coerceIn(0.0, 1.0), seconds, count)
    }

    private fun movingAvg(x: DoubleArray, w: Int): DoubleArray {
        val n = x.size; val h = w / 2
        val pre = DoubleArray(n + 1)
        for (i in 0 until n) pre[i + 1] = pre[i] + x[i]
        return DoubleArray(n) { i ->
            val a = maxOf(0, i - h); val b = minOf(n, i + h + 1)
            (pre[b] - pre[a]) / (b - a)
        }
    }

    /** 正規化自相關（只算重疊段），在 -1～1。 */
    private fun corr(y: DoubleArray, k: Int): Double {
        var s = 0.0; var a = 0.0; var b = 0.0
        for (i in 0 until y.size - k) { s += y[i] * y[i + k]; a += y[i] * y[i]; b += y[i + k] * y[i + k] }
        val d = sqrt(a * b)
        return if (d > 0) s / d else 0.0
    }
}

/** 什麼時候開 PPG 量一段（純函式，有測試）。耗電：每 10 分鐘最多 30 秒＝綠光工作週期 5%。 */
object PpgHrPolicy {
    /**
     * 🔴 code104 起預設關（備援和 adb 手動都關）。
     *   開關 5 次崩 2 次。驅動一崩，所有感測器（動作、心率、皮溫）一起停，比心率掉了還糟。
     * 算法本身是好的（真實綠光 3/3 在系統心率 ±5 內），先留著。重開前要先找到不會弄崩驅動的用法
     *   （例如不要頻繁開關、或改走 PPG_ON_DEMAND），而且要有人盯著驅動崩潰紀錄。
     */
    const val ENABLED = false

    /** 系統心率斷多久才啟動備援 */
    const val DEAD_AFTER_MS = 10 * 60_000L
    /** 兩次量測至少隔多久 */
    const val EVERY_MS = 10 * 60_000L
    const val WINDOW_S = 30
    /** code102：adb 手動量一次（am broadcast -a 這個 -p com.example.watchbridge） */
    const val ACTION_NOW = "com.example.watchbridge.PPG_NOW"

    fun shouldTry(pulseAgeMs: Long, sinceLastTryMs: Long, charging: Boolean, confidentlyOff: Boolean, supported: Boolean): Boolean =
        ENABLED && supported && !charging && !confidentlyOff && pulseAgeMs >= DEAD_AFTER_MS && sinceLastTryMs >= EVERY_MS
}
