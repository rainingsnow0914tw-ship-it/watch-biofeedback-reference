package com.example.watchbridge

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.util.Log
import kotlin.math.PI
import kotlin.math.sin

/**
 * 警報的**聲音**。震動那條在 Buzzer，這支是聲音。
 *
 * ## 為什麼不用手錶內建的鬧鈴 mp3
 *
 * 手錶 `/system/media/audio/alarms/` 有十個現成的檔（Beep_Beep、Digital_Beep…），
 * 直接播是最省事的做法。**但那正好是對老人最無效的那種聲音。**
 *
 * NFPA 的研究（Bruck & Thomas，多次重複驗證，已寫進 NFPA 72-2010 §18.4.5.3）：
 *
 * | 訊號 | 65 歲以上被叫醒所需音量 |
 * |---|---|
 * | 一般高音警報（3000+ Hz） | 中位數 **65 dBA** |
 * | **520 Hz 方波** | 中位數 **45 dBA** |
 *
 * 差 20 dB ＝ **十倍聲壓**。更關鍵的一組：連續播 95 dBA 三分半，
 * **15.6% 的老人整段睡過去**；換成 520 Hz 方波，**一個都沒有**。
 * 對 65 歲以上族群，低頻警報的有效性約 **六倍**。
 *
 * 原因是老年性聽力損失（presbycusis）**從高頻開始壞**。
 * 一個 3100 Hz 的警報，對一個 75 歲的人可能根本不存在。
 *
 * 所以 NFPA 72 現在明文規定：**睡眠空間的警報必須是 520 Hz ± 10% 的方波。**
 * 這支就是照那個規格自己合成，不用內建檔案。
 *
 * ## 為什麼是方波不是正弦波
 *
 * 方波在 520 Hz 之外還帶著 1560、2600、3640 Hz…的奇次諧波。
 * 好處有兩個：
 *   · **穿透力**：多個頻帶同時傳，比單一純音更難被環境噪音蓋掉
 *   · **保險**：萬一某個人正好在 520 Hz 附近有聽力缺口，諧波還在
 * 而且手錶喇叭很小，純正弦的 520 Hz 基頻會很虛；
 * 方波的諧波反而讓小喇叭聽起來更響。
 *
 * ⚠ 但**這裡刻意不用完美方波**，而是把諧波做了衰減（見 square()）。
 *   完美方波在小喇叭上會破音失真，失真之後反而聽不清節奏。
 *
 * ## 節奏用 T-3，跟震動同一套
 *
 * ISO 8201 / NFPA 72 的火警疏散訊號：**響 0.5 秒 × 3 次，中間停 0.5 秒，
 * 然後停 1.5 秒**，如此循環。使用者 自己憑直覺講出來的「嘟嘟嘟 → 停」就是這個。
 *
 * 聲音跟震動用同一個節奏是刻意的 —— **兩個通道講同一句話**，
 * 一個沒被感覺到，另一個還在說同樣的事。
 *
 * ## ⚠ 這支手錶有一個會讓警報變啞的陷阱
 *
 * 2026-08-17 從 `dumpsys audio` 查到：
 * ```
 * - STREAM_ALARM (aliased to: STREAM_NOTIFICATION):
 * - STREAM_NOTIFICATION:
 *      Muted: true
 * ```
 * **這支錶把鬧鐘音軌併到通知音軌了**，而通知音軌在「靜音／只震動」時是 muted。
 * 也就是說：程式呼叫得很成功、日誌寫著「已播放」，**而人什麼都聽不到**。
 *
 * 這跟 manifest 忘了宣告 VIBRATE 是同一隻病 —— **安靜地失敗**。
 * 所以 play() 會回報實際的靜音狀態與音量，**不要只看有沒有丟例外**。
 */
object Siren {

    const val ACTION = "com.example.watchbridge.SIREN"
    const val TAG = "WatchBridge"

    /** NFPA 72 §18.4.5.3 規定的基頻。±10% 內都合規。 */
    private const val FREQ_HZ = 520.0

    private const val SAMPLE_RATE = 44100

    /** T-3：響 0.5 秒、停 0.5 秒，三輪，然後停 1.5 秒。 */
    private const val ON_MS = 500
    private const val GAP_MS = 500
    private const val CYCLE_GAP_MS = 1500
    private const val PULSES = 3

    @Volatile private var track: AudioTrack? = null
    @Volatile private var playing = false

    /**
     * 開始鳴叫，直到有人叫 stop() 或撐到 maxSeconds。
     *
     * @param maxSeconds 上限。**一定要有** —— 使用者 說得對：
     *   「總不能一直警報到手機跟手錶沒電為止吧，那家屬打電話來反而沒電了」。
     *   警報的目的是把人叫來，不是把電池耗光。電池耗光之後
     *   **連「打電話確認」這條退路都沒了**。
     * @return 這次是不是真的送得出聲音（false = 被系統靜音，人聽不到）
     */
    fun start(ctx: Context, maxSeconds: Int = 60): Boolean {
        stop()   // 不要疊兩層

        maxVolume(ctx)   // 警報期間開到最大，結束時還原
        val am = ctx.getSystemService(AudioManager::class.java)
        val vol = am.getStreamVolume(AudioManager.STREAM_ALARM)
        val max = am.getStreamMaxVolume(AudioManager.STREAM_ALARM)
        // isStreamMute 在 API 23+ 可用。這是判斷「會不會安靜地失敗」的唯一方法。
        val muted = runCatching { am.isStreamMute(AudioManager.STREAM_ALARM) }.getOrDefault(false)
        Log.i(TAG, "警報聲：音量 $vol/$max，靜音=$muted，${FREQ_HZ.toInt()}Hz 方波 T-3")
        if (muted || vol == 0) {
            // **不要自作主張把使用者的音量調大。** 那是她的裝置設定。
            // 但一定要讓上層知道「這次是啞的」，好走震動或別的通道。
            Log.e(TAG, "⚠ 鬧鐘音軌是靜音的 —— 這次警報聽不到，只能靠震動")
        }

        val attrs = AudioAttributes.Builder()
            // USAGE_ALARM：讓系統把它當警報處理（勿擾模式的例外、不被音樂壓過）
            .setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build()

        val fmt = AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(SAMPLE_RATE)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .build()

        // ⚠ 2026-08-17 05:10 使用者 的耳朵抓到的 bug：**她說「你放了兩聲對不對」**——
        //     應該是三聲。
        //
        //     原因：`AudioTrack.write()` 在 MODE_STREAM 下是**把資料丟進緩衝區就回傳**，
        //     不是等它播完才回傳。原本的寫法是
        //         for (3) { write(0.5秒的音); sleep(0.5秒) }
        //     緩衝區夠大 → 三次 write 幾乎瞬間全部塞完 → 三個脈衝**首尾相連**
        //     變成一聲 1.5 秒的長音，中間根本沒有停頓。
        //     8 秒剛好放兩輪半 —— 所以聽起來是兩聲。
        //
        //     機器那邊完全看不出來：dumpsys 顯示 state:started、mutedState:none，
        //     程式自己也回報「有聲」。**全綠，而節奏是錯的。**
        //     這是那條老規矩的又一個實例：綠燈不等於能用，
        //     而且**只有真的用耳朵聽才驗得到**。
        //
        // 正解：把一整個 T-3 循環（含中間的靜音）一次算成 PCM，
        //     交給音效卡按取樣點播。節奏變成取樣級精確，
        //     **不再依賴執行緒的 sleep** —— 順便免疫 Doze 底下的排程延遲。
        val cycle = t3Cycle()
        val minBuf = AudioTrack.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT
        )

        val t = AudioTrack.Builder()
            .setAudioAttributes(attrs)
            .setAudioFormat(fmt)
            .setTransferMode(AudioTrack.MODE_STREAM)
            // 緩衝區刻意**只放半個循環**：write() 因此會阻塞，
            // 迴圈自然被音效卡的消耗速度節流，不會一次塞進好幾個循環。
            // 這也讓 stop() 的反應快 —— 緩衝區裡積著的音是停不掉的，
            // 積越多、按了停之後還會響越久。
            .setBufferSizeInBytes(maxOf(minBuf, cycle.size))
            .build()

        track = t
        playing = true
        t.play()

        // 節奏用背景執行緒跑。**不要用 Handler/Timer** ——
        // 這支之後會在警報情境下被呼叫，那時候系統可能正要進休眠，
        // 而 java.util.Timer 在 Doze 底下會被凍住（08-16 已經被這個咬過一次）。
        Thread {
            val deadline = System.currentTimeMillis() + maxSeconds * 1000L
            try {
                // write() 會阻塞到緩衝區收得下為止，所以這個迴圈自然就是
                // 一循環接一循環，不需要自己算時間。
                while (playing && System.currentTimeMillis() < deadline) {
                    t.write(cycle, 0, cycle.size)
                }
            } catch (e: Exception) {
                Log.w(TAG, "警報聲中斷：${e.javaClass.simpleName}")
            } finally {
                Log.i(TAG, "警報聲結束")
                stop()
                restoreVolume(ctx)
            }
        }.apply { isDaemon = true; name = "siren" }.start()

        return !muted && vol > 0
    }

    // ── 音量 ────────────────────────────────────────────────────
    // 警報期間把鬧鐘音量開到最大，結束後**還原成使用者原本的設定**。
    //
    // 為什麼敢動使用者的設定：這是警報，不是提示音。
    // 一個因為手錶昨天調小聲而沒被聽到的求救，等於沒有這個功能。
    // 為什麼一定要還原：那是她的裝置，我們只借用警報的那幾分鐘。
    @Volatile private var savedVolume: Int? = null

    private fun maxVolume(ctx: Context) {
        val am = ctx.getSystemService(AudioManager::class.java)
        runCatching {
            if (savedVolume == null) savedVolume = am.getStreamVolume(AudioManager.STREAM_ALARM)
            am.setStreamVolume(
                AudioManager.STREAM_ALARM,
                am.getStreamMaxVolume(AudioManager.STREAM_ALARM),
                0,
            )
        }.onFailure { Log.w(TAG, "調不動音量：${it.javaClass.simpleName}") }
    }

    private fun restoreVolume(ctx: Context) {
        val v = savedVolume ?: return
        savedVolume = null
        runCatching {
            ctx.getSystemService(AudioManager::class.java)
                .setStreamVolume(AudioManager.STREAM_ALARM, v, 0)
            Log.i(TAG, "音量還原成 $v")
        }
    }

    fun stop() {
        playing = false
        val t = track ?: return
        track = null
        runCatching { t.pause(); t.flush(); t.stop(); t.release() }
    }

    fun isPlaying(): Boolean = playing

    /**
     * 一整個 T-3 循環：（響 0.5s ＋ 停 0.5s）× 3 ＋ 停 1.5s ＝ 4.5 秒。
     *
     * **整段一次算好**，靜音的部分就是一堆 0。這樣節奏由音效卡的取樣時脈決定，
     * 是取樣級精確的，不會因為執行緒被排程延遲而走鐘。
     */
    private fun t3Cycle(): ShortArray {
        val on = SAMPLE_RATE * ON_MS / 1000
        val gap = SAMPLE_RATE * GAP_MS / 1000
        val tail = SAMPLE_RATE * CYCLE_GAP_MS / 1000
        val out = ShortArray(PULSES * (on + gap) + tail)   // 尾巴之外全部預設 0 ＝ 靜音
        val pulse = square(ON_MS)
        for (p in 0 until PULSES) pulse.copyInto(out, p * (on + gap))
        return out
    }

    /**
     * 合成一段 520 Hz 方波。
     *
     * ⚠ 2026-08-17 使用者 的第二個回饋：**「看起來不像火警欸，就像一般的 BB 聲，沒有很尖。」**
     *
     * 她是對的，而且原因是我自己造成的：**第一版只疊到七次諧波還做了衰減**，
     * 怕小喇叭破音。結果波形太接近純音 —— 圓潤、不刺耳、
     * **聽起來就像一般的提示音**。
     *
     * 這裡有一個真實的衝突，要寫清楚免得以後有人「順手改回去」：
     *
     * | | 判斷的人 | 想要的 |
     * |---|---|---|
     * | 「聽起來夠緊急嗎」 | 使用者（年輕，高頻完好） | **尖銳** |
     * | 「叫得醒嗎」 | 她爸（老年性聽損，高頻先壞） | **低頻** |
     *
     * **兩邊都要滿足，而且做得到** —— 因為方波的基頻和諧波是分開的兩件事：
     *   · 基頻仍然是 520 Hz  → 老人的耳朵收得到（NFPA 的依據不變）
     *   · 諧波疊到 15 次     → 年輕的耳朵聽起來尖銳、像警報
     *
     * 也就是說「尖」和「低」不衝突，**衝突的是我上一版偷懶削掉的東西**。
     *
     * 疊到 15 次而不是無限：真正的理想方波在數位取樣下會產生疊頻失真，
     * 而 520×15 = 7800 Hz 已經接近手錶小喇叭的上限，再高只是噪音。
     *
     * 頭尾各 3 毫秒淡入淡出（原本 5 毫秒）—— 太長會把脈衝的起音磨鈍，
     * 而**起音的銳利度正是「像不像警報」的關鍵**。留一點點只為了消喀噠聲。
     */
    private fun square(ms: Int): ShortArray {
        val n = SAMPLE_RATE * ms / 1000
        val out = ShortArray(n)
        val fade = SAMPLE_RATE * 3 / 1000
        // 先算出峰值好做正規化 —— 諧波多了之後峰值會超過 1，
        // 直接乘會削波失真（那種失真是「破音」，不是「尖銳」）。
        var peak = 0.0
        val raw = DoubleArray(n)
        for (i in 0 until n) {
            val t = i.toDouble() / SAMPLE_RATE
            var v = 0.0
            var h = 1
            while (h <= 15) {          // 奇次諧波 1,3,…,15 → 520Hz 基頻 + 亮度
                v += sin(2 * PI * FREQ_HZ * h * t) / h
                h += 2
            }
            raw[i] = v
            if (kotlin.math.abs(v) > peak) peak = kotlin.math.abs(v)
        }
        val norm = if (peak > 0) 0.95 / peak else 1.0
        for (i in 0 until n) {
            val env = when {
                i < fade -> i.toDouble() / fade
                i > n - fade -> (n - i).toDouble() / fade
                else -> 1.0
            }
            out[i] = (raw[i] * norm * env * Short.MAX_VALUE).toInt()
                .coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt()).toShort()
        }
        return out
    }
}
