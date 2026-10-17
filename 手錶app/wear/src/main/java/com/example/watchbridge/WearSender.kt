package com.example.watchbridge

import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.Asset
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.Wearable
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 手錶端：把當天的資料檔一段一段送去手機。
 *
 * 為什麼走藍牙不走 Wi-Fi（2026-08-14 定案的理由）：
 *     手錶一連上藍牙就會自己把 Wi-Fi 關掉，這是 Samsung 的省電策略，改不了。
 *     所以「手錶→電腦（Wi-Fi）」只有在她刻意關藍牙時才通 —— 不可能長期用。
 *     **藍牙是一直開著的**，走它才是唯一能長跑的路。
 *     而且家人那邊沒有常開的電腦，只有手機 —— 這條路是那邊唯一可行的。
 *
 * 為什麼用 DataClient + Asset：
 *     MessageClient 是射後不理，**斷線就丟**。
 *     DataClient 自己排隊、自己重送 —— 手機不在身邊時資料留在手錶上，
 *     等靠近再補送。對「整夜的資料」來說，可靠比即時重要。
 *
 * 斷點怎麼記：
 *     只記「已經送到第幾個 byte」，不記內容也不記行號。
 *     檔案是純 append 的，所以 offset 永遠有效；
 *     而且**寧可重送也不要漏送** —— 電腦端本來就有一套用指紋去重的邏輯。
 */
class WearSender(private val ctx: Context) {

    private val prefs = ctx.getSharedPreferences("sender", Context.MODE_PRIVATE)
    private val client by lazy { Wearable.getDataClient(ctx) }

    /**
     * 送一次。回傳這次送了幾個 byte（0 = 全部都送完了）。
     *
     * ⚠ 2026-08-15 修的跨日 bug：
     *     這支本來只看**今天**的檔案。午夜一過 day 就換掉，
     *     昨天最後那幾分鐘沒送完的部分**永遠被遺忘**。
     *     實測抓到：手機端 08-14 的檔案停在 23:57:41，但那天到 23:59:59 才結束。
     *
     *     現在改成：先送今天的（新資料對警報最重要），
     *     今天的送完了才回頭補前幾天沒送完的。
     */
    fun sendOnce(): Int {
        val dir = File(ctx.filesDir, "生理資料")
        val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())

        // ── 背壓：手機不在的時候不要一直往佇列裡塞 ──────────────
        // 送不出去的項目會留在手錶的資料層裡。塞太多之後 putDataItem 會開始
        // 失敗，而失敗的樣子不保證漂亮。與其等它壞，不如自己先停手 ——
        // **資料本來就好好地留在手錶的檔案裡，晚點送不會少一個 byte。**
        val pending = outstanding()
        if (pending >= MAX_PENDING) {
            Log.w(TAG, "佇列已有 $pending 塊沒送達（手機大概不在），這次不送")
            return 0
        }

        // 今天優先 —— 新資料對警報最重要。
        val n = sendDay(today)

        // ⚠ 2026-08-16 修的「補送餓死」bug：
        //     這裡本來是 `for (d in days) { if (sendDay(d) > 0) return ... }`。
        //     今天的檔案**每分鐘都在長**（約 10 KB），所以 sendDay(today)
        //     永遠回傳大於零，迴圈**永遠跑不到第二圈**。
        //     舊日期的積壓因此一輩子送不出去。
        //
        //     實測證據：08-13 的檔案 14,554,119 bytes，offset 停在 4,678,309 ——
        //     **68% 卡在手錶上，而那天正好是家裡出事那天。**
        //
        //     現在改成：今天送一塊，**另外**再給最舊的未完成日期一塊。
        //     兩邊都保證有進度，而且每次呼叫最多兩塊，藍牙吃得消。
        val backfill = dir.listFiles()
            .orEmpty()
            .mapNotNull { it.name.removeSuffix(".jsonl").takeIf { d -> d != today && d.length == 10 } }
            .sorted()                    // 由舊到新 —— 最舊的先補完，免得永遠有東西插隊
            // ⚠ 2026-10-02：不能先 take(7)。錶上留了 51 天檔案，最舊
            // 7 天全已送完，10-01 的最後 2,752 bytes 因此永遠沒被看到。
            // 逐日查本機長度與 offset 很便宜；仍只送最舊欠的一塊。
            .firstOrNull { d -> pendingBytes(d) > 0 }
            ?.let { sendDay(it) } ?: 0

        return n + backfill
    }

    /** 這一天還有幾個 byte 沒送。 */
    private fun pendingBytes(day: String): Long {
        val f = File(File(ctx.filesDir, "生理資料"), "$day.jsonl")
        if (!f.exists()) return 0
        val sent = prefs.getLong("offset_$day", 0L)
        return (f.length() - sent).coerceAtLeast(0)
    }

    /** 資料層裡還有幾塊沒被手機取走。取不到就當作 0（不要因為查不到就停止送）。 */
    private fun outstanding(): Int = runCatching {
        com.google.android.gms.tasks.Tasks.await(
            client.dataItems, 15, java.util.concurrent.TimeUnit.SECONDS
        ).use { buf -> buf.count { it.uri.path.orEmpty().startsWith("/bio/") } }
    }.getOrDefault(0)

    /** 送某一天的下一塊。回傳送了幾個 byte。 */
    private fun sendDay(day: String): Int {
        val f = File(File(ctx.filesDir, "生理資料"), "$day.jsonl")
        if (!f.exists()) return 0

        val key = "offset_$day"
        val sent = prefs.getLong(key, 0L)
        val total = f.length()
        // 檔案變小 = 換日或被清掉，offset 要歸零，否則會永遠讀不到東西
        val from = if (sent > total) 0L else sent
        if (from >= total) return 0

        val take = minOf(CHUNK, total - from)
        val buf = ByteArray(take.toInt())

        // ⚠ 2026-08-15 修：這裡本來是 `f.inputStream().use { it.skip(from); it.read(buf) }`。
        //
        //     `skip()` 和 `read(buf)` **都不保證做滿**：
        //       · skip 少跳 → 從**錯的位置**開始讀 → 送出去的是錯位的資料
        //       · read 少讀 → 一次只送到一小塊，追不上產生速度
        //     實測症狀：8 分鐘只追回 20 KB，而手錶每分鐘產生約 10 KB —— 越追越遠。
        //
        //     RandomAccessFile 的 seek + readFully 是**精確**的：
        //     位置一定對，讀不滿會直接丟例外而不是默默少讀。
        java.io.RandomAccessFile(f, "r").use { raf ->
            raf.seek(from)
            raf.readFully(buf)
        }

        // 只送完整的行 —— 半行 JSON 到了手機端會變成壞資料
        val lastNl = buf.lastIndexOf('\n'.code.toByte())
        if (lastNl < 0) return 0
        val chunk = buf.copyOf(lastNl + 1)

        // ⚠ 2026-08-16 修的**靜默吃資料** bug：路徑本來是固定的 `/bio/$day`。
        //
        //     資料層是「一個路徑存一個項目」。手機不在身邊時，項目排在佇列裡等，
        //     而**下一塊用同一個路徑 put 進去，會直接把還沒送出去的那塊蓋掉**。
        //     偏偏 putDataItem 只是寫到本機資料層就算成功 —— 於是 offset 照樣往前，
        //     被蓋掉的那塊**永遠不會再送，也永遠不會有人知道**。
        //
        //     手機離開 30 分鐘 ≈ 6 塊被蓋成 1 塊，中間五塊人間蒸發。
        //     出國、手機沒電、關機一晚 —— 都會這樣。
        //
        //     現在路徑帶上位移：每一塊都是獨立的項目，誰都蓋不到誰。
        //     而且手機端可以**照著位移寫到檔案的正確位置** ——
        //     重複送變成覆寫同樣的內容（無害），順序亂掉也不影響結果。
        val req = PutDataMapRequest.create("/bio/$day/$from").apply {
            dataMap.putAsset("chunk", Asset.createFromBytes(chunk))
            dataMap.putString("day", day)
            dataMap.putLong("offset", from)
            dataMap.putInt("lines", chunk.count { it == '\n'.code.toByte() })
        }.asPutDataRequest().setUrgent()

        return try {
            // ⚠ 2026-08-15 修的致命 bug：這裡本來是 `Tasks.await(task)`，**沒有逾時**。
            //
            //     沒逾時的 await 就是無限期阻塞。藍牙那層只要卡住一次，
            //     這個呼叫**永遠不會回來** —— 而它跑在計時器的執行緒上，
            //     那條執行緒一死，之後所有的傳送就再也不會發生。
            //
            //     症狀非常陰險：**收資料照常**（不同執行緒），app 也活著、
            //     前景服務也正常，只有「送」這件事無聲地停在某一刻。
            //     實測：手錶累積到 5.11 MB，手機只收到 4.33 MB，差 780 KB。
            //
            //     教訓：**任何在週期性工作裡的 await／join，一律要給逾時。**
            com.google.android.gms.tasks.Tasks.await(
                client.putDataItem(req), 45, java.util.concurrent.TimeUnit.SECONDS
            )
            prefs.edit().putLong(key, from + chunk.size).apply()
            Log.i(TAG, "送出 ${chunk.size} bytes（$day，累計 ${from + chunk.size}/$total）")
            chunk.size
        } catch (e: Exception) {
            // 失敗**不要**推進 offset —— 下次原地重送。
            Log.w(TAG, "送不出去（${e.javaClass.simpleName}: ${e.message}）")
            0
        }
    }

    companion object {
        const val TAG = "WatchBridge"
        private const val CHUNK = 180_000L

        /**
         * 資料層裡最多容許幾塊沒送達。
         *
         * 40 塊 × 180 KB ≈ 7 MB。超過就代表手機已經離開一段時間了，
         * 再塞下去只是把資料層撐爆。停手是安全的 —— **檔案還在手錶上，
         * offset 也沒推進，等手機回來就從原地接著送。**
         */
        private const val MAX_PENDING = 40
    }
}
