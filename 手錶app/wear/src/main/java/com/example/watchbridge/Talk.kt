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
 *
 *
 * 所以這支分兩半：
 *                 → 一行字走 MessageClient 到手機 /said → 手機存起來等電腦來拿。
 *
 * ⚠ 為什麼這裡用 MessageClient 而生理資料用 DataClient：
 *   生理資料不能丟，所以走會排隊重送的 DataClient；
 *
 */
class TalkActivity : Activity() {

    private lateinit var status: TextView

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        //   am start -n com.example.watchbridge/.TalkActivity --es model check     只查狀態
        //   am start -n com.example.watchbridge/.TalkActivity --es model download  請辨識服務下載中文
        intent.getStringExtra("model")?.let { modelPack(it); finish(); return }
        // 收音畫面開著的時候螢幕不准熄；畫面關掉（送出、收起）旗子就跟著消失，不會害錶一直亮。
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        status = TextView(this).apply {
            text = "跟阿寶說…"
            textSize = 16f
            setTextColor(Color.parseColor("#C9B6F2"))
            gravity = Gravity.CENTER
            setPadding(24, 40, 24, 20)
        }
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
        halo = android.view.View(this).apply {
            background = android.graphics.drawable.GradientDrawable().apply {
                shape = android.graphics.drawable.GradientDrawable.OVAL
                gradientType = android.graphics.drawable.GradientDrawable.SWEEP_GRADIENT
                colors = intArrayOf(Color.parseColor("#9B6DD6"), Color.parseColor("#E9DDFF"), Color.parseColor("#5A3A8A"), Color.parseColor("#9B6DD6"))
            }
            alpha = 0.25f
            setOnClickListener {
                if (done) return@setOnClickListener
                if (heard.isEmpty() && partial.isBlank()) { status.text = "還沒聽到字，說完再點我"; return@setOnClickListener }
                TalkLog.record(applicationContext, "點圓送出", JSONObject().put("heard_len", heard.length + partial.length)
                    .put("listen_ms", System.currentTimeMillis() - listenStartedAt))
                finishListening()
            }
        }
        spin = android.animation.ObjectAnimator.ofFloat(halo, "rotation", 0f, 360f).apply {
            duration = 1600; repeatCount = android.animation.ValueAnimator.INFINITE
            interpolator = android.view.animation.LinearInterpolator()
        }
        Speaker.warmUp(this)
        setContentView(android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.BLACK)
            val d = (resources.displayMetrics.density * 72).toInt()
            addView(halo, android.widget.LinearLayout.LayoutParams(d, d).apply { gravity = Gravity.CENTER_HORIZONTAL; setMargins(0, 24, 0, 6) })
            addView(status)
            addView(volumeBtn, android.widget.LinearLayout.LayoutParams(
                android.widget.LinearLayout.LayoutParams.WRAP_CONTENT,
                android.widget.LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { gravity = Gravity.CENTER_HORIZONTAL; setMargins(0, 8, 0, 30) })
        })
        // 第一版用 RecognizerIntent（開 Google 的畫面幫我聽），多一層。
        // 需要 RECORD_AUDIO 權限（manifest＋adb pm grant）；引擎還是系統那個（Google）。
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-TW")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            // 不用固定秒數：靜音 1.5 秒才算講完（不斷尾）、至少聽 3 秒（不斷頭）、
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
        Speaker.stop()
        status.text = "準備中…"
        listenIntent = intent
        buzz(40)
        // 以前每次開畫面都新建一個 SpeechRecognizer、關畫面就銷毀，每次都要重新綁 Google 的辨識服務（1～2 秒）。
        // 現在放在 Ears 裡留著重用：第二次起不用重綁。
        val sr = Ears.get(this)
        recognizer = sr
        listenStartedAt = System.currentTimeMillis()
        sr.setRecognitionListener(object : android.speech.RecognitionListener {
            override fun onReadyForSpeech(p: Bundle?) {
                status.text = if (heard.isEmpty()) "🎤 說吧\n說完點圓圈就送" else "「$heard」\n還在聽…點圓圈就送"
                halo.alpha = 0.7f
                if (!spin!!.isStarted) spin!!.start()   // 光波流轉＝在聽
                if (heard.isEmpty()) Log.i(TAG, "耳朵就位，從按下到能聽 ${System.currentTimeMillis() - listenStartedAt} ms")
            }
            override fun onBeginningOfSpeech() {
                halo.alpha = 1f
                status.removeCallbacks(grace)
            }
            override fun onRmsChanged(rms: Float) {
                val s = (1f + ((rms + 2f) / 12f).coerceIn(0f, 1f) * 1.2f)
                halo.scaleX = s; halo.scaleY = s
            }
            override fun onBufferReceived(b: ByteArray?) {}
            override fun onEndOfSpeech() { halo.alpha = 0.7f; halo.scaleX = 1f; halo.scaleY = 1f }
            override fun onPartialResults(p: Bundle?) {
                val s = p?.getStringArrayList(android.speech.SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
                if (!s.isNullOrBlank()) { partial = s; status.text = "「${heard}${if (heard.isEmpty()) "" else " "}$s」" }
            }
            override fun onEvent(t: Int, p: Bundle?) {}
            override fun onError(code: Int) {
                //   finishListening 會先 cancel() 辨識器，辨識器回一個錯誤 5（＝我被關了）；超過 15 秒不走重聽，就蓋成失敗畫面。
                //   已經在送／送完了，辨識器再說什麼都不理。
                if (done || finishing) { Log.i(TAG, "送出後的辨識錯誤 $code，不理"); return }
                Log.w(TAG, "辨識錯誤 $code")
                TalkLog.record(applicationContext, "辨識錯誤", JSONObject().put("code", code).put("heard_len", heard.length)
                    .put("listen_ms", System.currentTimeMillis() - listenStartedAt))
                if (code == android.speech.SpeechRecognizer.ERROR_CLIENT || code == android.speech.SpeechRecognizer.ERROR_SERVER ||
                    code == android.speech.SpeechRecognizer.ERROR_RECOGNIZER_BUSY) { recognizer = null; Ears.drop() }
                val silent = code == android.speech.SpeechRecognizer.ERROR_NO_MATCH || code == android.speech.SpeechRecognizer.ERROR_SPEECH_TIMEOUT
                if (heard.isNotEmpty() || partial.isNotBlank()) { finishListening(); return }
                val client = code == android.speech.SpeechRecognizer.ERROR_CLIENT
                if ((silent || client) && System.currentTimeMillis() - listenStartedAt < 15_000 && retries < 3) {
                    retries++
                    TalkLog.record(applicationContext, if (client) "錯誤5重聽" else "沒聽到重聽", JSONObject().put("retry", retries).put("listen_ms", System.currentTimeMillis() - listenStartedAt))
                    status.text = "🎤 我在聽，說吧"
                    if (client) {
                        val again = listenIntent
                        status.postDelayed({ if (!done && again != null) listenInApp(again) }, 400)
                    } else runCatching { listenIntent?.let { sr.startListening(it) } }
                    return
                }
                // 12＝語言不支援＝Google 辨識中文要連網，錶沒網路、又沒離線中文包。
                val offline = code in intArrayOf(1, 2, 11, 12, 13)
                if (offline) { recognizer = null; Ears.drop() }
                if (offline) {
                    val net = TalkLog.netNow(applicationContext)
                    when (TalkNetPolicy.onOffline(net, wifiWaits)) {
                        TalkNetPolicy.Act.WAIT_WIFI -> { listenIntent?.let { waitWifiThenListen(it, code) }; return }
                        TalkNetPolicy.Act.GIVE_UP -> {
                            spin?.cancel()
                            status.text = "Wi-Fi 一直接不上 📵\n靠近手機再按一次"
                            status.postDelayed({ finishAndRemoveTask() }, 3000)
                            return
                        }
                        TalkNetPolicy.Act.BLUETOOTH -> {}
                    }
                }
                if (offline && System.currentTimeMillis() - listenStartedAt < 15_000 && netRetries < 2) {
                    netRetries++
                    var dropped = false
                    if (code == android.speech.SpeechRecognizer.ERROR_NETWORK && WifiKeeper.holding) {
                        WifiKeeper.pauseForBluetooth(applicationContext, 60_000L); dropped = true
                    }
                    TalkLog.record(applicationContext, "沒網路重試", JSONObject().put("code", code).put("retry", netRetries).put("wifi_paused", dropped))
                    status.text = "📶 等網路接上…"
                    val again = listenIntent
                    status.postDelayed({ if (!done && again != null) listenInApp(again) }, 2_500)
                    return
                }
                spin?.cancel()
                status.text = when {
                    silent -> "沒聽到，再按一次"
                    offline -> "錶現在沒網路 📵\n等 2～3 秒再按一次"
                    else -> "辨識失敗（$code）"
                }
                status.postDelayed({ finishAndRemoveTask() }, if (offline) 3000 else 1500)
            }
            override fun onResults(r: Bundle?) {
                val text = r?.getStringArrayList(android.speech.SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim().orEmpty()
                if (text.isBlank() && heard.isEmpty()) { onError(android.speech.SpeechRecognizer.ERROR_NO_MATCH); return }
                if (text.isNotBlank()) { if (heard.isNotEmpty()) heard.append(' '); heard.append(text) }
                partial = ""
                status.text = "「$heard」\n還在聽…點圓圈就送"
                status.removeCallbacks(grace)
                status.postDelayed(grace, GRACE_MS)
                runCatching { listenIntent?.let { sr.startListening(it) } }
            }
        })
        sr.startListening(intent)
        status.postDelayed(maxListen, MAX_LISTEN_MS)
    }

    private val maxListen = Runnable {
        if (!done) {
            TalkLog.record(applicationContext, "聽太久先送", JSONObject().put("heard_len", heard.length + partial.length))
            finishListening()
        }
    }

    private val heard = StringBuilder()
    // 邊講邊出的字（partial）從來沒存起來，話整個不見，連一筆 talk 紀錄都沒有。
    private var partial = ""
    private var retries = 0
    private var netRetries = 0
    private var wifiWaits = 0
    private var finishing = false

    private fun waitWifiThenListen(again: Intent, code: Int) {
        wifiWaits++
        val started = System.currentTimeMillis()
        status.removeCallbacks(maxListen)
        val hadHold = WifiKeeper.holding
        if (!hadHold) WifiKeeper.hold(applicationContext, 3)
        TalkLog.record(applicationContext, "等WiFi", JSONObject().put("code", code).put("wait", wifiWaits).put("held", hadHold))
        halo.alpha = 0.4f
        val tick = object : Runnable {
            override fun run() {
                if (done || isFinishing) return
                val waited = System.currentTimeMillis() - started
                val net = TalkLog.netNow(applicationContext)
                when {
                    TalkNetPolicy.ready(net) -> {
                        TalkLog.record(applicationContext, "WiFi接上", JSONObject().put("waited_ms", waited))
                        status.text = "📶 接上了"
                        status.postDelayed({ if (!done) listenInApp(again) }, 500)
                    }
                    TalkNetPolicy.timedOut(waited) -> {
                        TalkLog.record(applicationContext, "WiFi等不到", JSONObject().put("waited_ms", waited))
                        spin?.cancel()
                        status.text = "Wi-Fi 接不上 📵\n靠近手機再按一次"
                        status.postDelayed({ finishAndRemoveTask() }, 3000)
                    }
                    else -> {
                        status.text = "📶 手機不在旁邊\n正在接 Wi-Fi… ${waited / 1000} 秒"
                        status.postDelayed(this, 1000)
                    }
                }
            }
        }
        status.post(tick)
    }
    private var done = false   // 已送出／已處理，避免 onStop 和 finishListening 送兩次
    private var listenIntent: Intent? = null
    private var spin: android.animation.ObjectAnimator? = null
    private val grace = Runnable { finishListening() }

    private fun finishListening() {
        finishing = true
        status.removeCallbacks(grace); status.removeCallbacks(maxListen)
        runCatching { recognizer?.cancel() }
        spin?.cancel()
        halo.alpha = 0.35f; halo.scaleX = 1f; halo.scaleY = 1f
        if (done) return
        val text = (heard.toString() + (if (partial.isNotBlank()) " $partial" else "")).trim()
        heard.clear(); partial = ""
        if (text.isBlank()) { finishing = false; status.text = "沒聽到，再按一次"; status.postDelayed({ finishAndRemoveTask() }, 1500); return }
        sendToAbao(text)
    }

    override fun onStop() {
        super.onStop()
        if (done || isChangingConfigurations) return
        val text = (heard.toString() + (if (partial.isNotBlank()) " $partial" else "")).trim()
        status.removeCallbacks(grace)
        runCatching { recognizer?.cancel() }
        spin?.cancel()
        if (text.isNotBlank()) {
            heard.clear(); partial = ""
            TalkLog.record(applicationContext, "熄屏先送", JSONObject().put("text_len", text.length)
                .put("listen_ms", System.currentTimeMillis() - listenStartedAt))
            sendToAbao(text)
        } else {
            done = true
            TalkLog.record(applicationContext, "離開畫面", JSONObject().put("listen_ms", System.currentTimeMillis() - listenStartedAt))
        }
        finishAndRemoveTask()
    }

    private var listenStartedAt = 0L

    override fun onDestroy() {
        status.removeCallbacks(grace); status.removeCallbacks(maxListen)
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
        done = true
        runCatching { VoiceTeach.onSaid(applicationContext, text) }
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
            TalkLog.record(applicationContext, if (sent > 0) "送出" else "送不出去", JSONObject().put("nodes", sent).put("text_len", text.length))
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
        const val MAX_LISTEN_MS = 60_000L
        const val GRACE_MS = 2000L
    }
}

/** 耳朵：一個 SpeechRecognizer 留著重用，省掉每次重綁辨識服務的 1～2 秒。只能在主執行緒碰。 */
/**
 */
/** 離線辨識包：查狀態，mode=download 時順便請辨識服務下載 zh-TW（Android 13 以上才有這兩個 API）。 */
private fun android.app.Activity.modelPack(mode: String) {
    val ctx = applicationContext
    if (android.os.Build.VERSION.SDK_INT < 33) { TalkLog.record(ctx, "離線包", JSONObject().put("mode", mode).put("error", "sdk<33")); return }
    val sr = android.speech.SpeechRecognizer.createSpeechRecognizer(ctx)
    val want = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-TW")
    if (mode == "download") sr.triggerModelDownload(want)
    sr.checkRecognitionSupport(want, ctx.mainExecutor, object : android.speech.RecognitionSupportCallback {
        override fun onSupportResult(s: android.speech.RecognitionSupport) {
            val zh = { l: List<String> -> l.filter { it.startsWith("zh") || it.startsWith("cmn") || it.startsWith("yue") }.joinToString() }
            TalkLog.record(ctx, "離線包", JSONObject().put("mode", mode)
                .put("installed", zh(s.installedOnDeviceLanguages)).put("pending", zh(s.pendingOnDeviceLanguages))
                .put("supported", zh(s.supportedOnDeviceLanguages)).put("online", zh(s.onlineLanguages)))
            sr.destroy()
        }
        override fun onError(error: Int) {
            TalkLog.record(ctx, "離線包", JSONObject().put("mode", mode).put("error", error)); sr.destroy()
        }
    })
}

object TalkLog {
    fun record(ctx: android.content.Context, what: String, extra: JSONObject) {
        runCatching {
            val o = JSONObject()
                .put("t", java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS", java.util.Locale.US).format(java.util.Date()))
                .put("type", "talk").put("what", what).put("net", netNow(ctx))
            extra.keys().forEach { k -> o.put(k, extra.get(k)) }
            val dir = java.io.File(ctx.filesDir, "生理資料").apply { mkdirs() }
            val f = java.io.File(dir, java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US).format(java.util.Date()) + ".jsonl")
            JsonlBuffer.append(f, o.toString())
            android.util.Log.i("WatchBridge", "talk $what $o")
        }
    }

    /** 錶現在靠什麼上網：wifi／bluetooth（借手機）／cellular／none。 */
    fun netNow(ctx: android.content.Context): String = runCatching {
        val cm = ctx.getSystemService(android.net.ConnectivityManager::class.java)
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return "none"
        val parts = mutableListOf<String>()
        if (caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI)) parts += "wifi"
        if (caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_BLUETOOTH)) parts += "bluetooth"
        if (caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR)) parts += "cellular"
        if (!caps.hasCapability(android.net.NetworkCapabilities.NET_CAPABILITY_VALIDATED)) parts += "未驗證"
        if (parts.isEmpty()) "其他" else parts.joinToString("+")
    }.getOrDefault("讀不到")
}

object QuickSaid {
    fun send(ctx: android.content.Context, text: String) {
        Thread {
            val payload = JSONObject().put("t", System.currentTimeMillis()).put("text", text).put("quick", true).toString().toByteArray(Charsets.UTF_8)
            val sent = runCatching {
                val nodes = Tasks.await(Wearable.getNodeClient(ctx).connectedNodes)
                var n = 0
                for (node in nodes) { Tasks.await(Wearable.getMessageClient(ctx).sendMessage(node.id, TalkActivity.SAID_PATH, payload)); n++ }
                n
            }.onFailure { Log.e("WatchBridge", "快速回覆送不出去", it) }.getOrDefault(0)
            runCatching {
                ctx.getSystemService(Vibrator::class.java).vibrate(VibrationEffect.createOneShot(if (sent > 0) 60 else 200, VibrationEffect.DEFAULT_AMPLITUDE))
            }
        }.start()
    }
}


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
    /*
     *  對講當下留著（第二句 0.6 秒），IDLE_MS 沒再用就放掉。 */
    fun release(afterMs: Long = IDLE_MS) { h.removeCallbacks(idleDrop); h.postDelayed(idleDrop, afterMs) }
    const val IDLE_MS = 120_000L
}

object VoicePrefs {
    private const val FILE = "abao_voice"
    private const val KEY = "volume"   // 0.2 ～ 1.0，鬧鐘音軌的比例
    fun get(ctx: android.content.Context): Float =
        ctx.getSharedPreferences(FILE, android.content.Context.MODE_PRIVATE).getFloat(KEY, 1.0f).coerceIn(0.2f, 1.0f)
    fun set(ctx: android.content.Context, v: Float) =
        ctx.getSharedPreferences(FILE, android.content.Context.MODE_PRIVATE).edit().putFloat(KEY, v.coerceIn(0.2f, 1.0f)).apply()

    private const val QUIET = "quiet"
    fun quiet(ctx: android.content.Context): Boolean =
        ctx.getSharedPreferences(FILE, android.content.Context.MODE_PRIVATE).getBoolean(QUIET, false)
    fun setQuiet(ctx: android.content.Context, on: Boolean) =
        ctx.getSharedPreferences(FILE, android.content.Context.MODE_PRIVATE).edit().putBoolean(QUIET, on).apply()

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

    fun shouldSpeak(ctx: android.content.Context, speakRequested: Boolean, wake: Boolean = false): Boolean =
        SayPolicy.shouldSpeak(speakRequested, wake, quiet(ctx), systemQuiet(ctx))
}

/**
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
        // 手機端：speak=true 送純文字（舊版相容）；speak=false 才送 JSON {"text","speak":false}。
        var text = raw
        var speakRequested = true
        var buzz = ""
        var ask = false
        var wake = false
        if (raw.startsWith("{")) {
            runCatching { JSONObject(raw) }.getOrNull()?.let { j ->
                text = j.optString("text", raw).trim()
                speakRequested = j.optBoolean("speak", true)
                buzz = j.optString("buzz", "")
                ask = j.optBoolean("ask", false)
                wake = j.optBoolean("wake", false)
            }
        }
        if (text.isBlank()) return
        CardLog.add(this, text, ask)
        val willSpeak = VoicePrefs.shouldSpeak(this, speakRequested, wake)
        Log.i(TAG, "阿寶說（speak=$speakRequested wake=$wake → ${if (willSpeak) "唸" else "只給字"}）：$text")
        runCatching {
            val pattern = SayPolicy.pattern(buzz, wake, willSpeak)
            val vib = getSystemService(Vibrator::class.java)
            val effect = if (SayPolicy.fullAmplitude(buzz, wake) && vib.hasAmplitudeControl()) {
                val amp = IntArray(pattern.size) { i -> if (i % 2 == 1) 255 else 0 }
                VibrationEffect.createWaveform(pattern, amp, -1)
            } else VibrationEffect.createWaveform(pattern, -1)
            if (SayPolicy.alarmUsage(wake)) vib.vibrate(effect,
                android.os.VibrationAttributes.Builder().setUsage(android.os.VibrationAttributes.USAGE_ALARM).build())
            else vib.vibrate(effect)
        }
        notify(text)
        // 照 AlarmOverlay 的做法：覆蓋視窗＋喚醒鎖把螢幕點亮，字放大。
        // 所以先貼字、點亮，等 SPEAK_DELAY_MS 再開口，兩個就同步了。
        val main = android.os.Handler(android.os.Looper.getMainLooper())
        runCatching {
            startActivity(Intent(this, SayCardActivity::class.java)
                .putExtra("text", text).putExtra("spoken", willSpeak).putExtra("ask", ask).putExtra("wake", wake)
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
            .setContentIntent(android.app.PendingIntent.getActivity(this, 7, Intent(this, HistoryActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE))
            .build()
        // 以前每張都蓋同一個通知 ID → 通知列永遠只剩最後一張。改成每張一個 ID，往下拉通知列能看前幾張。
        nm.notify(NOTIF_ID + ((System.currentTimeMillis() / 1000L) % 1000L).toInt(), n)
    }.onFailure { Log.w(TAG, "通知沒發出去", it) }

    private fun speak(text: String) = Speaker.speak(applicationContext, text)

    companion object {
        const val TAG = "WatchBridge"
        const val SAY_PATH = "/say"
        const val SHOW_MS = 10 * 60_000L
        const val WAKE_MS = 20_000L        // 螢幕點亮的時間（唸完就交給系統）
        const val SPEAK_DELAY_MS = 200L
        const val CHANNEL = "abao_say"
        const val NOTIF_ID = 9012
    }
}

/**
 * 覆蓋視窗拿不到實體鍵、捲動也走系統慢路；改成 Activity 之後：實體鍵（返回）＝收起、捲動原生、
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
        val ask = intent.getBooleanExtra("ask", false)
        val quietNow = !intent.getBooleanExtra("wake", false) && (VoicePrefs.quiet(this) || VoicePrefs.systemQuiet(this))
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
            addView(pill("🎤 回阿寶", accentC, Color.BLACK, 14f).apply {
                setOnClickListener {
                    Speaker.stop()
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
            if (ask) {
                addView(android.widget.LinearLayout(this@SayCardActivity).apply {
                    orientation = android.widget.LinearLayout.HORIZONTAL
                    gravity = android.view.Gravity.CENTER
                    addView(pill("👍 對", accentC, Color.BLACK, 15f).apply { setOnClickListener { QuickSaid.send(applicationContext, "對"); Speaker.stop(); finish() } },
                        android.widget.LinearLayout.LayoutParams(android.widget.LinearLayout.LayoutParams.WRAP_CONTENT, android.widget.LinearLayout.LayoutParams.WRAP_CONTENT).apply { setMargins(0, 0, dp(10), 0) })
                    addView(pill("👎 不是", Color.parseColor("#2AFFFFFF"), Color.WHITE, 15f).apply { setOnClickListener { QuickSaid.send(applicationContext, "不是"); Speaker.stop(); finish() } })
                }, android.widget.LinearLayout.LayoutParams(
                    android.widget.LinearLayout.LayoutParams.WRAP_CONTENT, android.widget.LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { setMargins(0, dp(10), 0, 0); gravity = android.view.Gravity.CENTER_HORIZONTAL })
            }
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

object Speaker {
    const val TAG = "WatchBridge"

    // ① 引擎常駐暖機：以前每句都新建一個 TextToSpeech（初始化 1～3 秒），現在開 app／收到訊息時先暖好，之後直接講。
    // ② 唸之前把 emoji 拿掉（畫面照樣顯示）。
    @Volatile private var engine: TextToSpeech? = null
    @Volatile private var ready = false
    @Volatile private var pendingRestore: (() -> Unit)? = null
    private val emoji = Regex("""[\p{So}\x{1F000}-\x{1FAFF}\x{2600}-\x{27BF}\x{2B00}-\x{2BFF}\x{FE0F}\x{200D}\x{1F3FB}-\x{1F3FF}]""")

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
        // 有 Google 語音引擎就指定用它（會講中文）；沒裝才走系統預設。
        val google = "com.google.android.tts"
        val useGoogle = runCatching { ctx.packageManager.getPackageInfo(google, 0); true }.getOrDefault(false)
        val init = TextToSpeech.OnInitListener { status ->
            val eng = e ?: return@OnInitListener
            if (status != TextToSpeech.SUCCESS) { Log.w(TAG, "語音引擎暖機失敗 $status"); engine = null; return@OnInitListener }
            val lang = eng.setLanguage(Locale.TAIWAN)
            if (lang == TextToSpeech.LANG_MISSING_DATA || lang == TextToSpeech.LANG_NOT_SUPPORTED) Log.w(TAG, "預設引擎沒有台灣中文（$lang）")
            eng.setOnUtteranceProgressListener(object : android.speech.tts.UtteranceProgressListener() {
                override fun onStart(id: String?) {}
                override fun onDone(id: String?) { pendingRestore?.invoke(); pendingRestore = null }
                @Deprecated("Deprecated in Java")
                override fun onError(id: String?) { pendingRestore?.invoke(); pendingRestore = null }
            })
            ready = true
            Log.i(TAG, "語音引擎暖好了：${if (useGoogle) google else eng.defaultEngine}（台灣中文=$lang）")
        }
        e = if (useGoogle) TextToSpeech(ctx.applicationContext, init, google) else TextToSpeech(ctx.applicationContext, init)
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
        val stream = android.media.AudioManager.STREAM_ALARM
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
