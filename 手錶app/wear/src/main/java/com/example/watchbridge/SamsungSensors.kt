package com.example.watchbridge

import android.content.Context
import android.util.Log
import com.samsung.android.service.health.tracking.ConnectionListener
import com.samsung.android.service.health.tracking.HealthTracker
import com.samsung.android.service.health.tracking.HealthTrackerException
import com.samsung.android.service.health.tracking.HealthTrackingService
import com.samsung.android.service.health.tracking.data.DataPoint
import com.samsung.android.service.health.tracking.data.HealthTrackerType
import com.samsung.android.service.health.tracking.data.ValueKey
import org.json.JSONObject

/**
 * 三星 Health Sensor SDK —— 皮膚溫度、IBI、EDA、體組成。
 *
 * 為什麼要有這一層（2026-08-12 使用者 的原話）：
 *     「你剛剛說的那些東西手環不就可以代替了嗎？」
 *
 * 她說得對。心率步數卡路里是幾百塊手環就有的，Watch5 Pro 貴在那顆 BioActive
 * 感測器，而那顆只有走三星自己的 SDK 才打得開。Health Services 公開 API
 * **拿不到皮膚溫度、拿不到 IBI**。
 *
 * 這份檔案裡的每一個型別名稱、每一個 ValueKey，都是把 .aar 反編譯出來看到的，
 * 不是從說明書抄的 —— 說明書上連 ValueKey 清單都沒列全。
 *
 * ⚠️ 前提：手錶要開「Health Sensor Service 開發者模式」，否則連線會被
 *     SDK_POLICY 擋下來（app 的簽章沒有在三星那邊註冊）。
 *     開發階段這樣就夠，不用申請合作夥伴；要上架給別人用才要審核。
 *
 * ⚠️ 醫療免責（授權合約第 3.3 條，也是我們自己一開始就定的規矩）：
 *     這些數字**不得用於診斷、治療或任何醫療判斷**。皮膚溫度尤其不是體溫，
 *     只看自己跟自己比的趨勢。
 */
class SamsungSensors(private val ctx: Context) {

    /** 回報給畫面／logcat 的單一出口，讓上層不用認識三星的型別。 */
    fun interface Sink {
        fun onLine(text: String)
    }

    private val motion = MotionMeter()
    private var lastIdleWrite = 0L
    private var service: HealthTrackingService? = null
    private val trackers = mutableListOf<HealthTracker>()
    private var sink: Sink? = null

    /** 這台錶實際支援的清單 —— 連上之後才知道，不預設。 */
    var supported: List<HealthTrackerType> = emptyList()
        private set

    fun start(sink: Sink) {
        this.sink = sink
        val listener = object : ConnectionListener {
            override fun onConnectionSuccess() {
                Log.i(TAG, "三星 SDK 連上了")
                // 把「SDK 通了」寫進資料流。**沒有這一筆，SDK 被擋掉的時候
                // 資料看起來只是「今天剛好沒有皮膚溫度」**，跟正常長得一樣。
                onRecord?.invoke("sdk_status") { put("ok", true) }
                val cap = service?.trackingCapability
                supported = cap?.supportHealthTrackerTypes ?: emptyList()
                Log.i(TAG, "SDK 版本 ${cap?.version}，支援 ${supported.size} 種：")
                supported.forEach { Log.i(TAG, "     $it") }
                sink.onLine("三星 SDK：${supported.size} 種")
                startWanted()
            }

            override fun onConnectionEnded() {
                Log.w(TAG, "三星 SDK 連線結束")
                onRecord?.invoke("sdk_status") { put("ok", false); put("error_code", -1) }
                sink.onLine("SDK 連線結束")
            }

            override fun onConnectionFailed(e: HealthTrackerException) {
                // 這裡最常見的是 SDK_POLICY —— 手錶沒開開發者模式。
                // 一定要把 errorCode 印出來，否則會以為是「不支援」而白白換路線。
                Log.e(TAG, "三星 SDK 連不上 errorCode=${e.errorCode} ${e.message}")
                // ⚠ 這一筆非寫不可（2026-08-16）。
                //
                //   三星官方文件原話：開發者模式「is not intended for app users.
                //   Do not share the developer mode guide with app users.」
                //   但在合作夥伴登記核准之前，家人的錶只能靠這個模式跑。
                //
                //   而它一旦被關掉（恢復原廠、重新配對、Health Sensor Service 被重裝），
                //   **IBI 與皮膚溫度就直接消失** —— 心率照收、動作量照收、
                //   app 沒當、通知還在，只有那兩項悄悄不見了。
                //   **沒有這一筆，沒有人會發現。**
                onRecord?.invoke("sdk_status") {
                    put("ok", false)
                    put("error_code", e.errorCode)
                }
                sink.onLine("SDK 連不上（${e.errorCode}）\n可能沒開開發者模式")
            }
        }
        service = HealthTrackingService(listener, ctx).also { it.connectService() }
    }

    /**
     * 只開「這台錶真的支援」的那幾種，而且只開連續型的。
     * on-demand 型（BIA / ECG）一次只能開一個，而且要使用者主動觸發，
     * 不適合放在背景長跑，之後另外做。
     */
    private fun startWanted() {
        val wanted = listOf(
            HealthTrackerType.SKIN_TEMPERATURE_CONTINUOUS,
            HealthTrackerType.HEART_RATE_CONTINUOUS,
            // ⚠ 2026-08-16：加速度計**搬回這裡**。這是撤回我自己前一天的「優化」。
            //
            //   2026-08-15 我把它換成 Android 的 SensorManager（5 Hz），
            //   理由是省電（5.5%/小時 → 想省成 3%）。當晚看起來很成功：
            //   取樣數從 1496 掉到 300，螢幕關著實測 4 分鐘不漏。
            //
            //   **但那個 4 分鐘測試是在 CPU 醒著的情況下做的。**
            //   隔天分析整晚資料才發現：
            //       人不動 → CPU 睡 → 感測器交付被節流
            //       安靜時**每分鐘只收到 34 筆**（目標 300），87% 的分鐘不足 60 筆
            //       而同一支錶放在充電座上收滿 374 筆（CPU 沒睡）
            //   —— **最需要精度的時候取樣最爛**，整晚的睡眠資料全是廢的。
            //
            //   試過用 maxReportLatencyUs 硬體批次救，**救不回來**。查硬體才知道：
            //       LSM6DSO Accelerometer | FIFO=(300,300) | minRate=6.25Hz | **non-wakeUp**
            //   non-wakeUp = 佇列滿了也不會叫醒 CPU，攢滿就丟。
            //   實測開了批次之後 5 分鐘產出 **0 筆**窗口，比原本更糟。
            //
            //   三星這條路雖然吃電，但它**連續好幾晚穩定給 1490 筆/分鐘**，
            //   包括整夜睡眠。使用者 說過「先不用管電量」「要穩不要快」，
            //   而**睡眠正是這個專案的核心時段**。
            //
            //   教訓：**省電的優化，要在「最省電的情境」下驗證，不是在插著線的情境。**
            HealthTrackerType.ACCELEROMETER_CONTINUOUS,
            // 血氧刻意不放這裡 —— 2026-08-12 實測：SPO2 會霸佔整顆 PPG 感測器。
            // 開了它之後 25 秒內灌了 2656 筆，而心率／IBI／皮膚溫度**全部歸零**，
            // 接著血氧自己也開始回 status=-6。
            // 官方文件只寫「on-demand 一次只能開一個」，沒說它會把連續型的一起餓死。
            // 所以血氧只能做成「使用者主動量一次」，不能常駐。
        )
        // 沒開的：
        //   EDA_CONTINUOUS       這台錶硬體就沒有（已用 dumpsys sensorservice 查證）
        //   ACCELEROMETER / PPG  取樣率 25Hz 以上，開了會把日誌灌爆，
        //                        等資料能存進電腦再開，否則只是製造垃圾
        //   ECG / BIA            要手指按著錶框，不能背景跑（使用者 自己點出來的）
        for (type in wanted) {
            if (type !in supported) {
                Log.w(TAG, "$type 這台錶不支援，跳過")
                continue
            }
            runCatching {
                val t = service!!.getHealthTracker(type)
                t.setEventListener(makeListener(type))
                trackers += t
                Log.i(TAG, "$type 已開始")
            }.onFailure { Log.e(TAG, "$type 開不起來", it) }
        }
        sink?.onLine("已開 ${trackers.size} 個感測器")
    }

    private fun makeListener(type: HealthTrackerType) =
        object : HealthTracker.TrackerEventListener {
            override fun onDataReceived(points: MutableList<DataPoint>) {
                for (p in points) describe(type, p)?.let { Log.i(TAG, it) }
            }

            override fun onFlushCompleted() = Unit

            override fun onError(err: HealthTracker.TrackerError) {
                Log.e(TAG, "$type 出錯：$err")
                sink?.onLine("$type 出錯")
            }
        }

    /** 把 DataPoint 翻成人看得懂的一行。取不到的欄位就跳過，不要硬塞 0。 */
    private fun describe(type: HealthTrackerType, p: DataPoint): String? = runCatching {
        when (type) {
            HealthTrackerType.SKIN_TEMPERATURE_CONTINUOUS -> {
                val skin = p.getValue(ValueKey.SkinTemperatureSet.OBJECT_TEMPERATURE)
                val air = p.getValue(ValueKey.SkinTemperatureSet.AMBIENT_TEMPERATURE)
                // 皮膚溫度單看沒有意義，環境溫度是它的對照組 —— 冷氣房裡手涼
                // 不代表發燒退了。所以兩個一起記，之後才有辦法校正。
                // ⚠ 一定要用 p.getTimestamp()，不能用「收到的當下」。
                // 2026-08-13 實測：三星是**一批一批**送皮膚溫度的，
                // 26 筆不同的溫度值全部被蓋上同一秒（11:53:43）。
                // 這跟前一晚 logcat 那個坑是同一種病：
                // **用「收到的時間」而不是「量測的時間」**，資料看起來好好的但全錯。
                onTemp?.invoke(skin, air)
                onRecord?.invoke("skin_temp") {
                    put("skin", skin); put("watch_ref", air)
                    put("sensor_t", p.timestamp)
                }
                "SKINTEMP 皮膚=%.2f 環境=%.2f".format(skin, air)
            }

            HealthTrackerType.HEART_RATE_CONTINUOUS -> {
                val hr = p.getValue(ValueKey.HeartRateSet.HEART_RATE)
                val st = p.getValue(ValueKey.HeartRateSet.HEART_RATE_STATUS)
                val ibi = p.getValue(ValueKey.HeartRateSet.IBI_LIST)
                val ibiSt = p.getValue(ValueKey.HeartRateSet.IBI_STATUS_LIST)
                // IBI_STATUS 非 0 代表那一拍不可信，算 HRV 前一定要濾掉，
                // 否則一個雜訊就會把 RMSSD 拉高好幾倍。
                val good = ibi.orEmpty().filterIndexed { i, _ -> ibiSt?.getOrNull(i) == 0 }
                // status != 1 代表沒量到（多半是沒戴）。那種情況它照樣每秒送一筆，
                // 一天就是 86400 筆「什麼都沒有」—— 會把硬碟塞滿垃圾，
                // 而且之後分析時要一直濾掉。
                // 但也不能完全不記：**「她把錶脫了」本身就是要知道的事**。
                // 折衷：量到就每筆都記，沒量到就每分鐘記一筆。
                val now = System.currentTimeMillis()
                if (st == 1 || now - lastIdleWrite > 60_000) {
                    if (st != 1) lastIdleWrite = now
                    onRecord?.invoke("hr_ibi") {
                        put("hr", hr)
                        put("status", st)
                        put("ibi", org.json.JSONArray(good))
                        put("sensor_t", p.timestamp)   // 同上：量測時間，不是收到時間
                    }
                }
                // ⚠ 2026-08-25 補：本來這裡只印 `good`（濾過的），
                //   於是「三星沒送 IBI」跟「我們自己濾光了」在 log 上長得一模一樣。
                //   九天下來 99% 的記錄 IBI 是空的，而我**分不出是哪一種** ——
                //   又是同一隻病：儀器本身看不見自己的盲點。
                //   現在把原始筆數與 status 值一起印，一眼就分得出來。
                "HRV HR=$hr status=$st IBIraw=${ibi.orEmpty().size} " +
                    "IBIgood=${good.size} st=${ibiSt.orEmpty().joinToString(",")} " +
                    "IBI=${good.joinToString(",")}"
            }

            HealthTrackerType.ACCELEROMETER_CONTINUOUS -> {
                val w = motion.feed(
                    p.getValue(ValueKey.AccelerometerSet.ACCELEROMETER_X),
                    p.getValue(ValueKey.AccelerometerSet.ACCELEROMETER_Y),
                    p.getValue(ValueKey.AccelerometerSet.ACCELEROMETER_Z),
                    p.timestamp,
                ) ?: return@runCatching null   // 這一分鐘還沒滿，不寫檔也不印
                onRecord?.invoke("motion") {
                    put("samples", w.samples)
                    put("activity", (w.activity * 100).toInt() / 100.0)
                    put("peak", (w.peak * 100).toInt() / 100.0)
                    put("still_ratio", (w.stillRatio * 1000).toInt() / 1000.0)
                    put("sensor_t", p.timestamp)
                    // ⚠ 版本標記非帶不可。2026-08-15/16 三天內動作量換過三種取樣方式，
                    //   尺度全都不同。沒有這個欄位，之後算基線會把三種混在一起，
                    //   而且**算出來的東西看起來完全正常**。
                    put("mv", MV)
                    // 取樣不足的那一分鐘，數值會被灌大（相鄰兩筆隔更久、差更大）。
                    // 標出來讓下游可以整分鐘丟掉，而不是拿去算。
                    put("trust", w.samples >= MIN_TRUSTWORTHY_SAMPLES)
                }
                "MOTION 動作量=%.1f 尖峰=%.1f 靜止比例=%.2f（%d 筆）"
                    .format(w.activity, w.peak, w.stillRatio, w.samples)
            }

            HealthTrackerType.SPO2 -> {
                val st = p.getValue(ValueKey.SpO2Set.STATUS)
                val v = p.getValue(ValueKey.SpO2Set.SPO2)
                // status != 2（CALCULATING/完成）時那個數字沒有意義。
                // 一定要把 status 一起記，否則會把「量測中的暫時值」
                // 當成真的血氧存進資料庫。
                "SPO2 血氧=$v% status=$st hr=${p.getValue(ValueKey.SpO2Set.HEART_RATE)}"
            }

            else -> "$type $p"
        }
    }.getOrNull()

    /**
     * 重新掛一次感測器。
     *
     * 2026-08-13 實測到的問題：錶離開手腕之後三星的追蹤器會停掉，
     * **戴回去它不會自己恢復**。Health Services 那條會自己回來，
     * 三星這條不會 —— 結果就是心率有、皮膚溫度整晚是空的。
     *
     * 所以「從沒戴變成戴上」的時候要主動叫它一次。
     * （使用者 04:36 戴上錶，到 04:50 皮膚溫度一筆都沒有，是她問
     *   「環境 35 度？我開 20 度冷氣」才順藤摸出來的。）
     */
    // ── 隨選體溫 ────────────────────────────────────────────────
    private var 隨選溫度: HealthTracker? = null
    private var 隨選開始 = 0L

    /**
     * **立刻要一筆體溫**，不等 CONTINUOUS 那條的下一批。
     *
     * ## 為什麼需要這個（2026-08-26 使用者 問出來的）
     *
     * 她的原話：「所以我在想到底**你能不能同步看到心率跟體溫的互動**」。
     *
     * 答案是：照原本的做法**不能**，而且差得很遠。實測 45 分鐘：
     * ```
     *   心率    2,689 筆   平均每  1.0 秒一筆
     *   皮膚溫     28 筆   平均每 96.4 秒一筆，而且是三次爆發
     *              → 密度差 96 倍，兩批之間空了 35 分鐘
     * ```
     * 而那 35 分鐘的空窗，正好蓋住她戴上錶的那一刻 ——
     * 系統手上只有一筆「她戴上之前」的溫度，卻要拿它判斷「她現在戴著沒」。
     *
     * **這不是門檻調不好，是拿兩個不同時鐘的東西互相對照。**
     *
     * 這支錶支援 `SKIN_TEMPERATURE_ON_DEMAND`（19 種能力裡有），
     * 所以正確解法不是把溫度的有效期拉長去遷就它，而是**需要的時候直接去要一筆**。
     *
     * ## ⚠ 為什麼做得這麼短命
     *
     * 08-12 開 SPO2（也是 on-demand）那次：**25 秒內灌了 2656 筆，
     * 而心率／IBI／皮膚溫度全部歸零** —— on-demand 會霸佔整顆感測器。
     * 官方只寫「一次只能開一個」，沒說它會把連續型的一起餓死。
     *
     * 所以這裡：**拿到第一筆就立刻關掉**，而且有逾時保險。
     * 呼叫端還要負責限制頻率（見 CollectorService 的 TEMP_ASK_COOLDOWN_MS）。
     *
     * @return true 表示有送出請求（不保證拿得到）
     */
    fun requestTempNow(): Boolean {
        if (HealthTrackerType.SKIN_TEMPERATURE_ON_DEMAND !in supported) return false
        if (隨選溫度 != null) return false          // 已經有一個在跑，不疊
        val svc = service ?: return false
        val t = runCatching {
            svc.getHealthTracker(HealthTrackerType.SKIN_TEMPERATURE_ON_DEMAND)
        }.getOrNull() ?: return false
        隨選溫度 = t
        隨選開始 = System.currentTimeMillis()
        runCatching {
            t.setEventListener(object : HealthTracker.TrackerEventListener {
                override fun onDataReceived(points: MutableList<DataPoint>) {
                    for (p in points) runCatching {
                        val skin = p.getValue(ValueKey.SkinTemperatureSet.OBJECT_TEMPERATURE)
                        val air = p.getValue(ValueKey.SkinTemperatureSet.AMBIENT_TEMPERATURE)
                        onTemp?.invoke(skin, air)
                        onRecord?.invoke("skin_temp") {
                            put("skin", skin); put("watch_ref", air)
                            put("sensor_t", p.timestamp)
                            put("on_demand", true)   // ★ 標記來源，之後分析要分得出來
                        }
                        Log.i(TAG, "隨選體溫 皮膚=%.2f 環境=%.2f".format(skin, air))
                    }
                    closeTempNow()      // **拿到就走，一秒都不多佔**
                }
                override fun onFlushCompleted() = Unit
                override fun onError(err: HealthTracker.TrackerError) {
                    Log.e(TAG, "隨選體溫出錯：$err"); closeTempNow()
                }
            })
        }.onFailure { closeTempNow(); return false }
        return true
    }

    /** 關掉隨選體溫。**逾時也要關** —— 開著不關就是 08-12 那個把別人餓死的形狀。 */
    fun closeTempNow() {
        隨選溫度?.let { runCatching { it.unsetEventListener() } }
        隨選溫度 = null
    }

    /** 逾時保險：呼叫端每分鐘叫一次，超過 20 秒還沒拿到就強制關掉。 */
    fun sweepTempNow(now: Long) {
        if (隨選溫度 != null && now - 隨選開始 > 20_000L) {
            Log.w(TAG, "隨選體溫 20 秒沒回應 —— 強制關掉，不讓它繼續佔感測器")
            closeTempNow()
        }
    }

    fun restartTrackers() {
        closeTempNow()
        trackers.forEach { runCatching { it.unsetEventListener() } }
        trackers.clear()
        if (supported.isNotEmpty()) startWanted() else runCatching { service?.connectService() }
    }

    fun stop() {
        trackers.forEach { runCatching { it.unsetEventListener() } }
        trackers.clear()
        runCatching { service?.disconnectService() }
        service = null
    }

    companion object {
        const val TAG = "WatchBridge"

        /**
         * 動作量的指標版本。**尺度不同的版本絕對不能互相比較。**
         *
         *   v1  三星 SDK 25 Hz，三星原始單位　　（2026-08-13 ~ 08-15）
         *   v2  SensorManager 5 Hz，毫 m/s²　　（08-15 深夜 ~ 08-16）
         *       ⚠ **這一版的資料在安靜時是壞的** —— CPU 睡著時交付被節流，
         *         每分鐘只剩 34 筆取樣，數值被灌大。不要拿去算任何基線。
         *   v3  同 v2 + 硬體批次　　　　　　　　（08-16，只活了幾分鐘）
         *       ⚠ 加速度計是 non-wakeUp、FIFO 只有 300 筆 —— 批次反而完全不送，
         *         5 分鐘產出 0 筆窗口。**實際上沒有 v3 的資料。**
         *   v4  三星 SDK 25 Hz　　　　　　　　　（08-16 之後）
         *       **尺度與 v1 相同**（同一個 MotionMeter、同一個門檻 30、同樣的原始單位），
         *       所以 v1 與 v4 的資料**可以合併比較**。分開編號只是為了保留時間軸。
         */
        const val MV = 4

        /**
         * 一分鐘至少要這麼多筆取樣，那一分鐘才可信。
         *
         * 三星這條路實測穩定給 1490 筆/分鐘，所以 700 是很寬鬆的下限 ——
         * 它要抓的是「這一分鐘明顯不對勁」，不是日常波動。
         */
        const val MIN_TRUSTWORTHY_SAMPLES = 700

        /**
         * 收集服務掛在這裡，把每一筆量到的東西寫進檔案。
         *
         * 為什麼是 companion 上的一個變數，而不是建構子參數：
         * 這一層本來就只會有一個實例在跑，多繞一層依賴注入沒有換到任何東西。
         * 沒掛的時候（例如只跑畫面版）就只寫 logcat，不會壞。
         */
        var onRecord: ((String, JSONObject.() -> Unit) -> Unit)? = null

        /**
         * 皮膚溫度的**即時**管線 —— 跟 onRecord 分開走。
         *
         * onRecord 收到的是「要寫進檔案的 JSON」，佩戴判斷讀不到裡面的值。
         * 但 2026-08-25 的洞需要**在判斷的當下**就知道溫差是正是負，
         * 所以這裡直接把兩個數字交出去，不經過序列化。
         */
        var onTemp: ((Float, Float) -> Unit)? = null
    }
}
