package com.example.watchbridge

import android.content.Context
import android.os.VibrationEffect
import android.os.Vibrator
import android.util.Log

/**
 * 震動 —— 警報唯一的輸出通道。
 *
 * ## 為什麼獨立成一支
 *
 *
 * 一、**手錶 manifest 從來沒宣告 VIBRATE**，而程式一直在呼叫 Vibrator，
 *    呼叫包在 `runCatching` 裡 —— **安靜地失敗**。
 *    沒確認手腕有沒有感覺到，所以完全沒發現。
 *
 * 二、**我的測試方法本身是錯的。** 用 `am broadcast` 測，那條路繞過了畫面，
 *    改成真的點畫面上的按鈕，系統才記到 `Vibration for uid=10170 ... FINISHED`。
 *
 *
 * 抽成獨立一支，是為了讓它**可以被單獨觸發、單獨驗證**，不必經過任何畫面。
 *
 *
 *
 *
 * 為什麼是這個節奏而不是平均分佈：
 *   · 它刻意跟任何日常提示音都不像 —— 不會被當成一般通知忽略
 *
 *    脈衝長度與間隔的實際毫秒數，公開資料查不到（標準本身要付費買），
 *    而且觸覺的知覺門檻跟聽覺不一樣。
 *    所以下面的數字**是照那個節奏結構推的，不是照標準抄的**。
 */
object Buzzer {

    const val ACTION = "com.example.watchbridge.VIBE"

    /** 警報一律用最大強度。這不是提示音，是要把人叫起來。 */
    private const val MAX = 255

    /**
     * 可以試的節奏。每一組是（時間陣列，強度陣列）。
     * 時間是 [停, 震, 停, 震, …]，強度對應每一段（0 = 停）。
     */
    val PATTERNS: Map<String, Pair<LongArray, IntArray>> = mapOf(

        // ── ★ T-3：國際火警疏散訊號 ★ ───────────────────────
        //
        //
        // 規格（ISO 8201 / NFPA 72 / ANSI S3.41）：
        //     0.5 秒響　0.5 秒停　×3　→　停 1.5 秒　→　重複
        //
        // 為什麼一定要用這個而不是自己編：
        //   · 這個節奏在法規上**專屬於火警疏散**，不准拿來當別的提示音 ——
        //     所以沒有人會把它誤認成一般通知
        //   · 全世界的人從小被訓練成聽到它就要動作
        //
        // 這裡放三個循環（約 13 秒）。真正的火警規定至少響 180 秒，
        // 但那是階梯層要決定的事，不是這一支。
        "t3" to (
            longArrayOf(
                0,
                500, 500, 500, 500, 500,   // 嘟 嘟 嘟
                1500,                       // 長停
                500, 500, 500, 500, 500,
                1500,
                500, 500, 500, 500, 500,
                1500,
            ) to intArrayOf(
                0,
                MAX, 0, MAX, 0, MAX,
                0,
                MAX, 0, MAX, 0, MAX,
                0,
                MAX, 0, MAX, 0, MAX,
                0,
            )
        ),

        // ── 基準：一下很長的 ─────────────────────────────────
        "long" to (longArrayOf(0, 2000) to intArrayOf(0, MAX)),

        // 噠噠噠（停）噠噠　　（長停）　噠噠噠（停）噠噠
        "medical_high" to (
            longArrayOf(
                0,
                200, 100, 200, 100, 200,   // 噠噠噠
                350,                        // 組內停頓
                200, 100, 200,              // 噠噠
                800,                        // 兩組之間
                200, 100, 200, 100, 200,
                350,
                200, 100, 200,
            ) to intArrayOf(
                0,
                MAX, 0, MAX, 0, MAX,
                0,
                MAX, 0, MAX,
                0,
                MAX, 0, MAX, 0, MAX,
                0,
                MAX, 0, MAX,
            )
        ),

        // ── 救護車那種：長—短交替、越來越急 ────────────────
        "siren" to (
            longArrayOf(
                0,
                900, 200, 600, 200, 400, 150, 300, 150, 250, 150, 250, 150, 250,
            ) to intArrayOf(
                0,
                MAX, 0, MAX, 0, MAX, 0, MAX, 0, MAX, 0, MAX, 0, MAX,
            )
        ),

        // ── 三下，長而分明 ───────────────────────────────────
        "triple_long" to (
            longArrayOf(0, 700, 400, 700, 400, 700) to
                intArrayOf(0, MAX, 0, MAX, 0, MAX)
        ),

        //
        // 特徵是**急促的短連發**，不是一下長的。
        //
        "gentle" to (
            longArrayOf(
                0,
                120, 80, 120, 80, 120, 80, 120,       // 嘟嘟嘟嘟（4）
                600,                                   // 暫停一下
                120, 80, 120, 80, 120, 80, 120, 80, 120,  // 嘟嘟嘟嘟嘟（5）
                600,
                120, 80, 120, 80, 120, 80, 120,
                600,
                120, 80, 120, 80, 120, 80, 120, 80, 120,
            ) to intArrayOf(
                0,
                MAX, 0, MAX, 0, MAX, 0, MAX,
                0,
                MAX, 0, MAX, 0, MAX, 0, MAX, 0, MAX,
                0,
                MAX, 0, MAX, 0, MAX, 0, MAX,
                0,
                MAX, 0, MAX, 0, MAX, 0, MAX, 0, MAX,
            )
        ),

        // 同上但每一下更長 —— 如果 120 毫秒還是太輕微就試這個
        "gentle_strong" to (
            longArrayOf(
                0,
                250, 120, 250, 120, 250, 120, 250,
                700,
                250, 120, 250, 120, 250, 120, 250, 120, 250,
                700,
                250, 120, 250, 120, 250, 120, 250,
            ) to intArrayOf(
                0,
                MAX, 0, MAX, 0, MAX, 0, MAX,
                0,
                MAX, 0, MAX, 0, MAX, 0, MAX, 0, MAX,
                0,
                MAX, 0, MAX, 0, MAX, 0, MAX,
            )
        ),

        // ── 對照組：原本記一筆用的那個短震 ───────────────────
        "tick" to (longArrayOf(0, 60) to intArrayOf(0, VibrationEffect.DEFAULT_AMPLITUDE)),
    )

    /** 震一個節奏。回傳有沒有真的送出去。 */
    fun buzz(ctx: Context, pattern: String): Boolean {
        val (timings, amps) = PATTERNS[pattern] ?: PATTERNS["medical_high"]!!
        return runCatching {
            val v = ctx.getSystemService(Vibrator::class.java)
            if (!v.hasVibrator()) {
                Log.e(TAG, "這台裝置沒有震動器"); return false
            }
            // ⚠ 有沒有強度控制要問裝置，不要假設。
            //   沒有的話 createWaveform 帶 amplitudes 會被忽略或丟例外，
            //   那就退回只帶時間的版本（系統會用預設強度）。
            val effect = if (v.hasAmplitudeControl()) {
                VibrationEffect.createWaveform(timings, amps, -1)
            } else {
                Log.w(TAG, "這台錶沒有強度控制，只能用預設強度")
                VibrationEffect.createWaveform(timings, -1)
            }
            //
            //   不帶的話系統把它歸成 **TOUCH（點按回饋）** ——
            //
            //   實測日誌原文：`VibrationAttributes{mUsage=TOUCH}`。
            //   振幅已經是 255（硬體支援 AMPLITUDE_CONTROL、預設就 255），
            //   卡住的是**類別**不是數值。
            //
            //   USAGE_ALARM 另外兩個好處：
            //     · 走鬧鐘的強度設定（通常比觸覺回饋高一級）
            //     · 勿擾模式底下不會被靜音（鬧鐘本來就該吵醒人）
            val attrs = android.os.VibrationAttributes.Builder()
                .setUsage(android.os.VibrationAttributes.USAGE_ALARM)
                .build()
            v.vibrate(effect, attrs)
            Log.i(TAG, "震動 $pattern（${timings.size} 段，USAGE_ALARM，強度控制=${v.hasAmplitudeControl()}）")
            true
        }.onFailure { Log.e(TAG, "震不出來（$pattern）", it) }.getOrDefault(false)
    }

    fun stop(ctx: Context) {
        runCatching { ctx.getSystemService(Vibrator::class.java).cancel() }
    }

    const val TAG = "WatchBridge"
}
