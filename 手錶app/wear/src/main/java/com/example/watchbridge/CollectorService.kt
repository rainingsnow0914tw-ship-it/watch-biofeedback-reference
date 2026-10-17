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
 * 為什麼非做不可（2026-08-13 凌晨查出來的）：
 *     前一版把感測器掛在 Activity 上。手錶螢幕一暗，系統就把 Activity 凍結，
 *     **app 還活著（pidof 有值）但一筆資料都不送**。
 *     等於「戴著就會收」這件事根本是假的，只有盯著錶面時才在收。
 *
 * 為什麼要自己寫檔，不再用 logcat（同一晚查出來的）：
 *     logcat 是除錯用的環形緩衝區，幾 MB 就滿，滿了從頭蓋掉。
 *     而手錶硬碟有 8.3 GB、空著 5.2 GB —— 一天的資料才幾 MB，
 *     存一年都用不完。**資料本來就不該放在除錯通道裡。**
 *
 *     這一改的真正意義是：**連線不穩從「災難」變成「小麻煩」**。
 *     以前斷線＝資料真的消失；現在斷線只是晚一點搬回電腦。
 *     （使用者 原話：「無線偵錯好像很不穩耶，它很容易自動關掉」——
 *       那個問題治不好，但可以讓它不再要命。）
 *
 * 為什麼要記電量和充電狀態（使用者 2026-08-13 指定）：
 *     光看「不在手上」會誤判。兩個訊號合起來才分得出：
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

    private lateinit var out: File
    private var hrCount = 0

    // 心率降頻用的狀態。詳細理由寫在 onDataReceived 裡。
    private var lastHrBpm = 0
    private var lastHrAt = 0L
    private var lastHrCount = 0

    /**
     * 開機的那個瞬間（epoch 毫秒）。
     *
     * Health Services 的樣本帶的是「開機後經過多久」，要加上這個才變成真實時間。
     * 用 `elapsedRealtime()` 反推：現在的牆鐘時間 − 開機到現在的時間。
     *
     * ⚠ 算一次就好，**不要每次用都重算**：牆鐘會被 NTP 校正、會被使用者改，
     * 每次重算的話同一個樣本在不同時候會算出不同的時間。
     */
    private val bootMs =
        System.currentTimeMillis() - android.os.SystemClock.elapsedRealtime()
    /**
     * 錶在不在手上 —— **三態，不是布林**。
     *
     * ⚠ 2026-08-16 改。原本是 `private var onBody = true`，兩個問題：
     *
     *   一、**預設 true = 零證據就宣稱戴著。** 服務剛啟動、第一個
     *       availability 事件還沒到之前，它一律說「在手上」。
     *
     *   二、**`ACQUIRING`（還在找訊號）被算成在手上。**
     *       實測三天：ACQUIRING 出現 33/8/17 次，比 AVAILABLE 還多。
     *
     * 為什麼這件事很嚴重：離腕放在充電座上的錶，動作量比睡著的人還靜
     * （實測 219 分鐘、中位 42、95.9% 低於任何合理門檻）。
     * 所以**「在不在手上」是所有身體判斷的唯一前置閘門**，
     * 而那個閘門本來預設開著。
     *
     * 三態的意義：**「不知道」永遠不能當成「在手上」。**
     * 下游看到 unknown 一律回「說不準」，不是回「正常」。
     */
    private var onBody: String = ONBODY_UNKNOWN

    /** 最後一次收到 availability 的時刻。太久沒更新就退回「不知道」。 */
    private var onBodyAt = 0L

    /** 最後一次量到有效脈搏。**跟 availability 完全獨立的一條證據。** */
    private var lastPulseAt = 0L

    /**
     * 最後一次皮膚溫度，以及**溫差**（皮膚 − 錶身）。
     *
     * 第三條獨立證據，2026-08-25 加的。前兩條在下面那個洞裡會互相矛盾，
     * 而**矛盾的時候需要的是第三票，不是把其中一票調大聲。**
     */
    private var lastTempAt = 0L
    private var lastTempDelta = 0.0

    /** 上次因為「脈搏回來了」踢三星感測器的時刻。用來限制踢的頻率。 */
    private var lastKickAt = 0L

    /** 上次主動去要一筆體溫的時刻。 */
    private var lastTempAskAt = 0L

    /**
     * 她**親口說**脫錶的時刻（手腕上按「脫錶」那一顆）。
     *
     * **這是唯一一條不是感測器給的證據，而它比任何感測器都可靠。**
     * 感測器只能看到「有沒有脈搏」，看不到「她正打算脫下來」。
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
     * 最後一次還在充電的時刻。**用來把「剛下充電座」那段的溫度否決票關掉。**
     *
     * 充電會把錶身加熱到 34~35°C，拿下來戴上之後溫差是強烈負值卻其實戴著
     * （2026-08-26 實測 -5.95）。那正是她每天最常戴錶的時刻。
     */
    private var lastChargingAt = 0L

    /**
     * 佩戴狀態 —— **多證據融合的結果**，不是單一感測器的回報。
     *
     * ## 這個設計是 使用者 2026-08-17 指出來的
     *
     * > 「availability 過期只能表示很久沒更新，不能直接等於佩戴狀態 unknown。
     * >   佩戴狀態該用多證據融合，比如心率 IBI、動作量，
     * >   **如果生理訊號還在持續，就應該標 inferred on，而不是 unknown**。」
     *
     * 她說得對，而且指出了我兩次修錯的共同根源：
     * **我一直把「三星怎麼說」跟「她到底有沒有戴」當成同一個變數。**
     *
     *   08-16 的錯：預設 true → 零證據就宣稱戴著
     *   08-17 早上的錯：過期就 unknown → 一天 83.8% 變成「不知道」，判斷層變廢的
     *   08-17 中午的錯：用脈搏**覆蓋** availability → 兩個變數又被混成一個
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
     *    它的意思是「這支錶在某個活人身上」，不保證是**誰**、也不保證貼合良好。
     *    但它遠比 `unknown` 強 —— 脈搏是物理事實，不是推論。
     *
     * ⚠️ **`off` 優先於脈搏。** 裝置主動說「我離開手腕了」的時候，
     *    就算還讀到殘留訊號也不能推翻它 —— 那多半是雜訊或錶壓在什麼東西上。
     */
    private fun wearState(charging: Boolean): String {
        val now = System.currentTimeMillis()
        expireUserOffAt(now)
        val availFresh = onBodyAt > 0 && now - onBodyAt <= ONBODY_STALE_MS
        val pulseFresh = lastPulseAt > 0 && now - lastPulseAt <= PULSE_FRESH_MS

        // ★ 她親口說脫錶，而且之後沒有新的脈搏 → 就是沒戴。**最高優先。**
        //   （比 charging 還前面嗎？不 —— 充電是物理事實，兩者不會衝突；
        //     放在 charging 後面只是因為充電那條更便宜。）
        // 只用十二小時過期，不因單一脈搏永久清掉標記：離腕 PPG 可能是假訊號。
        val userSaysOff = UserOffPolicy.isExplicitlyOff(userOffAt, lastPulseAt, now)
        if (charging) lastChargingAt = now
        // 剛下充電座那段，錶身還是熱的 → 溫度的否決票不算數。
        val tempPoisoned = lastChargingAt > 0 && now - lastChargingAt <= AFTER_CHARGE_BLIND_MS

        return when {
            // ⚠ 2026-08-18：**在充電座上就是沒戴，這件事不需要推論。**
            //
            //   實測 08-18 一整天：272 筆 unknown 裡有 237 筆（87%）是
            //   **錶充飽了還躺在充電座上** —— availability 早就過期、脈搏當然也沒有，
            //   於是融合出來的結論是「不知道她有沒有戴」。
            //
            //   但充電這件事是**電路上的物理事實**，比任何感測器都硬：
            //   一支接著充電線的錶不可能在手腕上。
            //   放這一條之後，今天的盲區從 19.5% 掉到 2.5%。
            //
            //   為什麼這很重要：影子模式的三條出場條件之一就是「瞎掉的時數 < 20%」，
            //   而 19.5% 那個數字**幾乎全部是這一個可以直接消掉的原因**。
            //   不修的話，我會拿一份「勉強及格」的成績單去說系統看得見 ——
            //   而真正的盲區其實只有 2.5%，兩個數字導向完全不同的判斷。
            charging -> ONBODY_OFF
            userSaysOff -> ONBODY_OFF        // ★ 人講的話，不用再猜

            // ⚠⚠ 2026-08-25：這裡本來只有一行 `availFresh && onBody == OFF -> OFF`，
            //    而那一行會讓手錶在她**戴回去之後，最多聾一個小時**。
            //
            //    洞是怎麼發現的：她把錶放桌上出門、回來戴上，我在旁邊看著資料 ——
            //    心率 14:30:16 就恢復了（status=1、HR=99，貨真價實的脈搏），
            //    **但 on_body 到 14:38 還在說「沒戴」。**
            //
            //    去翻 availability 的事件史才看懂：
            //      14:23:04  UNAVAILABLE_DEVICE_OFF_BODY   ← 脫下來，正確
            //      14:29:56  ACQUIRING                     ← 戴回去，它只說「我在讀」
            //      （然後就沒有了 —— **AVAILABLE 一直沒來**）
            //
            //    整天 34 個 availability 事件裡，AVAILABLE 只出現 4 次，
            //    而且每一次前面都是 UNAVAILABLE→ACQUIRING→AVAILABLE 這個**重啟**的形狀。
            //    **AVAILABLE 是「感測器重新掛好了」的意思，不是「她戴上了」的意思。**
            //
            //    後果有多嚴重：ONBODY_STALE_MS 是一小時，所以在那一小時裡
            //    **所有以 on_body 當閘門的判斷全部關著** —— 警報不會響、
            //    worn_minutes 少算、triage 一律回「說不準」。
            //    她戴著錶、心跳正常在跳，而系統認為手腕上沒有人。
            //
            //    為什麼不能只用「脈搏回來了」就翻案：拿九天資料重放過，
            //    那樣會翻掉 46,975 筆，其中 **5.2% 被溫度打臉**（錶壓在東西上
            //    也會吐出漂亮的假脈搏 —— 08-20 02 時就是這樣，HR=96 status=1）。
            //    所以要第三票。
            availFresh && onBody == ONBODY_OFF -> {
                val offAge = now - onBodyAt
                // **這一拍脈搏是不是在它說「我離開了」之後才出現的。**
                // 不用門檻，用因果順序 —— 桌上的錶不會生出新的有效脈搏。
                val pulseAfterOff = lastPulseAt > onBodyAt
                val tempFresh = lastTempAt > 0 && now - lastTempAt <= TEMP_FRESH_MS
                // ⚠ 2026-08-26 第二次修正：**溫度從「必要票」降級成「否決票」。**
                //
                //   第一版要求溫差 > +0.15 才肯翻案。裝上去當天早上就看到它失效：
                //   她 04:19 戴上錶，心率 04:22 就有了（HR=93 status=1，真的），
                //   **但溫度停在 04:17 那一批，溫差 +0.11 —— 差 0.04 過不了門檻。**
                //   系統整整十分鐘說她沒戴，直到 04:29 AVAILABLE 自己補發。
                //
                //   查了才知道根本原因：**皮膚溫度本來就很稀疏。**
                //   九天資料，戴著時的批次空檔中位數是 **485~3127 秒（8~52 分鐘）**，
                //   90 百分位大多是一小時。要求「10 分鐘內的新鮮溫度」等於
                //   **大部分時候根本沒有票可以投** —— 重放九天，65.7% 的情況溫度是空的。
                //
                //   那要不要乾脆讓脈搏自己說了算？先去問資料會不會出事：
                //   九天裡「on_body=off 但有看似有效的心率」共 **51,396 筆**，
                //   ```
                //     中位 80   95% 93   99% 98   最高 118
                //     ≥120（警報門檻）：**0 筆**
                //   ```
                //   **假脈搏從來沒有到過警報門檻。** 最高 118，而門檻是 120。
                //   所以誤判成 inferred_on 不會製造假警報，最多讓戴錶時數多算一點。
                //
                //   而反過來的錯 —— 她戴著卻被判成沒戴 —— **會讓警報整段關掉**。
                //   兩種錯的代價差太多了，所以：
                //
                //       溫度說「明顯沒戴」→ 否決，維持 off
                //       溫度沒話說         → 相信脈搏
                //
                //   ⚠ 這裡**故意**保留 08-20 那型的風險（錶壓在東西上吐假脈搏）。
                //     代價已經量過了：那型的心率上限是 118，碰不到警報。
                //     如果哪天看到 off-body 的假脈搏破 120，這一條要立刻重新檢討。
                when {
                    // 剛講的話要信。裝置說「我離開手腕了」的那一刻，它是對的。
                    offAge <= ONBODY_OFF_TRUST_MS -> ONBODY_OFF
                    // 溫度否決票：溫差明顯是負的，那就真的不在手上。
                    // ⚠ 但剛下充電座那 15 分鐘不算 —— 那時候錶身是熱的，
                    //   溫差會強烈負值**卻其實戴著**（2026-08-26 實測 -5.95）。
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
     * 這個結論怎麼來的。**分析時要能分辨「不知道」的三種原因** ——
     * 「從來沒知道過」跟「知道過但過期了」的資訊量差很多。
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
     * ## 為什麼不能掛在狀態轉換上（2026-08-25 當場撞到的死結）
     *
     * 第一版我把那一腳寫在「收到脈搏而系統還說沒戴」的地方，看起來很合理。
     * 裝到錶上之後才發現它**永遠不會執行**：
     *
     * ```
     * 三星判定沒戴 → 停送心率 → 收不到脈搏 → 踢不出去 → 三星永遠不知道要恢復
     *      ↑                                                    │
     *      └────────────────────────────────────────────────────┘
     * ```
     *
     * **觸發條件裡不能包含「那個壞掉的東西本身」。**
     * 這跟 08-18 那條家規是同一件事：檢查工具不能依賴被檢查的對象。
     *
     * 所以改成掛在**動作窗口**上 —— 加速度計是我們自己註冊的，
     * 不受三星佩戴判斷影響，任何狀態下都每分鐘進來一次。它是這支程式裡
     * 最接近「心跳訊號」的東西。
     *
     * ## 踢的節奏
     *
     * | 情況 | 多久踢一次 | 為什麼 |
     * |---|---|---|
     * | 充電中 | 不踢 | 在充電座上沒有脈搏是正常的，不是故障 |
     * | 裝置明確說沒戴 | 20 分鐘 | 多半真的放在桌上，慢慢確認就好 |
     * | 其他（含 ACQUIRING／unknown）| 5 分鐘 | **這才是危險區** —— 她可能戴著而我們瞎著 |
     */
    private fun sensorWatchdog() {
        val now = System.currentTimeMillis()
        samsung.sweepTempNow(now)          // 逾時保險：卡住的隨選請求要收掉
        val (_, charging) = battery()
        if (charging) return

        // ── 溫度太舊就主動去要一筆（2026-08-26 加）──────────────
        //
        //   佩戴判斷需要溫度當否決票，但 CONTINUOUS 那條是**一批一批**送的，
        //   實測空檔中位數 8~52 分鐘 —— 決策要用的時候常常沒有。
        //   這支錶支援 SKIN_TEMPERATURE_ON_DEMAND，所以不要等，直接去要。
        //
        //   ⚠ 只在「有脈搏、代表她可能戴著」的時候要 ——
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
        if (pulseAge <= SENSOR_DEAD_MS) return
        val confidentlyOff = onBody == ONBODY_OFF && now - onBodyAt <= ONBODY_STALE_MS
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
    }

    // ── 電池 ────────────────────────────────────────────────────
    // 用黏性廣播讀當下狀態就夠，不用註冊接收器一直被叫醒。
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
            //   下游**必須**把 unknown 當成「不知道」，不能當成 false 也不能當成 true。
            val wear = wearState(charging)
            put("on_body", wear)
            // 不是明確的 on/off 就把來源帶上 —— 分析時要看得出這個結論怎麼來的。
            if (wear != ONBODY_ON && wear != ONBODY_OFF) put("on_body_why", wearWhy(charging))
            body()
        }
        try {
            val f = File(out, "${day.format(Date())}.jsonl")
            f.appendText(o.toString() + "\n")
        } catch (e: Exception) {
            Log.e(TAG, "寫檔失敗", e)
        }
    }

    // ── Health Services 即時心率 ────────────────────────────────
    private val callback = object : MeasureCallback {
        override fun onAvailabilityChanged(
            dataType: DeltaDataType<*, *>, availability: Availability
        ) {
            // 這裡是「在不在手上」最可靠的來源 —— 裝置**主動說**的，
            // 不是我們從「資料沒了」猜出來的。
            // （2026-08-13 的教訓：我看到資料斷了就說「應該是充電」，
            //   結果那只是取樣間隔。沒有資料不是一種資料。）
            val wasOff = onBody == ONBODY_OFF
            // ⚠ ACQUIRING 不算「在手上」—— 它的意思是「我還不知道」。
            //   把「還不知道」算成「在手上」，等於用零證據打開所有身體判斷的閘門。
            // ⚠ 2026-08-16 第二次修正。第一版把 ACQUIRING 也算成 unknown，
            //   但實測當天的分佈是：
            //       ACQUIRING 25 次、OFF_BODY 24 次、**AVAILABLE 只有 5 次**
            //   如果 ACQUIRING 一律當「不知道」，系統會有大半時間什麼都不能判 ——
            //   那不是謹慎，是把自己弄瞎。
            //
            //   想清楚語意：**ACQUIRING 的意思是「我正在重新讀」，不是「我忘了」。**
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

            // 從「沒戴」回到「戴上」：三星那條不會自己恢復，要踢它一下。
            // 不踢的話心率會回來、但皮膚溫度和 IBI 整晚都是空的（2026-08-13 實測）。
            if (wasOff && onBody == ONBODY_ON) {
                Log.i(TAG, "戴回來了，重新掛三星的感測器")
                samsung.restartTrackers()
            }
        }

        override fun onDataReceived(data: DataPointContainer) {
            for (p in data.getData(DataType.HEART_RATE_BPM)) {
                hrCount++
                val bpm = p.value.toInt()
                if (bpm <= 0) continue          // 沒讀到的時候會給 0，別讓它污染基準

                // ⚠ 2026-08-17 14:40：量到有效心跳 = **錶一定在某個人的手腕上**。
                //
                //   這是比 availability 更硬的證據 —— availability 是三星的推論，
                //   脈搏是物理事實。
                //
                //   為什麼非加不可：availability **只在狀態改變時才發**，
                //   穩定戴著好幾小時完全不會有事件。而 08-16 我加的 15 分鐘過期規則
                //   就把那些時段全部判成 unknown ——
                //   實測結果：**一天 845 分鐘裡有 708 分鐘（83.8%）是「不知道」**，
                //   而 triage 對 unknown 一律回「說不準」→ **判斷層又變成廢的**。
                //
                //   那是我 08-16 修「預設 true」修過頭造成的。修對一半比不修更糟：
                //   零證據宣稱戴著是錯的，但把「有脈搏」也說成不知道，
                //   等於把自己弄瞎。
                // ⚠ 這裡**不覆蓋 availability**（我第一版是直接覆蓋，那是錯的）。
                //   使用者 指出：「availability 過期只能表示很久沒更新，
                //   不能直接等於佩戴狀態 unknown。佩戴狀態該用多證據融合。」
                //   所以脈搏獨立記在自己的變數裡，融合在 wearState() 裡做。
                lastPulseAt = System.currentTimeMillis()

                // 2026-08-25：踢感測器的邏輯移到 sensorWatchdog()，
                // 由每分鐘的動作窗口驅動 —— 見那個函式上面的說明（**這裡會死結**）。

                // ⚠ 2026-08-17 05:20 修的**資料損失** bug。
                //
                //   這裡本來是 `val now = System.currentTimeMillis()` —— 牆上的時鐘。
                //   **Health Services 是整批送資料的**：一次 onDataReceived 可能帶進
                //   兩百多筆、橫跨十分鐘的樣本，而它們是在**同一毫秒**被交到手上的。
                //   用牆上的時鐘判斷「距離上次多久」，整批只有第一筆通得過，
                //   **其餘兩百多筆全部被丟掉** —— 十分鐘的心率被壓成一個點。
                //
                //   實測證據（v0.24 跑 19.6 分鐘）：
                //       04:55:02  bpm=85   略過=0
                //       05:05:03  bpm=100  略過=527  跳動=true
                //   兩筆間隔十分鐘，而且第二筆是靠「跳動」規則才寫出來的 ——
                //   **「每分鐘一筆」那條十分鐘內一次都沒觸發。**
                //
                //   而且時間戳也是錯的：整批都會被蓋上「送達時間」，
                //   不是「量到的時間」。分析時看到的心率會集中在幾個尖峰上，
                //   中間一片空白 —— 看起來像感測器壞了。
                //
                //   這跟皮膚溫度那個坑是同一隻病（當時的修法是 p.timestamp）。
                //   **凡是感測器資料，時間一律用樣本自己的，不要用寫入的那一刻。**
                val sampleMs = bootMs + p.timeDurationFromBoot.toMillis()
                val now = sampleMs

                // ⚠ 2026-08-17 降頻。**這一型佔了全部資料的 39.2%，而且是重複的** ——
                //     三星 SDK 那條 `hr_ibi` 的紀錄裡本來就帶著 hr 欄位。
                //     兩者來源不同（Health Services vs 三星），留著互相對照有價值，
                //     但**不需要一秒一次**。
                //
                //     省下來的不是硬碟（手機還有 653 GB），**是藍牙要搬的量**。
                //     少四成 = 每次醒來追得更快 = 鋸齒更不容易再出現。
                //
                // ⚠ 但**不可以**做成單純的「每分鐘寫一筆」。
                //     那樣會把兩筆之間的變化整段抹掉 —— 而那正好是警報唯一的依據。
                //     心率從 70 衝到 130 再掉回來，如果剛好落在間隔裡，
                //     檔案裡會看起來一整分鐘都很平靜。
                //     **對一個要判斷「人是不是出事了」的系統，這是不能接受的。**
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
                    // 分析時才分得出「感測器停了」和「只是我降頻了」。
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

        // 這裡本來抓了一個 PARTIAL_WAKE_LOCK。**已經拿掉。**
        //
        // 拿掉的理由：WakeLock 是土法煉鋼，在手錶上很耗電，而且實測沒有解決問題。
        // 正解是 OngoingActivity + CATEGORY_WORKOUT（見 notification()）——
        // 那是 Wear OS 給健康量測 app 的正式管道，
        // 系統認得它，就不會在螢幕暗掉時把服務凍住。
        //
        // 來源：GitHub 上 KAIST iclab 的 GalaxyWatch-PPG-Logger。
        // 2026-08-13 使用者 說「也可以去 GitHub 上看看別人怎麼寫的找點靈感」，
        // 就是那一句話讓我找到這個做法的。

        write("service_start") { put("version", "0.22-sendfix") }

        runCatching {
            measureClient.registerMeasureCallback(DataType.HEART_RATE_BPM, callback)
        }.onFailure { Log.e(TAG, "Health Services 註冊失敗", it) }

        // ⚠ 順序很重要：onRecord 一定要在 samsung.start() **之前**設好。
        //
        //   2026-08-16 實測踩到：三星 SDK 的連線回呼跑在**背景執行緒**上，
        //   會跟 onCreate 的後續程式碼賽跑。原本 onRecord 設在 start() 後面十幾行，
        //   結果 onConnectionSuccess 先跑到 —— 那時候 onRecord 還是 null，
        //   那一筆 sdk_status **就這樣掉了**，而且沒有任何跡象。
        //
        //   諷刺的是我加那筆記錄的目的正是「不要讓東西安靜消失」。
        SamsungSensors.onRecord = { type, json ->
            write(type) { json(this) }
            // 動作窗口是**唯一在任何狀態下都會持續進來的東西** —— 拿它當心跳。
            if (type == "motion") {
                sensorWatchdog()
                // 2026-09-17：週日體重提醒。內部自己吃掉所有例外 —— 提醒壞掉不能拖垮收集。
                每週體重.檢查(this@CollectorService, lastPulseAt)
            }
        }
        // ⚠ 跟 onRecord 一樣，一定要在 samsung.start() **之前**設好。
        SamsungSensors.onTemp = { skin, air ->
            lastTempAt = System.currentTimeMillis()
            lastTempDelta = (skin - air).toDouble()
        }

        // 三星那層獨立跑，它連不上也不能拖累已經驗證可用的即時心率。
        samsung.start { line -> Log.i(TAG, "samsung: $line") }

        // ⚠ 2026-08-16 停用：MotionSampler（SensorManager 5 Hz）已經撤回。
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
                //   2026-08-16 就是這樣讓整晚的睡眠資料變成廢的。
                //   下游看到 trust=false **一律當作「這一分鐘不知道」**，
                //   不要拿去算基線、不要拿去判睡眠、不要拿去校門檻。
                put("trust", w.samples >= MotionSampler.MIN_TRUSTWORTHY_SAMPLES)
            }
        }
        registerReceiver(
            recControl,
            IntentFilter().apply {
                addAction(FallRecorder.ACTION_START)
                addAction(FallRecorder.ACTION_STOP)
                addAction(MarkActivity.ACTION)     // 手腕上按的「記一筆」
                addAction(Buzzer.ACTION)           // 震動測試（不用重編就能換節奏）
                addAction(Siren.ACTION)            // 警報聲測試（同上，音色要由耳朵決定）
                addAction(AlarmActivity.ACTION)    // 整套警報演練（畫面＋聲音＋震動）
            },
            Context.RECEIVER_EXPORTED,   // 要讓 adb 的廣播進得來
        )

        // ⚠ 傳送**不能**跑在 Timer 自己的執行緒上。
        // 2026-08-15 實測：一次卡死的 await 就讓整個 Timer 執行緒停擺，
        // 之後再也沒有任何傳送 —— 而且 app 還活著、前景服務還正常，
        // 完全看不出來。
        //
        // 現在 Timer 只負責「叫一聲」，真正的工作丟給獨立的執行緒池。
        // 就算某一次卡住，下一次的排程照樣會來。
        // ⚠ 2026-08-16 深夜換掉的東西：這裡本來是
        //     sendTimer.scheduleAtFixedRate(..., 60_000L, 5 * 60_000L)
        //   **java.util.Timer 在 Doze 底下會被凍住**，實測 25 分鐘沒送過一次，
        //   醒來時三次擠在同一秒補齊 —— 整天的傳輸鋸齒就是這個。
        //   改用系統鬧鐘，休眠底下照樣會響。詳見 SendAlarm 的檔頭。
        SendAlarm.schedule(this)

        Log.i(TAG, "收集服務啟動，寫到 ${out.absolutePath}")
    }

    // ── 跌倒測試的遙控 ──────────────────────────────────────────
    // 用廣播控制，這樣 使用者 躺在床上被推的時候什麼都不用按，
    // 我從電腦這邊開始／停止就好。
    private val recorder by lazy { FallRecorder(this) }
    private val recControl = object : BroadcastReceiver() {
        override fun onReceive(c: Context?, i: Intent?) {
            when (i?.action) {
                // 標記直接寫進當天的資料檔，跟心率走同一條管線到手機。
                // **不另外開一條路** —— 少一條路就少一個會壞的地方，
                // 而且它自動享有既有的補送與斷點續傳。
                // 震動測試。**刻意做成廣播可觸發** —— 節奏最後要由 使用者 的手決定，
                // 每換一個數字就重編一次 APK 的話，這件事根本試不完。
                Buzzer.ACTION -> {
                    val pat = i.getStringExtra("pattern") ?: "chloe"
                    val ok = Buzzer.buzz(this@CollectorService, pat)
                    Log.i(TAG, "震動測試 $pat → ${if (ok) "送出" else "失敗"}")
                }
                // 警報聲測試。跟震動一樣做成廣播可觸發 ——
                // 音色、音量、聽不聽得見**只有耳朵能判斷**，
                // 每改一個參數就重編一次 APK 的話這件事試不完。
                // 整套警報演練。**這條路一定要留著** —— 警報是這個系統
                // 唯一「平常永遠不會執行到」的路徑，不定期演練的話，
                // 它會在真的需要的那一天才第一次被執行。
                AlarmActivity.ACTION -> {
                    if (i.getBooleanExtra("stop", false)) {
                        AlarmOverlay.hide(this@CollectorService)
                    } else if (i.getIntExtra("after", 0) > 0) {
                        // ⚠ 延遲觸發，**這是唯一能測到「螢幕全黑」情境的方法**。
                        //
                        //   2026-08-17 06:00 發現的問題：手錶一睡著，adb 就斷線，
                        //   所以沒辦法「等它睡著再用 adb 觸發」——
                        //   **連線這個動作本身就會把它叫醒。**
                        //
                        //   而「螢幕全黑時的第一次警報」正是真實情境
                        //   （半夜倒下的時候，錶一定是黑的），
                        //   也正是 使用者 唯一測出問題的那一次。
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
                        // 成功時多發一則，就等於多一個要關的東西（使用者 08-17 實測回饋）。
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

                    // ★ 2026-08-26：**她說脫錶，那就是脫錶。人的話比感測器大。**
                    //
                    //   這一顆按鈕的全部價值在這裡 —— 不是為了事後分析好看，
                    //   是為了**當下就把那 76.6% 的模糊地帶關掉**。
                    //
                    //   九天資料裡「不是戴著」的 99,300 筆，有 76,107 筆是
                    //   「裝置說沒戴、但 PPG 還在吐脈搏」—— 那一格我永遠猜不準，
                    //   而 08-25/26 整晚在調的門檻全都是在猜它。
                    //
                    //   ⚠ 為什麼是覆蓋而不是「參考」：
                    //     她按這顆的時候，錶還在她手上（要按才按得到）。
                    //     所以**接下來那幾秒的脈搏是真的**，但意義是「正在脫」。
                    //     如果只當參考，脈搏會繼續把狀態撐在 inferred_on，這顆就白按了。
                    //
                    //   ⚠ 怎麼解除：不需要她再按一次「戴上」。
                    //     `wearState()` 裡的規則是「脫錶標記之後才出現的脈搏 → 她戴回來了」，
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
                FallRecorder.ACTION_START -> {
                    val tag = i.getStringExtra("tag") ?: "test"
                    val f = recorder.start(tag)
                    Log.i(TAG, "▶ 錄影開始：$f")
                }
                FallRecorder.ACTION_STOP -> Log.i(TAG, "■ 錄影停止，${recorder.stop()} 筆")
            }
        }
    }

    // ── 定期把資料送去手機 ──────────────────────────────────────
    // 五分鐘一次。刻意不做「一有資料就送」——藍牙傳輸有固定成本，
    // 攢一批再送比較省電，而且 DataClient 本來就會排隊重送，
    // 晚五分鐘沒有任何損失。
    private val sender by lazy { WearSender(this) }
    // 單執行緒池：一次只送一塊（不要並發搶同一個 offset），
    // 但**卡住的那一次不會拖垮排程**。
    private val sendPool = java.util.concurrent.Executors.newSingleThreadExecutor()

    /** 只允許一條傳送執行緒。鬧鐘連續響兩次也不會兩邊搶同一個 offset。 */
    private val sending = java.util.concurrent.atomic.AtomicBoolean(false)

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // ⚠ 2026-10-17 08:30 實測：連裝七版之後，**待觸發清單裡沒有 SEND_NOW 鬧鐘**，
        //   手錶從 07:29 起一小時沒送資料，手機停在 07:30。Android 每次更新套件都會清掉
        //   這個 app 的鬧鐘；onCreate 那一次排程不知哪次沒排成（之前沒有任何錯誤訊息）。
        //   所以每次被叫都重排一次 —— 同一個 PendingIntent（FLAG_UPDATE_CURRENT）是冪等的，
        //   多排不會多響，少排會整條管路安靜死掉。
        SendAlarm.schedule(this)
        // 鬧鐘來敲門 → 在**背景執行緒**上送。
        //
        // ⚠ 為什麼是這裡而不是在 SendAlarm 裡直接送（2026-08-17 02:07 實測）：
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

                        // ★ 2026-08-26：順便把錄音也送走（使用者 問「藍牙不能傳錄音嗎」）。
                        //
                        //   跟在生理資料後面，是因為**生理資料優先** ——
                        //   錄音晚幾分鐘到沒關係，心率晚到會影響警報。
                        //
                        //   ⚠ 包在自己的 runCatching 裡：錄音那條炸掉**不可以**
                        //     拖垮生理資料的傳送。這跟看門狗那條「摘要算爆了不可以
                        //     拖垮看門狗」是同一條規矩。
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
        // 被系統殺掉要自己回來 —— 這支的全部意義就是「不能停」
        return START_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        write("service_stop") { put("hr_count", hrCount) }
        runCatching {
            measureClient.unregisterMeasureCallbackAsync(DataType.HEART_RATE_BPM, callback)
        }
        samsung.stop()
        motion.stop()
        SamsungSensors.onRecord = null
        SamsungSensors.onTemp = null
        runCatching { unregisterReceiver(recControl) }
        recorder.stop()
        runCatching { sendPool.shutdownNow() }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ── 常駐通知 ────────────────────────────────────────────────
    // 這個不是裝飾。系統就是看到它才知道「這個 app 在做正事」，
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
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val builder = androidx.core.app.NotificationCompat.Builder(this, CHANNEL)
            .setContentTitle("阿寶在收資料")
            .setContentText("心率、皮膚溫度、心跳間隔")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentIntent(tap)
            .setOngoing(true)
            // ★ 這一行是關鍵。Wear OS 看到 CATEGORY_WORKOUT 才知道
            //   「這是健康量測，別在螢幕暗掉時凍結它」。
            //   一般的通知沒有這個待遇（2026-08-13 實測：光是前景服務會被凍）。
            .setCategory(androidx.core.app.NotificationCompat.CATEGORY_WORKOUT)
            .setPriority(androidx.core.app.NotificationCompat.PRIORITY_LOW)

        // 再向系統註冊成「進行中的活動」——這是 Wear OS 專屬的東西，
        // 讓它出現在錶面上，也讓系統把它當成使用者正在進行的事。
        androidx.wear.ongoing.OngoingActivity.Builder(this, NOTIF_ID, builder)
            .setStaticIcon(android.R.drawable.ic_dialog_info)
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
        /** 一次醒來最多送幾塊。4 × 180 KB ≈ 720 KB，藍牙吃得消。 */
        private const val SEND_CHUNKS_PER_WAKE = 4

        /** 平常多久記一筆心率。 */
        private const val HR_EVERY_MS = 60_000L
        /** 跟上一筆差幾下就算「有事」，立刻記。靜息時分鐘間的正常起伏約 2–5 下。 */
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
         * 脈搏多久內算「還在」。
         *
         * ⚠ 2026-08-18 從 5 分鐘改成 25 分鐘，**這次是用真實睡眠資料校的**。
         *
         * 08-17 那晚 21:00~00:30 的實測：
         *     心率取樣間隔　中位 5.9 分、p90 10.2 分、**最大 19.9 分**
         *     超過 5 分鐘的間隔佔 59%
         *
         * 所以 5 分鐘的窗口在睡覺時有一大半時間不成立 →
         * 掉回 unknown → **夜裡「戴著」只剩 41~60%**，
         * 而那正是最需要判斷的時段。
         *
         * 25 分鐘 ≈ 實測最大間隔 20 分鐘 × 1.25 的餘裕。
         * 代價是「脫下來之後最多還會撐 25 分鐘才轉 unknown」——
         * 可以接受，因為真的脫下來時 availability 會主動發 OFF_BODY 事件，
         * 那條路比脈搏更快，而且 off 的優先級高於脈搏。
         *
         * ⚠️ 這個數字只用**一個人、一個晚上**校過。家人的取樣模式可能不同，
         *    裝到他們身上要重新量一次，不要直接沿用。
         */
        const val PULSE_FRESH_MS = 25 * 60_000L

        /**
         * 「在不在手上」的判斷多久算過期。
         *
         * 15 分鐘。availability 事件是狀態改變時才送，不是定期送 ——
         * 所以久沒更新有兩種可能：狀態真的沒變，或者**那條通道死了**。
         * 分不出來的時候要說「不知道」，不能沿用一個很舊的判斷。
         */
        // ⚠ 2026-08-17 從 15 分鐘改成 60 分鐘，而且加上「心跳也會刷新」。
        //
        //   15 分鐘 + 只靠 availability 刷新的組合是錯的：
        //   availability 只在狀態改變時才發，穩定戴著好幾小時不會有任何事件，
        //   於是時鐘走完就宣告 unknown。實測 83.8% 的一天都變成「不知道」。
        //
        //   現在有兩個東西會刷新：availability 事件、**以及量到有效心跳**。
        //   所以「過期」的真正意思變成：
        //   **一小時內既沒有狀態變化、也完全量不到脈搏** —— 那才真的該說不知道。
        const val ONBODY_STALE_MS = 60 * 60_000L

        /**
         * availability 說「沒戴」之後，**無條件相信它多久**。
         *
         * 之所以不是 ONBODY_STALE_MS 那個一小時：實測發現 OFF_BODY 會準時發，
         * 但**戴回來時對應的 AVAILABLE 不會發**（08-25 實測，只發 ACQUIRING）。
         * 一小時的信任期等於「脫下來一次就聾一小時」。
         */
        const val ONBODY_OFF_TRUST_MS = 3 * 60_000L

        /** 兩次「踢感測器」之間至少隔多久。避免它一直在重啟反而收不到資料。 */
        /**
         * 心率斷多久算「感測器死了」。
         *
         * ## ⚠ 2026-08-26 大幅調高：4 分鐘 → 15 分鐘
         *
         * 第一版寫「三星正常是每秒一筆，斷 4 分鐘一定不對」——**那句話是錯的。**
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
         * 而我用 4 分鐘當門檻的後果，08-26 凌晨那 2.5 小時全都記下來了：
         * 踢了 18 次，而**每一次「斷掉的秒數 + 踢完再等的秒數」都等於 ~600 秒**：
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
         * 這是「把正常行為當成故障」的教科書案例：
         * **下門檻之前要先量那件事本來長什麼樣子。** 我沒量就寫了一句「一定不對」。
         *
         * 15 分鐘 = 自然週期 600 秒 + 一半的餘裕。重裝後感測器真的死掉那種
         * （08-25 實測連續 12 分鐘零資料、而且 pulse_age=-1 代表根本沒醒過）照樣抓得到。
         */
        const val SENSOR_DEAD_MS = 15 * 60_000L

        /**
         * 一般情況下兩次踢之間的間隔。
         * 同樣 08-26 調高：真的死掉的話 15 分鐘踢一次夠了，
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
         * ⚠ on-demand 會霸佔感測器（08-12 血氧那次把連續型全餓死），
         * 所以寧可少要幾次。
         */
        const val TEMP_ASK_COOLDOWN_MS = 5 * 60_000L

        /** 裝置明確說沒戴時的間隔 —— 多半真的沒戴，不用一直踢。 */
        const val KICK_COOLDOWN_OFF_MS = 20 * 60_000L

        /**
         * 溫度多久之內算數。
         *
         * ⚠ 2026-08-26 從 10 分鐘放寬到 45 分鐘。原本那句「10 分鐘是實測的正常間隔上限」
         * **又是我沒量就寫的**。真的量了之後：戴著時的批次空檔中位數 8~52 分鐘、
         * 90 百分位約一小時。10 分鐘的窗只涵蓋得到少數情況。
         *
         * 45 分鐘會用到比較舊的溫度，但它現在只當**否決票** ——
         * 而「45 分鐘前明顯不在手上」本身就是很強的證據，不會因為舊而變假。
         */
        const val TEMP_FRESH_MS = 45 * 60_000L

        /**
         * 溫差門檻。**主訊號是溫差，不是絕對皮膚溫。**
         *
         * 2026-08-25 校正：絕對皮膚溫整天跟著環境跑（27.67~34.73，跨 7 度），
         * 冷氣餐廳裡戴著錶只有 32.01 —— 用絕對值當門檻會把她判成沒戴。
         * 溫差不跟著跑，因為環境同時影響皮膚感測器跟錶身，會互相抵銷。
         */
        const val DELTA_WORN = 0.15

        /**
         * 溫差低到這個程度就是**明確不在手上** —— 這是否決票的門檻。
         *
         * ## ⚠ 2026-08-26 從 -0.3 放寬到 -1.5（使用者 一句話問出來的）
         *
         * 她問「為什麼量不到心跳，我明明就戴上了」。查那 15 分鐘查到這個：
         * ```
         *   09:39:56  剛從充電座拿起來戴上
         *             錶身 34.92°C   ← 充電充熱的
         *             皮膚 28.97°C   ← 手腕還沒把它暖起來
         *             溫差 -5.95     ← 舊規則會說「沒戴」，而她明明戴著
         * ```
         * **「剛充完電就戴上」正是每天最常做的動作**，而溫度規則在那個時刻是反的。
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
         * ⚠ 另外注意「確定沒戴」的中位數是 **+0.17** ——
         *   **溫度本來就只在尾巴那一段有鑑別力**，它是否決票不是必要票，
         *   這組數字就是原因。
         */
        const val DELTA_OFF = -1.5

        /**
         * 離開充電座之後多久之內，**不採信溫度的否決票**。
         *
         * 充電會把錶身加熱到 34~35°C，而手腕要十幾分鐘才把皮膚感測器暖回來。
         * 這段期間溫差是**強烈負值卻戴著**（實測 -5.95），
         * 拿它去否決等於在她最常戴錶的時刻把系統弄瞎。
         *
         * 實測 09:39 下充電座 → 09:55 溫差才轉正，取 15 分鐘。
         */
        const val AFTER_CHARGE_BLIND_MS = 15 * 60_000L
        private const val CHANNEL = "collector"
        private const val NOTIF_ID = 1

        fun start(ctx: Context) {
            ctx.startForegroundService(Intent(ctx, CollectorService::class.java))
        }
    }
}
