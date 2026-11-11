package com.example.watchbridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.health.services.client.HealthServices
import androidx.health.services.client.MeasureCallback
import androidx.health.services.client.data.Availability
import androidx.health.services.client.data.DataPointContainer
import androidx.health.services.client.data.DataType
import androidx.health.services.client.data.DataTypeAvailability
import androidx.health.services.client.data.DeltaDataType
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 長駐收集服務。
 *
 *     前一版把感測器掛在 Activity 上。手錶螢幕一暗，系統就把 Activity 凍結，
 *     **app 還活著（pidof 有值）但一筆資料都不送**。
 *
 * 為什麼要自己寫檔，不再用 logcat（同一晚查出來的）：
 *     logcat 是除錯用的環形緩衝區，幾 MB 就滿，滿了從頭蓋掉。
 *     而手錶硬碟有 8.3 GB、空著 5.2 GB —— 一天的資料才幾 MB，
 *     存一年都用不完。**資料本來就不該放在除錯通道裡。**
 *
 *     以前斷線＝資料真的消失；現在斷線只是晚一點搬回電腦。
 *       那個問題治不好，但可以讓它不再要命。）
 *
 *         不在手上 ＋ 充電中   → 拿下來充電，正常
 *         不在手上 ＋ 沒在充電 → 脫了放在旁邊 ← 這個才要注意
 *
 * ⚠️ 這一版**還沒有**急/不急的判斷（狀態機）和藍牙警報。
 *    那是下一步。這一版只保證一件事：**戴著就一直收，而且不會掉。**
 */
class CollectorService : Service() {

    private val measureClient by lazy { HealthServices.getClient(this).measureClient }
    private val samsung by lazy { SamsungSensors(this) }
    private val motion by lazy { MotionSampler(this, hz = 5) }
    private val env by lazy { EnvSampler(this) }

    private lateinit var out: File
    private var hrCount = 0

    // 心率降頻用的狀態。詳細理由寫在 onDataReceived 裡。
    private var lastHrBpm = 0
    private var lastHrAt = 0L
    private var lastHrCount = 0

    /**
     * 開機的那個瞬間（epoch 毫秒）。
     *
     * 用 `elapsedRealtime()` 反推：現在的牆鐘時間 − 開機到現在的時間。
     *
     * 每次重算的話同一個樣本在不同時候會算出不同的時間。
     */
    private val bootMs =
        System.currentTimeMillis() - android.os.SystemClock.elapsedRealtime()
    /**
     * 錶在不在手上 —— **三態，不是布林**。
     *
     *
     *   一、**預設 true = 零證據就宣稱戴著。** 服務剛啟動、第一個
     *
     *   二、**`ACQUIRING`（還在找訊號）被算成在手上。**
     *       實測三天：ACQUIRING 出現 33/8/17 次，比 AVAILABLE 還多。
     *
     * 為什麼這件事很嚴重：離腕放在充電座上的錶，動作量比睡著的人還靜
     * （實測 219 分鐘、中位 42、95.9% 低於任何合理門檻）。
     * 而那個閘門本來預設開著。
     *
     */
    private var onBody: String = ONBODY_UNKNOWN

    private var onBodyAt = 0L

    /** 最後一次量到有效脈搏。**跟 availability 完全獨立的一條證據。** */
    private var lastPulseAt = 0L

    /**
     * 最後一次皮膚溫度，以及**溫差**（皮膚 − 錶身）。
     *
     * 而**矛盾的時候需要的是第三票，不是把其中一票調大聲。**
     */
    private var lastTempAt = 0L
    private var lastTempDelta = 0.0
    private var lastSkinC = Double.NaN
    private var lastMotion = Double.NaN
    private var lastMotionAt = 0L

    private var lastKickAt = 0L

    /** 上次主動去要一筆體溫的時刻。 */
    private var lastTempAskAt = 0L

    /**
     *
     * **這是唯一一條不是感測器給的證據，而它比任何感測器都可靠。**
     */
    @Volatile private var userOffAt = 0L
    private val offPrefs by lazy {
        getSharedPreferences("collector_off_wrist", Context.MODE_PRIVATE)
    }

    private fun persistUserOffAt(markedAt: Long) {
        userOffAt = markedAt
        if (!offPrefs.edit().putLong("user_off_at", markedAt).commit()) {
            Log.e(TAG, "脫錶標記未能持久化；本次服務仍保留記憶體標記")
        }
    }

    private fun expireUserOffAt(now: Long) {
        if (userOffAt > 0 && UserOffPolicy.restore(userOffAt, now) == 0L) {
            userOffAt = 0L
            offPrefs.edit().remove("user_off_at").apply()
            Log.i(TAG, "舊脫錶標記已過期；後續回到感測器證據，無證據就 unknown")
        }
    }

    /**
     *
     * 充電會把錶身加熱到 34~35°C，拿下來戴上之後溫差是強烈負值卻其實戴著
     */
    private var lastChargingAt = 0L

    /**
     * 佩戴狀態 —— **多證據融合的結果**，不是單一感測器的回報。
     *
     *
     * >   佩戴狀態該用多證據融合，比如心率 IBI、動作量，
     *
     *
     *
     * 現在三條證據各自獨立記錄，融合在這裡發生，而且**結論帶來源**：
     *
     * | 回傳 | 意思 | 什麼時候 |
     * |---|---|---|
     * | `on` | 裝置**主動說**在手上 | availability = AVAILABLE 且沒過期 |
     * | `off` | 裝置**主動說**不在手上 | availability = OFF_BODY 且沒過期 |
     * | `inferred_on` | 裝置沒在講話，**但脈搏還在** | availability 過期／不確定，但近期有有效心跳 |
     * | `unknown` | 既沒人講話，也沒有生命跡象 | 兩邊都沒有 |
     *
     * ⚠️ **`inferred_on` 比 `on` 弱，下游要分得出來。**
     *    但它遠比 `unknown` 強 —— 脈搏是物理事實，不是推論。
     *
     *    就算還讀到殘留訊號也不能推翻它 —— 那多半是雜訊或錶壓在什麼東西上。
     */
    private fun wearState(charging: Boolean): String {
        val now = System.currentTimeMillis()
        expireUserOffAt(now)
        val availFresh = onBodyAt > 0 && now - onBodyAt <= ONBODY_STALE_MS
        val pulseFresh = lastPulseAt > 0 && now - lastPulseAt <= PULSE_FRESH_MS

        //   （比 charging 還前面嗎？不 —— 充電是物理事實，兩者不會衝突；
        //     放在 charging 後面只是因為充電那條更便宜。）
        // 只用十二小時過期，不因單一脈搏永久清掉標記：離腕 PPG 可能是假訊號。
        val userSaysOff = UserOffPolicy.isExplicitlyOff(userOffAt, lastPulseAt, now)
        if (charging) lastChargingAt = now
        // 剛下充電座那段，錶身還是熱的 → 溫度的否決票不算數。
        val tempPoisoned = lastChargingAt > 0 && now - lastChargingAt <= AFTER_CHARGE_BLIND_MS

        return when {
            //
            //   **錶充飽了還躺在充電座上** —— availability 早就過期、脈搏當然也沒有，
            //
            //   但充電這件事是**電路上的物理事實**，比任何感測器都硬：
            //   一支接著充電線的錶不可能在手腕上。
            //   放這一條之後，今天的盲區從 19.5% 掉到 2.5%。
            //
            //   而 19.5% 那個數字**幾乎全部是這一個可以直接消掉的原因**。
            //   而真正的盲區其實只有 2.5%，兩個數字導向完全不同的判斷。
            charging -> ONBODY_OFF
            userSaysOff -> ONBODY_OFF        // ★ 人講的話，不用再猜

            //
            //
            //    去翻 availability 的事件史才看懂：
            //      （然後就沒有了 —— **AVAILABLE 一直沒來**）
            //
            //    整天 34 個 availability 事件裡，AVAILABLE 只出現 4 次，
            //    而且每一次前面都是 UNAVAILABLE→ACQUIRING→AVAILABLE 這個**重啟**的形狀。
            //
            //    後果有多嚴重：ONBODY_STALE_MS 是一小時，所以在那一小時裡
            //    **所有以 on_body 當閘門的判斷全部關著** —— 警報不會響、
            //
            //    那樣會翻掉 46,975 筆，其中 **5.2% 被溫度打臉**（錶壓在東西上
            //    所以要第三票。
            availFresh && onBody == ONBODY_OFF -> {
                val offAge = now - onBodyAt
                // 不用門檻，用因果順序 —— 桌上的錶不會生出新的有效脈搏。
                val pulseAfterOff = lastPulseAt > onBodyAt
                val tempFresh = lastTempAt > 0 && now - lastTempAt <= TEMP_FRESH_MS
                //
                //   第一版要求溫差 > +0.15 才肯翻案。裝上去當天早上就看到它失效：
                //
                //   查了才知道根本原因：**皮膚溫度本來就很稀疏。**
                //   九天資料，戴著時的批次空檔中位數是 **485~3127 秒（8~52 分鐘）**，
                //   **大部分時候根本沒有票可以投** —— 重放九天，65.7% 的情況溫度是空的。
                //
                //   ```
                //     中位 80   95% 93   99% 98   最高 118
                //     ≥120（警報門檻）：**0 筆**
                //   ```
                //   **假脈搏從來沒有到過警報門檻。** 最高 118，而門檻是 120。
                //   所以誤判成 inferred_on 不會製造假警報，最多讓戴錶時數多算一點。
                //
                //   兩種錯的代價差太多了，所以：
                //
                //       溫度沒話說         → 相信脈搏
                //
                //     代價已經量過了：那型的心率上限是 118，碰不到警報。
                //     如果哪天看到 off-body 的假脈搏破 120，這一條要立刻重新檢討。
                when {
                    offAge <= ONBODY_OFF_TRUST_MS -> ONBODY_OFF
                    // 溫度否決票：溫差明顯是負的，那就真的不在手上。
                    // ⚠ 但剛下充電座那 15 分鐘不算 —— 那時候錶身是熱的，
                    pulseAfterOff && tempFresh && !tempPoisoned &&
                        lastTempDelta < DELTA_OFF -> ONBODY_OFF
                    // 有新脈搏、溫度沒有反對 → 當成戴著（inferred_on 本來就比 on 弱）
                    pulseAfterOff -> ONBODY_INFERRED_ON
                    else -> ONBODY_OFF
                }
            }
            availFresh && onBody == ONBODY_ON -> ONBODY_ON
            pulseFresh -> ONBODY_INFERRED_ON                    // 沒人講話但脈搏在
            else -> ONBODY_UNKNOWN
        }
    }

    /**
     */
    private fun wearWhy(charging: Boolean): String {
        val now = System.currentTimeMillis()
        val availAge = if (onBodyAt > 0) (now - onBodyAt) / 1000 else -1
        val pulseAge = if (lastPulseAt > 0) (now - lastPulseAt) / 1000 else -1
        val userOff = if (userOffAt > 0) (now - userOffAt) / 1000 else -1
        return "avail=$onBody(${availAge}s) pulse=${pulseAge}s chg=$charging 她說脫錶=${userOff}s"
    }

    private val iso = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS", Locale.US)
    private val day = SimpleDateFormat("yyyy-MM-dd", Locale.US)

    /**
     * 感測器看門狗 —— **三星的心率停了，要有人去踢它。**
     *
     *
     * 裝到錶上之後才發現它**永遠不會執行**：
     *
     * ```
     * 三星判定沒戴 → 停送心率 → 收不到脈搏 → 踢不出去 → 三星永遠不知道要恢復
     *      ↑                                                    │
     *      └────────────────────────────────────────────────────┘
     * ```
     *
     *
     * 所以改成掛在**動作窗口**上 —— 加速度計是我們自己註冊的，
     * 不受三星佩戴判斷影響，任何狀態下都每分鐘進來一次。它是這支程式裡
     *
     * ## 踢的節奏
     *
     * | 情況 | 多久踢一次 | 為什麼 |
     * |---|---|---|
     * | 充電中 | 不踢 | 在充電座上沒有脈搏是正常的，不是故障 |
     * | 裝置明確說沒戴 | 20 分鐘 | 多半真的放在桌上，慢慢確認就好 |
     */
    private fun sensorWatchdog() {
        val now = System.currentTimeMillis()
        samsung.sweepTempNow(now)          // 逾時保險：卡住的隨選請求要收掉
        val (_, charging) = battery()
        if (charging) return

        //
        //   佩戴判斷需要溫度當否決票，但 CONTINUOUS 那條是**一批一批**送的，
        //   實測空檔中位數 8~52 分鐘 —— 決策要用的時候常常沒有。
        //   這支錶支援 SKIN_TEMPERATURE_ON_DEMAND，所以不要等，直接去要。
        //
        //     錶放在抽屜裡的時候去量體溫，只是白白耗電。
        val tempAge = if (lastTempAt > 0) now - lastTempAt else Long.MAX_VALUE
        val pulseAlive = lastPulseAt > 0 && now - lastPulseAt <= PULSE_FRESH_MS
        if (pulseAlive && tempAge > TEMP_ASK_AFTER_MS &&
            now - lastTempAskAt > TEMP_ASK_COOLDOWN_MS
        ) {
            lastTempAskAt = now
            val sent = samsung.requestTempNow()
            val ageTxt = if (lastTempAt == 0L) "從來沒有過" else "${tempAge / 60000} 分鐘沒更新"
            Log.i(TAG, "溫度$ageTxt —— 主動要一筆（送出=$sent）")
        }
        val pulseAge = if (lastPulseAt > 0) now - lastPulseAt else Long.MAX_VALUE
        val confidentlyOff = onBody == ONBODY_OFF && now - onBodyAt <= ONBODY_STALE_MS
        if (ppgFallback(now, pulseAge, confidentlyOff)) return
        if (pulseAge <= SENSOR_DEAD_MS) return
        val cooldown = if (confidentlyOff) KICK_COOLDOWN_OFF_MS else KICK_COOLDOWN_MS
        if (now - lastKickAt < cooldown) return
        lastKickAt = now
        val ageTxt = if (pulseAge == Long.MAX_VALUE) "從來沒有過" else "${pulseAge / 1000}秒"
        Log.i(TAG, "看門狗：心率斷了 $ageTxt（avail=$onBody）—— 踢三星感測器")
        write("sensor_kick") {
            put("pulse_age_s", if (pulseAge == Long.MAX_VALUE) -1 else pulseAge / 1000)
            put("avail", onBody)
        }
        runCatching { samsung.restartTrackers() }
            .onFailure { Log.e(TAG, "踢失敗：$it") }
        refreshHr("watchdog")
    }

    //   觸發與節奏在 PpgHrPolicy（純函式有測試）：系統心率斷 ≥10 分鐘、不在充電、沒有明確脫錶 → 每 10 分鐘量 30 秒。
    //   算法在 PpgHr。算出來寫 type=hr＋src="ppg"（下游照讀 bpm；要分來源看 src），沒算出來寫 ppg_try 說原因。
    private var lastPpgTryAt = 0L
    private var lastPpgAt = 0L
    private var lastPpgBpm = 0
    private var lastPpgQ = 0.0
    private var ppgTries = 0
    private var ppgOks = 0
    private var lastPpgResult = ""
    private var ppgWake: android.os.PowerManager.WakeLock? = null

    /** 回 true＝這一分鐘開始量了（呼叫端這分鐘就別再踢三星）。 */
    private fun ppgFallback(now: Long, pulseAge: Long, confidentlyOff: Boolean): Boolean {
        val since = if (lastPpgTryAt > 0) now - lastPpgTryAt else Long.MAX_VALUE
        if (!PpgHrPolicy.shouldTry(pulseAge, since, false, confidentlyOff, samsung.ppgSupported)) return false
        lastPpgTryAt = now
        return startPpg("fallback")
    }

    /**
     * 真正開始量（備援和 adb 手動共用）。code102：why＝fallback／adb。
     * 手動那條（am broadcast -a com.example.watchbridge.PPG_NOW）不管系統心率活不活都量 ——
     * 心率活著的時候反而最好用：同一支錶、同一段時間的系統 bpm 就是標準答案（存成 ref_hr）。
     */
    private fun startPpg(why: String): Boolean {
        ppgMotionAtStart = lastMotion
        ppgWhy = why
        synchronized(ppgRef) { ppgRef.clear() }
        ppgTries++
        // 量的那 30 秒不讓 CPU 睡（逾時自動放，最多 WINDOW_S+15 秒）；量完就放
        runCatching {
            ppgWake = getSystemService(android.os.PowerManager::class.java)
                .newWakeLock(android.os.PowerManager.PARTIAL_WAKE_LOCK, "watchbridge:ppghr")
                .apply { acquire((PpgHrPolicy.WINDOW_S + 15) * 1000L) }
        }
        val r = samsung.startPpgWindow(PpgHrPolicy.WINDOW_S) { ts, vs, bad -> ppgActive = false; onPpgWindow(ts, vs, bad) }
        lastPpgResult = r
        if (r != "started") {
            releasePpgWake()
            write("ppg_try") { put("ok", false); put("why", r); put("trigger", why) }
            return false
        }
        ppgActive = true
        return true
    }

    private var ppgMotionAtStart = Double.NaN
    private var ppgWhy = ""
    @Volatile private var ppgActive = false
    /** code102：PPG 量的那 30 秒裡，Health Services 每一筆系統心率（標準答案） */
    private val ppgRef = ArrayList<Int>()

    /** code101：原始波形留在錶上 files/ppg_raw/（ASCII 路徑，adb run-as 拉得回來），只留最新 60 個，拿來離線調算法。 */
    private fun savePpgRaw(ts: LongArray, vs: DoubleArray, e: PpgHr.Est?) {
        runCatching {
            if (ts.isEmpty()) return
            val dir = File(filesDir, "ppg_raw").apply { mkdirs() }
            val name = java.text.SimpleDateFormat("yyyyMMdd_HHmmss", java.util.Locale.US).format(java.util.Date(ts.first())) +
                "_q" + (if (e != null) Math.round(e.quality * 100) else 0) + ".json"
            val o = JSONObject().put("t0", ts.first())
                .put("dt", org.json.JSONArray().apply { ts.forEach { put(it - ts.first()) } })
                .put("v", org.json.JSONArray().apply { vs.forEach { put(it.toLong()) } })
                .put("q", e?.quality ?: JSONObject.NULL).put("bpm_guess", e?.bpm ?: JSONObject.NULL)
                .put("motion", if (ppgMotionAtStart.isNaN()) JSONObject.NULL else ppgMotionAtStart)
                .put("trigger", ppgWhy)
                .put("ref_hr", org.json.JSONArray().apply { synchronized(ppgRef) { ppgRef.forEach { put(it) } } })
            File(dir, name).writeText(o.toString())
            dir.listFiles()?.sortedByDescending { it.name }?.drop(60)?.forEach { it.delete() }
        }.onFailure { Log.w(TAG, "PPG 原始波形存不了：$it") }
    }

    private fun releasePpgWake() {
        runCatching { ppgWake?.takeIf { it.isHeld }?.release() }
        ppgWake = null
    }

    private fun onPpgWindow(ts: LongArray, vs: DoubleArray, bad: Int) {
        releasePpgWake()
        val e = runCatching { PpgHr.estimate(ts, vs) }.getOrNull()
        savePpgRaw(ts, vs, e)
        if (e != null && e.ok) {
            val bpm = Math.round(e.bpm).toInt()
            ppgOks++; lastPpgAt = System.currentTimeMillis(); lastPpgBpm = bpm; lastPpgQ = e.quality
            lastPpgResult = "ok"
            Log.i(TAG, "🫀 PPG 備援心率 $bpm bpm（q=${"%.2f".format(e.quality)}，${ts.size} 筆）")
            // code102：adb 手動量的是測試，寫 ppg_test，不混進真正的 hr（下游不一定看 src）
            write(if (ppgWhy == "adb") "ppg_test" else "hr") {
                put("bpm", bpm)
                put("src", "ppg")
                synchronized(ppgRef) { if (ppgRef.isNotEmpty()) put("ref_hr_median", ppgRef.sorted()[ppgRef.size / 2]) }
                put("q", Math.round(e.quality * 100) / 100.0)
                put("sensor_t", ts.last())
                put("win_s", Math.round(e.seconds * 10) / 10.0)
                put("n", ts.size)
                if (bad > 0) put("bad_status", bad)
            }
        } else {
            lastPpgResult = if (e == null) "too_few_samples" else "low_quality"
            Log.i(TAG, "🫀 PPG 備援沒算出來：$lastPpgResult（${ts.size} 筆，q=${e?.quality}）")
            write("ppg_try") {
                put("ok", false); put("why", lastPpgResult); put("n", ts.size)
                if (!ppgMotionAtStart.isNaN()) put("motion", ppgMotionAtStart)   // code101：低品質是不是因為在動
                if (e != null) { put("q", Math.round(e.quality * 100) / 100.0); put("bpm_guess", Math.round(e.bpm)) }
                if (bad > 0) put("bad_status", bad)
            }
        }
    }

    private var lastReregAt = 0L
    private var permReported: List<String>? = null
    private var permReportAt = 0L

    /** 查心率要的四個權限；缺的清單變了、或還缺而且滿一小時，就寫一筆 perm_missing（ok=true 代表補回來了）。 */
    fun checkPerms(): List<String> {
        val granted = HrRefreshPolicy.NEEDED.associateWith {
            checkSelfPermission(it) == android.content.pm.PackageManager.PERMISSION_GRANTED
        }
        val miss = HrRefreshPolicy.missing(granted)
        val now = System.currentTimeMillis()
        if (HrRefreshPolicy.shouldReportPerms(miss, permReported, permReportAt, now)) {
            permReportAt = now
            write("perm_missing") { put("missing", org.json.JSONArray(miss)); put("ok", miss.isEmpty()) }
            Log.w(TAG, "心率權限：" + if (miss.isEmpty()) "都補回來了" else "缺 $miss")
        }
        permReported = miss
        return miss
    }

    /**
     * 把 Health Services 的 HEART_RATE_BPM 掛號拆掉重掛（本來只在 onCreate 掛一次 → 權限／三星設定變了也接不回來）。
     */
    fun refreshHr(why: String): String {
        val now = System.currentTimeMillis()
        val hrAge = if (lastHrAt > 0) now - lastHrAt else Long.MAX_VALUE
        val since = if (lastReregAt > 0) now - lastReregAt else Long.MAX_VALUE
        val miss = checkPerms()
        if (!HrRefreshPolicy.shouldReregister(why, hrAge, since)) return "hr_fresh_or_too_soon"
        lastReregAt = now
        write("hr_rereg") {
            put("why", why); put("hr_age_s", if (hrAge == Long.MAX_VALUE) -1 else hrAge / 1000)
            put("missing", org.json.JSONArray(miss))
        }
        Log.i(TAG, "重掛 Health Services 心率（$why）")
        return runCatching {
            val f = measureClient.unregisterMeasureCallbackAsync(DataType.HEART_RATE_BPM, callback)
            f.addListener({
                runCatching { measureClient.registerMeasureCallback(DataType.HEART_RATE_BPM, callback) }
                    .onFailure { Log.e(TAG, "重掛心率失敗", it); write("hr_rereg") { put("why", why); put("error", it.toString().take(120)) } }
            }, mainExecutor)
            "hr_reregistered"
        }.getOrElse { "hr_rereg_failed: $it" }
    }

    /** 佩戴融合判斷＋心率新鮮度，給電腦經手機問。只讀。 */
    fun diagSnapshot(): JSONObject {
        val now = System.currentTimeMillis()
        val (pct, charging) = battery()
        fun age(t: Long) = if (t > 0) (now - t) / 1000 else -1
        return JSONObject()
            .put("wear", wearState(charging)).put("wear_why", wearWhy(charging))
            .put("battery", pct).put("charging", charging)
            .put("hr_bpm", lastHrBpm).put("hr_age_s", age(lastHrAt))
            .put("pulse_age_s", age(lastPulseAt)).put("temp_age_s", age(lastTempAt))
            .put("last_kick_age_s", age(lastKickAt)).put("hr_count", hrCount)
            .put("skin_c", if (lastSkinC.isNaN()) JSONObject.NULL else Math.round(lastSkinC * 100) / 100.0)
            .put("skin_age_s", age(lastTempAt))
            .put("motion", if (lastMotion.isNaN()) JSONObject.NULL else lastMotion)
            .put("motion_age_s", age(lastMotionAt))
            .put("sdk_connected", samsung.connected).put("sdk_rebinds", sdkRebinds)   // code103
            .put("stall_kicks", stallKicks).put("ppg_enabled", PpgHrPolicy.ENABLED)    // code104
            .put("step_total", stepTotal).put("step_age_s", age(stepAt))
            .put("step_perm", stepPermitted()).put("step_registered", stepRegistered).put("step_stalls", stepStalls)
            .put("walk_streak", walkStreak)
            .put("phone_nearby", phoneNearby ?: JSONObject.NULL)
            .put("phone_away_s", if (phoneAwaySince > 0) (now - phoneAwaySince) / 1000 else 0)
            .put("phone_alerts", phoneAlerts)
            .put("wifi_seen_age_s", age(wifiSeenAt)).put("wifi_cycles", wifiCycles)
            .put("wifi_notified", wifiNotified).put("wifi_stuck_episodes", wifiStuckEpisodes)
            .put("ppg_supported", samsung.ppgSupported)
            .put("ppg_bpm", lastPpgBpm).put("ppg_q", Math.round(lastPpgQ * 100) / 100.0)
            .put("ppg_age_s", age(lastPpgAt)).put("ppg_try_age_s", age(lastPpgTryAt))
            .put("ppg_tries", ppgTries).put("ppg_oks", ppgOks).put("ppg_last", lastPpgResult)
    }

    /**
     * 自己 startService 給自己：服務本來就在前景，不會被背景限制擋；真正的傳送仍走 onStartCommand 那條（有 sending 鎖，不會重疊）。
     */
    fun sendNow(): String {
        if (sending.get()) return "already_sending"
        return runCatching {
            startService(Intent(this, CollectorService::class.java).setAction(SendAlarm.ACTION)); "sent"
        }.getOrElse { "start_failed: $it" }
    }

    fun kickSensors(): String {
        lastKickAt = System.currentTimeMillis()
        write("sensor_kick") { put("by", "remote_diag"); put("avail", onBody) }
        val k = runCatching { samsung.restartTrackers(); "kicked" }.getOrElse { "kick_failed: $it" }
        return "$k+${refreshHr("remote")}"
    }

    // ── 電池 ────────────────────────────────────────────────────
    // 用黏性廣播讀當下狀態就夠，不用註冊接收器一直被叫醒。
    /** 探針進度一句話送回畫面。畫面沒開也沒關係，廣播沒人收就算了。 */
    private fun probeSay(text: String) {
        sendBroadcast(Intent(ProbeActivity.STATUS).apply { setPackage(packageName); putExtra("text", text) })
    }

    /**
     * 數值來自錶上的 SharedPreferences（用 PROBE_PROFILE 廣播設），沒設就用**通用預設值**——
     * gender：三星文件 0＝女 1＝男。
     */
    private fun biaProfile(): com.samsung.android.service.health.tracking.data.TrackerUserProfile {
        val sp = getSharedPreferences("probe_profile", Context.MODE_PRIVATE)
        return com.samsung.android.service.health.tracking.data.TrackerUserProfile.Builder()
            .setHeight(sp.getFloat("height", 160f))
            .setWeight(sp.getFloat("weight", 55f))
            .setGender(sp.getInt("gender", 0))
            .setAge(sp.getInt("age", 35))
            .build()
    }

    private fun battery(): Pair<Int, Boolean> {
        val i = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            ?: return -1 to false
        val level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = i.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
        val status = i.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
                status == BatteryManager.BATTERY_STATUS_FULL
        return (if (level < 0) -1 else level * 100 / scale) to charging
    }

    // ── 寫檔 ────────────────────────────────────────────────────
    /**
     * 一行一筆 JSON，直接 append。
     *
     * 刻意不做批次緩衝：手錶隨時可能被系統收掉，
     * 攢在記憶體裡的東西會跟著沒。**寧可多寫幾次磁碟，也不要丟資料。**
     */
    private fun write(type: String, body: JSONObject.() -> Unit) {
        val (level, charging) = battery()
        val o = JSONObject().apply {
            put("t", iso.format(Date()))
            put("type", type)
            put("batt", level)
            put("charging", charging)
            // ⚠ 這裡送出的是三態字串（on / off / unknown），不再是 true/false。
            val wear = wearState(charging)
            put("on_body", wear)
            // 不是明確的 on/off 就把來源帶上 —— 分析時要看得出這個結論怎麼來的。
            if (wear != ONBODY_ON && wear != ONBODY_OFF) put("on_body_why", wearWhy(charging))
            body()
        }
        try {
            val f = File(out, "${day.format(Date())}.jsonl")
            JsonlBuffer.append(f, o.toString())
        } catch (e: Exception) {
            Log.e(TAG, "寫檔失敗", e)
        }
    }

    // ── Health Services 即時心率 ────────────────────────────────
    private val callback = object : MeasureCallback {
        override fun onAvailabilityChanged(
            dataType: DeltaDataType<*, *>, availability: Availability
        ) {
            //   結果那只是取樣間隔。沒有資料不是一種資料。）
            val wasOff = onBody == ONBODY_OFF
            //   但實測當天的分佈是：
            //       ACQUIRING 25 次、OFF_BODY 24 次、**AVAILABLE 只有 5 次**
            //   那不是謹慎，是把自己弄瞎。
            //
            //   它是狀態轉換中的過渡，不是對既有知識的否定。
            //   所以 ACQUIRING **沿用上一個已知狀態**，而不是清空。
            //
            //   安全性由 ONBODY_STALE_MS 那道時限守著：ACQUIRING 卡超過一小時
            //   沒有變成 AVAILABLE 或 OFF_BODY，onBodyNow() 照樣會退回 unknown。
            //   **過渡可以沿用，卡住不行。**
            when (availability) {
                DataTypeAvailability.AVAILABLE -> {
                    onBody = ONBODY_ON; onBodyAt = System.currentTimeMillis()
                }
                DataTypeAvailability.UNAVAILABLE_DEVICE_OFF_BODY -> {
                    onBody = ONBODY_OFF; onBodyAt = System.currentTimeMillis()
                }
                DataTypeAvailability.ACQUIRING -> {
                    // 沿用舊值，但**不更新時間戳** —— 這樣過期時鐘會繼續走，
                    // 一直 ACQUIRING 下去終究會過期成 unknown。
                }
                else -> {
                    onBody = ONBODY_UNKNOWN; onBodyAt = System.currentTimeMillis()
                }
            }
            write("availability") { put("state", availability.toString()) }
            Log.i(TAG, "availability=$availability")

            if (wasOff && onBody == ONBODY_ON) {
                Log.i(TAG, "戴回來了，重新掛三星的感測器")
                samsung.restartTrackers()
            }
        }

        override fun onDataReceived(data: DataPointContainer) {
            for (p in data.getData(DataType.HEART_RATE_BPM)) {
                hrCount++
                val bpm = p.value.toInt()
                if (ppgActive && bpm > 0) synchronized(ppgRef) { ppgRef += bpm }   // code102：PPG 對照用的標準答案
                if (bpm <= 0) continue          // 沒讀到的時候會給 0，別讓它污染基準

                //
                //   這是比 availability 更硬的證據 —— availability 是三星的推論，
                //   脈搏是物理事實。
                //
                //   為什麼非加不可：availability **只在狀態改變時才發**，
                //   就把那些時段全部判成 unknown ——
                //
                //   等於把自己弄瞎。
                // ⚠ 這裡**不覆蓋 availability**（我第一版是直接覆蓋，那是錯的）。
                //   所以脈搏獨立記在自己的變數裡，融合在 wearState() 裡做。
                lastPulseAt = System.currentTimeMillis()

                // 由每分鐘的動作窗口驅動 —— 見那個函式上面的說明（**這裡會死結**）。

                //
                //   這裡本來是 `val now = System.currentTimeMillis()` —— 牆上的時鐘。
                //   **Health Services 是整批送資料的**：一次 onDataReceived 可能帶進
                //   兩百多筆、橫跨十分鐘的樣本，而它們是在**同一毫秒**被交到手上的。
                //   **其餘兩百多筆全部被丟掉** —— 十分鐘的心率被壓成一個點。
                //
                //   實測證據（v0.24 跑 19.6 分鐘）：
                //
                //   中間一片空白 —— 看起來像感測器壞了。
                //
                //   **凡是感測器資料，時間一律用樣本自己的，不要用寫入的那一刻。**
                val sampleMs = bootMs + p.timeDurationFromBoot.toMillis()
                val now = sampleMs

                //     三星 SDK 那條 `hr_ibi` 的紀錄裡本來就帶著 hr 欄位。
                //     兩者來源不同（Health Services vs 三星），留著互相對照有價值，
                //     但**不需要一秒一次**。
                //
                //     省下來的不是硬碟（手機還有 653 GB），**是藍牙要搬的量**。
                //     少四成 = 每次醒來追得更快 = 鋸齒更不容易再出現。
                //
                //     那樣會把兩筆之間的變化整段抹掉 —— 而那正好是警報唯一的依據。
                //     心率從 70 衝到 130 再掉回來，如果剛好落在間隔裡，
                //     檔案裡會看起來一整分鐘都很平靜。
                //
                //     所以規則是兩條：
                //       · 平常     —— 每分鐘一筆（基線用）
                //       · 一有跳動 —— 立刻寫，最快五秒一筆（事件用）
                //     最壞情況（劇烈運動）約每分鐘 12 筆，仍比原本的 60 筆少八成。
                val jumped = lastHrBpm > 0 &&
                        kotlin.math.abs(bpm - lastHrBpm) >= HR_JUMP_BPM &&
                        now - lastHrAt >= HR_JUMP_MIN_GAP_MS
                if (now - lastHrAt >= HR_EVERY_MS || jumped) {
                    // skipped 記下這一筆代表吃掉了幾個原始樣本 ——
                    val skipped = hrCount - lastHrCount - 1
                    write("hr") {
                        put("bpm", bpm)
                        put("skipped", skipped.coerceAtLeast(0))
                        // 樣本自己的時間。跟三星那條的 sensor_t 同名同義 ——
                        // **分析時一律用這個，不要用外層的 t**（那是寫入時間）。
                        put("sensor_t", sampleMs)
                        if (jumped) put("jump", true)
                    }
                    lastHrBpm = bpm; lastHrAt = now; lastHrCount = hrCount
                }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        val savedUserOffAt = offPrefs.getLong("user_off_at", 0L)
        userOffAt = UserOffPolicy.restore(savedUserOffAt, System.currentTimeMillis())
        if (savedUserOffAt != userOffAt) {
            offPrefs.edit().remove("user_off_at").apply()
        }
        out = File(filesDir, "生理資料").apply { mkdirs() }
        startForeground(NOTIF_ID, notification())
        live = this
        runCatching { WifiKeeper.restore(this) }.onFailure { Log.w(TAG, "保持Wi-Fi 恢復失敗：$it") }

        // 這裡本來抓了一個 PARTIAL_WAKE_LOCK。**已經拿掉。**
        //
        // 拿掉的理由：WakeLock 是土法煉鋼，在手錶上很耗電，而且實測沒有解決問題。
        // 正解是 OngoingActivity + CATEGORY_WORKOUT（見 notification()）——
        // 那是 Wear OS 給健康量測 app 的正式管道，
        // 系統認得它，就不會在螢幕暗掉時把服務凍住。
        //
        // 來源：GitHub 上 KAIST iclab 的 GalaxyWatch-PPG-Logger。
        // 就是那一句話讓我找到這個做法的。

        val ver = runCatching { packageManager.getPackageInfo(packageName, 0).versionName }.getOrNull() ?: "?"
        write("service_start") { put("version", ver) }

        runCatching {
            measureClient.registerMeasureCallback(DataType.HEART_RATE_BPM, callback)
        }.onFailure { Log.e(TAG, "Health Services 註冊失敗", it) }
        lastReregAt = System.currentTimeMillis()
        checkPerms()

        // ⚠ 順序很重要：onRecord 一定要在 samsung.start() **之前**設好。
        //
        //   會跟 onCreate 的後續程式碼賽跑。原本 onRecord 設在 start() 後面十幾行，
        //   結果 onConnectionSuccess 先跑到 —— 那時候 onRecord 還是 null，
        //   那一筆 sdk_status **就這樣掉了**，而且沒有任何跡象。
        //
        SamsungSensors.onRecord = { type, json ->
            write(type) {
                json(this)
                if (type == "motion") {
                    put("step_total", stepTotal)
                    put("step_age_s", if (stepAt > 0) (System.currentTimeMillis() - stepAt) / 1000 else -1)
                }
            }
            // 動作窗口是**唯一在任何狀態下都會持續進來的東西** —— 拿它當心跳。
            if (type == "motion") {
                runCatching {
                    val o = JSONObject().also { json(it) }
                    if (o.has("activity")) { lastMotion = o.optDouble("activity"); lastMotionAt = System.currentTimeMillis() }
                    walkTick(o.optDouble("activity", Double.NaN), o.optDouble("still_ratio", Double.NaN))
                }
                sensorWatchdog()
                runCatching { AdbKeeper.ensure(this@CollectorService) }
            }
        }
        // 探針進度 → 廣播給 ProbeActivity 顯示（code64）。
        SamsungSensors.probeStatus = { text -> probeSay(text) }
        // ⚠ 跟 onRecord 一樣，一定要在 samsung.start() **之前**設好。
        SamsungSensors.onTemp = { skin, air ->
            lastTempAt = System.currentTimeMillis()
            lastTempDelta = (skin - air).toDouble()
            lastSkinC = skin.toDouble()
        }

        // 三星那層獨立跑，它連不上也不能拖累已經驗證可用的即時心率。
        samsung.start { line -> Log.i(TAG, "samsung: $line") }
        runCatching { registerSteps() }
        sdkStartAt = System.currentTimeMillis()
        sdkGuard.postDelayed(sdkGuardTick, 60_000L)   // code103：獨立計時器，不靠動作窗口（見 SdkRebindPolicy）

        env.start { w ->
            write("env") {
                put("hpa", (w.hpaMed * 100).toInt() / 100.0)
                put("hpa_min", (w.hpaMin * 100).toInt() / 100.0)
                put("hpa_max", (w.hpaMax * 100).toInt() / 100.0)
                put("hpa_n", w.hpaN)
                put("lux", (w.lux * 10).toInt() / 10.0)
                put("lux_min", (w.luxMin * 10).toInt() / 10.0)
                put("lux_max", (w.luxMax * 10).toInt() / 10.0)
                put("lux_n", w.luxN)
            }
        }

        //   理由見 SamsungSensors.kt 裡的長註解 —— 簡短版：
        //   人不動的時候 CPU 睡，交付被節流，安靜時每分鐘只剩 34 筆取樣。
        //   **檔案留著不刪**，因為那段踩坑紀錄比程式碼本身值錢。
        if (false) motion.start { w, hz ->
            write("motion") {
                put("samples", w.samples)
                put("activity", (w.activity * 100).toInt() / 100.0)
                put("peak", (w.peak * 100).toInt() / 100.0)
                put("still_ratio", (w.stillRatio * 1000).toInt() / 1000.0)
                put("hz", hz)
                put("mv", MotionSampler.METRIC_VERSION)
                // ⚠ 這個旗標比動作量本身重要。
                //   取樣不足時動作量會被灌大，而數字看起來完全正常 ——
                //   不要拿去算基線、不要拿去判睡眠、不要拿去校門檻。
                put("trust", w.samples >= MotionSampler.MIN_TRUSTWORTHY_SAMPLES)
            }
        }
        registerReceiver(
            recControl,
            IntentFilter().apply {
                addAction(FallRecorder.ACTION_START)
                addAction(FallRecorder.ACTION_STOP)
                addAction(MarkActivity.ACTION)
                addAction(ProbeActivity.ACTION)    // 🫀 量心電圖／體脂探針（code64）
                addAction(ProbeActivity.PROFILE)
                addAction(Buzzer.ACTION)           // 震動測試（不用重編就能換節奏）
                addAction(Siren.ACTION)            // 警報聲測試（同上，音色要由耳朵決定）
                addAction(AlarmActivity.ACTION)    // 整套警報演練（畫面＋聲音＋震動）
                addAction(PpgHrPolicy.ACTION_NOW)  // code102：adb 手動量一次 PPG（收原始波形調算法）
            },
            Context.RECEIVER_EXPORTED,   // 要讓 adb 的廣播進得來
        )

        // ⚠ 傳送**不能**跑在 Timer 自己的執行緒上。
        // 之後再也沒有任何傳送 —— 而且 app 還活著、前景服務還正常，
        // 完全看不出來。
        //
        // 就算某一次卡住，下一次的排程照樣會來。
        //     sendTimer.scheduleAtFixedRate(..., 60_000L, 5 * 60_000L)
        //   **java.util.Timer 在 Doze 底下會被凍住**，實測 25 分鐘沒送過一次，
        //   醒來時三次擠在同一秒補齊 —— 整天的傳輸鋸齒就是這個。
        //   改用系統鬧鐘，休眠底下照樣會響。詳見 SendAlarm 的檔頭。
        SendAlarm.schedule(this)

        Log.i(TAG, "收集服務啟動，寫到 ${out.absolutePath}")
    }

    // ── 跌倒測試的遙控 ──────────────────────────────────────────
    // 我從電腦這邊開始／停止就好。
    private val recorder by lazy { FallRecorder(this) }
    private val recControl = object : BroadcastReceiver() {
        override fun onReceive(c: Context?, i: Intent?) {
            when (i?.action) {
                // 標記直接寫進當天的資料檔，跟心率走同一條管線到手機。
                // **不另外開一條路** —— 少一條路就少一個會壞的地方，
                // 而且它自動享有既有的補送與斷點續傳。
                // 每換一個數字就重編一次 APK 的話，這件事根本試不完。
                Buzzer.ACTION -> {
                    val pat = i.getStringExtra("pattern") ?: "gentle"
                    val ok = Buzzer.buzz(this@CollectorService, pat)
                    Log.i(TAG, "震動測試 $pat → ${if (ok) "送出" else "失敗"}")
                }
                // 警報聲測試。跟震動一樣做成廣播可觸發 ——
                // 音色、音量、聽不聽得見**只有耳朵能判斷**，
                // 每改一個參數就重編一次 APK 的話這件事試不完。
                // 整套警報演練。**這條路一定要留著** —— 警報是這個系統
                // 它會在真的需要的那一天才第一次被執行。
                AlarmActivity.ACTION -> {
                    if (i.getBooleanExtra("stop", false)) {
                        AlarmOverlay.hide(this@CollectorService)
                    } else if (i.getIntExtra("after", 0) > 0) {
                        //
                        //   **連線這個動作本身就會把它叫醒。**
                        //
                        //   （半夜倒下的時候，錶一定是黑的），
                        //   測不到的情境 ＝ 沒有被驗證過的情境。
                        val secs = i.getIntExtra("after", 0)
                        Log.i(TAG, "警報演練：${secs} 秒後觸發（讓螢幕先睡著）")
                        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(
                            { AlarmOverlay.show(this@CollectorService) },
                            secs * 1000L,
                        )
                    } else {
                        Log.i(TAG, "警報演練：開畫面")
                        // ⚠ 用覆蓋視窗，**不要**改回 startActivity 或全螢幕通知。
                        //   那兩條在這支錶上都是 BAL_BLOCK，而且是安靜失敗的。
                        //   證據寫在 AlarmOverlay 的檔頭。
                        // 備援通知由 AlarmOverlay 在**失敗時**自己發，這裡不要另外發 ——
                        AlarmOverlay.show(this@CollectorService)
                    }
                }
                Siren.ACTION -> {
                    if (i.getBooleanExtra("stop", false)) {
                        Siren.stop(); Log.i(TAG, "警報聲：停")
                    } else {
                        val secs = i.getIntExtra("secs", 15)
                        val audible = Siren.start(this@CollectorService, secs)
                        Log.i(TAG, "警報聲測試 ${secs}s → ${if (audible) "有聲" else "⚠ 被靜音，人聽不到"}")
                    }
                }
                MarkActivity.ACTION -> {
                    val tag = i.getStringExtra("tag") ?: "other"

                    //
                    //   這一顆按鈕的全部價值在這裡 —— 不是為了事後分析好看，
                    //   是為了**當下就把那 76.6% 的模糊地帶關掉**。
                    //
                    //
                    //     如果只當參考，脈搏會繼續把狀態撐在 inferred_on，這顆就白按了。
                    //
                    //     跟 availability 那條同一個形狀 —— **用因果順序，不用門檻。**
                    if (tag == "off_wrist") {
                        onBody = ONBODY_OFF
                        onBodyAt = System.currentTimeMillis()
                        persistUserOffAt(onBodyAt)
                        Log.i(TAG, "她說脫錶了 —— 佩戴狀態直接設成 off，不再靠脈搏猜")
                    }
                    val label = i.getStringExtra("label") ?: tag
                    write("mark") { put("tag", tag); put("label", label) }
                    Log.i(TAG, "記一筆：$label")
                }
                PpgHrPolicy.ACTION_NOW -> {
                    // code104：PPG 會弄崩廠商感測驅動，手動也關（見 PpgHrPolicy.ENABLED）
                    val ok = if (PpgHrPolicy.ENABLED && samsung.ppgSupported) startPpg("adb") else false
                    Log.i(TAG, "🫀 手動量 PPG：${if (ok) "開始" else "沒開（supported=${samsung.ppgSupported}，$lastPpgResult）"}")
                }
                ProbeActivity.ACTION -> {
                    val kind = i.getStringExtra("kind") ?: "ecg"
                    val profile = if (kind == "bia") biaProfile() else null
                    val msg = samsung.startProbe(kind, profile)
                    Log.i(TAG, "探針 $kind：$msg")
                    probeSay(msg)
                }
                ProbeActivity.PROFILE -> {
                    val sp = getSharedPreferences("probe_profile", Context.MODE_PRIVATE).edit()
                    if (i.hasExtra("height")) sp.putFloat("height", i.getFloatExtra("height", 0f))
                    if (i.hasExtra("weight")) sp.putFloat("weight", i.getFloatExtra("weight", 0f))
                    if (i.hasExtra("gender")) sp.putInt("gender", i.getIntExtra("gender", 0))
                    if (i.hasExtra("age")) sp.putInt("age", i.getIntExtra("age", 0))
                    sp.apply()
                    write("bia_probe") { put("event", "profile_set") }
                    Log.i(TAG, "BIA 身高體重已存到錶上")
                }
                FallRecorder.ACTION_START -> {
                    val tag = i.getStringExtra("tag") ?: "test"
                    // motion=true：錄動作畫面送來的（50 Hz、三軌、motion_rec/、抓 wakelock）
                    val f = if (i.getBooleanExtra("motion", false)) recorder.startMotion(tag)
                            else recorder.start(tag)
                    Log.i(TAG, "▶ 錄影開始：$f")
                }
                FallRecorder.ACTION_STOP -> Log.i(TAG, "■ 錄影停止，${recorder.stop()} 筆")
            }
        }
    }

    // ── 定期把資料送去手機 ──────────────────────────────────────
    // 攢一批再送比較省電，而且 DataClient 本來就會排隊重送，
    // 晚五分鐘沒有任何損失。
    private val sender by lazy { WearSender(this) }
    // 單執行緒池：一次只送一塊（不要並發搶同一個 offset），
    // 但**卡住的那一次不會拖垮排程**。
    private val sendPool = java.util.concurrent.Executors.newSingleThreadExecutor()

    /** 只允許一條傳送執行緒。鬧鐘連續響兩次也不會兩邊搶同一個 offset。 */
    private val sending = java.util.concurrent.atomic.AtomicBoolean(false)

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        //   這個 app 的鬧鐘；onCreate 那一次排程不知哪次沒排成（之前沒有任何錯誤訊息）。
        //   所以每次被叫都重排一次 —— 同一個 PendingIntent（FLAG_UPDATE_CURRENT）是冪等的，
        //   多排不會多響，少排會整條管路安靜死掉。
        SendAlarm.schedule(this)
        // 鬧鐘來敲門 → 在**背景執行緒**上送。
        //
        //     BroadcastReceiver.onReceive 跑在主執行緒，而 Tasks.await() 禁止在主執行緒呼叫，
        //     會丟 IllegalStateException: Must not be called on the main application thread。
        //     那個失敗**不會推進 offset**，所以資料會永遠送不出去，
        //     而 app 表面上完全正常 —— 比它要修的那個 bug 更嚴重。
        //
        //     前景服務沒有 goAsync 那種十秒時限，這裡送最安全。
        Log.i(TAG, "onStartCommand action=${intent?.action}")
        if (intent?.action == SendAlarm.ACTION) {
            if (sending.compareAndSet(false, true)) {
                Thread {
                    try {
                        val sender = WearSender(this)
                        var total = 0
                        // 一次醒來多送幾塊。回 0 就停 ——
                        // 可能是送完了，也可能是手機不在（背壓），兩種都不該硬撐。
                        for (i in 0 until SEND_CHUNKS_PER_WAKE) {
                            val n = runCatching { sender.sendOnce() }.getOrDefault(0)
                            if (n == 0) break
                            total += n
                        }
                        Log.i(TAG, "傳送輪結束，總共 $total bytes")

                        //
                        //   跟在生理資料後面，是因為**生理資料優先** ——
                        //   錄音晚幾分鐘到沒關係，心率晚到會影響警報。
                        //
                        //   ⚠ 包在自己的 runCatching 裡：錄音那條炸掉**不可以**
                        val audio = runCatching { AudioSender(this).sendOnce() }
                            .onFailure { Log.e(TAG, "錄音傳送出錯（不影響生理資料）", it) }
                            .getOrDefault(0)
                        if (audio > 0) Log.i(TAG, "另外送出 $audio 個錄音檔")
                    } catch (e: Exception) {
                        Log.e(TAG, "傳送出錯", e)
                    } finally {
                        sending.set(false)
                    }
                }.apply { isDaemon = true }.start()
            } else {
                Log.i(TAG, "上一輪還在送，這次跳過")
            }
        }
        return START_STICKY
    }

    // ── code103：三星 SDK 啟動卡住就自己重 bind（判斷在 SdkRebindPolicy，純函式有測試）──
    private var sdkStartAt = 0L
    private var sdkRebindAt = 0L
    private var sdkRebinds = 0
    private val sdkGuard = android.os.Handler(android.os.Looper.getMainLooper())
    private val sdkGuardTick: Runnable = object : Runnable {
        override fun run() {
            val now = System.currentTimeMillis()
            val connected = samsung.connected
            val since = if (sdkRebindAt > 0) now - sdkRebindAt else Long.MAX_VALUE
            if (SdkRebindPolicy.shouldRebind(connected, now - sdkStartAt, since, sdkRebinds)) {
                sdkRebinds++; sdkRebindAt = now
                val r = samsung.rebind()
                Log.w(TAG, "🔌 三星 SDK ${(now - sdkStartAt) / 1000} 秒還沒連上 —— 重 bind 第 $sdkRebinds 次：$r")
                write("sdk_rebind") { put("try", sdkRebinds); put("since_start_s", (now - sdkStartAt) / 1000); put("result", r) }
            }
            if (connected && sdkRebinds > 0 && !sdkRebindOkWritten) {
                sdkRebindOkWritten = true
                write("sdk_rebind") { put("ok", true); put("tries", sdkRebinds) }
            }
            // code104：資料流停擺就重掛（廠商驅動崩潰後 tracker 不會自己回來）
            val streamAge = now - (if (lastMotionAt > 0) lastMotionAt else sdkStartAt)
            val sinceStall = if (lastStallKickAt > 0) now - lastStallKickAt else Long.MAX_VALUE
            if (SdkRebindPolicy.shouldKickStall(connected, streamAge, sinceStall)) {
                lastStallKickAt = now; stallKicks++
                Log.w(TAG, "🔌 三星資料流 ${streamAge / 1000} 秒沒進來（驅動可能崩過）—— 重掛感測器")
                write("sdk_stall") { put("stream_age_s", streamAge / 1000); put("kick", stallKicks) }
                runCatching { samsung.restartTrackers() }.onFailure { Log.e(TAG, "重掛失敗：$it") }
                refreshHr("stall")
            }
            runCatching { wifiStuckTick(now) }
            sdkGuard.postDelayed(this, 60_000L)   // code104：一直看（code103 連上就停，停擺就沒人管）
        }
    }
    private var sdkRebindOkWritten = false
    private var lastStallKickAt = 0L
    private var stallKicks = 0

    //      我們自己聽 TYPE_STEP_COUNTER：連續 5 分鐘像走路卻一步都沒動 → step_stall＋重註冊（30 分鐘最多一次）。
    private val sensorMgr by lazy { getSystemService(android.hardware.SensorManager::class.java) }
    private var stepTotal = -1L
    private var stepAt = 0L
    private var stepAtStreakStart = -1L
    private var walkStreak = 0
    private val recentWalk = ArrayDeque<Boolean>()
    private var lastStepReregAt = 0L
    private var stepStalls = 0
    private var stepRegistered = false
    private var phoneNearby: Boolean? = null
    private var phoneAwaySince = 0L
    private var phoneAlerted = false
    private var phoneAlerts = 0

    private val stepListener = object : android.hardware.SensorEventListener {
        override fun onSensorChanged(e: android.hardware.SensorEvent) {
            stepTotal = e.values[0].toLong(); stepAt = System.currentTimeMillis()
        }
        override fun onAccuracyChanged(s: android.hardware.Sensor?, a: Int) = Unit
    }

    private fun stepSensor() = sensorMgr.getDefaultSensor(android.hardware.Sensor.TYPE_STEP_COUNTER)
    private fun stepPermitted() =
        checkSelfPermission(android.Manifest.permission.ACTIVITY_RECOGNITION) == android.content.pm.PackageManager.PERMISSION_GRANTED

    private fun registerSteps(): Boolean {
        val s = stepSensor() ?: run { Log.w(TAG, "🚶 這支錶沒有 TYPE_STEP_COUNTER"); return false }
        stepRegistered = sensorMgr.registerListener(stepListener, s, android.hardware.SensorManager.SENSOR_DELAY_NORMAL)
        Log.i(TAG, "🚶 計步器註冊=${stepRegistered}（權限=${stepPermitted()}）")
        return stepRegistered
    }

    private fun walkTick(activity: Double, still: Double) {
        val now = System.currentTimeMillis()
        val walking = WalkPolicy.isWalkingMinute(activity, still)
        recentWalk.addLast(walking); while (recentWalk.size > 3) recentWalk.removeFirst()
        if (walking) { if (walkStreak == 0) stepAtStreakStart = stepTotal; walkStreak++ } else walkStreak = 0
        val delta = if (stepAtStreakStart < 0 || stepTotal < 0) 0L else stepTotal - stepAtStreakStart
        val stall = WalkPolicy.isStepStall(walkStreak, delta, stepSensor() != null, stepPermitted())
        val since = if (lastStepReregAt > 0) now - lastStepReregAt else Long.MAX_VALUE
        if (WalkPolicy.shouldReregister(stall, since)) {
            lastStepReregAt = now; stepStalls++
            Log.w(TAG, "🚶 走了 $walkStreak 分鐘計步器沒動（step_total=$stepTotal）—— 重註冊")
            write("step_stall") {
                put("walk_min", walkStreak); put("step_total", stepTotal)
                put("step_age_s", if (stepAt > 0) (now - stepAt) / 1000 else -1); put("n", stepStalls)
            }
            runCatching { sensorMgr.unregisterListener(stepListener) }
            runCatching { registerSteps() }
        }
        runCatching { phoneLeftCheck() }
    }

    private var wifiSeenAt = 0L
    private var wifiCycles = 0
    private var wifiLastCycleAt = 0L
    private var wifiNotified = false
    private var wifiStuckEpisodes = 0

    private fun wifiStuckTick(now: Long) {
        if (onWifi()) {
            if (wifiCycles > 0 || wifiNotified) write("wifi_stuck") { put("ok", true); put("cycles", wifiCycles); put("notified", wifiNotified) }
            wifiSeenAt = now; wifiCycles = 0; wifiNotified = false; wifiLastCycleAt = 0L
            return
        }
        val radioOn = runCatching { getSystemService(android.net.wifi.WifiManager::class.java).isWifiEnabled }.getOrDefault(false)
        val noWifiFor = now - (if (wifiSeenAt > 0) wifiSeenAt else sdkStartAt)
        val stuck = WifiStuckPolicy.isStuck(radioOn, WifiKeeper.remembered(this), false, noWifiFor)
        val (_, charging) = battery()
        val since = if (wifiLastCycleAt > 0) now - wifiLastCycleAt else Long.MAX_VALUE
        when (WifiStuckPolicy.decide(stuck, charging, wifiCycles, since, wifiNotified)) {
            WifiStuckPolicy.Act.CYCLE -> {
                if (wifiCycles == 0) wifiStuckEpisodes++
                wifiCycles++; wifiLastCycleAt = now
                Log.w(TAG, "📶 Wi-Fi 開著卻 ${noWifiFor / 60000} 分鐘沒連上 —— 放掉再要（第 $wifiCycles 次）")
                write("wifi_stuck") { put("act", "cycle"); put("n", wifiCycles); put("no_wifi_min", noWifiFor / 60000) }
                WifiKeeper.pauseForBluetooth(this, WifiStuckPolicy.CYCLE_PAUSE_MS)
            }
            WifiStuckPolicy.Act.NOTIFY -> {
                if (wifiCycles == 0) wifiStuckEpisodes++
                wifiNotified = true
                Log.w(TAG, "📶 Wi-Fi ${noWifiFor / 60000} 分鐘沒連上（充電中＝${charging}）—— 安靜通知她")
                write("wifi_stuck") { put("act", "notify"); put("charging", charging); put("no_wifi_min", noWifiFor / 60000) }
                notifyWifiStuck(noWifiFor)
            }
            WifiStuckPolicy.Act.NONE -> Unit
        }
    }

    private fun notifyWifiStuck(noWifiFor: Long) = runCatching {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel("wifi_stuck", "Wi-Fi 卡住", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 7106,
            Intent(android.provider.Settings.ACTION_WIFI_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE)
        nm.notify(7106, androidx.core.app.NotificationCompat.Builder(this, "wifi_stuck")
            .setSmallIcon(R.drawable.ic_bridge)
            .setContentTitle("📶 Wi-Fi 好像卡住了")
            .setContentText("錶 ${noWifiFor / 60000} 分鐘沒連上 Wi-Fi。如果在常用的 Wi-Fi 範圍內，把 Wi-Fi 關掉再打開。")
            .setStyle(androidx.core.app.NotificationCompat.BigTextStyle()
                .bigText("錶 ${noWifiFor / 60000} 分鐘沒連上 Wi-Fi。如果在常用的 Wi-Fi 範圍內，把 Wi-Fi 關掉再打開一次通常就會好。點這裡直接到 Wi-Fi 設定。"))
            .setContentIntent(open).setAutoCancel(true).setSilent(true).build())
    }

    private fun onWifi(): Boolean = runCatching {
        val cm = getSystemService(android.net.ConnectivityManager::class.java)
        cm.allNetworks.any { n ->
            cm.getNetworkCapabilities(n)?.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) == true
        }
    }.getOrDefault(false)

    private fun phoneLeftCheck() {
        com.google.android.gms.wearable.Wearable.getNodeClient(this).connectedNodes.addOnSuccessListener { nodes ->
            val now = System.currentTimeMillis()
            val near = nodes.any { it.isNearby }
            phoneNearby = near
            if (near) { phoneAwaySince = 0L; phoneAlerted = false; return@addOnSuccessListener }
            if (phoneAwaySince == 0L) phoneAwaySince = now
            val (_, charging) = battery()
            val worn = wearState(charging) != ONBODY_OFF
            val away = now - phoneAwaySince
            if (WalkPolicy.shouldAlertPhoneLeft(away, recentWalk.count { it }, onWifi(), worn, charging, phoneAlerted)) {
                phoneAlerted = true; phoneAlerts++
                alertPhoneLeft(away)
            }
        }
    }

    private fun alertPhoneLeft(awayMs: Long) {
        Log.w(TAG, "📱 手機沒帶？藍牙斷了 ${awayMs / 1000} 秒、在走路、沒連 Wi-Fi")
        write("phone_left") { put("away_s", awayMs / 1000); put("walk3", recentWalk.count { it }) }
        runCatching {
            val vib = getSystemService(android.os.VibratorManager::class.java).defaultVibrator
            vib.vibrate(android.os.VibrationEffect.createWaveform(SayPolicy.WAKE_PATTERN, -1),
                android.os.VibrationAttributes.Builder().setUsage(android.os.VibrationAttributes.USAGE_ALARM).build())
        }
        runCatching {
            startActivity(Intent(this, SayCardActivity::class.java)
                .putExtra("text", "📱 手機沒帶？\n錶跟手機斷線 ${awayMs / 60000} 分鐘了")
                .putExtra("spoken", false).putExtra("ask", false).putExtra("wake", true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP))
        }.onFailure { Log.w(TAG, "📱 卡片開不了", it) }
    }

    override fun onDestroy() {
        super.onDestroy()
        runCatching { sensorMgr.unregisterListener(stepListener) }
        sdkGuard.removeCallbacksAndMessages(null)
        if (live === this) live = null
        write("service_stop") { put("hr_count", hrCount) }
        JsonlBuffer.flush()
        runCatching {
            measureClient.unregisterMeasureCallbackAsync(DataType.HEART_RATE_BPM, callback)
        }
        samsung.stop()
        motion.stop()
        env.stop()
        SamsungSensors.onRecord = null
        SamsungSensors.onTemp = null
        SamsungSensors.probeStatus = null
        runCatching { unregisterReceiver(recControl) }
        recorder.stop()
        runCatching { sendPool.shutdownNow() }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ── 常駐通知 ────────────────────────────────────────────────
    // 才不會在螢幕暗掉時把它凍結。拿掉它整支服務就失去意義。
    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "生理資料收集",
                    NotificationManager.IMPORTANCE_LOW)
            )
        }
        val tap = PendingIntent.getActivity(
            this, 0, Intent(this, TalkActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val builder = androidx.core.app.NotificationCompat.Builder(this, CHANNEL)
            .setContentTitle("阿寶在收資料")
            .setContentText("心率、皮膚溫度、心跳間隔")
            .setSmallIcon(R.drawable.ic_talk)
            .setContentIntent(tap)
            .setOngoing(true)
            // ★ 這一行是關鍵。Wear OS 看到 CATEGORY_WORKOUT 才知道
            .setCategory(androidx.core.app.NotificationCompat.CATEGORY_WORKOUT)
            .setPriority(androidx.core.app.NotificationCompat.PRIORITY_LOW)

        androidx.wear.ongoing.OngoingActivity.Builder(this, NOTIF_ID, builder)
            // 要換回紫色麥克風：改回 R.drawable.ic_talk。
            .setStaticIcon(R.drawable.ic_blank)
            .setTouchIntent(tap)
            .setStatus(
                androidx.wear.ongoing.Status.Builder()
                    .addTemplate("收集生理資料中").build()
            )
            .build()
            .apply(this)

        return builder.build()
    }

    companion object {
        /** 正在跑的那一個（Diag.kt 讀狀態用）；沒在跑＝null。 */
        @Volatile var live: CollectorService? = null

        /** 一次醒來最多送幾塊。4 × 180 KB ≈ 720 KB，藍牙吃得消。 */
        private const val SEND_CHUNKS_PER_WAKE = 4

        /** 平常多久記一筆心率。 */
        private const val HR_EVERY_MS = 60_000L
        private const val HR_JUMP_BPM = 10
        /** 就算一直在跳，也最快五秒一筆 —— 免得運動時又變回一秒一筆。 */
        private const val HR_JUMP_MIN_GAP_MS = 5_000L

        const val TAG = "WatchBridge"

        const val ONBODY_ON = "on"
        const val ONBODY_OFF = "off"
        const val ONBODY_UNKNOWN = "unknown"

        /** 裝置沒在講話，但脈搏還在 —— **比 unknown 強得多，比 on 弱**。 */
        const val ONBODY_INFERRED_ON = "inferred_on"

        /**
         *
         *
         *     心率取樣間隔　中位 5.9 分、p90 10.2 分、**最大 19.9 分**
         *     超過 5 分鐘的間隔佔 59%
         *
         * 所以 5 分鐘的窗口在睡覺時有一大半時間不成立 →
         * 而那正是最需要判斷的時段。
         *
         * 25 分鐘 ≈ 實測最大間隔 20 分鐘 × 1.25 的餘裕。
         * 可以接受，因為真的脫下來時 availability 會主動發 OFF_BODY 事件，
         * 那條路比脈搏更快，而且 off 的優先級高於脈搏。
         *
         *    裝到他們身上要重新量一次，不要直接沿用。
         */
        const val PULSE_FRESH_MS = 25 * 60_000L

        /**
         *
         * 15 分鐘。availability 事件是狀態改變時才送，不是定期送 ——
         * 所以久沒更新有兩種可能：狀態真的沒變，或者**那條通道死了**。
         */
        //
        //   15 分鐘 + 只靠 availability 刷新的組合是錯的：
        //   availability 只在狀態改變時才發，穩定戴著好幾小時不會有任何事件，
        //
        //   現在有兩個東西會刷新：availability 事件、**以及量到有效心跳**。
        //   **一小時內既沒有狀態變化、也完全量不到脈搏** —— 那才真的該說不知道。
        const val ONBODY_STALE_MS = 60 * 60_000L

        /**
         *
         * 之所以不是 ONBODY_STALE_MS 那個一小時：實測發現 OFF_BODY 會準時發，
         */
        const val ONBODY_OFF_TRUST_MS = 3 * 60_000L

        /**
         *
         *
         * 三星確實在有互動時每秒一筆，但安靜下來之後會退回**每 10 分鐘量一次**。
         *
         * 九天資料的空檔分佈，每一天都長一樣：
         * ```
         *   中位數     1~4 秒
         *   90 百分位  58~391 秒
         *   99 百分位  **585~610 秒**   ← 每一天都卡在 600 秒
         * ```
         * **600 秒不是故障，是三星的省電量測週期。**
         *
         * ```
         *   斷 244 + 等 229 = 473
         *   斷 253 + 等 170 = 423
         *   斷 254 + 等 343 = 597
         *   斷 554 + 等  47 = 601
         *   斷 561 + 等  44 = 605      中位數 589 秒
         * ```
         * **踢不踢，心率都在第 10 分鐘回來。那 18 次全是白踢。**
         * 代價是耗電從平常的每小時 2.4~3.4% 變成 **7.1%** —— 續航從 30~40 小時掉到 14 小時。
         *
         *
         * 15 分鐘 = 自然週期 600 秒 + 一半的餘裕。重裝後感測器真的死掉那種
         */
        const val SENSOR_DEAD_MS = 15 * 60_000L

        /**
         * 一般情況下兩次踢之間的間隔。
         * **踢得密不會讓它活過來，只會讓電更快沒。**
         */
        const val KICK_COOLDOWN_MS = 15 * 60_000L

        /**
         * 溫度多久沒更新就主動去要一筆。
         * 設 5 分鐘：比 CONTINUOUS 的自然間隔（中位 8~52 分）短，
         * 但又不會在它剛送完一批之後馬上又去吵它。
         */
        const val TEMP_ASK_AFTER_MS = 5 * 60_000L

        /**
         * 兩次主動要溫度之間至少隔多久。
         * 所以寧可少要幾次。
         */
        const val TEMP_ASK_COOLDOWN_MS = 5 * 60_000L

        /** 裝置明確說沒戴時的間隔 —— 多半真的沒戴，不用一直踢。 */
        const val KICK_COOLDOWN_OFF_MS = 20 * 60_000L

        /**
         * 溫度多久之內算數。
         *
         * **又是我沒量就寫的**。真的量了之後：戴著時的批次空檔中位數 8~52 分鐘、
         * 90 百分位約一小時。10 分鐘的窗只涵蓋得到少數情況。
         *
         * 45 分鐘會用到比較舊的溫度，但它現在只當**否決票** ——
         */
        const val TEMP_FRESH_MS = 45 * 60_000L

        /**
         * 溫差門檻。**主訊號是溫差，不是絕對皮膚溫。**
         *
         * 溫差不跟著跑，因為環境同時影響皮膚感測器跟錶身，會互相抵銷。
         */
        const val DELTA_WORN = 0.15

        /**
         * 溫差低到這個程度就是**明確不在手上** —— 這是否決票的門檻。
         *
         *
         * ```
         *             錶身 34.92°C   ← 充電充熱的
         *             皮膚 28.97°C   ← 手腕還沒把它暖起來
         * ```
         *
         * 拉九天資料量三種情境：
         * ```
         *   戴著（非剛下充電座）    n=7932   5% +0.10   中位 +0.51
         *   確定沒戴（非充電）      n= 759   5% -4.12   中位 +0.17  ← 注意中位是正的！
         *   剛下充電座 15 分鐘內    n= 347   5% -5.95   中位 -1.12  ← 中毒區
         * ```
         * 門檻取捨：
         * ```
         *   -0.3：誤殺戴著的 0.90%   抓到沒戴的 37.0%
         *   -1.5：誤殺戴著的 0.09%   抓到沒戴的 33.1%   ← 誤殺少十倍，只少抓 4 個百分點
         * ```
         * **兩種錯的代價不對稱**（誤判沒戴 = 警報關掉），所以選 -1.5。
         *
         *   **溫度本來就只在尾巴那一段有鑑別力**，它是否決票不是必要票，
         *   這組數字就是原因。
         */
        const val DELTA_OFF = -1.5

        /**
         * 離開充電座之後多久之內，**不採信溫度的否決票**。
         *
         * 充電會把錶身加熱到 34~35°C，而手腕要十幾分鐘才把皮膚感測器暖回來。
         * 這段期間溫差是**強烈負值卻戴著**（實測 -5.95），
         *
         */
        const val AFTER_CHARGE_BLIND_MS = 15 * 60_000L
        private const val CHANNEL = "collector"
        private const val NOTIF_ID = 1

        fun start(ctx: Context) {
            ctx.startForegroundService(Intent(ctx, CollectorService::class.java))
        }
    }
}

/**
 * 現在攢著，15 秒或 16 KB 寫一次；傳送（WearSender.sendOnce）與服務停止前一定先 flush，送出去的永遠是完整的。
 * 代價：app 被殺的那一刻最多丟 15 秒資料；換到的是少幾千次 flash 寫入與 CPU 喚醒。
 */
object JsonlBuffer {
    private val buf = StringBuilder()
    private var file: java.io.File? = null
    private val h = android.os.Handler(android.os.Looper.getMainLooper())
    private val tick = Runnable { flush() }
    private var pending = false

    @Synchronized
    fun append(f: java.io.File, line: String) {
        if (file != null && file != f) flushLocked()   // 跨日換檔：先把前一天的寫完
        file = f
        buf.append(line).append(10.toChar())
        if (buf.length > 16_000) flushLocked()
        else if (!pending) { pending = true; h.postDelayed(tick, 15_000) }
    }

    @Synchronized
    fun flush() = flushLocked()

    private fun flushLocked() {
        pending = false; h.removeCallbacks(tick)
        val f = file ?: return
        if (buf.isEmpty()) return
        runCatching { f.appendText(buf.toString()) }.onFailure { android.util.Log.e("WatchBridge", "緩衝寫檔失敗", it) }
        buf.setLength(0)
    }
}
