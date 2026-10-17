package com.example.watchbridge.phone

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread
import kotlinx.coroutines.runBlocking
import org.json.JSONObject

/**
 * 手機端的小伺服器：讓電腦來拿手錶送過來的資料。
 *
 * 為什麼是「等別人來拿」而不是「主動送出去」：
 *     Android 預設禁止 app 送未加密的 HTTP（CLEARTEXT），
 *     而它**只管「往外送」，不管「別人來拿」**。
 *     這個坑 2026-08-11 在 HC Webhook 那邊就踩過一次，解法一樣：把方向反過來。
 *
 *     附帶好處：電腦的 IP 換了無所謂、電腦關機資料也留在手機上等它回來。
 *
 * 為什麼是 8788 而不是 8787：
 *     8787 是 HC Webhook 佔著的（那條是 Samsung Health 整理過的資料）。
 *     兩條路並存、互相補：
 *         8787 → 睡眠分期、壓力分數（三星算好的，慢但獨有）
 *         8788 → 心率、IBI、皮膚溫度、動作量（我們自己收的，快且原始）
 *
 * ⚠️ 這支跟 HC Webhook 一樣綁在 0.0.0.0，**同一個 Wi-Fi 上誰都連得到**。
 *    所以帶 token。之後上雲時這一層就換掉。
 */
class BridgeServer : Service() {

    private var server: ServerSocket? = null
    private val ringHandler = Handler(Looper.getMainLooper())
    private val ringLock = Any()
    private var activeRingtone: Ringtone? = null
    private var activeVibrator: Vibrator? = null
    private var stopRing: Runnable? = null
    private val notificationIds = AtomicInteger(4100)

    /**
     * 這台手機的存取權杖。
     *
     * ## ⚠ 2026-08-27：從寫死改成**首次啟動隨機產生**
     *
     * 原本是 `DEFAULT_TOKEN = "<舊的寫死權杖，2026-08-27 已廢除>"` 寫死在程式裡，
     * 而且**已經進了 git 歷史**（08-18 就推上去了）。
     * 掃描器每次都把它列成「現在就算事故」。
     *
     * 之所以拖到今天才改，是因為我記憶裡寫著「家人正在戴，改它會斷資料」——
     * **那是 08-19 的狀態，而我拿一個過期的事實當成不能動的理由。**
     * 2026-08-27 使用者 說：「我家人現在沒有戴啊，一直都是我在用，
     * 因為我們還沒測試完，而且管線都還沒接上雲端。」
     *
     * → **過期的顧慮跟真的顧慮長得一模一樣，差別只在有沒有去問。**
     *
     * 現在：第一次啟動時產生一串隨機字元存進 SharedPreferences，
     * 之後就固定用那一串。**程式碼裡不再有任何可以當密碼的字串。**
     * 電腦端從 `生理資料/puller_config.json` 讀（那個檔在 .gitignore 裡）。
     */
    private val token: String by lazy {
        val p = getSharedPreferences("bridge", Context.MODE_PRIVATE)
        p.getString("token", null) ?: run {
            // 32 個十六進位字元 = 128 bit。這是同一個 Wi-Fi 內的門鎖，夠了。
            val fresh = java.security.SecureRandom().let { rnd ->
                (1..16).joinToString("") { "%02x".format(rnd.nextInt(256)) }
            }
            p.edit().putString("token", fresh).apply()
            // ⚠ 只在**產生的那一次**印出來，之後不再印。
            //   電腦端要靠這一行拿到它（adb logcat），所以不能不印；
            //   但每次啟動都印就等於把它散在 log 裡。
            Log.w(TAG, "首次產生存取權杖：$fresh —— 請寫進電腦端的 puller_config.json")
            fresh
        }
    }

    override fun onCreate() {
        super.onCreate()
        startForeground(1, notification())
        thread(isDaemon = true) { serve() }
        // 看門狗的鬧鐘由系統保管，所以就算這個服務等一下死掉，它照樣會醒過來、
        // 發現服務不見了、再把它叫回來。**這是唯一能監測「自己死掉」的機制。**
        StaleWatchdog.schedule(this)
    }

    override fun onStartCommand(i: Intent?, f: Int, id: Int) = START_STICKY
    override fun onBind(i: Intent?): IBinder? = null

    /**
     * 安全網：萬一系統還是對這個服務下了時限，**一定要自己乖乖停掉**。
     *
     * 不停的下場是 ANR：
     *     "A foreground service of <type> did not stop within its timeout"
     * ——那正是 2026-08-14 早上 使用者 收到的那則「阿寶橋接壞掉了」。
     *
     * 停掉之後靠 START_STICKY 讓系統把它帶回來。
     * **寧可斷幾秒再自己回來，也不要整個 app 崩掉讓她看到錯誤訊息。**
     */
    override fun onTimeout(startId: Int, fgsType: Int) {
        Log.w(TAG, "系統給了時限（type=$fgsType），主動停掉，等系統重啟")
        stopSelf()
    }

    override fun onDestroy() {
        synchronized(ringLock) { stopActiveRingLocked() }
        super.onDestroy()
        runCatching { server?.close() }
    }

    private fun serve() {
        runCatching {
            server = ServerSocket().apply {
                reuseAddress = true
                bind(InetSocketAddress(PORT))
            }
            Log.i(TAG, "伺服器起來了，port $PORT")
        }.onFailure {
            Log.e(TAG, "伺服器起不來", it); return
        }

        while (!Thread.currentThread().isInterrupted) {
            val sock = runCatching { server!!.accept() }.getOrNull() ?: break
            thread(isDaemon = true) {
                runCatching { handle(sock) }.onFailure { Log.e(TAG, "處理請求失敗", it) }
                runCatching { sock.close() }
            }
        }
    }

    /**
     * 只認幾條路徑，刻意做得很笨：
     *   GET /status        → 幾個檔、多大、最後更新時間
     *   GET /day?d=<日期>  → 那一天的原始 JSONL
     *   GET /watchdog      → 看門狗的執行軌跡（純文字）
     *   GET /notes         → 使用者 自己記的對照筆記
     *   POST /ring         → 用鬧鐘音量響鈴並震動（最長 30 秒）
     *   POST /notify       → 「有人在家」高重要度通知
     * 所有路徑都必須帶完全相符的 Bearer token。
     */
    private fun handle(sock: java.net.Socket) {
        sock.soTimeout = 5_000
        val input = BufferedInputStream(sock.getInputStream())
        val line = readHttpLine(input) ?: return
        val headers = linkedMapOf<String, String>()
        while (true) {
            val h = readHttpLine(input) ?: break
            if (h.isBlank()) break
            val split = h.indexOf(':')
            if (split > 0) headers[h.substring(0, split).trim()] = h.substring(split + 1).trim()
        }

        val out = sock.getOutputStream()
        fun send(code: String, body: ByteArray, type: String = "application/json") {
            out.write(
                ("HTTP/1.1 $code\r\nContent-Type: $type; charset=utf-8\r\n" +
                 "Content-Length: ${body.size}\r\nConnection: close\r\n\r\n").toByteArray(Charsets.UTF_8)
            )
            out.write(body); out.flush()
        }
        fun json(code: String, value: JSONObject) = send(code, value.toString().toByteArray(Charsets.UTF_8))
        fun error(code: String, kind: String, message: String) =
            json(code, JSONObject().put("error", kind).put("message", message))

        val authorization = headers.entries.firstOrNull {
            it.key.equals("Authorization", ignoreCase = true)
        }?.value
        if (!RemoteCommandPolicy.isAuthorized(authorization, token)) {
            error("401 Unauthorized", "token", "需要完全相符的 Authorization: Bearer <token>")
            return
        }

        val request = line.split(" ", limit = 3)
        val method = request.getOrNull(0).orEmpty()
        val path = request.getOrNull(1).orEmpty()
        val remoteRoute = RemoteCommandPolicy.route(method, path)
        if (remoteRoute == RemoteCommandPolicy.Route.METHOD_NOT_ALLOWED) {
            error("405 Method Not Allowed", "method", "/ring 與 /notify 只接受 POST")
            return
        }

        val body = if (remoteRoute == RemoteCommandPolicy.Route.RING ||
            remoteRoute == RemoteCommandPolicy.Route.NOTIFY ||
            remoteRoute == RemoteCommandPolicy.Route.SAY
        ) {
            val lengthText = headers.entries.firstOrNull {
                it.key.equals("Content-Length", ignoreCase = true)
            }?.value
            if (headers.keys.any { it.equals("Transfer-Encoding", ignoreCase = true) }) {
                error("400 Bad Request", "unsupported_encoding", "請使用 Content-Length，不接受分塊請求")
                return
            }
            val length = if (lengthText == null) 0 else lengthText.toIntOrNull() ?: run {
                error("400 Bad Request", "invalid_length", "Content-Length 必須是非負整數")
                return
            }
            if (length < 0 || length > MAX_BODY_BYTES) {
                error("413 Payload Too Large", "body_too_large", "請求內容上限為 $MAX_BODY_BYTES bytes")
                return
            }
            try {
                readBody(input, length)
            } catch (failure: Exception) {
                error("400 Bad Request", "incomplete_body", "請求內容未完整送達")
                return
            }
        } else ""

        val dir = File(filesDir, "手錶資料").apply { mkdirs() }

        when {
            remoteRoute == RemoteCommandPolicy.Route.RING -> {
                val payload = parseObject(body) ?: run {
                    error("400 Bad Request", "invalid_json", "ring 內容必須是 JSON")
                    return
                }
                val rawSeconds = if (payload.has("seconds") && !payload.isNull("seconds")) {
                    payload.opt("seconds").toString()
                } else null
                val seconds = RemoteCommandPolicy.ringSeconds(rawSeconds) ?: run {
                    error("400 Bad Request", "invalid_seconds", "seconds 必須是大於 0 的整數；最高 30 秒")
                    return
                }
                val result = runRemoteCommand { triggerRing(seconds) }
                json(result.status, result.body)
            }
            remoteRoute == RemoteCommandPolicy.Route.NOTIFY -> {
                val payload = parseObject(body) ?: run {
                    error("400 Bad Request", "invalid_json", "notify 內容必須是 JSON")
                    return
                }
                val title = payload.optString("title").trim()
                val text = payload.optString("text").trim()
                if (title.isBlank() || text.isBlank()) {
                    error("400 Bad Request", "missing_text", "title 與 text 都不能空白")
                    return
                }
                if (title.length > 100 || text.length > 2_000) {
                    error("400 Bad Request", "text_too_long", "title 上限 100 字，text 上限 2000 字")
                    return
                }
                val replyRequired = payload.optBoolean(
                    "reply_required",
                    payload.optBoolean("reply", false),
                )
                val result = runRemoteCommand { postRemoteNotification(title, text, replyRequired) }
                json(result.status, result.body)
            }
            path.startsWith("/status") -> {
                val files = dir.listFiles().orEmpty().sortedBy { it.name }
                val js = files.joinToString(",") {
                    """{"day":"${it.nameWithoutExtension}","bytes":${it.length()},""" +
                    """"modified":${it.lastModified()}}"""
                }
                send("200 OK", """{"files":[$js],"now":${System.currentTimeMillis()}}""".toByteArray())
            }
            path.startsWith("/notes") -> {
                // 使用者 自己記的那一份。電腦端拿去跟數據對照 ——
                // **這是整個專案唯一的外部真相來源**，沒有它我只能自己驗自己。
                val f = File(dir, NotesStore.FILE)
                if (f.exists()) send("200 OK", f.readBytes(), "application/x-ndjson")
                else send("404 Not Found", """{"error":"還沒有任何對照筆記"}""".toByteArray())
            }
            path.startsWith("/watchdog") -> {
                // 看門狗的執行軌跡。有這條我才能**知道**它有在跑，
                // 而不是相信它有在跑 —— 那正是這整套要修的病。
                val f = File(filesDir, StaleWatchdog.LOG)
                if (f.exists()) send("200 OK", f.readBytes(), "text/plain")
                else send("404 Not Found", """{"error":"看門狗還沒跑過"}""".toByteArray())
            }
            path.startsWith("/day") -> {
                val d = Regex("d=([0-9-]+)").find(path)?.groupValues?.get(1)
                    ?: SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
                val f = File(dir, "$d.jsonl")
                if (f.exists()) send("200 OK", f.readBytes(), "application/x-ndjson")
                else send("404 Not Found", """{"error":"no data for $d"}""".toByteArray())
            }
            path.startsWith("/summary") -> {
                // 摘要層。一天 11 MB 的原始資料在**手機上**壓成約 9 KB。
                //
                // ⚠ 為什麼要有 `?build=1`：摘要是算出來的，不是收到的。
                //   帶了就當場重算（給我驗證用），不帶就讀現成的（給日常用）。
                //   **分開的理由**：如果每次讀都重算，那「讀到的東西」跟
                //   「排程算出來的東西」就永遠一樣，我就驗不出排程有沒有在跑。
                val d = Regex("d=([0-9-]+)").find(path)?.groupValues?.get(1)
                    ?: SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
                if (path.contains("build=1")) Summary.build(filesDir, d)
                val f = File(File(filesDir, Summary.DIR), "$d.jsonl")
                if (f.exists()) send("200 OK", f.readBytes(), "application/x-ndjson")
                else send("404 Not Found",
                    """{"error":"$d 還沒有摘要（加 &build=1 可以當場算）"}""".toByteArray())
            }
            path.startsWith("/sleep") -> {
                // 三星自己判好的睡眠。**這是我們自己的 app 直接讀 Health Connect** ——
                //
                // 本來借第三方的「Health Connect Webhook」，它 08-27 改版時自己換了鑰匙，
                // 我們的資料**斷了三天而沒有任何紅燈**（每日檢查只驗了手錶那條管路）。
                // 👤 使用者 一句話點破：「你以後是要自動把睡眠拉到我們做的 APP 嗎？」
                //
                // ⚠ runBlocking 在這裡是安全的：這個 handler 本來就跑在自己的 thread 上
                //   （見上面的 thread { handle(sock) }），不是主執行緒。
                val d = Regex("days=([0-9]+)").find(path)?.groupValues?.get(1)?.toIntOrNull() ?: 7
                val js = runBlocking { HealthConnect.睡眠(applicationContext, d.coerceIn(1, 30)) }
                send("200 OK", js.toString().toByteArray())
            }
            remoteRoute == RemoteCommandPolicy.Route.SAY -> {
                // 2026-10-17 手腕對講機「阿寶 → 她」：電腦送一句話，轉給錶唸出來（SayService）。
                // 用 MessageClient：要快，錶不在旁邊就回 409 讓電腦改走 /notify。
                val payload = parseObject(body) ?: run {
                    error("400 Bad Request", "invalid_json", "say 內容必須是 JSON")
                    return
                }
                val text = payload.optString("text").trim()
                if (text.isBlank() || text.length > 300) {
                    error("400 Bad Request", "bad_text", "text 需 1～300 字")
                    return
                }
                val nodes = runCatching {
                    com.google.android.gms.tasks.Tasks.await(
                        com.google.android.gms.wearable.Wearable.getNodeClient(applicationContext).connectedNodes)
                }.getOrDefault(emptyList())
                if (nodes.isEmpty()) {
                    json("409 Conflict", JSONObject().put("ok", false).put("action", "say").put("error", "watch_not_connected"))
                    return
                }
                // 2026-10-17 21:00 speak 旗標（她的設計：她問→唸；我主動→只震＋字）。
                // speak=true 照舊送純文字（舊版錶也看得懂）；speak=false 才送 JSON，錶端看到「{」才解析。
                val speak = payload.optBoolean("speak", true)
                val wire = if (speak) text else JSONObject().put("text", text).put("speak", false).toString()
                var sent = 0
                for (node in nodes) {
                    runCatching {
                        com.google.android.gms.tasks.Tasks.await(
                            com.google.android.gms.wearable.Wearable.getMessageClient(applicationContext)
                                .sendMessage(node.id, "/say", wire.toByteArray(Charsets.UTF_8)))
                        sent++
                    }.onFailure { Log.w(TAG, "say 送不到 ${node.displayName}", it) }
                }
                json(if (sent > 0) "200 OK" else "502 Bad Gateway",
                    JSONObject().put("ok", sent > 0).put("action", "say").put("nodes", sent))
            }
            path.startsWith("/said") -> {
                // 她在錶上說過的話（WearReceiver 收的）。?since=<ms> 只拿之後的。
                val since = Regex("since=([0-9]+)").find(path)?.groupValues?.get(1)?.toLongOrNull() ?: 0L
                val f = File(filesDir, WearReceiver.SAID_FILE)
                val arr = org.json.JSONArray()
                if (f.exists()) {
                    f.readLines().forEach { line ->
                        runCatching { JSONObject(line) }.getOrNull()?.let {
                            if (it.optLong("t_recv") > since) arr.put(it)
                        }
                    }
                }
                json("200 OK", JSONObject().put("lines", arr).put("now", System.currentTimeMillis()))
            }
            path.startsWith("/recordings") -> {
                // 手錶的錄音。2026-10-17 加。
                //
                // 在此之前，錄音走藍牙到手機 `files/錄音/` 之後，**電腦只能用 USB 線＋run-as 撈** ——
                // 👤 使用者：「我偶爾會用錄音來跟你傳消息，在不方便用手機的時候。」
                // 手機要是沒插線，那句話就卡在手機裡。這條路讓它跟生理資料走同一條 Wi-Fi。
                //
                //   GET    /recordings            列出 [{name, bytes, modified}]
                //   GET    /recordings/<name>     下載那一檔（audio/mp4）
                //   DELETE /recordings/<name>     電腦撈完、轉錄完、寫進 jsonl 才會來刪（三個條件在電腦端驗）
                //
                // 檔名只允許單層，擋 `..` 跟 `/`，不然這條路就變成讀整個私有目錄的門。
                val audio = File(filesDir, WearReceiver.AUDIO_DIR).apply { mkdirs() }
                val rest = path.substringBefore('?').removePrefix("/recordings").removePrefix("/")
                if (rest.isEmpty()) {
                    val arr = org.json.JSONArray()
                    audio.listFiles().orEmpty().filter { it.isFile }.sortedBy { it.lastModified() }.forEach {
                        arr.put(JSONObject().put("name", it.name).put("bytes", it.length()).put("modified", it.lastModified()))
                    }
                    json("200 OK", JSONObject().put("files", arr).put("now", System.currentTimeMillis()))
                } else {
                    val name = try { java.net.URLDecoder.decode(rest, "UTF-8") } catch (_: Exception) { "" }
                    if (name.isBlank() || name.contains('/') || name.contains('\\') || name.contains("..")) {
                        error("400 Bad Request", "bad_name", "檔名只能是單層檔名")
                        return
                    }
                    val f = File(audio, name)
                    if (!f.isFile) {
                        error("404 Not Found", "no_such_recording", "沒有這個錄音：$name")
                        return
                    }
                    if (method.equals("DELETE", ignoreCase = true)) {
                        val ok = f.delete()
                        json(if (ok) "200 OK" else "500 Internal Server Error",
                            JSONObject().put("ok", ok).put("action", "delete").put("name", name))
                    } else {
                        send("200 OK", f.readBytes(), "audio/mp4")
                    }
                }
            }
            else -> send("404 Not Found", """{"error":"unknown path"}""".toByteArray())
        }
    }

    private data class CommandResult(val status: String, val body: JSONObject)

    private fun runRemoteCommand(block: () -> CommandResult): CommandResult = try {
        block()
    } catch (failure: SecurityException) {
        CommandResult(
            "403 Forbidden",
            JSONObject().put("error", "permission_denied")
                .put("message", "系統拒絕操作；請檢查通知與震動權限"),
        )
    } catch (failure: Exception) {
        Log.e(TAG, "遠端指令失敗", failure)
        CommandResult(
            "500 Internal Server Error",
            JSONObject().put("error", "command_failed")
                .put("message", "系統未能執行指令；沒有回報成功"),
        )
    }

    private fun parseObject(body: String): JSONObject? = runCatching {
        JSONObject(if (body.isBlank()) "{}" else body)
    }.getOrNull()

    private fun readHttpLine(input: InputStream): String? {
        val bytes = ByteArrayOutputStream()
        while (true) {
            val value = input.read()
            if (value == -1) return if (bytes.size() == 0) null else bytes.toString(Charsets.UTF_8.name())
            if (value == '\n'.code) return bytes.toString(Charsets.UTF_8.name())
            if (value != '\r'.code) bytes.write(value)
            require(bytes.size() <= MAX_HEADER_LINE_BYTES) { "HTTP header line too long" }
        }
    }

    private fun readBody(input: InputStream, length: Int): String {
        val bytes = ByteArray(length)
        var offset = 0
        while (offset < length) {
            val read = input.read(bytes, offset, length - offset)
            if (read < 0) throw IllegalArgumentException("request body ended early")
            offset += read
        }
        return bytes.toString(Charsets.UTF_8)
    }

    private fun triggerRing(seconds: Int): CommandResult {
        if (checkSelfPermission(Manifest.permission.VIBRATE) != PackageManager.PERMISSION_GRANTED) {
            return CommandResult(
                "403 Forbidden",
                JSONObject().put("error", "vibration_permission_missing")
                    .put("message", "app 缺少 VIBRATE 宣告；本次沒有執行響鈴"),
            )
        }
        if (getSystemService(NotificationManager::class.java).currentInterruptionFilter ==
            NotificationManager.INTERRUPTION_FILTER_NONE
        ) {
            return CommandResult(
                "409 Conflict",
                JSONObject().put("error", "do_not_disturb_total_silence")
                    .put("message", "手機勿擾設定禁止所有聲音（包含鬧鐘）；請先允許鬧鐘"),
            )
        }
        val audio = getSystemService(AudioManager::class.java)
        if (audio.getStreamVolume(AudioManager.STREAM_ALARM) <= 0) {
            return CommandResult(
                "409 Conflict",
                JSONObject().put("error", "alarm_volume_zero")
                    .put("message", "手機鬧鐘音量目前是 0；沒有假裝已響鈴"),
            )
        }

        val uri = RingtoneManager.getActualDefaultRingtoneUri(this, RingtoneManager.TYPE_ALARM)
            ?: return CommandResult(
                "409 Conflict",
                JSONObject().put("error", "alarm_tone_missing")
                    .put("message", "手機沒有可用的預設鬧鐘鈴聲"),
            )
        val ringtone = RingtoneManager.getRingtone(this, uri)
            ?: return CommandResult(
                "503 Service Unavailable",
                JSONObject().put("error", "ringtone_unavailable")
                    .put("message", "系統無法開啟預設鬧鐘鈴聲"),
            )
        val alarmAttributes = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build()
        ringtone.audioAttributes = alarmAttributes
        ringtone.isLooping = true
        ringtone.volume = 1f

        val vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            getSystemService(VibratorManager::class.java).defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
        }
        if (!vibrator.hasVibrator()) {
            return CommandResult(
                "503 Service Unavailable",
                JSONObject().put("error", "vibrator_unavailable")
                    .put("message", "這台手機沒有可用的震動器；沒有只做一半"),
            )
        }

        return runCatching {
            synchronized(ringLock) {
                stopActiveRingLocked()
                activeRingtone = ringtone
                activeVibrator = vibrator
                ringtone.play()
                @Suppress("DEPRECATION")
                vibrator.vibrate(
                    VibrationEffect.createWaveform(longArrayOf(0, 700, 300), 0),
                    alarmAttributes,
                )
                val stopThisRing = Runnable {
                    synchronized(ringLock) {
                        // 舊請求的 callback 已開始等待時，不能把新一輪鈴聲停掉。
                        if (activeRingtone === ringtone) stopActiveRingLocked()
                    }
                }
                stopRing = stopThisRing
                check(ringHandler.postDelayed(stopThisRing, seconds * 1_000L))
            }
            CommandResult(
                "200 OK",
                JSONObject().put("ok", true).put("action", "ring").put("seconds", seconds)
                    .put("audibility_verified", false),
            )
        }.getOrElse { failure ->
            synchronized(ringLock) { stopActiveRingLocked() }
            CommandResult(
                if (failure is SecurityException) "403 Forbidden" else "500 Internal Server Error",
                JSONObject().put("error", if (failure is SecurityException) "permission_denied" else "ring_failed")
                    .put("message", "系統未能執行響鈴，請檢查 app 權限與鬧鐘設定"),
            )
        }
    }

    private fun stopActiveRingLocked() {
        stopRing?.let { ringHandler.removeCallbacks(it) }
        stopRing = null
        runCatching { activeRingtone?.stop() }
        runCatching { activeVibrator?.cancel() }
        activeRingtone = null
        activeVibrator = null
    }

    private fun postRemoteNotification(
        title: String,
        text: String,
        replyRequired: Boolean,
    ): CommandResult {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            return CommandResult(
                "403 Forbidden",
                JSONObject().put("error", "notification_permission_missing")
                    .put("message", "請先打開阿寶橋接的通知權限；本次沒有靜默假裝成功"),
            )
        }

        val manager = getSystemService(NotificationManager::class.java)
        if (!manager.areNotificationsEnabled()) {
            return CommandResult(
                "409 Conflict",
                JSONObject().put("error", "notifications_disabled")
                    .put("message", "阿寶橋接的通知已被系統關閉"),
            )
        }
        manager.createNotificationChannel(
            NotificationChannel(REMOTE_CH, "有人在家", NotificationManager.IMPORTANCE_HIGH)
                .apply { description = "從家中電腦送到 使用者 手機的通知" },
        )
        val importance = manager.getNotificationChannel(REMOTE_CH)?.importance
            ?: return CommandResult(
                "503 Service Unavailable",
                JSONObject().put("error", "notification_channel_missing")
                    .put("message", "系統未建立「有人在家」通知頻道"),
            )
        if (importance < NotificationManager.IMPORTANCE_HIGH) {
            return CommandResult(
                "409 Conflict",
                JSONObject().put("error", if (importance == NotificationManager.IMPORTANCE_NONE)
                    "notification_channel_disabled" else "notification_channel_not_high")
                    .put("message", "請在系統設定允許「有人在家」頻道的高重要度通知"),
            )
        }

        val id = notificationIds.incrementAndGet()
        val openApp = PendingIntent.getActivity(
            this,
            id,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val shownTitle = if (replyRequired) "【請回覆】$title" else title
        val notification = Notification.Builder(this, REMOTE_CH)
            .setContentTitle(shownTitle)
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setSubText(if (replyRequired) "需要妳回覆" else "有人在家")
            .setCategory(Notification.CATEGORY_MESSAGE)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentIntent(openApp)
            .setAutoCancel(true)
            .build()
        manager.notify(id, notification)
        return CommandResult(
            "200 OK",
            JSONObject().put("ok", true).put("action", "notify")
                .put("notification_id", id).put("reply_required", replyRequired),
        )
    }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH, "手錶橋接", NotificationManager.IMPORTANCE_MIN)
        )
        return Notification.Builder(this, CH)
            .setContentTitle("阿寶橋接")
            .setContentText("收手錶資料中（port $PORT）")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setOngoing(true)
            .build()
    }

    companion object {
        const val TAG = "WatchBridgePhone"
        const val PORT = 8788
        private const val CH = "bridge"
        private const val REMOTE_CH = "someone_home"
        private const val MAX_BODY_BYTES = 16 * 1024
        private const val MAX_HEADER_LINE_BYTES = 8 * 1024
        // ⚠ 2026-08-27 拿掉了 `DEFAULT_TOKEN`。
        //   **這裡不可以再出現任何「可以當密碼用的字串」** ——
        //   一旦寫進來就會進 git 歷史，而歷史清起來比想像中麻煩。
        //   權杖改成首次啟動隨機產生，見上面 `token` 的說明。

        fun start(ctx: Context) {
            ctx.startForegroundService(Intent(ctx, BridgeServer::class.java))
        }
    }
}
