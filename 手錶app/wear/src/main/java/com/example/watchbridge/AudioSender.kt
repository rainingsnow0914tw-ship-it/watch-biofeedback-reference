package com.example.watchbridge

import android.content.Context
import android.util.Log
import com.google.android.gms.tasks.Tasks
import com.google.android.gms.wearable.Asset
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.Wearable
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * 把手錶上的錄音，**走藍牙**送到手機。
 *
 * ## 為什麼要有這支（2026-08-26 使用者 問的）
 *
 * > 「你的藍牙不能把錄音導傳過來嗎，這樣子以後就不用一直開無線偵錯」
 *
 * **她問得對，而且答案是通道早就在了。**
 * 生理資料一天穩定送 11,000 筆走的就是 `DataClient` + `Asset`，
 * 而 `Asset` 本來就是設計來傳二進位檔案的 —— 錄音就是二進位檔案。
 *
 * ## 這支解掉的問題有多真實
 *
 * 在此之前錄音只能靠 adb 撈，而手錶的無線偵錯**會自己關**。
 * 結果：**她 08-25 23:16 和 08-26 00:06 錄的兩筆，躺在錶上沒人撈**，
 * 而程式還印了「手錶上目前沒有任何錄音檔」。
 *
 * 那兩筆撈回來之後，第二筆（「躺在床上滑手機」）當場抓到一個
 * 會安靜污染睡眠分析的東西 —— **躺著滑手機的活動量 21，跟睡著的 19 分不出來。**
 * 錄音的價值是真的，所以送達的路必須可靠。
 *
 * ## ⚠ 為什麼送完不刪
 *
 * `Tasks.await(putDataItem)` 回來只代表**寫進本機資料層**，不代表手機收到了。
 * 這跟 `WearSender` 那個 offset bug 是同一課：
 * **「我送出去了」跟「它到了」是兩件事。**
 * 所以這裡只在 prefs 記「送過了」，檔案留在錶上，
 * 等別的路徑確認手機有了再刪。錄音一筆 70~150 KB，一天幾筆，留著不痛。
 */
class AudioSender(private val ctx: Context) {

    private val prefs = ctx.getSharedPreferences("audio_sender", Context.MODE_PRIVATE)

    /**
     * 資料層裡**現在還在**的 `/audio` 項目路徑。
     *
     * ★ 這是唯一可信的回執：**手機寫檔成功之後會把項目刪掉**，
     *   所以「項目不見了」＝「真的送到了」。
     */
    private fun livePaths(): Set<String> = runCatching {
        Tasks.await(Wearable.getDataClient(ctx).dataItems, 20, TimeUnit.SECONDS)
            .use { buf ->
                buf.mapNotNull { it.uri.path }.filter { it.startsWith("$PATH/") }.toSet()
            }
    }.getOrElse {
        Log.w(TAG, "問不到資料層現況，這輪不動已送出的狀態：$it")
        // ⚠ 問不到的時候回 null 語意的空集合是**危險**的 ——
        //   那會讓「還沒送到」被誤判成「已送到」。所以呼叫端要另外判斷。
        emptySet()
    }

    /**
     * 送一輪。回傳這次送出幾個檔。
     *
     * ## ⚠⚠ 2026-08-26/27：**這裡曾經有一個安靜掉檔的 bug**
     *
     * 第一版拿 `Tasks.await(putDataItem)` 成功當成「送到了」，直接記 `sent=true`。
     * 結果她 08-26 19:53 錄的那筆，手錶記著「已送出」、**手機七小時後還是空的**，
     * 而且**永遠不會重試** —— 因為狀態已經寫成送過了。
     *
     * 最諷刺的是：**這個風險我自己在下面的註解裡寫過**
     * （「送出去了跟它到了是兩件事」），寫完還是照樣拿 putDataItem 當回執。
     *
     * **註解寫對了不等於程式做對了。**
     *
     * 現在改成三段：
     * ```
     * ① 先問資料層：上一輪送的還在不在？不在了 = 手機吃掉了 = 真的送達
     * ② 還在而且超過 STUCK_MS → 卡住了，改個欄位重送（同內容不會觸發變更）
     * ③ 沒送過的才送新的
     * ```
     */
    fun sendOnce(): Int {
        var n = 0
        // ── ① 收回執 ──────────────────────────────────────────
        val live = livePaths()
        for ((k, v) in prefs.all) {
            if (!k.startsWith("pending_")) continue
            val path = v as? String ?: continue
            if (path !in live) {
                prefs.edit().remove(k).putBoolean("done_" + k.removePrefix("pending_"), true).apply()
                Log.i(TAG, "錄音送達確認：${k.removePrefix("pending_")}（資料層已清空）")
            }
        }

        for (f in scan()) {
            val key = "${f.name}_${f.length()}"
            if (prefs.getBoolean("done_$key", false)) continue
            // ── ② 卡住的要重送 ────────────────────────────────
            val pendingPath = prefs.getString("pending_$key", null)
            if (pendingPath != null) {
                val since = prefs.getLong("at_$key", 0L)
                if (System.currentTimeMillis() - since < STUCK_MS) continue   // 還在路上，等
                Log.w(TAG, "錄音「${f.name}」卡在資料層 ${(System.currentTimeMillis() - since) / 60000} 分鐘 —— 重送")
            }
            // 還在錄的檔案不要送 —— 大小會變，送出去是半截的。
            if (System.currentTimeMillis() - f.lastModified() < STILL_RECORDING_MS) {
                Log.i(TAG, "「${f.name}」剛動過，可能還在錄，這輪先跳過")
                continue
            }
            if (f.length() > MAX_BYTES) {
                Log.w(TAG, "「${f.name}」${f.length() / 1024} KB 太大，跳過")
                prefs.edit().putBoolean("done_$key", true).apply()   // 別每輪都重試
                continue
            }
            val bytes = runCatching { f.readBytes() }
                .onFailure { Log.e(TAG, "讀不到「${f.name}」", it) }.getOrNull() ?: continue

            // 路徑帶上大小 —— 同名不同內容才不會互相蓋掉（跟 /bio 帶 offset 同一個道理）。
            val path = "$PATH/${f.name.hashCode()}_${f.length()}"
            val tries = prefs.getInt("try_$key", 0) + 1
            val req = PutDataMapRequest.create(path).apply {
                dataMap.putAsset("audio", Asset.createFromBytes(bytes))
                dataMap.putString("name", f.name)
                dataMap.putLong("mtime", f.lastModified())
                dataMap.putLong("size", f.length())
                // ⚠ **重送一定要改到內容**，否則 DataClient 當成沒變、不發事件。
                //   2026-08-26 實測：同路徑同內容重送，putDataItem 回成功但什麼都沒發生。
                dataMap.putInt("try", tries)
            }.asPutDataRequest().setUrgent()

            val ok = runCatching {
                Tasks.await(Wearable.getDataClient(ctx).putDataItem(req), 60, TimeUnit.SECONDS)
            }.onFailure { Log.e(TAG, "送不出「${f.name}」", it) }.isSuccess
            if (ok) {
                // ★ 只記「在路上」，**不記「送到了」** —— 回執要等資料層那筆消失。
                prefs.edit()
                    .putString("pending_$key", path)
                    .putLong("at_$key", System.currentTimeMillis())
                    .putInt("try_$key", tries)
                    .apply()
                Log.i(TAG, "錄音「${f.name}」${f.length() / 1024} KB 交給資料層（第 $tries 次），等回執")
                n++
            }
            if (n >= MAX_PER_ROUND) break     // 一輪最多幾個，別把藍牙塞爆
        }
        return n
    }

    /**
     * 找錄音。**兩個資料夾都要找，而且要進子資料夾。**
     *
     * ⚠ 三星把錄音放在 `Recordings/Voice Recorder/` 這個**子資料夾**裡，
     *   而且會隨版本搬家（08-20 那筆在 `Music/Voice Recorder/`，
     *   08-25 之後在 `Recordings/Voice Recorder/`）。
     *   所以不要寫死路徑，遞迴找。
     */
    private fun scan(): List<File> = DIRS.flatMap { d ->
        runCatching {
            File(d).walkTopDown().maxDepth(3)
                .filter { file ->
                    file.isFile &&
                        EXT.any { e -> file.name.lowercase().endsWith(e) } &&
                        isVoiceNote(file)
                }
                .toList()
        }.getOrElse {
            Log.w(TAG, "看不到 $d（可能沒有 READ_MEDIA_AUDIO 權限）：$it")
            emptyList()
        }
    }.sortedBy { it.lastModified() }

    /**
     * 是不是**她錄的**，而不是手錶自己帶的音檔。
     *
     * ⚠ 2026-08-26 差點出事：`/storage/emulated/0/Music/` 底下有
     *   `Samsung/Over_the_Horizon.mp3` —— **三星的內建鈴聲**。
     *   第一版只用副檔名過濾，會把鈴聲當成她的錄音送過去。
     *
     *   這是「**只看形狀不看來源**」的典型錯：`.mp3` 只說明它是音檔，
     *   不說明它是誰放的。所以再加一層來源判斷。
     */
    private fun isVoiceNote(f: File): Boolean {
        val path = f.absolutePath
        if (path.contains("/Samsung/")) return false        // 內建資源
        if (f.name.startsWith(".")) return false            // 縮圖／暫存
        // 三星錄音程式一律放在 `.../Voice Recorder/` 底下，檔名是「語音 001_W_...」。
        return path.contains("Voice Recorder") ||
            f.name.startsWith("語音") ||
            f.name.startsWith("Voice")
    }

    companion object {
        const val TAG = "WatchBridge"
        const val PATH = "/audio"
        private val DIRS = listOf(
            "/storage/emulated/0/Recordings",
            "/storage/emulated/0/Music",
            "/storage/emulated/0/Sounds",
        )
        private val EXT = listOf(".m4a", ".mp3", ".amr", ".wav", ".3gp", ".ogg")

        /** 最後修改在這個時間內就當成「可能還在錄」。 */
        private const val STILL_RECORDING_MS = 60_000L

        /**
         * 交給資料層之後多久還沒被吃掉，就當成卡住、重送。
         *
         * 30 分鐘：正常送達是幾分鐘內的事（實測 08-27 重送後約 16 分鐘內落地，
         * 而那 16 分鐘大部分是在等下一輪傳送輪）。留一點餘裕給手機不在身邊的情況 ——
         * **手機不在的時候本來就送不到，那不是卡住。**
         */
        private const val STUCK_MS = 30 * 60_000L

        /** 單檔上限。一分鐘語音大約 150 KB。 */
        private const val MAX_BYTES = 2_000_000L

        private const val MAX_PER_ROUND = 3
    }
}
