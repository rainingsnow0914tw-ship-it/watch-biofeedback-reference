package com.example.watchbridge

import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.speech.RecognizerIntent
import android.speech.tts.TextToSpeech
import android.util.Log
import android.view.Gravity
import android.widget.TextView
import com.google.android.gms.tasks.Tasks
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.WearableListenerService
import java.util.Locale
import org.json.JSONObject

/**
 * 手腕對講機（2026-10-17，👤 使用者 的設計）
 *
 * 她的話：「手錶的 Gemini 裡面也是有一個語音轉文字的系統，直接轉成文字而非音檔，
 * 因為文字比較小嘛，然後告訴手機，再回傳過來，再用 TTS 唸出來。」
 *
 * 所以這支分兩半：
 *   TalkActivity  她 → 阿寶：按一下 → 錶自己把聲音變文字（系統的辨識器，不是我們錄音檔）
 *                 → 一行字走 MessageClient 到手機 /said → 手機存起來等電腦來拿。
 *   SayService    阿寶 → 她：手機送 /say 過來 → 震一下 → 用錶的語音引擎唸出來，
 *                 同時留一則通知讓她沒聽清楚可以看字。
 *
 * ⚠ 為什麼這裡用 MessageClient 而生理資料用 DataClient：
 *   生理資料不能丟，所以走會排隊重送的 DataClient；
 *   她的一句話要的是**快**，手機不在旁邊丟掉也沒關係 —— 畫面會告訴她「沒送出去」，
 *   她可以改用三星錄音機那條（會排隊）。兩條路分工，不是取代。
 *
 * 要驗的（10-17 寫的時候還沒驗）：
 *   · 錶上的辨識器支不支援台灣中文、要不要手機在旁邊連網
 *   · 三星語音引擎有沒有台灣中文語音（沒有的話會用英文腔念中文，要她去錶上下載）
 */
class TalkActivity : Activity() {

    private lateinit var status: TextView

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        status = TextView(this).apply {
            text = "跟阿寶說…"
            textSize = 16f
            setTextColor(Color.parseColor("#C9B6F2"))
            gravity = Gravity.CENTER
            setPadding(24, 40, 24, 20)
        }
        // 👤 10-17：「紫色那個 app 裡面做一個阿寶的聲音調節。」底下一顆小鈕開音量畫面。
        val volumeBtn = android.widget.Button(this).apply {
            text = "🔊 阿寶音量"
            textSize = 12f
            setTextColor(Color.BLACK)
            setBackgroundColor(Color.parseColor("#9B6DD6"))
            setOnClickListener {
                runCatching { recognizer?.cancel() }
                startActivity(Intent(this@TalkActivity, VoiceVolumeActivity::class.java))
                finishAndRemoveTask()
            }
        }
        // 收音光圈：隨音量呼吸（onRmsChanged），讓她知道「它在聽、聽得到我」。
        // 👤 10-17：「沒看到會呼吸的光圈」—— 圓點字元在錶的字型畫不出來，改成真的畫一個圓。
        // 👤 10-17 23:10 三個狀態要看得出來：準備中（暗、不動）→ 在聽（光波流轉：紫色漸層繞圈）→ 收到聲音（忽大忽小）
        halo = android.view.View(this).apply {
            background = android.graphics.drawable.GradientDrawable().apply {
                shape = android.graphics.drawable.GradientDrawable.OVAL
                gradientType = android.graphics.drawable.GradientDrawable.SWEEP_GRADIENT
                colors = intArrayOf(Color.parseColor("#9B6DD6"), Color.parseColor("#E9DDFF"), Color.parseColor("#5A3A8A"), Color.parseColor("#9B6DD6"))
            }
            alpha = 0.25f
        }
        spin = android.animation.ObjectAnimator.ofFloat(halo, "rotation", 0f, 360f).apply {
            duration = 1600; repeatCount = android.animation.ValueAnimator.INFINITE
            interpolator = android.view.animation.LinearInterpolator()
        }
        Speaker.warmUp(this)   // 她講完我馬上要回，先把引擎暖好
        setContentView(android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.BLACK)
            val d = (resources.displayMetrics.density * 56).toInt()
            addView(halo, android.widget.LinearLayout.LayoutParams(d, d).apply { gravity = Gravity.CENTER_HORIZONTAL; setMargins(0, 24, 0, 6) })
            addView(status)
            addView(volumeBtn, android.widget.LinearLayout.LayoutParams(
                android.widget.LinearLayout.LayoutParams.WRAP_CONTENT,
                android.widget.LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { gravity = Gravity.CENTER_HORIZONTAL; setMargins(0, 8, 0, 30) })
        })
        // 👤 10-17 第四次：「為什麼不能直接點就說話，要跳回 Google 白色介面？」
        // 第一版用 RecognizerIntent（開 Google 的畫面幫我聽），多一層。
        // 改成 app 自己用 SpeechRecognizer 聽：按下去就「我在聽…」，字邊聽邊出，講完直接送。
        // 需要 RECORD_AUDIO 權限（manifest＋adb pm grant）；引擎還是系統那個（Google）。
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-TW")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            // 👤 10-17：「用時間來收音不太好，斷頭斷尾；Gemini 是聽到聲音沒了才送。」
            // 不用固定秒數：靜音 1.5 秒才算講完（不斷尾）、至少聽 3 秒（不斷頭）、
            // 「可能講完了」的判斷放寬到 1.2 秒，她停頓想一下不會被切。
            // 👤 10-17 第六次：「沒聽到聲音之後再延後五秒左右再傳，因為有時候是語氣的停頓。」
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 5000L)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 4000L)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 3000L)
        }
        val micOk = checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED
        if (micOk && android.speech.SpeechRecognizer.isRecognitionAvailable(this)) {
            listenInApp(intent)
        } else {
            // 退路：沒麥克風權限或沒有辨識服務 → 還是開 Google 那個畫面
            Log.w(TAG, "改走 RecognizerIntent（mic=$micOk）")
            try {
                @Suppress("DEPRECATION")
                startActivityForResult(intent, REQ)
            } catch (e: Exception) {
                Log.e(TAG, "錶上沒有語音辨識器", e)
                status.text = "這支錶沒有語音辨識\n改用錄音機敲門手勢"
                status.postDelayed({ finishAndRemoveTask() }, 2500)
            }
        }
    }

    private var recognizer: android.speech.SpeechRecognizer? = null
    private lateinit var halo: android.view.View

    private fun listenInApp(intent: Intent) {
        Speaker.stop()   // 阿寶還在唸就先閉嘴，不然麥克風會把喇叭錄進去
        status.text = "準備中…"
        listenIntent = intent
        buzz(40)
        // 👤 10-17 23:10：「按回阿寶到光圈真的能聽，中間隔了兩三秒。」
        // 以前每次開畫面都新建一個 SpeechRecognizer、關畫面就銷毀，每次都要重新綁 Google 的辨識服務（1～2 秒）。
        // 現在放在 Ears 裡留著重用：第二次起不用重綁。
        val sr = Ears.get(this)
        recognizer = sr
        listenStartedAt = System.currentTimeMillis()
        sr.setRecognitionListener(object : android.speech.RecognitionListener {
            override fun onReadyForSpeech(p: Bundle?) {
                status.text = if (heard.isEmpty()) "🎤 說吧" else "「$heard」\n還在聽，說完我就送"
                halo.alpha = 0.7f
                if (!spin!!.isStarted) spin!!.start()   // 光波流轉＝在聽
                if (heard.isEmpty()) Log.i(TAG, "耳朵就位，從按下到能聽 ${System.currentTimeMillis() - listenStartedAt} ms")
            }
            override fun onBeginningOfSpeech() {
                halo.alpha = 1f
                status.removeCallbacks(grace)   // 她又開口了，剛才那段不是結尾
            }
            override fun onRmsChanged(rms: Float) {
                // rms 大約 -2～10 dB；映射成 1.0～2.2 倍，像呼吸（忽大忽小＝正在收她的聲音）
                val s = (1f + ((rms + 2f) / 12f).coerceIn(0f, 1f) * 1.2f)
                halo.scaleX = s; halo.scaleY = s
            }
            override fun onBufferReceived(b: ByteArray?) {}
            override fun onEndOfSpeech() { halo.alpha = 0.7f; halo.scaleX = 1f; halo.scaleY = 1f }
            override fun onPartialResults(p: Bundle?) {
                val s = p?.getStringArrayList(android.speech.SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
                if (!s.isNullOrBlank()) status.text = "「${heard}${if (heard.isEmpty()) "" else " "}$s」"
            }
            override fun onEvent(t: Int, p: Bundle?) {}
            override fun onError(code: Int) {
                Log.w(TAG, "辨識錯誤 $code")
                if (code == android.speech.SpeechRecognizer.ERROR_CLIENT || code == android.speech.SpeechRecognizer.ERROR_SERVER ||
                    code == android.speech.SpeechRecognizer.ERROR_RECOGNIZER_BUSY) { recognizer = null; Ears.drop() }
                val silent = code == android.speech.SpeechRecognizer.ERROR_NO_MATCH || code == android.speech.SpeechRecognizer.ERROR_SPEECH_TIMEOUT
                if (silent && heard.isNotEmpty()) { finishListening(); return }   // 第二段沒聲音＝她真的講完了
                spin?.cancel()
                // 👤 10-18 05:12：她在客廳（手機留在房間）按回阿寶，錶顯示「辨識失敗（12）」。
                // 12＝語言不支援＝Google 辨識中文要連網，錶離手機太遠沒網路、又沒離線中文包。翻成人話。
                val offline = code in intArrayOf(1, 2, 11, 12, 13)
                if (offline) { recognizer = null; Ears.drop() }
                status.text = when {
                    silent -> "沒聽到，再按一次"
                    offline -> "錶連不上網 📵\n靠近手機再試一次"
                    else -> "辨識失敗（$code）"
                }
                status.postDelayed({ finishAndRemoveTask() }, if (offline) 3000 else 1500)
            }
            override fun onResults(r: Bundle?) {
                val text = r?.getStringArrayList(android.speech.SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim().orEmpty()
                if (text.isBlank() && heard.isEmpty()) { onError(android.speech.SpeechRecognizer.ERROR_NO_MATCH); return }
                // 👤 10-17 23:10：「等我說完之後稍微停一兩秒，確定我真的說完，而不是中間喘口氣。」
                // 辨識器自己切一段就先收著，再開一段繼續聽；GRACE_MS 內她沒再開口 → 整段送出。
                if (text.isNotBlank()) { if (heard.isNotEmpty()) heard.append(' '); heard.append(text) }
                status.text = "「$heard」\n還在聽，說完我就送"
                status.removeCallbacks(grace)
                status.postDelayed(grace, GRACE_MS)
                runCatching { listenIntent?.let { sr.startListening(it) } }
            }
        })
        sr.startListening(intent)
    }

    private val heard = StringBuilder()
    private var listenIntent: Intent? = null
    private var spin: android.animation.ObjectAnimator? = null
    private val grace = Runnable { finishListening() }

    /** 她真的講完了：停聽、把收到的整段送出去。 */
    private fun finishListening() {
        status.removeCallbacks(grace)
        runCatching { recognizer?.cancel() }
        spin?.cancel()
        halo.alpha = 0.35f; halo.scaleX = 1f; halo.scaleY = 1f
        val text = heard.toString().trim()
        heard.clear()
        if (text.isBlank()) { status.text = "沒聽到，再按一次"; status.postDelayed({ finishAndRemoveTask() }, 1500); return }
        sendToAbao(text)
    }

    private var listenStartedAt = 0L

    override fun onDestroy() {
        status.removeCallbacks(grace)
        spin?.cancel()
        runCatching { recognizer?.cancel() }   // 不馬上銷毀，留 2 分鐘給下一句（見 Ears）
        Ears.release()
        super.onDestroy()
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ) return
        val text = data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()?.trim().orEmpty()
        if (resultCode != RESULT_OK || text.isBlank()) {
            status.text = "沒聽到，再按一次"
            status.postDelayed({ finishAndRemoveTask() }, 1500)
            return
        }
        sendToAbao(text)
    }

    private fun sendToAbao(text: String) {
        status.text = "「$text」\n送給阿寶…"
        Thread {
            val payload = JSONObject().put("t", System.currentTimeMillis()).put("text", text).toString()
                .toByteArray(Charsets.UTF_8)
            val sent = runCatching {
                val nodes = Tasks.await(Wearable.getNodeClient(applicationContext).connectedNodes)
                var n = 0
                for (node in nodes) {
                    Tasks.await(Wearable.getMessageClient(applicationContext).sendMessage(node.id, SAID_PATH, payload))
                    n++
                }
                n
            }.onFailure { Log.e(TAG, "送不出去", it) }.getOrDefault(0)
            runOnUiThread {
                if (sent > 0) {
                    status.text = "送出了，等阿寶回"
                    buzz(60)
                } else {
                    status.text = "手機不在旁邊，沒送出去\n（可改用錄音機）"
                    buzz(200)
                }
                status.postDelayed({ finishAndRemoveTask() }, if (sent > 0) 1200 else 3000)
            }
        }.start()
    }

    private fun buzz(ms: Long) = runCatching {
        getSystemService(Vibrator::class.java).vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE))
    }

    companion object {
        const val TAG = "WatchBridge"
        const val REQ = 7
        const val SAID_PATH = "/said"
        const val GRACE_MS = 2000L   // 辨識器切段後再等這麼久沒新聲音才送（👤「一秒到兩秒」）
    }
}

/** 耳朵：一個 SpeechRecognizer 留著重用，省掉每次重綁辨識服務的 1～2 秒。只能在主執行緒碰。 */
object Ears {
    private var sr: android.speech.SpeechRecognizer? = null
    private val h = android.os.Handler(android.os.Looper.getMainLooper())
    private val idleDrop = Runnable { drop() }
    fun get(ctx: android.content.Context): android.speech.SpeechRecognizer {
        h.removeCallbacks(idleDrop)
        sr?.let { return it }
        return android.speech.SpeechRecognizer.createSpeechRecognizer(ctx.applicationContext).also { sr = it }
    }
    /** 出錯壞掉時丟掉，下一次重建。 */
    fun drop() { h.removeCallbacks(idleDrop); runCatching { sr?.destroy() }; sr = null }
    /** 👤 10-18 05:40「整個錶都卡」：辨識器留著會把 Google 助理程序（43MB）撐在記憶體裡。
     *  對講當下留著（第二句 0.6 秒），IDLE_MS 沒再用就放掉。 */
    fun release(afterMs: Long = IDLE_MS) { h.removeCallbacks(idleDrop); h.postDelayed(idleDrop, afterMs) }
    const val IDLE_MS = 120_000L
}

/** 阿寶聲音大小。存 SharedPreferences，SayService 唸的時候讀。 */
object VoicePrefs {
    private const val FILE = "abao_voice"
    private const val KEY = "volume"   // 0.2 ～ 1.0，鬧鐘音軌的比例
    fun get(ctx: android.content.Context): Float =
        ctx.getSharedPreferences(FILE, android.content.Context.MODE_PRIVATE).getFloat(KEY, 1.0f).coerceIn(0.2f, 1.0f)
    fun set(ctx: android.content.Context, v: Float) =
        ctx.getSharedPreferences(FILE, android.content.Context.MODE_PRIVATE).edit().putFloat(KEY, v.coerceIn(0.2f, 1.0f)).apply()

    // 🤫 安靜模式（👤 10-17 21:00：「公共場合叫它大聲唸出來有點尷尬」）。開著：所有訊息只震＋顯示字，想聽按 🔊。
    private const val QUIET = "quiet"
    fun quiet(ctx: android.content.Context): Boolean =
        ctx.getSharedPreferences(FILE, android.content.Context.MODE_PRIVATE).getBoolean(QUIET, false)
    fun setQuiet(ctx: android.content.Context, on: Boolean) =
        ctx.getSharedPreferences(FILE, android.content.Context.MODE_PRIVATE).edit().putBoolean(QUIET, on).apply()

    /** 錶本身開了勿擾／劇院模式 → 自動當安靜模式，不用她多按一下。 */
    fun systemQuiet(ctx: android.content.Context): Boolean {
        val dnd = runCatching {
            val f = ctx.getSystemService(NotificationManager::class.java).currentInterruptionFilter
            f != NotificationManager.INTERRUPTION_FILTER_ALL && f != NotificationManager.INTERRUPTION_FILTER_UNKNOWN
        }.getOrDefault(false)
        val theater = runCatching {
            android.provider.Settings.Global.getInt(ctx.contentResolver, "theater_mode_on", 0) == 1
        }.getOrDefault(false)
        return dnd || theater
    }

    /** 現在該不該出聲：她要的 speak 旗標 且 沒開安靜模式 且 錶沒勿擾。 */
    fun shouldSpeak(ctx: android.content.Context, speakRequested: Boolean): Boolean =
        speakRequested && !quiet(ctx) && !systemQuiet(ctx)
}

/**
 * 阿寶音量（👤 10-17：「紫色那個 app 裡面做一個阿寶的聲音調節」）。
 * 大字百分比、－／＋ 一格 10%、「試聽」用同一條路唸一句、「好了」存。
 */
class VoiceVolumeActivity : Activity() {
    private lateinit var pct: TextView
    private var v = 1.0f

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        v = VoicePrefs.get(this)
        pct = TextView(this).apply {
            textSize = 30f; setTextColor(Color.WHITE); gravity = Gravity.CENTER
        }
        fun btn(label: String, w: Int = android.widget.LinearLayout.LayoutParams.WRAP_CONTENT, on: () -> Unit) =
            android.widget.Button(this).apply {
                text = label; textSize = 16f; setTextColor(Color.BLACK)
                setBackgroundColor(Color.parseColor("#9B6DD6")); setOnClickListener { on() }
            }
        val row = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.HORIZONTAL; gravity = Gravity.CENTER
            addView(btn("－") { v = (v - 0.1f).coerceAtLeast(0.2f); show() })
            addView(pct, android.widget.LinearLayout.LayoutParams(0, android.widget.LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(btn("＋") { v = (v + 0.1f).coerceAtMost(1.0f); show() })
        }
        setContentView(android.widget.ScrollView(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(android.widget.LinearLayout(this@VoiceVolumeActivity).apply {
                orientation = android.widget.LinearLayout.VERTICAL; gravity = Gravity.CENTER_HORIZONTAL
                setPadding(30, 40, 30, 50)
                addView(TextView(this@VoiceVolumeActivity).apply {
                    text = "🔊 阿寶音量"; textSize = 14f; setTextColor(Color.parseColor("#C9B6F2")); gravity = Gravity.CENTER
                })
                addView(row)
                addView(btn("試聽") { VoicePrefs.set(this@VoiceVolumeActivity, v); Speaker.speak(this@VoiceVolumeActivity, "妳，這樣的音量可以嗎？") },
                    android.widget.LinearLayout.LayoutParams(android.widget.LinearLayout.LayoutParams.MATCH_PARENT, android.widget.LinearLayout.LayoutParams.WRAP_CONTENT).apply { setMargins(0, 10, 0, 0) })
                // 🤫 安靜模式開關（👤 10-17 21:00）。開：阿寶的話全部只震＋字，卡片上有 🔊 想聽再按。
                val quietBtn = android.widget.Button(this@VoiceVolumeActivity).apply {
                    textSize = 14f; setTextColor(Color.WHITE)
                    fun paint() {
                        val on = VoicePrefs.quiet(this@VoiceVolumeActivity)
                        text = if (on) "🤫 安靜模式：開" else "🔈 安靜模式：關"
                        setBackgroundColor(Color.parseColor(if (on) "#2E7D6B" else "#444444"))
                    }
                    paint()
                    setOnClickListener { VoicePrefs.setQuiet(this@VoiceVolumeActivity, !VoicePrefs.quiet(this@VoiceVolumeActivity)); paint() }
                }
                addView(quietBtn, android.widget.LinearLayout.LayoutParams(android.widget.LinearLayout.LayoutParams.MATCH_PARENT, android.widget.LinearLayout.LayoutParams.WRAP_CONTENT).apply { setMargins(0, 8, 0, 0) })
                addView(btn("好了") { VoicePrefs.set(this@VoiceVolumeActivity, v); finishAndRemoveTask() },
                    android.widget.LinearLayout.LayoutParams(android.widget.LinearLayout.LayoutParams.MATCH_PARENT, android.widget.LinearLayout.LayoutParams.WRAP_CONTENT).apply { setMargins(0, 8, 0, 0) })
            })
        })
        show()
    }

    private fun show() { pct.text = "${(v * 100).toInt()}%" }
}

/** 手機送 /say 過來 → 錶震一下、唸出來、留一則通知。 */
class SayService : WearableListenerService() {


    override fun onCreate() { super.onCreate(); Speaker.warmUp(this) }

    override fun onMessageReceived(event: MessageEvent) {
        if (event.path != SAY_PATH) return
        val raw = String(event.data, Charsets.UTF_8).trim()
        if (raw.isBlank()) return
        // 👤 10-17 21:00 她的設計：她對錶講話→阿寶唸出來；阿寶自己傳來的→先震一震、只給字，旁邊 🔊 想聽再按。
        // 手機端：speak=true 送純文字（舊版相容）；speak=false 才送 JSON {"text","speak":false}。
        var text = raw
        var speakRequested = true
        if (raw.startsWith("{")) {
            runCatching { JSONObject(raw) }.getOrNull()?.let { j ->
                text = j.optString("text", raw).trim()
                speakRequested = j.optBoolean("speak", true)
            }
        }
        if (text.isBlank()) return
        val willSpeak = VoicePrefs.shouldSpeak(this, speakRequested)
        Log.i(TAG, "阿寶說（speak=$speakRequested → ${if (willSpeak) "唸" else "只給字"}）：$text")
        runCatching {
            // 要唸：輕輕兩下（聲音馬上接上）；只給字：重一點兩下，她才知道有話來了
            val pattern = if (willSpeak) longArrayOf(0, 80, 80, 80) else longArrayOf(0, 140, 90, 140, 90, 220)
            getSystemService(Vibrator::class.java).vibrate(VibrationEffect.createWaveform(pattern, -1))
        }
        notify(text)
        // 👤 10-17：「手錶怎麼沒有亮？起碼讓它亮起來，讓我可以看到你打的字。」
        // 照 AlarmOverlay 的做法：覆蓋視窗＋喚醒鎖把螢幕點亮，字放大。
        // 👤 10-17 第四次：「聲音先出、三四秒後才出文字。」—— 螢幕點亮要兩三秒，聲音不用等。
        // 所以先貼字、點亮，等 SPEAK_DELAY_MS 再開口，兩個就同步了。
        val main = android.os.Handler(android.os.Looper.getMainLooper())
        runCatching {
            startActivity(Intent(this, SayCardActivity::class.java)
                .putExtra("text", text).putExtra("spoken", willSpeak)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NO_ANIMATION))
        }.onFailure { Log.w(TAG, "開不了卡片畫面，通知還在", it) }
        if (willSpeak) main.postDelayed({ speak(text) }, SPEAK_DELAY_MS)
    }


    private fun notify(text: String) = runCatching {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "阿寶說", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "阿寶透過電腦→手機→手錶講的話；沒聽清楚可以在這裡看字"
            }
        )
        val n = androidx.core.app.NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_bridge)
            .setContentTitle("阿寶")
            .setContentText(text)
            .setStyle(androidx.core.app.NotificationCompat.BigTextStyle().bigText(text))
            .setPriority(androidx.core.app.NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()
        // 👤 10-18 05:50「不小心一滑，阿寶之前講的話就不見了，哪裡找得到？」
        // 以前每張都蓋同一個通知 ID → 通知列永遠只剩最後一張。改成每張一個 ID，往下拉通知列能看前幾張。
        nm.notify(NOTIF_ID + ((System.currentTimeMillis() / 1000L) % 1000L).toInt(), n)
    }.onFailure { Log.w(TAG, "通知沒發出去", it) }

    private fun speak(text: String) = Speaker.speak(applicationContext, text)

    companion object {
        const val TAG = "WatchBridge"
        const val SAY_PATH = "/say"
        const val SHOW_MS = 10 * 60_000L   // 字最多留 10 分鐘（她要「留到我按掉」，這只是保險）
        const val WAKE_MS = 20_000L        // 螢幕點亮的時間（唸完就交給系統）
        const val SPEAK_DELAY_MS = 200L    // 先亮字再開口的間隔。1500 時她說差兩三秒（引擎暖機還要 1 秒），改 500：「先有畫面，稍微停頓就出聲」
        const val CHANNEL = "abao_say"
        const val NOTIF_ID = 9012
    }
}

/**
 * 阿寶說的話那張卡（10-18 05:30 從 SayService 的覆蓋視窗改成真正的畫面）。
 * 👤 「滑動有時候卡卡的」「實體鍵按下去能不能也把卡收回去」——
 * 覆蓋視窗拿不到實體鍵、捲動也走系統慢路；改成 Activity 之後：實體鍵（返回）＝收起、捲動原生、
 * theme 關掉 swipe-to-dismiss（👤 10-17「一滑就不見了」），所以只有 ✕ 和實體鍵會收。
 */
class SayCardActivity : Activity() {
    private var wake: android.os.PowerManager.WakeLock? = null
    private val autoClose = Runnable { finish() }

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        setShowWhenLocked(true); setTurnScreenOn(true)
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
            android.view.WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        show(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        show(intent)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {   // 實體鍵（返回）＝收起
        Speaker.stop()
        finish()
    }

    private fun show(intent: Intent) {
        val text = intent.getStringExtra("text").orEmpty()
        val spoken = intent.getBooleanExtra("spoken", false)
        val quietNow = VoicePrefs.quiet(this) || VoicePrefs.systemQuiet(this)
        // 👤 10-17：「做得好看一點，做幾張底圖輪流出。」四款漸層輪流（用時間挑），不用圖片檔。
        val palettes = listOf(
            Triple("💜 阿寶", intArrayOf(Color.parseColor("#2A1B4D"), Color.parseColor("#0B0F1E")), "#C9B6F2"),   // 紫夜
            Triple("🌿 阿寶", intArrayOf(Color.parseColor("#0F3D3A"), Color.parseColor("#06161A")), "#9FE3D1"),   // 青晨
            Triple("🌸 阿寶", intArrayOf(Color.parseColor("#4A1F3A"), Color.parseColor("#1A0A14")), "#F4B6D2"),   // 玫瑰
            Triple("🌙 阿寶", intArrayOf(Color.parseColor("#4A3A12"), Color.parseColor("#1A1406")), "#F1D98A"),   // 金黃
        )
        val (title, colors, accent) = palettes[((System.currentTimeMillis() / 60_000L) % palettes.size).toInt()]
        val bg = android.graphics.drawable.GradientDrawable(android.graphics.drawable.GradientDrawable.Orientation.TL_BR, colors)
        val accentC = Color.parseColor(accent)
        val column = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            gravity = android.view.Gravity.CENTER_HORIZONTAL
            setPadding(34, 44, 34, 60)   // 圓錶：上下留多一點，角落不被切
            addView(TextView(this@SayCardActivity).apply {
                this.text = if (quietNow) "$title 🤫" else title   // 🤫＝這張沒唸，是安靜模式／勿擾
                textSize = 14f; setTextColor(accentC); gravity = android.view.Gravity.CENTER; setPadding(0, 0, 0, 8)
            })
            // 👤 「給我一個能夠馬上回你的鍵，直接說話就傳回去。」按下去：收掉這張、開「跟阿寶說」直接聽。
            addView(pill("🎤 回阿寶", accentC, Color.BLACK, 14f).apply {
                setOnClickListener {
                    Speaker.stop()   // 按「回阿寶」＝我閉嘴、換妳說
                    runCatching {
                        startActivity(Intent(this@SayCardActivity, TalkActivity::class.java)
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NO_ANIMATION))
                    }.onFailure { Log.w(TAG, "開不了跟阿寶說", it) }
                    finish()
                }
            }, android.widget.LinearLayout.LayoutParams(
                android.widget.LinearLayout.LayoutParams.WRAP_CONTENT, android.widget.LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { setMargins(0, 4, 0, 14); gravity = android.view.Gravity.CENTER_HORIZONTAL })
            addView(TextView(this@SayCardActivity).apply {
                this.text = text
                textSize = 17f; setTextColor(Color.WHITE); gravity = android.view.Gravity.CENTER
                setLineSpacing(0f, 1.2f)
                setShadowLayer(6f, 0f, 2f, Color.parseColor("#80000000"))
            })
            addView(android.view.View(this@SayCardActivity).apply { setBackgroundColor(Color.parseColor("#33FFFFFF")) },
                android.widget.LinearLayout.LayoutParams(dp(56), dp(1)).apply { setMargins(0, dp(10), 0, dp(10)); gravity = android.view.Gravity.CENTER_HORIZONTAL })
            addView(android.widget.LinearLayout(this@SayCardActivity).apply {
                orientation = android.widget.LinearLayout.HORIZONTAL
                gravity = android.view.Gravity.CENTER
                addView(roundBtn("🔊", if (spoken) "再聽" else "唸給我聽", accentC, Color.BLACK) { Speaker.speak(applicationContext, text) },
                    android.widget.LinearLayout.LayoutParams(android.widget.LinearLayout.LayoutParams.WRAP_CONTENT, android.widget.LinearLayout.LayoutParams.WRAP_CONTENT).apply { setMargins(0, 0, dp(14), 0) })
                addView(roundBtn("✕", "收起", Color.parseColor("#2AFFFFFF"), Color.WHITE, stroke = Color.parseColor("#66FFFFFF")) { Speaker.stop(); finish() })
            }, android.widget.LinearLayout.LayoutParams(
                android.widget.LinearLayout.LayoutParams.WRAP_CONTENT, android.widget.LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { gravity = android.view.Gravity.CENTER_HORIZONTAL })
        }
        val root = android.widget.ScrollView(this).apply {
            background = bg
            isVerticalScrollBarEnabled = false
            overScrollMode = android.view.View.OVER_SCROLL_NEVER
            addView(column)
        }
        setContentView(root)
        // 點亮螢幕唸的那段時間；之後交給系統熄屏（👤「字留到我按掉，熄屏沒關係」）
        wake?.let { if (it.isHeld) runCatching { it.release() } }
        @Suppress("DEPRECATION")
        wake = getSystemService(android.os.PowerManager::class.java).newWakeLock(
            android.os.PowerManager.SCREEN_BRIGHT_WAKE_LOCK or android.os.PowerManager.ACQUIRE_CAUSES_WAKEUP or android.os.PowerManager.ON_AFTER_RELEASE,
            "watchbridge:say",
        ).apply { acquire(SayService.WAKE_MS) }
        root.removeCallbacks(autoClose)
        root.postDelayed(autoClose, SayService.SHOW_MS)   // 保險：10 分鐘沒點也收掉
    }

    override fun onDestroy() {
        wake?.let { if (it.isHeld) runCatching { it.release() } }
        wake = null
        super.onDestroy()
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    /** 圓角膠囊鈕（回阿寶）。 */
    private fun pill(label: String, bg: Int, fg: Int, size: Float): TextView = TextView(this).apply {
        text = label; textSize = size; setTextColor(fg); gravity = android.view.Gravity.CENTER
        typeface = android.graphics.Typeface.DEFAULT_BOLD
        setPadding(dp(18), dp(8), dp(18), dp(8))
        background = android.graphics.drawable.GradientDrawable().apply {
            shape = android.graphics.drawable.GradientDrawable.RECTANGLE
            cornerRadius = dp(22).toFloat()
            setColor(bg)
        }
        isClickable = true; isFocusable = true
    }

    /** 圓形小鈕＋底下一行小字（🔊 再聽／✕ 收起）。 */
    private fun roundBtn(icon: String, caption: String, bg: Int, fg: Int, stroke: Int? = null, on: () -> Unit): android.view.View =
        android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            gravity = android.view.Gravity.CENTER_HORIZONTAL
            addView(TextView(this@SayCardActivity).apply {
                text = icon; textSize = 16f; setTextColor(fg); gravity = android.view.Gravity.CENTER
                background = android.graphics.drawable.GradientDrawable().apply {
                    shape = android.graphics.drawable.GradientDrawable.OVAL
                    setColor(bg)
                    if (stroke != null) setStroke(dp(1), stroke)
                }
                isClickable = true; isFocusable = true
                setOnClickListener { on() }
            }, android.widget.LinearLayout.LayoutParams(dp(44), dp(44)))
            addView(TextView(this@SayCardActivity).apply {
                text = caption; textSize = 9f; setTextColor(Color.parseColor("#B0FFFFFF")); gravity = android.view.Gravity.CENTER
                setPadding(0, dp(3), 0, 0)
            })
        }

    companion object { const val TAG = "WatchBridge" }
}

/** 唸出來（SayService 和音量畫面的「試聽」共用）。音量比例讀 VoicePrefs。 */
object Speaker {
    const val TAG = "WatchBridge"

    // 👤 10-17 20:15：「回來之後聲音要等好幾秒才出來」「emoji 都唸出來了」。
    // ① 引擎常駐暖機：以前每句都新建一個 TextToSpeech（初始化 1～3 秒），現在開 app／收到訊息時先暖好，之後直接講。
    // ② 唸之前把 emoji 拿掉（畫面照樣顯示）。
    // ③ Google 引擎在這支錶沒有 zh-TW 資料（10-17 實測），直接用系統預設（三星 SMT），省一次失敗。
    @Volatile private var engine: TextToSpeech? = null
    @Volatile private var ready = false
    @Volatile private var pendingRestore: (() -> Unit)? = null
    private val emoji = Regex("""[\p{So}\x{1F000}-\x{1FAFF}\x{2600}-\x{27BF}\x{2B00}-\x{2BFF}\x{FE0F}\x{200D}\x{1F3FB}-\x{1F3FF}]""")

    // 👤 10-17 22:55：「心率 70～109 他唸成七萬零一百零九」—— 以前把 ～ 換成全形逗號，三星引擎把「70，109」當千分位。
    // 現在：數字之間的 ～ ~ - – — 至 先換成「到」；數字之間的逗號／點也不留；其他地方的 ～ 才換逗號（語氣停頓）。
    private val numRange = Regex("""(\d)\s*[～~\-–—]\s*(\d)""")
    private val numComma = Regex("""(\d)[，,](\d)""")
    fun stripEmoji(s: String): String = emoji.replace(s, "")
        .replace(numRange, "$1到$2")
        .replace(numComma, "$1$2")
        .replace(Regex("""[～~]+"""), "，")
        .replace(Regex("""\s{2,}"""), " ").trim()

    @Synchronized
    fun warmUp(ctx: android.content.Context) {
        if (engine != null) return
        var e: TextToSpeech? = null
        e = TextToSpeech(ctx.applicationContext) { status ->
            val eng = e ?: return@TextToSpeech
            if (status != TextToSpeech.SUCCESS) { Log.w(TAG, "語音引擎暖機失敗 $status"); engine = null; return@TextToSpeech }
            val lang = eng.setLanguage(Locale.TAIWAN)
            if (lang == TextToSpeech.LANG_MISSING_DATA || lang == TextToSpeech.LANG_NOT_SUPPORTED) Log.w(TAG, "預設引擎沒有台灣中文（$lang）")
            eng.setOnUtteranceProgressListener(object : android.speech.tts.UtteranceProgressListener() {
                override fun onStart(id: String?) {}
                override fun onDone(id: String?) { pendingRestore?.invoke(); pendingRestore = null }
                @Deprecated("Deprecated in Java")
                override fun onError(id: String?) { pendingRestore?.invoke(); pendingRestore = null }
            })
            ready = true
            Log.i(TAG, "語音引擎暖好了：${eng.defaultEngine}")
        }
        engine = e
    }

    fun stop() {
        runCatching { engine?.stop() }
        runCatching { pendingRestore?.invoke() }
        pendingRestore = null
    }

    fun speak(applicationContext: android.content.Context, text: String) {
        val clean = stripEmoji(text)
        if (clean.isBlank()) return
        val am = applicationContext.getSystemService(android.media.AudioManager::class.java)
        val stream = android.media.AudioManager.STREAM_ALARM   // 錶上最響的那條（10-17 她要大聲）
        val prevVol = runCatching { am.getStreamVolume(stream) }.getOrDefault(-1)
        val maxVol = runCatching { am.getStreamMaxVolume(stream) }.getOrDefault(0)
        if (prevVol >= 0 && maxVol > 0) {
            runCatching { am.setStreamVolume(stream, (maxVol * VoicePrefs.get(applicationContext)).toInt().coerceIn(1, maxVol), 0) }
                .onFailure { Log.w(TAG, "調不了音量（勿擾？），照原音量唸", it) }
        }
        val restore: () -> Unit = { if (prevVol >= 0) runCatching { am.setStreamVolume(stream, prevVol, 0) } }
        val params = Bundle().apply {
            putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 1.0f)
            putInt(TextToSpeech.Engine.KEY_PARAM_STREAM, android.media.AudioManager.STREAM_ALARM)
        }
        fun go(eng: TextToSpeech) {
            pendingRestore?.invoke(); pendingRestore = restore
            eng.speak(clean, TextToSpeech.QUEUE_FLUSH, params, "abao-say")
        }
        val eng = engine
        if (eng != null && ready) { go(eng); return }
        // 還沒暖好：暖好再講（最多等 4 秒，之後就放棄、把音量放回去）
        warmUp(applicationContext)
        val h = android.os.Handler(android.os.Looper.getMainLooper())
        var tries = 0
        lateinit var tick: Runnable
        tick = Runnable {
            val e2 = engine
            if (e2 != null && ready) go(e2)
            else if (++tries < 20) h.postDelayed(tick, 200)
            else { Log.w(TAG, "語音引擎 4 秒內沒暖好，放棄唸（通知和畫面還在）"); restore() }
        }
        h.post(tick)
    }
}
