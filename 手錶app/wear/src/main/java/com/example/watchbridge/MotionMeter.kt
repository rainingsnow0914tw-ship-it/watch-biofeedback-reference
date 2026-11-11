package com.example.watchbridge

import kotlin.math.abs
import kotlin.math.sqrt

/**
 *
 *     加速度計每秒送 25 筆以上，一天就是兩百多萬筆。
 *     那個量級存下來只是製造垃圾，之後每次分析都要重算一遍。
 *     **在源頭壓成有意義的數字，後面的邏輯才簡單。**
 *
 * 用哪個指標：
 *     手舉著不動，重力方向變了但沒有動作 → 這個指標不會被騙。
 *
 *
 *     而查到的研究數字是：健康的人睡覺時每 15–30 分鐘就會換一次姿勢，
 *     一晚 10–30 次大翻身。**完全不動超過 30–45 分鐘，即使睡覺也異常。**
 */
class MotionMeter(
    private val windowMs: Long = 60_000,
    /*
     *  所以換資料來源就要重設，見下面 stillThreshold 的說明。 */
    private val stillThreshold: Double = 30.0,
) {

    private var windowStart = 0L
    private var n = 0
    private var sumDiff = 0.0
    private var peak = 0.0
    private var lastMag = -1.0
    private var stillCount = 0
    private var sumX = 0.0; private var sumY = 0.0; private var sumZ = 0.0; private var nG = 0

    // stillThreshold 已經移到建構參數。
    //
    // 換成 SensorManager 之後單位變成毫 m/s²（重力 = 9810，已實測 9759 驗證），
    // 兩邊的 30 不是同一個 30 —— 不改的話靜止比例會整個錯掉，
    // 而且**錯得很安靜**：數字照樣印出來，只是意義變了。

    /** 一分鐘的結果。回 null 代表這一分鐘還沒滿。 */
    data class Window(
        val samples: Int,
        val activity: Double,   // 平均相鄰差 —— 主要指標
        val peak: Double,       // 這一分鐘最大的一下
        val stillRatio: Double, // 有多少比例的取樣是靜止的
        val gx: Double = 0.0,   // 平均重力方向（單位向量）。錶面朝上平放：gz ≈ +1、gx gy ≈ 0
        val gy: Double = 0.0,
        val gz: Double = 0.0,
        val tiltDeg: Double = -1.0, // 錶面相對水平的傾角：0 = 平放朝上，90 = 立著，180 = 面朝下
    )

    /**
     * 餵一筆進來。滿一分鐘就回傳結果並自動重置，否則回 null。
     *
     * 刻意不在這裡做時間判斷以外的事 —— 這支要跑在 25Hz 的回呼裡，
     * 每一筆都要便宜。複雜的分析留給電腦端。
     */
    fun feed(x: Int, y: Int, z: Int, tMs: Long): Window? {
        if (windowStart == 0L) windowStart = tMs

        val mag = sqrt((x.toDouble() * x) + (y.toDouble() * y) + (z.toDouble() * z))
        sumX += x; sumY += y; sumZ += z; nG++
        if (lastMag >= 0) {
            val d = abs(mag - lastMag)
            sumDiff += d
            if (d > peak) peak = d
            if (d < stillThreshold) stillCount++
            n++
        }
        lastMag = mag

        if (tMs - windowStart < windowMs) return null

        val mx = if (nG > 0) sumX / nG else 0.0; val my = if (nG > 0) sumY / nG else 0.0; val mz = if (nG > 0) sumZ / nG else 0.0
        val gm = sqrt(mx * mx + my * my + mz * mz)
        val (gx, gy, gz) = if (gm > 0) Triple(mx / gm, my / gm, mz / gm) else Triple(0.0, 0.0, 0.0)
        val tilt = if (gm > 0) Math.toDegrees(kotlin.math.acos(gz.coerceIn(-1.0, 1.0))) else -1.0
        val w = if (n > 0) Window(n, sumDiff / n, peak, stillCount.toDouble() / n, gx, gy, gz, tilt)
        else Window(0, 0.0, 0.0, 1.0, gx, gy, gz, tilt)

        windowStart = tMs
        n = 0; sumDiff = 0.0; peak = 0.0; stillCount = 0
        sumX = 0.0; sumY = 0.0; sumZ = 0.0; nG = 0
        return w
    }
}
