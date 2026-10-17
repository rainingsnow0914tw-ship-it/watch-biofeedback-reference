package com.example.watchbridge.phone

import android.util.Log
import com.google.android.gms.wearable.DataEvent
import com.google.android.gms.wearable.DataEventBuffer
import com.google.android.gms.wearable.DataMapItem
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.WearableListenerService
import com.google.android.gms.tasks.Tasks
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 手機端：收手錶送過來的生理資料。
 *
 * 為什麼非有這一支不可（2026-08-14 定的）：
 *     手錶一連上藍牙就會自己把 Wi-Fi 關掉，所以「手錶 → 電腦（Wi-Fi）」
 *     只有在她刻意關藍牙時才通。那條路不可能長期用。
 *     而**藍牙是一直開著的** —— 所以資料要走藍牙到手機，再從手機出去。
 *
 *     這也是給家人用的版本唯一可行的路：他們家沒有常開的電腦。
 *
 * 為什麼用 DataClient + Asset 而不是 MessageClient：
 *     MessageClient 是「射後不理」，**斷線就丟**。
 *     DataClient 會自己排隊、自己重送 —— 手機不在身邊時資料留在手錶上，
 *     等下次靠近再補送。對「整夜資料」這種東西，可靠比即時重要。
 *     大塊資料放 Asset 裡，那是它設計來裝二進位負載的地方。
 *
 * ⚠️ 手機端和手錶端**必須是同一個 applicationId、同一把簽章金鑰**。
 *    不一樣的話兩邊看不到對方，而且**完全不會報錯**，只是永遠收不到。
 */
class WearReceiver : WearableListenerService() {

    override fun onDataChanged(events: DataEventBuffer) {
        for (event in events) {
            if (event.type != DataEvent.TYPE_CHANGED) continue
            val item = event.dataItem
            val p = item.uri.path.orEmpty()

            // ★ 2026-08-26：錄音也走同一條藍牙通道進來（使用者 問的）。
            //   放在生理資料那段**之前**只是因為要提早 continue，
            //   不代表優先權 —— 兩者互不影響。
            if (p.startsWith(AUDIO_PATH)) {
                receiveAudio(item)
                continue
            }
            if (!p.startsWith(PATH)) continue

            val map = DataMapItem.fromDataItem(item).dataMap
            val asset = map.getAsset("chunk") ?: continue
            val day = map.getString("day") ?: today()
            val lines = map.getInt("lines", -1)
            // -1 代表這是舊格式的項目（沒帶位移）。舊項目還可能留在佇列裡，
            // 所以不能假設一定有。
            val offset = map.getLong("offset", -1L)

            // 這裡刻意用 Tasks.await 而不是 coroutine：
            // WearableListenerService 的 onDataChanged 本來就跑在背景執行緒上，
            // 阻塞是安全的，而且省掉一整包 coroutine 相依。
            val bytes = runCatching {
                Tasks.await(
                    Wearable.getDataClient(applicationContext).getFdForAsset(asset)
                ).inputStream.use { it.readBytes() }
            }.onFailure { Log.e(TAG, "讀不到 asset", it) }.getOrNull()
            if (bytes == null) continue

            val dir = File(filesDir, "手錶資料").apply { mkdirs() }
            val f = File(dir, "$day.jsonl")

            // ⚠ 2026-08-16 改：本來是無條件 `appendBytes`。
            //
            //     append 有兩個問題，而且都不會報錯：
            //       · 同一塊重送 → 內容被寫**兩次**（實測抓到過 2 行重複）
            //       · 藍牙資料層**不保證送達順序** → 檔案裡的行會亂序
            //
            //     現在手錶把位移寫在項目裡，這裡就照著位移寫到**那個位置**。
            //     於是這條路變成冪等的：
            //       · 重送 = 用一模一樣的內容覆蓋同一段，無害
            //       · 亂序 = 各自寫各自的位置，最後結果一樣
            //     手機的檔案因此是手錶檔案的**逐位元組鏡像**，而不是「大致上一樣」。
            //
            //     副作用：如果後面的塊先到，中間會暫時是一段 0x00。
            //     那幾行 JSON 解析不了會被跳過，等前面的塊補上就自己好了。
            val ok = runCatching {
                if (offset >= 0) {
                    java.io.RandomAccessFile(f, "rw").use { raf ->
                        raf.seek(offset)
                        raf.write(bytes)
                    }
                } else {
                    f.appendBytes(bytes)      // 舊格式的殘留項目
                }
            }.onFailure { Log.e(TAG, "寫檔失敗", it) }.isSuccess

            if (!ok) continue
            Log.i(TAG, "收到 $day @$offset：${bytes.size} bytes / $lines 行 → ${f.length()}")

            // 寫成功才刪。**刪掉之前一定要確定資料已經落地** ——
            // 反過來的話，寫檔失敗又刪了項目，那塊就真的沒了。
            //
            // 非刪不可：資料層是有容量上限的共享空間，不清會一直堆到滿，
            // 之後手錶那邊 putDataItem 就會開始失敗。
            runCatching {
                Tasks.await(Wearable.getDataClient(applicationContext).deleteDataItems(item.uri))
            }.onFailure { Log.w(TAG, "刪不掉已處理的項目 ${item.uri}", it) }
        }
    }

    /**
     * 收手錶送來的錄音檔。
     *
     * 2026-08-26 加。在此之前錄音只能靠 adb 從手錶撈，
     * 而手錶的無線偵錯**會自己關** —— 她 08-25 錄的兩筆躺在錶上沒人撈，
     * 程式還印了「手錶上目前沒有任何錄音檔」。
     *
     * ⚠ **同名先比大小再決定要不要寫。** 三星的檔名是
     *   `語音 001_W_20260825_231643.m4a`，帶了日期時間所以幾乎不會撞名 ——
     *   但「幾乎」不是「不會」，而重送是常態（DataClient 本來就會重送）。
     *   大小一樣就當同一個檔，跳過；不一樣才寫，而且加後綴保住兩份。
     */
    private fun receiveAudio(item: com.google.android.gms.wearable.DataItem) {
        val map = DataMapItem.fromDataItem(item).dataMap
        val asset = map.getAsset("audio") ?: return
        val name = map.getString("name") ?: return
        val size = map.getLong("size", -1L)
        val mtime = map.getLong("mtime", 0L)

        val dir = File(filesDir, AUDIO_DIR).apply { mkdirs() }
        var out = File(dir, name)
        if (out.exists()) {
            if (size >= 0 && out.length() == size) {
                Log.i(TAG, "錄音「$name」已經有了（$size bytes），跳過")
                // 已經有了也要把資料層那筆清掉，否則會一直佔空間。
                runCatching {
                    Tasks.await(Wearable.getDataClient(applicationContext).deleteDataItems(item.uri))
                }
                return
            }
            out = File(dir, name.replace(Regex("(\\.[^.]+)$"), "_$size$1"))
        }

        val bytes = runCatching {
            Tasks.await(Wearable.getDataClient(applicationContext).getFdForAsset(asset))
                .inputStream.use { it.readBytes() }
        }.onFailure { Log.e(TAG, "讀不到錄音 asset", it) }.getOrNull() ?: return

        val ok = runCatching {
            out.writeBytes(bytes)
            if (mtime > 0) out.setLastModified(mtime)
        }.onFailure { Log.e(TAG, "寫錄音檔失敗", it) }.isSuccess
        if (!ok) return
        Log.i(TAG, "收到錄音「${out.name}」${bytes.size} bytes → ${out.absolutePath}")

        // 寫成功才刪 —— 跟生理資料同一條規矩。
        runCatching {
            Tasks.await(Wearable.getDataClient(applicationContext).deleteDataItems(item.uri))
        }.onFailure { Log.w(TAG, "刪不掉已處理的錄音項目 ${item.uri}", it) }
    }

    /**
     * 2026-10-17 手腕對講機：她在錶上按「跟阿寶說」，錶把話變文字送到這裡。
     * 存成一行 JSON，等電腦（每 15 秒）來 GET /said 拿。
     * 用 MessageClient 是因為要快；丟了錶上會顯示「沒送出去」，她可改走錄音機那條。
     */
    override fun onMessageReceived(event: com.google.android.gms.wearable.MessageEvent) {
        if (event.path != SAID_PATH) return
        val raw = String(event.data, Charsets.UTF_8)
        val text = runCatching { org.json.JSONObject(raw).optString("text") }.getOrDefault("").trim()
        if (text.isBlank()) return
        val line = org.json.JSONObject()
            .put("t_recv", System.currentTimeMillis())
            .put("t_watch", runCatching { org.json.JSONObject(raw).optLong("t") }.getOrDefault(0L))
            .put("text", text)
            .put("node", event.sourceNodeId)
        runCatching { File(filesDir, SAID_FILE).appendText(line.toString() + "\n") }
            .onFailure { Log.e(TAG, "她說的話寫不進檔", it) }
        Log.i(TAG, "她在錶上說：$text")
    }

    private fun today() = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())

    companion object {
        const val TAG = "WatchBridgePhone"
        const val PATH = "/bio"
        const val AUDIO_PATH = "/audio"
        const val SAID_PATH = "/said"
        const val SAID_FILE = "她說的.jsonl"
        const val AUDIO_DIR = "錄音"   // filesDir 底下放手錶錄音的資料夾；BridgeServer /recordings 也讀這裡
    }
}
