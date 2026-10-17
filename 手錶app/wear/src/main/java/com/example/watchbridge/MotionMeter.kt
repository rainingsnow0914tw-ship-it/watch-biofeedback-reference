package com.example.watchbridge

import kotlin.math.abs
import kotlin.math.sqrt

/**
 * 動作量計 —— 把加速度計的原始波形，在手錶上就壓成「每分鐘一個數字」。
 *
 * 為什麼一定要在手錶上先算（2026-08-13 使用者 問「這兩個資料能不能用」時定的）：
 *     加速度計每秒送 25 筆以上，一天就是兩百多萬筆。
 *     那個量級存下來只是製造垃圾，之後每次分析都要重算一遍。
 *     **在源頭壓成有意義的數字，後面的邏輯才簡單。**
 *
 * 用哪個指標：
 *     用「相鄰兩筆的加速度大小差了多少」的平均（mean absolute successive difference）。
 *     這是睡眠研究裡 actigraphy 的標準做法 —— 它量的是「變化」，不是「姿勢」。
 *     手舉著不動，重力方向變了但沒有動作 → 這個指標不會被騙。
 *
 * 為什麼要這個數字（使用者 的場景）：
 *     「你偵測到我怎麼躺在床上都沒動，十個小時了，懷疑我一定不正常，就先震動我」
 *
 *     而查到的研究數字是：健康的人睡覺時每 15–30 分鐘就會換一次姿勢，
 *     一晚 10–30 次大翻身。**完全不動超過 30–45 分鐘，即使睡覺也異常。**
 *     所以門檻不需要到十小時 —— 但要用她自己的基線，不是抄通用值。
 */
class MotionMeter(
    private val windowMs: Long = 60_000,
    /** 相鄰差小於這個值就算「靜止」。**單位跟餵進來的資料一致** ——
     *  所以換資料來源就要重設，見下面 stillThreshold 的說明。 */
    private val stillThreshold: Double = 30.0,
) {

    private var windowStart = 0L
    private var n = 0
    private var sumDiff = 0.0
    private var peak = 0.0
    private var lastMag = -1.0
    private var stillCount = 0

    // stillThreshold 已經移到建構參數。
    //
    // 為什麼（2026-08-15）：原本寫死 30.0，那是照**三星 SDK 的原始單位**校的。
    // 換成 SensorManager 之後單位變成毫 m/s²（重力 = 9810，已實測 9759 驗證），
    // 兩邊的 30 不是同一個 30 —— 不改的話靜止比例會整個錯掉，
    // 而且**錯得很安靜**：數字照樣印出來，只是意義變了。

    /** 一分鐘的結果。回 null 代表這一分鐘還沒滿。 */
    data class Window(
        val samples: Int,
        val activity: Double,   // 平均相鄰差 —— 主要指標
        val peak: Double,       // 這一分鐘最大的一下
        val stillRatio: Double, // 有多少比例的取樣是靜止的
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
        if (lastMag >= 0) {
            val d = abs(mag - lastMag)
            sumDiff += d
            if (d > peak) peak = d
            if (d < stillThreshold) stillCount++
            n++
        }
        lastMag = mag

        if (tMs - windowStart < windowMs) return null

        val w = if (n > 0) Window(n, sumDiff / n, peak, stillCount.toDouble() / n)
        else Window(0, 0.0, 0.0, 1.0)

        windowStart = tMs
        n = 0; sumDiff = 0.0; peak = 0.0; stillCount = 0
        return w
    }
}
