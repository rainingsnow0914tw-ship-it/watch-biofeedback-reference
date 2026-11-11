package com.example.watchbridge

import android.content.Context
import android.util.Log
import com.samsung.android.service.health.tracking.ConnectionListener
import com.samsung.android.service.health.tracking.HealthTracker
import com.samsung.android.service.health.tracking.HealthTrackerException
import com.samsung.android.service.health.tracking.HealthTrackingService
import com.samsung.android.service.health.tracking.data.DataPoint
import com.samsung.android.service.health.tracking.data.HealthTrackerType
import com.samsung.android.service.health.tracking.data.PpgType
import com.samsung.android.service.health.tracking.data.ValueKey
import org.json.JSONObject

/**
 * 三星 Health Sensor SDK —— 皮膚溫度、IBI、EDA、體組成。
 *
 *
 * 感測器，而那顆只有走三星自己的 SDK 才打得開。Health Services 公開 API
 * **拿不到皮膚溫度、拿不到 IBI**。
 *
 * 這份檔案裡的每一個型別名稱、每一個 ValueKey，都是把 .aar 反編譯出來看到的，
 * 不是從說明書抄的 —— 說明書上連 ValueKey 清單都沒列全。
 *
 *     SDK_POLICY 擋下來（app 的簽章沒有在三星那邊註冊）。
 *     開發階段這樣就夠，不用申請合作夥伴；要上架給別人用才要審核。
 *
 *     只看自己跟自己比的趨勢。
 */
class SamsungSensors(private val ctx: Context) {

    /** 回報給畫面／logcat 的單一出口，讓上層不用認識三星的型別。 */
    fun interface Sink {
        fun onLine(text: String)
    }

    private val motion = MotionMeter()
    private var lastIdleWrite = 0L
    // 每一小段的起點留在 seg：[[sensor_t, 這段幾拍], …]，電腦端 ibi_windows 拆回原樣算 HRV，空檔不會被藏起來。
    private val ibiBuf = mutableListOf<Long>()
    private val rawBuf = mutableListOf<Long>()
    private val rawStBuf = mutableListOf<Int>()
    private val segBuf = org.json.JSONArray()
    private val hrBuf = mutableListOf<Int>()
    private var batchStart = 0L
    private var batchSensorT = 0L

    private fun flushIbi() {
        if (hrBuf.isEmpty()) return
        val hrs = hrBuf.toList(); val ibis = ibiBuf.toList(); val seg = org.json.JSONArray(segBuf.toString()); val t0 = batchSensorT
        val raws = rawBuf.toList(); val rawSts = rawStBuf.toList()
        hrBuf.clear(); ibiBuf.clear(); rawBuf.clear(); rawStBuf.clear(); while (segBuf.length() > 0) segBuf.remove(0); batchStart = 0L
        onRecord?.invoke("hr_ibi") {
            put("hr", hrs.last())
            put("status", 1)
            put("ibi", org.json.JSONArray(ibis))
            put("sensor_t", t0)
            put("hrs", org.json.JSONArray(hrs))
            put("seg", seg)
            put("batch", 15)
            put("ibi_raw", org.json.JSONArray(raws))
            put("ibi_st", org.json.JSONArray(rawSts))
        }
    }
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
                Log.e(TAG, "三星 SDK 連不上 errorCode=${e.errorCode} ${e.message}")
                //
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
     * 不適合放在背景長跑，之後另外做。
     */
    private fun startWanted() {
        val wanted = listOf(
            HealthTrackerType.SKIN_TEMPERATURE_CONTINUOUS,
            HealthTrackerType.HEART_RATE_CONTINUOUS,
            //
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
            //   而**睡眠正是這個專案的核心時段**。
            //
            HealthTrackerType.ACCELEROMETER_CONTINUOUS,
            // 開了它之後 25 秒內灌了 2656 筆，而心率／IBI／皮膚溫度**全部歸零**，
            // 接著血氧自己也開始回 status=-6。
        )
        // 沒開的：
        //   EDA_CONTINUOUS       這台錶硬體就沒有（已用 dumpsys sensorservice 查證）
        //   ACCELEROMETER / PPG  取樣率 25Hz 以上，開了會把日誌灌爆，
        //                        等資料能存進電腦再開，否則只是製造垃圾
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

    private fun describe(type: HealthTrackerType, p: DataPoint): String? = runCatching {
        when (type) {
            HealthTrackerType.SKIN_TEMPERATURE_CONTINUOUS -> {
                val skin = p.getValue(ValueKey.SkinTemperatureSet.OBJECT_TEMPERATURE)
                val air = p.getValue(ValueKey.SkinTemperatureSet.AMBIENT_TEMPERATURE)
                // 皮膚溫度單看沒有意義，環境溫度是它的對照組 —— 冷氣房裡手涼
                // 不代表發燒退了。所以兩個一起記，之後才有辦法校正。
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
                // 而且之後分析時要一直濾掉。
                // 折衷：量到就每筆都記，沒量到就每分鐘記一筆。
                val now = System.currentTimeMillis()
                if (st == 1) {
                    if (hrBuf.isEmpty()) { batchStart = now; batchSensorT = p.timestamp }
                    hrBuf += hr
                    ibiBuf += good.map { it.toLong() }
                    ibi.orEmpty().forEachIndexed { i, v -> rawBuf += v.toLong(); rawStBuf += (ibiSt?.getOrNull(i) ?: -9) }
                    segBuf.put(org.json.JSONArray().put(p.timestamp).put(good.size))
                    if (now - batchStart >= 15_000) flushIbi()
                } else {
                    flushIbi()   // 狀態變了（脫錶）：先把攢著的寫掉
                }
                if (st != 1 && now - lastIdleWrite > 60_000) {
                    lastIdleWrite = now
                    onRecord?.invoke("hr_ibi") {
                        put("hr", hr)
                        put("status", st)
                        put("ibi", org.json.JSONArray(good))
                        put("sensor_t", p.timestamp)   // 同上：量測時間，不是收到時間
                    }
                }
                //   九天下來 99% 的記錄 IBI 是空的，而我**分不出是哪一種** ——
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
                    //   tilt 0°＝錶面朝上平放（桌上）、~60～90°＝手腕自然垂著、180°＝面朝下。
                    put("g", org.json.JSONArray().put((w.gx * 100).toInt() / 100.0).put((w.gy * 100).toInt() / 100.0).put((w.gz * 100).toInt() / 100.0))
                    put("tilt", (w.tiltDeg * 10).toInt() / 10.0)
                    put("sensor_t", p.timestamp)
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
                // 當成真的血氧存進資料庫。
                "SPO2 血氧=$v% status=$st hr=${p.getValue(ValueKey.SpO2Set.HEART_RATE)}"
            }

            else -> "$type $p"
        }
    }.getOrNull()

    /**
     * 重新掛一次感測器。
     *
     * **戴回去它不會自己恢復**。Health Services 那條會自己回來，
     * 三星這條不會 —— 結果就是心率有、皮膚溫度整晚是空的。
     *
     */
    // ── 隨選體溫 ────────────────────────────────────────────────
    private var 隨選溫度: HealthTracker? = null
    private var 隨選開始 = 0L

    /**
     * **立刻要一筆體溫**，不等 CONTINUOUS 那條的下一批。
     *
     *
     *
     * 答案是：照原本的做法**不能**，而且差得很遠。實測 45 分鐘：
     * ```
     *   心率    2,689 筆   平均每  1.0 秒一筆
     *   皮膚溫     28 筆   平均每 96.4 秒一筆，而且是三次爆發
     *              → 密度差 96 倍，兩批之間空了 35 分鐘
     * ```
     *
     * **這不是門檻調不好，是拿兩個不同時鐘的東西互相對照。**
     *
     * 這支錶支援 `SKIN_TEMPERATURE_ON_DEMAND`（19 種能力裡有），
     * 所以正確解法不是把溫度的有效期拉長去遷就它，而是**需要的時候直接去要一筆**。
     *
     * ## ⚠ 為什麼做得這麼短命
     *
     * 而心率／IBI／皮膚溫度全部歸零** —— on-demand 會霸佔整顆感測器。
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

    // ── 隨選探針：心電圖（ECG）／體組成（BIA）────────────────────
    //
    //
    //   ECG 一批記 status(LEAD_OFF)、筆數、sequence 頭尾、前 5 個 mV 樣本；
    //
    private var 探針: HealthTracker? = null
    private var 探針種類: String? = null
    private var 探針批次 = 0
    private var 探針筆數 = 0
    private val 探針計時 = android.os.Handler(android.os.Looper.getMainLooper())
    private val 探針逾時 = Runnable { stopProbe("timeout_${PROBE_SECONDS}s") }

    /**
     * 回傳給畫面看的一句話。失敗原因（不支援、被佔用、SDK 丟例外）**都會寫進資料檔**，不吞。
     */
    fun startProbe(kind: String, profile: com.samsung.android.service.health.tracking.data.TrackerUserProfile?): String {
        val type = when (kind) {
            "ecg" -> HealthTrackerType.ECG_ON_DEMAND
            "bia" -> HealthTrackerType.BIA_ON_DEMAND
            else -> return "不認識的探針 $kind"
        }
        val rec = "${kind}_probe"
        if (探針 != null) {
            onRecord?.invoke(rec) { put("event", "busy"); put("busy_with", 探針種類) }
            return "上一個還在量（${探針種類}），等它結束"
        }
        val svc = service
        if (svc == null) {
            onRecord?.invoke(rec) { put("event", "error"); put("error", "sdk_not_connected") }
            return "三星 SDK 還沒連上"
        }
        if (type !in supported) {
            onRecord?.invoke(rec) { put("event", "error"); put("error", "not_supported"); put("supported", supported.size) }
            return "這台錶的能力清單沒有 $type"
        }
        val t = try {
            if (kind == "bia" && profile != null) svc.getHealthTracker(type, profile) else svc.getHealthTracker(type)
        } catch (e: Throwable) {
            // 被佔用／政策擋／任何 SDK 例外都走這裡。message 原文照記，errorCode 有就記。
            val code = (e as? HealthTrackerException)?.errorCode
            Log.e(TAG, "$kind 探針 getHealthTracker 失敗", e)
            onRecord?.invoke(rec) {
                put("event", "error"); put("error", "get_tracker_failed")
                put("exception", e.javaClass.simpleName); put("message", e.message ?: "")
                if (code != null) put("error_code", code)
            }
            return "開不起來：${e.javaClass.simpleName} ${code ?: ""} ${e.message ?: ""}".trim()
        }
        探針 = t; 探針種類 = kind; 探針批次 = 0; 探針筆數 = 0
        val startedAt = System.currentTimeMillis()
        onRecord?.invoke(rec) {
            put("event", "start"); put("tracker", type.name)
            if (kind == "bia") put("profile_source", if (profile != null) "prefs_or_default" else "none")
        }
        val ok = runCatching {
            t.setEventListener(object : HealthTracker.TrackerEventListener {
                override fun onDataReceived(points: MutableList<DataPoint>) {
                    探針批次 += 1; 探針筆數 += points.size
                    val batch = 探針批次
                    val total = 探針筆數
                    onRecord?.invoke(rec) {
                        put("event", "data"); put("batch", batch); put("n", points.size)
                        put("total", total); put("ms_since_start", System.currentTimeMillis() - startedAt)
                        if (points.isNotEmpty()) {
                            put("sensor_t_first", points.first().timestamp)
                            put("sensor_t_last", points.last().timestamp)
                        }
                        when (kind) {
                            "ecg" -> describeEcgBatch(points, this)
                            "bia" -> describeBiaBatch(points, this)
                        }
                    }
                    if (batch == 1 || batch % 10 == 0) probeStatus?.invoke("$kind 收到第 $batch 批，共 $total 筆")
                    if (kind == "bia") {
                        val done = points.any { p ->
                            runCatching { (p.getValue(ValueKey.BiaSet.PROGRESS) ?: 0f) >= 1f }.getOrDefault(false)
                        }
                        if (done) 探針計時.post { stopProbe("bia_complete") }
                    }
                }
                override fun onFlushCompleted() {
                    onRecord?.invoke(rec) { put("event", "flush_completed") }
                }
                override fun onError(err: HealthTracker.TrackerError) {
                    // SDK_POLICY_ERROR＝app 簽章沒在三星註冊（夥伴資格），開發者模式沒開也會。
                    // PERMISSION_ERROR＝BODY_SENSORS 沒給。兩個都要留在檔案裡，不能只印 logcat。
                    Log.e(TAG, "$kind 探針出錯：$err")
                    onRecord?.invoke(rec) { put("event", "error"); put("error", err.name) }
                    probeStatus?.invoke("$kind 出錯：${err.name}")
                    探針計時.post { stopProbe("error_${err.name}") }
                }
            })
        }.isSuccess
        if (!ok) {
            onRecord?.invoke(rec) { put("event", "error"); put("error", "set_listener_failed") }
            探針 = null; 探針種類 = null
            return "setEventListener 失敗"
        }
        探針計時.postDelayed(探針逾時, PROBE_SECONDS * 1000L)
        return "開始量 ${PROBE_SECONDS} 秒，手指不要放開"
    }

    /** 收工：flush → unset → 寫一行 end。[why] 是誰叫停的（timeout_30s／bia_complete／error_*／service_stop）。 */
    fun stopProbe(why: String) {
        val t = 探針 ?: return
        val kind = 探針種類 ?: "?"
        探針計時.removeCallbacks(探針逾時)
        val flushed = runCatching { t.flush() }.getOrDefault(false)
        runCatching { t.unsetEventListener() }
        探針 = null; 探針種類 = null
        val batches = 探針批次
        val total = 探針筆數
        onRecord?.invoke("${kind}_probe") {
            put("event", "end"); put("why", why)
            put("batches", batches); put("total", total); put("flushed", flushed)
        }
        Log.i(TAG, "$kind 探針結束（$why）：$batches 批 $total 筆")
        probeStatus?.invoke(
            if (total > 0) "$kind 量完：$total 筆（$why）" else "$kind 結束，0 筆（$why）"
        )
    }

    /** ECG 一批：status（LEAD_OFF）、sequence 頭尾、前 5 個 mV、PPG 綠光第一筆、閾值。只記，不判。 */
    private fun describeEcgBatch(points: List<DataPoint>, o: JSONObject) {
        val mv = points.mapNotNull { p -> runCatching { p.getValue(ValueKey.EcgSet.ECG_MV) }.getOrNull() }
        val lead = points.mapNotNull { p -> runCatching { p.getValue(ValueKey.EcgSet.LEAD_OFF) }.getOrNull() }
        val seq = points.mapNotNull { p -> runCatching { p.getValue(ValueKey.EcgSet.SEQUENCE) }.getOrNull() }
        o.put("lead_off_values", org.json.JSONArray(lead.distinct()))
        o.put("status", lead.firstOrNull() ?: JSONObject.NULL)
        if (seq.isNotEmpty()) { o.put("seq_first", seq.first()); o.put("seq_last", seq.last()) }
        o.put("mv_n", mv.size)
        o.put("mv_head", org.json.JSONArray(mv.take(5).map { (it * 1000).toInt() / 1000.0 }))
        points.firstOrNull()?.let { p ->
            runCatching { p.getValue(ValueKey.EcgSet.PPG_GREEN) }.getOrNull()?.let { o.put("ppg_green_first", it) }
            runCatching { p.getValue(ValueKey.EcgSet.MAX_THRESHOLD_MV) }.getOrNull()?.let { o.put("max_threshold_mv", it) }
            runCatching { p.getValue(ValueKey.EcgSet.MIN_THRESHOLD_MV) }.getOrNull()?.let { o.put("min_threshold_mv", it) }
        }
    }

    private fun describeBiaBatch(points: List<DataPoint>, o: JSONObject) {
        val last = points.lastOrNull() ?: return
        runCatching { last.getValue(ValueKey.BiaSet.STATUS) }.getOrNull()?.let { o.put("status", it) }
        runCatching { last.getValue(ValueKey.BiaSet.PROGRESS) }.getOrNull()?.let { o.put("progress", it) }
        val fields = listOf(
            "BODY_FAT_RATIO" to ValueKey.BiaSet.BODY_FAT_RATIO,
            "BODY_FAT_MASS" to ValueKey.BiaSet.BODY_FAT_MASS,
            "TOTAL_BODY_WATER" to ValueKey.BiaSet.TOTAL_BODY_WATER,
            "SKELETAL_MUSCLE_RATIO" to ValueKey.BiaSet.SKELETAL_MUSCLE_RATIO,
            "SKELETAL_MUSCLE_MASS" to ValueKey.BiaSet.SKELETAL_MUSCLE_MASS,
            "BASAL_METABOLIC_RATE" to ValueKey.BiaSet.BASAL_METABOLIC_RATE,
            "FAT_FREE_RATIO" to ValueKey.BiaSet.FAT_FREE_RATIO,
            "FAT_FREE_MASS" to ValueKey.BiaSet.FAT_FREE_MASS,
            "BODY_IMPEDANCE_MAGNITUDE" to ValueKey.BiaSet.BODY_IMPEDANCE_MAGNITUDE,
            "BODY_IMPEDANCE_DEGREE" to ValueKey.BiaSet.BODY_IMPEDANCE_DEGREE,
        )
        val present = fields.filter { (_, k) ->
            val v = runCatching { last.getValue(k) }.getOrNull()
            v != null && v.isFinite() && v != 0f
        }.map { it.first }
        o.put("fields_with_value", org.json.JSONArray(present))
    }

    private var ppgTracker: HealthTracker? = null
    private val ppgLock = Any()
    private val ppgT = ArrayList<Long>(1024)
    private val ppgV = ArrayList<Double>(1024)
    private var ppgBadStatus = 0
    private val ppgHandler = android.os.Handler(android.os.Looper.getMainLooper())

    val ppgSupported: Boolean get() = HealthTrackerType.PPG_CONTINUOUS in supported

    /** 開綠光 PPG 量 seconds 秒，量完在主執行緒呼叫 done(時間戳, 綠光值, status 非 0 的筆數)。回 started／busy／unsupported／… */
    fun startPpgWindow(seconds: Int, done: (LongArray, DoubleArray, Int) -> Unit): String {
        if (ppgTracker != null) return "busy"
        if (!ppgSupported) return "unsupported"
        val svc = service ?: return "no_service"
        return runCatching {
            val t = svc.getHealthTracker(HealthTrackerType.PPG_CONTINUOUS, setOf(PpgType.GREEN))
            synchronized(ppgLock) { ppgT.clear(); ppgV.clear(); ppgBadStatus = 0 }
            t.setEventListener(object : HealthTracker.TrackerEventListener {
                override fun onDataReceived(points: MutableList<DataPoint>) {
                    synchronized(ppgLock) {
                        for (p in points) {
                            val g = runCatching { p.getValue(ValueKey.PpgSet.PPG_GREEN) }.getOrNull() ?: continue
                            val st = runCatching { p.getValue(ValueKey.PpgSet.GREEN_STATUS) }.getOrNull()
                            if (st != null && st != 0) ppgBadStatus++
                            ppgT += p.timestamp; ppgV += g.toDouble()
                        }
                    }
                }
                override fun onFlushCompleted() = Unit
                override fun onError(err: HealthTracker.TrackerError) { Log.e(TAG, "PPG 出錯：$err") }
            })
            ppgTracker = t
            ppgHandler.postDelayed({ finishPpg(done) }, seconds * 1000L)
            Log.i(TAG, "🫀 PPG 備援開始量 ${seconds} 秒")
            "started"
        }.getOrElse { ppgTracker = null; "start_failed: ${it.toString().take(120)}" }
    }

    private fun finishPpg(done: ((LongArray, DoubleArray, Int) -> Unit)?) {
        val t = ppgTracker ?: return
        runCatching { t.unsetEventListener() }
        ppgTracker = null
        val (ts, vs, bad) = synchronized(ppgLock) { Triple(ppgT.toLongArray(), ppgV.toDoubleArray(), ppgBadStatus) }
        Log.i(TAG, "🫀 PPG 量完：${ts.size} 筆（status≠0：$bad）")
        done?.invoke(ts, vs, bad)
    }

    /** code103：連上過（拿到支援清單）才算 connected。 */
    val connected: Boolean get() = supported.isNotEmpty()

    /** code103：bind 卡住時整個重來 —— 斷掉舊的 HealthTrackingService，用同一個 sink 重新 start。 */
    fun rebind(): String {
        val s = sink ?: return "no_sink"
        trackers.forEach { runCatching { it.unsetEventListener() } }
        trackers.clear()
        runCatching { service?.disconnectService() }
        service = null
        return runCatching { start(s); "rebinding" }.getOrElse { "rebind_failed: ${it.toString().take(120)}" }
    }

    fun restartTrackers() {
        closeTempNow()
        trackers.forEach { runCatching { it.unsetEventListener() } }
        trackers.clear()
        if (supported.isNotEmpty()) startWanted() else runCatching { service?.connectService() }
    }

    fun stop() {
        runCatching { flushIbi() }
        stopProbe("service_stop")
        ppgHandler.removeCallbacksAndMessages(null); finishPpg(null)
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
         *       ⚠ **這一版的資料在安靜時是壞的** —— CPU 睡著時交付被節流，
         *         每分鐘只剩 34 筆取樣，數值被灌大。不要拿去算任何基線。
         *       ⚠ 加速度計是 non-wakeUp、FIFO 只有 300 筆 —— 批次反而完全不送，
         *         5 分鐘產出 0 筆窗口。**實際上沒有 v3 的資料。**
         *       **尺度與 v1 相同**（同一個 MotionMeter、同一個門檻 30、同樣的原始單位），
         *       所以 v1 與 v4 的資料**可以合併比較**。分開編號只是為了保留時間軸。
         */
        const val MV = 4

        /**
         * 一分鐘至少要這麼多筆取樣，那一分鐘才可信。
         *
         * 三星這條路實測穩定給 1490 筆/分鐘，所以 700 是很寬鬆的下限 ——
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
         * 所以這裡直接把兩個數字交出去，不經過序列化。
         */
        var onTemp: ((Float, Float) -> Unit)? = null

        /** 探針量幾秒。三星自家 ECG 也是 30 秒。 */
        const val PROBE_SECONDS = 30

        /** 探針的進度一句話，給畫面顯示用（CollectorService 轉成廣播）。 */
        var probeStatus: ((String) -> Unit)? = null
    }
}
