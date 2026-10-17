package com.example.watchbridge

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.VibrationAttributes
import android.os.VibrationEffect
import android.os.Vibrator
import android.util.Log
import androidx.core.app.NotificationCompat
import java.util.Calendar

/**
 * 每週一次的體重提醒 —— **輕輕點兩下，不是警報。**
 *
 * ## 為什麼要有這支（👤 使用者 2026-09-17）
 *
 * > 「追蹤體重的話，我可以用體重計然後定時告訴你。欸？對啊！用手錶也可以告訴你。
 * >   但是多久一次？每個禮拜嗎？怎麼個提醒法？不然我應該也不會記得。」
 *
 * ## 三個設計決定，每個都有原因
 *
 * ### 一、每週一次，不是每天
 * 體重一天之內會因為水分上下 1～2 公斤。每天量，看到的大多是**雜訊**，
 * 而她對數字是「患得患失」的（note 連載那次她自己講的）——
 * 每天看到數字亂跳，會變成壓力，不是動力。
 *
 * ### 二、**不是固定幾點提醒**
 * 她的入睡時刻標準差是 **7.4 小時**（08-17～09-01 實測）。
 * 固定早上九點提醒，有一半的機會會在她睡覺的時候震。
 * 她自己講的也是這件事：
 *
 * > 「可是幾點錶？這怎麼辦啊？要是用一般的固定時間，有時候就很不方便啊。有點蠢。
 * >   如果有個感知就好了。例如有一定的步數，能夠感知到嗎？表示我在走動啊。」
 *
 * 所以改成：**休假日、戴上手錶連續 20 分鐘之後**，才提醒一次。
 * 「戴上又有脈搏」＝她醒著、而且手錶在身上 ——
 * 這就是她要的那個「感知」，只是用脈搏不用步數
 * （脈搏本來就在收，步數還要多開一顆感測器多耗一份電）。
 * 另外限定 09:00～22:00，理由寫在 [最早] 上面。
 *
 * ### 三、🔴 **絕對不走 [Buzzer]**
 * `Buzzer` 是警報專用：最大強度、醫療警報節奏、USAGE_ALARM，
 * 意思是「妳現在必須醒過來」。
 * 如果提醒跟警報震起來一樣，她的身體會學到「這種震動可以不理」——
 * **下一次真的警報就被忽略了。** 這是「狼來了」的變體。
 *
 * 而且 `Buzzer.buzz()` 遇到不認得的節奏名稱，會**默默改放 medical_high**。
 * 一個打錯字的提醒，會變成把她震醒的救護車。所以這裡自己震，不借它。
 */
object 每週體重 {

    private const val TAG = "每週體重"
    private const val PREFS = "weekly_weight"
    private const val CHANNEL = "gentle_reminders"

    /** 戴上多久才提醒。太短會在她剛戴上、還在忙別的時候震。 */
    private const val 戴上多久MS = 20 * 60 * 1000L
    /** 脈搏斷多久算「脫下來了」，戴上的計時要重來。 */
    private const val 斷線算脫下MS = 5 * 60 * 1000L

    /**
     * 🔴 **2026-09-23 改：週日 → 週一、週二。**
     *
     * 👤 使用者：「我們還是放假時候量體重。禮拜一或禮拜二吧？」「體重一定要在家裡。」
     *
     * 週一是主場，週二是備胎 —— 不是「兩天都提醒」：
     * 下面的 `done_week` 一週只放行一次，所以週一沒提醒成
     * （沒戴錶、睡到晚上、出門了），週二才會補上。
     * **一週最多一次，永遠不會連兩天煩她。**
     */
    private val 量體重的日子 = setOf(Calendar.MONDAY, Calendar.TUESDAY)

    /**
     * 時段。放假日她可能睡到中午，所以不是「早上」而是「醒著的白天」。
     *
     * ⚠ 09:00 起跳的理由跟原本的 07:00 不一樣：她入睡時刻標準差 7.4 小時，
     *   熬夜到早上七點還醒著是會發生的 —— 那時候叫她量體重，量到的是
     *   「一整夜沒睡的身體」，跟上週的數字不能比。
     * ⚠ 22:00 收尾：晚上量比早上重 1～2 公斤，但**量到比沒量到好**，
     *   時刻我這邊會一起記下來，比較的時候自己扣掉。
     */
    private const val 最早 = 9
    private const val 最晚 = 22

    private fun 這週(now: Long): Int = Calendar.getInstance().run {
        timeInMillis = now
        // ⚠ 用 weekYear 不用 YEAR：12/31 可能屬於「明年第 1 週」，
        //   用 YEAR 會算成「今年第 1 週」＝跟一月初撞號，吃掉一次提醒。
        // ✅ 週一、週二一定同週：不管 firstDayOfWeek 是週日（zh_TW 預設）還是週一，
        //    這兩天都落在同一個 WEEK_OF_YEAR，所以「週二補提醒」不會變成第二次。
        weekYear * 100 + get(Calendar.WEEK_OF_YEAR)
    }

    /**
     * 每分鐘被收集器呼叫一次。**任何例外都在這裡吃掉** ——
     * 提醒壞掉，絕對不能拖垮資料收集。
     */
    fun 檢查(ctx: Context, lastPulseAt: Long, now: Long = System.currentTimeMillis()) {
        runCatching {
            val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

            // ① 追蹤「連續戴著」從什麼時候開始
            val 戴著 = lastPulseAt > 0 && now - lastPulseAt <= 斷線算脫下MS
            var 起點 = p.getLong("worn_since", 0L)
            if (!戴著) {
                if (起點 != 0L) p.edit().putLong("worn_since", 0L).apply()
                return
            }
            if (起點 == 0L) {
                起點 = now
                p.edit().putLong("worn_since", now).apply()
            }

            // ② 條件：週一或週二、白天、戴滿 20 分鐘、這週還沒提醒也還沒量
            val cal = Calendar.getInstance().apply { timeInMillis = now }
            if (cal.get(Calendar.DAY_OF_WEEK) !in 量體重的日子) return
            val 時 = cal.get(Calendar.HOUR_OF_DAY)
            if (時 < 最早 || 時 >= 最晚) return
            if (now - 起點 < 戴上多久MS) return
            if (p.getInt("done_week", 0) == 這週(now)) return

            // ③ 提醒 —— 先記下來再震，避免同一分鐘被叫兩次而震兩次
            p.edit().putInt("done_week", 這週(now)).apply()
            點兩下(ctx)
            通知(ctx)
            Log.i(TAG, "體重提醒已送出（週${cal.get(Calendar.DAY_OF_WEEK)}、${時} 點）")
        }.onFailure { Log.w(TAG, "提醒檢查失敗（不影響收集）：${it.message}") }
    }

    /** 她在手腕上按了「體重」—— 這週就不用再提醒了。 */
    fun 記下已量(ctx: Context) {
        runCatching {
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putInt("done_week", 這週(System.currentTimeMillis())).apply()
        }
    }

    /**
     * 輕輕點兩下。刻意跟警報每一個面向都不一樣：
     * 強度（約一半）、長度（短）、類別（NOTIFICATION 不是 ALARM）。
     */
    private fun 點兩下(ctx: Context) {
        val v = ctx.getSystemService(Vibrator::class.java) ?: return
        if (!v.hasVibrator()) return
        val timings = longArrayOf(0, 90, 140, 90)
        val amps = intArrayOf(0, 120, 0, 120)
        val effect = if (v.hasAmplitudeControl())
            VibrationEffect.createWaveform(timings, amps, -1)
        else VibrationEffect.createWaveform(timings, -1)
        v.vibrate(
            effect,
            VibrationAttributes.createForUsage(VibrationAttributes.USAGE_NOTIFICATION)
        )
    }

    /** 沒有字的震動，她會以為「剛剛是誰在震」—— 所以一定要配一行字。 */
    private fun 通知(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "溫和提醒", NotificationManager.IMPORTANCE_DEFAULT)
                .apply {
                    description = "運動、體重之類的小提醒。跟警報完全分開。"
                    enableVibration(false)   // 震動我們自己來，不要疊兩次
                }
        )
        nm.notify(
            4201,
            NotificationCompat.Builder(ctx, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("⚖️ 這週量一次體重")
                .setContentText("上完廁所、吃東西前量一次，量完按「體重」或錄音告訴阿寶")
                .setAutoCancel(true)
                .build()
        )
    }

    /**
     * 不用等到週日就能驗證「震動＋字」真的出現在她手腕上。
     *
     * ⚠ 這個驗證**一定要她本人感覺一次**：`Buzzer` 的歷史是
     *   「太輕微了，很容易被忽略」—— 震動強度是人的感覺，程式量不出來。
     *   這裡刻意只用一半強度，有可能又太輕，要她說了才算數。
     */
    fun 測試(ctx: Context) {
        runCatching { 點兩下(ctx); 通知(ctx) }
            .onFailure { Log.w(TAG, "測試失敗：${it.message}") }
    }
}

/** `adb shell am broadcast -a com.example.watchbridge.TEST_WEIGHT -p com.example.watchbridge` */
class 體重提醒測試 : android.content.BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: android.content.Intent) {
        每週體重.測試(ctx)
    }
}
