package com.example.watchbridge.phone

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 *
 * 在家：電腦走 Wi-Fi 直接問這支 app（/said、/say），快。
 *   錶說一句 → 這裡 POST /said 到中轉站 → 電腦拿去想 → 電腦 POST /say 到中轉站 → 這裡 GET /say 拿回來 → 轉給錶唸。
 *
 *
 * 設定：電腦在家時 POST /relay_config {url, token} 寫進來（SharedPreferences "relay"），不寫死在程式裡。
 */
object Relay {
    private const val TAG = "WatchBridgePhone"
    private const val PREF = "relay"
    private const val ACTIVE_MS = 10 * 60_000L
    private const val FAST_MS = 2_000L
    private const val SLOW_MS = 60_000L

    @Volatile private var lastSaidAt = 0L
    @Volatile private var burstUntil = 0L
    @Volatile private var running = false
    private const val BURST_MS = 1_000L
    private const val BURST_WINDOW_MS = 30_000L

    fun configured(ctx: Context): Boolean = url(ctx).isNotBlank() && token(ctx).isNotBlank()
    fun url(ctx: Context): String = ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).getString("url", "").orEmpty().trimEnd('/')
    fun token(ctx: Context): String = ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).getString("token", "").orEmpty()
    private fun sinceId(ctx: Context): Long = ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).getLong("since", 0L)
    private fun setSince(ctx: Context, v: Long) = ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).edit().putLong("since", v).apply()

    fun configure(ctx: Context, url: String, token: String) {
        ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).edit()
            .putString("url", url.trim().trimEnd('/')).putString("token", token.trim()).apply()
        Log.i(TAG, "中轉站設定好了：${url.trim()}")
        start(ctx)
    }

    private val outbox = java.util.concurrent.ConcurrentLinkedQueue<JSONObject>()
    private const val OUTBOX_MAX = 50

    fun onSaid(ctx: Context, text: String, tWatch: Long, node: String?) {
        lastSaidAt = System.currentTimeMillis()
        burstUntil = lastSaidAt + BURST_WINDOW_MS
        if (!configured(ctx)) return
        outbox.add(JSONObject().put("text", text).put("t_watch", tWatch).put("node", node).put("queued_at", lastSaidAt))
        while (outbox.size > OUTBOX_MAX) outbox.poll()
        thread(isDaemon = true) { flushOutbox(ctx) }
    }

    /** 把留著的話依序送上中轉站；送失敗就停（下一個輪詢 tick 再試，爆衝模式每 1 秒一次）。 */
    @Synchronized
    private fun flushOutbox(ctx: Context) {
        while (true) {
            val item = outbox.peek() ?: return
            if (System.currentTimeMillis() - item.optLong("queued_at") > 30 * 60_000L) { outbox.poll(); continue }   // 半小時前的話不送了
            try {
                post(ctx, "/said", item)
                outbox.poll()
                Log.i(TAG, "中轉站 /said 送出（剩 ${outbox.size}）：${item.optString("text").take(30)}")
            } catch (e: Exception) {
                Log.w(TAG, "中轉站 /said 送不到（留著重試，剩 ${outbox.size}）：${e.message}")
                burstUntil = System.currentTimeMillis() + BURST_WINDOW_MS   // 有話沒送出去就保持每秒重試
                return
            }
        }
    }

    @Synchronized
    fun start(ctx: Context) {
        if (running) return
        running = true
        val app = ctx.applicationContext
        thread(isDaemon = true, name = "relay-poll") {
            while (true) {
                if (configured(app)) {
                    if (outbox.isNotEmpty()) flushOutbox(app)
                    runCatching { pollOnce(app) }.onFailure { Log.w(TAG, "中轉站 /say 拿不到：${it.message}") }
                }
                val now = System.currentTimeMillis()
                val wait = when {
                    now < burstUntil -> BURST_MS
                    now - lastSaidAt < ACTIVE_MS -> FAST_MS
                    else -> SLOW_MS
                }
                try { Thread.sleep(wait) } catch (_: InterruptedException) { return@thread }
            }
        }
    }

    private fun pollOnce(ctx: Context) {
        val since = sinceId(ctx)
        val res = get(ctx, "/say?since=$since")
        val lines = res.optJSONArray("lines") ?: return
        var newest = since
        for (i in 0 until lines.length()) {
            val it = lines.getJSONObject(i)
            val id = it.optLong("id")
            if (id <= since) continue
            newest = maxOf(newest, id)
            val text = it.optString("text").trim()
            if (text.isBlank()) continue
            val speak = it.optBoolean("speak", true)
            val buzz = it.optString("buzz", ""); val ask = it.optBoolean("ask", false); val wake = it.optBoolean("wake", false)
            lastSaidAt = System.currentTimeMillis()
            val n = sendToWatch(ctx, text, speak, buzz, ask, wake)
            Log.i(TAG, "中轉站 → 錶（speak=$speak，nodes=$n）：$text")
        }
        if (newest != since) setSince(ctx, newest)
    }

    /** 跟 BridgeServer 的 POST /say 同一條線：speak=true 送純文字（舊版錶相容），false 才送 JSON。回送到幾個節點。 */
    /** speak=true 且沒有附加欄位才送純文字（舊版錶相容）；否則送 JSON。 */
    fun wireFor(text: String, speak: Boolean, buzz: String = "", ask: Boolean = false, wake: Boolean = false): String =
        if (!RemoteCommandPolicy.sayNeedsJson(speak, buzz, ask, wake)) text
        else JSONObject().put("text", text).put("speak", speak).apply {
            if (buzz.isNotBlank()) put("buzz", buzz); if (ask) put("ask", true); if (wake) put("wake", true)
        }.toString()

    fun sendToWatch(ctx: Context, text: String, speak: Boolean, buzz: String = "", ask: Boolean = false, wake: Boolean = false): Int {
        val wire = wireFor(text, speak, buzz, ask, wake)
        val nodes = runCatching {
            com.google.android.gms.tasks.Tasks.await(
                com.google.android.gms.wearable.Wearable.getNodeClient(ctx).connectedNodes)
        }.getOrDefault(emptyList())
        var sent = 0
        for (node in nodes) {
            runCatching {
                com.google.android.gms.tasks.Tasks.await(
                    com.google.android.gms.wearable.Wearable.getMessageClient(ctx)
                        .sendMessage(node.id, "/say", wire.toByteArray(Charsets.UTF_8)))
                sent++
            }.onFailure { Log.w(TAG, "say 送不到 ${node.displayName}", it) }
        }
        return sent
    }

    private fun open(ctx: Context, path: String, method: String): HttpURLConnection =
        (URL(url(ctx) + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 8_000; readTimeout = 12_000
            setRequestProperty("Authorization", "Bearer ${token(ctx)}")
            setRequestProperty("Content-Type", "application/json; charset=utf-8")
        }

    private fun get(ctx: Context, path: String): JSONObject {
        val c = open(ctx, path, "GET")
        try {
            if (c.responseCode != 200) throw IllegalStateException("HTTP ${c.responseCode}")
            return JSONObject(c.inputStream.bufferedReader(Charsets.UTF_8).readText())
        } finally { c.disconnect() }
    }

    private fun post(ctx: Context, path: String, body: JSONObject): JSONObject {
        val c = open(ctx, path, "POST")
        try {
            c.doOutput = true
            c.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            if (c.responseCode != 200) throw IllegalStateException("HTTP ${c.responseCode}")
            return JSONObject(c.inputStream.bufferedReader(Charsets.UTF_8).readText())
        } finally { c.disconnect() }
    }
}
