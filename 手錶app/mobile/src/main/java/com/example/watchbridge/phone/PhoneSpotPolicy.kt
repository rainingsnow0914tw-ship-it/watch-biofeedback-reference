package com.example.watchbridge.phone

import kotlin.math.abs
import kotlin.math.sqrt

/**
 * 純函式（不碰 Android），離線測試在 src/test。感測器那半在 PhoneSpot.kt。
 *
 * 現在亮屏那一刻看一眼：平放而且不動＝桌上被通知點亮；有在晃＝拿在手上。
 */
object PhoneSpotPolicy {
    enum class Spot(val zh: String) {
        POCKET("口袋／包包"), TABLE("桌上"), TABLE_DOWN("桌上（螢幕朝下）"), HAND("拿在手上"),
        EAR("貼耳講電話"), PROPPED("立著放／靠著"), UNKNOWN("不知道"),
    }

    /** 一次 2.5 秒取樣的結果。near／lux 可以是 null（那顆感測器沒回話或手機沒有）。 */
    data class Reading(
        val near: Boolean?,     // 距離感測器：有東西貼著（口袋、臉、桌面朝下）
        val lux: Float?,        // 環境光
        val gx: Float, val gy: Float, val gz: Float,   // 取樣期間加速度平均（不動時＝重力方向）
        val accStd: Float,      // 取樣期間加速度大小的標準差：有在晃就大
        val n: Int,             // 加速度樣本數；0＝沒量到
        val inCall: Boolean,    // 在通話（AudioManager mode）
        val screenOn: Boolean,
    )

    const val STILL_STD = 0.15f      // m/s²：放桌上幾乎是 0.02～0.05；手拿著就算很穩也 > 0.2
    const val FLAT_COS = 0.966f      // 傾斜 < 15° 算平放
    const val DARK_LUX = 10f         // 口袋／包包裡通常 < 5

    data class Result(val spot: Spot, val why: String, val tiltDeg: Int?)

    fun classify(r: Reading): Result {
        val g = sqrt(r.gx * r.gx + r.gy * r.gy + r.gz * r.gz)
        if (r.n == 0 || g < 3f) return Result(Spot.UNKNOWN, "沒量到加速度", null)
        val cz = r.gz / g                                   // 1＝螢幕朝上平放；-1＝螢幕朝下
        val tilt = Math.toDegrees(kotlin.math.acos(abs(cz).coerceIn(0f, 1f).toDouble())).toInt()
        val still = r.accStd < STILL_STD
        val flat = abs(cz) >= FLAT_COS
        val dark = r.lux != null && r.lux < DARK_LUX

        if (r.inCall && r.near == true) return Result(Spot.EAR, "通話中＋有東西貼著", tilt)
        if (flat && cz < 0 && still) return Result(Spot.TABLE_DOWN, "平放、螢幕朝下、不動", tilt)
        if (r.near == true && (dark || r.lux == null)) return Result(Spot.POCKET, "貼著東西＋暗", tilt)
        if (flat && still) return Result(Spot.TABLE, "平放、螢幕朝上、不動", tilt)
        if (!still) return Result(Spot.HAND, "在晃（${"%.2f".format(r.accStd)}）", tilt)
        if (r.screenOn) return Result(Spot.HAND, "螢幕亮、斜著、很穩（手拿著或手機架）", tilt)
        if (dark) return Result(Spot.POCKET, "斜著不動＋暗（沒距離感測時的猜法）", tilt)
        return Result(Spot.PROPPED, "斜著、不動、螢幕暗（立在架上／靠著）", tilt)
    }
}
