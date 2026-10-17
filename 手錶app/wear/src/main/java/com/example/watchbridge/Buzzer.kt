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
 * 2026-08-17 凌晨的測試發現三件事，每一件都會讓警報變成啞的：
 *
 * 一、**手錶 manifest 從來沒宣告 VIBRATE**，而程式一直在呼叫 Vibrator，
 *    呼叫包在 `runCatching` 裡 —— **安靜地失敗**。
 *    我前一天「驗證」記一筆時只確認資料有寫進檔案，
 *    沒確認手腕有沒有感覺到，所以完全沒發現。
 *
 * 二、**我的測試方法本身是錯的。** 用 `am broadcast` 測，那條路繞過了畫面，
 *    而震動是畫面那層呼叫的 —— 所以「測過了」是假的。
 *    改成真的點畫面上的按鈕，系統才記到 `Vibration for uid=10170 ... FINISHED`。
 *
 * 三、**強度用了預設值。** 使用者 的回饋是「太輕微了，很容易被忽略」。
 *    `DEFAULT_AMPLITUDE` 是給「有東西來了」用的觸覺提示，
 *    不是給「妳現在必須醒過來」用的。警報一律用最大強度。
 *
 * 抽成獨立一支，是為了讓它**可以被單獨觸發、單獨驗證**，不必經過任何畫面。
 *
 * ## 節奏參考的是醫療警報標準，不是我自己編的
 *
 * 使用者 說：「你要不要去參考一下救護車或是警笛的那種頻率啊？」
 * 她的直覺是對的，而且有更貼切的東西：**IEC 60601-1-8**（醫療電氣設備的警報標準）。
 *
 * 高優先級警報的規格是：**10 個等長脈衝，節奏 3-2-3-2，之後接一段較長的間隔**。
 * 也就是「噠噠噠 噠噠　噠噠噠 噠噠」重複。
 *
 * 為什麼是這個節奏而不是平均分佈：
 *   · **不對稱的節奏在知覺上比等間隔更「急」**，而且數得出來
 *   · 它刻意跟任何日常提示音都不像 —— 不會被當成一般通知忽略
 *   · 使用者 第一版的回饋正是「我分不清楚是兩下還是三下」，
 *     3-2 的分組就是為了解決這個
 *
 * ⚠️ **但那個標準是給「聲音」訂的，不是給震動。**
 *    脈衝長度與間隔的實際毫秒數，公開資料查不到（標準本身要付費買），
 *    而且觸覺的知覺門檻跟聽覺不一樣。
 *    所以下面的數字**是照那個節奏結構推的，不是照標準抄的**。
 *    **最後由 使用者 的手決定，不由我決定。**
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
        // 使用者 說「我們要用那個火警的聲音，這樣才會有人發現，
        // 如果只是一般的鈴聲一般人都不覺得有什麼」。
        // 而她前一句自己描述的節奏「嘟嘟嘟 → 停 → 嘟嘟嘟 → 停」，
        // **跟這個標準一模一樣**。她是憑直覺講出來的。
        //
        // 規格（ISO 8201 / NFPA 72 / ANSI S3.41）：
        //     0.5 秒響　0.5 秒停　×3　→　停 1.5 秒　→　重複
        //
        // 為什麼一定要用這個而不是自己編：
        //   · 這個節奏在法規上**專屬於火警疏散**，不准拿來當別的提示音 ——
        //     所以沒有人會把它誤認成一般通知
        //   · 全世界的人從小被訓練成聽到它就要動作
        //   · 「三下 + 長停」的分組讓人數得出來，不會像 使用者 抱怨的
        //     「我分不清楚是兩下還是三下」
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
        // 使用者 說「我們來個長震吧」。這一組是用來確認
        //「最大強度到底有多強」的基準，不是最終節奏。
        "long" to (longArrayOf(0, 2000) to intArrayOf(0, MAX)),

        // ── IEC 60601-1-8 高優先級：3-2-3-2 ──────────────────
        // 噠噠噠（停）噠噠　　（長停）　噠噠噠（停）噠噠
        // 這是醫療設備「立即危及生命」等級的節奏。
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
        // 使用者 提的方向。它的特徵是**變化**而不是重複 ——
        // 人對「一直在變的東西」比對「規律的東西」更難忽略。
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
        // 使用者 定的「震三下」規則，但把每一下拉長到數得出來。
        // 第一版 60 毫秒 × 3 的回饋是「分不清楚是兩下還是三下」。
        "triple_long" to (
            longArrayOf(0, 700, 400, 700, 400, 700) to
                intArrayOf(0, MAX, 0, MAX, 0, MAX)
        ),

        // ── ★ 使用者 自己描述的那個 ★ ────────────────────────
        // 她的原話：「嘟嘟嘟嘟　然後暫停一下　又嘟嘟嘟嘟嘟　又暫停一下」
        //
        // 特徵是**急促的短連發**，不是一下長的。
        // 「嘟」很短、間隔更短，所以聽起來（摸起來）像機關槍而不是像敲門。
        // 這跟 IEC 60601-1-8 的 3-2 分組是同一個道理：
        // **不規則的分組讓人數得出來、而且像「有事」不像「有通知」。**
        //
        // 4 下 → 停 → 5 下 → 停，照她講的來。
        "chloe" to (
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
        "chloe_strong" to (
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
        // 留著是為了讓「太輕微」這件事可以被直接比較出來。
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
            // ⚠ 2026-08-17：一定要帶 USAGE_ALARM。
            //
            //   不帶的話系統把它歸成 **TOUCH（點按回饋）** ——
            //   那是所有震動類別裡強度最低的一級，設計用途是「按鈕有反應」，
            //   不是「妳現在必須醒過來」。
            //
            //   實測日誌原文：`VibrationAttributes{mUsage=TOUCH}`。
            //   使用者 的回饋「還好吧，也不是非常大」就是這麼來的 ——
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
