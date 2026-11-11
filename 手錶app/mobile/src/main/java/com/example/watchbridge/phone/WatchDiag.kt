package com.example.watchbridge.phone

import android.content.Context
import android.util.Log
import com.google.android.gms.tasks.Tasks
import com.google.android.gms.wearable.MessageClient
import com.google.android.gms.wearable.Wearable
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * 等 /diag_reply 回來（最多 12 秒），整包交回 BridgeServer。
 * 用 live listener 收回覆（不改 manifest）：BridgeServer 在跑＝程序活著＝listener 收得到。
 */
object WatchDiag {
    private const val TAG = "WatchDiag"
    private const val WAIT_MS = 12_000L

    fun ask(ctx: Context, cmd: String): JSONObject {
        val nodes = runCatching { Tasks.await(Wearable.getNodeClient(ctx).connectedNodes, 5, TimeUnit.SECONDS) }
            .getOrDefault(emptyList())
        if (nodes.isEmpty()) return JSONObject().put("ok", false).put("error", "watch_not_connected")
        val id = UUID.randomUUID().toString().take(8)
        val replies = ConcurrentHashMap<String, JSONObject>()
        val latch = CountDownLatch(nodes.size)
        val mc = Wearable.getMessageClient(ctx)
        val listener = MessageClient.OnMessageReceivedListener { ev ->
            if (ev.path != "/diag_reply") return@OnMessageReceivedListener
            val j = runCatching { JSONObject(String(ev.data, Charsets.UTF_8)) }.getOrNull() ?: return@OnMessageReceivedListener
            if (j.optString("id") != id) return@OnMessageReceivedListener
            if (replies.putIfAbsent(ev.sourceNodeId, j) == null) latch.countDown()
        }
        runCatching { Tasks.await(mc.addListener(listener), 5, TimeUnit.SECONDS) }
        val names = HashMap<String, String>()
        try {
            val req = JSONObject().put("id", id).put("cmd", cmd).toString().toByteArray(Charsets.UTF_8)
            for (n in nodes) {
                names[n.id] = n.displayName
                runCatching { Tasks.await(mc.sendMessage(n.id, "/diag", req), 5, TimeUnit.SECONDS) }
                    .onFailure { Log.w(TAG, "送不到 ${n.displayName}", it); latch.countDown() }
            }
            latch.await(WAIT_MS, TimeUnit.MILLISECONDS)
        } finally {
            runCatching { mc.removeListener(listener) }
        }
        val arr = JSONArray()
        for (n in nodes) {
            val r = replies[n.id]
            arr.put((r ?: JSONObject().put("ok", false).put("error", "no_reply（錶的 App 可能是 code92 以前的版本，或藍牙剛好斷）"))
                .put("node", n.displayName).put("node_id", n.id))
        }
        return JSONObject().put("ok", replies.isNotEmpty()).put("cmd", cmd).put("watches", arr)
            .apply { if (replies.isEmpty()) put("error", "no_reply") }
    }
}
