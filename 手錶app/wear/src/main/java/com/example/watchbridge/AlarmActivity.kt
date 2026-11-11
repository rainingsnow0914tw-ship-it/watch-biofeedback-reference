package com.example.watchbridge

import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView

/**
 * 警報畫面 —— 紅底黃字的 HELP。
 *
 *
 *
 * 所以才需要有小字跑馬燈提醒，扶起他的人要敲三下螢幕停止警報，
 *
 * 所以這個畫面的讀者不是配戴者，**是一個從來沒看過這支錶的陌生人** ——
 * 路人、看護、救護員、急診護理師。
 *
 * 由此推出三條設計規則：
 *   一、**指示要看得懂，不能只有圖示。** 陌生人沒有任何背景知識。
 *   二、**要跑馬燈跑完整句。** 圓形錶面塞不下一整句，截斷的句子等於沒寫。
 *   三、**停止方法要寫在畫面上，不能寫在說明書裡。** 現場沒有人會去查說明書。
 *
 *
 *
 * 一個沒人知道怎麼關的警報，會一路響進救護車、響進急診室。
 * 那不只是當下尷尬 —— **那會讓長輩下一次不肯再戴這支錶。**
 * 一個被摘下來放在抽屜裡的警報器，救不了任何人。
 *
 *
 * ## 但停掉聲音 ≠ 這件事結束了
 *
 * 不管現場的人覺得嚴不嚴重。現場的人不知道這個人三天前血氧就在掉。
 */
class AlarmActivity : Activity() {

    private var taps = 0
    private var firstTapAt = 0L
    private lateinit var hint: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 螢幕是黑的、錶可能還鎖著 —— 這四個一起下才會亮起來並蓋過鎖定畫面。
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        window.addFlags(
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD
        )

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(RED)
            // 圓形錶面：四邊要留白，不然字會被切掉。
            setPadding(28, 28, 28, 28)
        }

        // ── HELP ────────────────────────────────────────────────
        // 用英文而不是中文：救護員、外籍看護、任何人都認得 HELP。
        // 這是全球通用的求救字，不需要翻譯。
        val help = TextView(this).apply {
            text = "HELP"
            setTextColor(YELLOW)
            setTypeface(Typeface.DEFAULT_BOLD)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 56f)
            gravity = Gravity.CENTER
        }
        root.addView(help)

        // ── 中文副標 ─────────────────────────────────────────────
        root.addView(TextView(this).apply {
            text = "請幫我"
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
            gravity = Gravity.CENTER
            setPadding(0, 4, 0, 10)
        })

        // ── 跑馬燈：怎麼關 ───────────────────────────────────────
        // ⚠ 這一行是整個畫面**最重要**的部分，不是裝飾。
        //   沒有它，扶起人的那個陌生人只能眼睜睜看著錶一直叫。
        //
        // marquee 要四個條件同時成立才會跑（少一個就變成靜止的截斷文字）：
        //   isSingleLine / ellipsize=MARQUEE / marqueeRepeatLimit=-1 / isSelected=true
        hint = TextView(this).apply {
            text = "　　敲螢幕三下　停止警報　　敲螢幕三下　停止警報　"
            setTextColor(YELLOW)
            setTypeface(Typeface.DEFAULT_BOLD)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 17f)
            isSingleLine = true
            ellipsize = android.text.TextUtils.TruncateAt.MARQUEE
            marqueeRepeatLimit = -1
            isSelected = true          // ← 沒有這行 marquee 不會動
            isFocusable = true
            isFocusableInTouchMode = true
        }
        root.addView(hint, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        setContentView(root)
        hint.requestFocus()

        // HELP 閃爍。用縮放不用透明度 —— 變淡的那半個週期會**降低可讀性**，
        // 而這個字在最壞的情況下是隔著一段距離被人瞄到的。
        // 縮放全程維持滿亮度，一樣抓得住眼睛。
        ObjectAnimator.ofFloat(help, "scaleX", 1f, 1.18f).apply {
            duration = 420; repeatCount = ValueAnimator.INFINITE
            repeatMode = ValueAnimator.REVERSE; start()
        }
        ObjectAnimator.ofFloat(help, "scaleY", 1f, 1.18f).apply {
            duration = 420; repeatCount = ValueAnimator.INFINITE
            repeatMode = ValueAnimator.REVERSE; start()
        }

        Log.i(TAG, "警報畫面開啟")
        Siren.start(this, maxSeconds = SIREN_SECONDS)
        Buzzer.buzz(this, "gentle")
    }

    /**
     * 敲三下停止。
     *
     * 而誤觸的代價是警報在真的需要的時候已經被關掉了。
     *
     * 而且任何人第一次看到就會做。
     *
     * 三下要在 2.5 秒內完成 —— 夠慢，慌張的人做得到；夠快，隨機的碰撞湊不齊。
     */
    override fun onTouchEvent(e: MotionEvent): Boolean {
        if (e.action != MotionEvent.ACTION_DOWN) return super.onTouchEvent(e)
        val now = System.currentTimeMillis()
        if (now - firstTapAt > TAP_WINDOW_MS) { taps = 0; firstTapAt = now }
        taps++
        Log.i(TAG, "警報畫面被敲 第 $taps 下")

        if (taps >= 3) {
            dismiss()
        } else {
            // 給回饋，不然敲的人不知道有沒有算到。
            hint.text = "　　再敲 ${3 - taps} 下　　"
            Buzzer.buzz(this, "tick")
        }
        return true
    }

    private fun dismiss() {
        Siren.stop()
        Log.i(TAG, "旁人敲三下 → 停止警報")

        // ⚠ 停聲音**不代表**這件事結束了。通知照樣送，而且一定送。
        //
        //   走既有的 mark 管線（已驗證會跟著心率一起傳到手機），
        //   **不另外開一條路** —— 少一條路就少一個會壞的地方。
        sendBroadcast(
            Intent(MarkActivity.ACTION).setPackage(packageName)
                .putExtra("tag", "alarm_dismissed")
                .putExtra("label", "旁人敲三下停止警報")
        )

        setContentView(TextView(this).apply {
            text = "已停止\n已通知家人"
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.BLACK)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 22f)
            gravity = Gravity.CENTER
        })
        window.decorView.postDelayed({ finish() }, 2500)
    }

    override fun onDestroy() {
        super.onDestroy()
        Siren.stop()   // 畫面沒了聲音也要停，不要留一個關不掉的東西在背景
        clear(this)    // 把那則全螢幕通知也收掉，不然它會一直掛在錶面上
    }

    companion object {
        const val TAG = "WatchBridge"
        const val ACTION = "com.example.watchbridge.ALARM_UI"

        private val RED = Color.parseColor("#C1121F")
        private val YELLOW = Color.parseColor("#FFE100")

        private const val TAP_WINDOW_MS = 2500L

        /**
         * 一次警報最多響多久。
         *
         */
        private const val SIREN_SECONDS = 90

        private const val CHANNEL = "alarm"
        private const val NOTIF_ID = 911

        /**
         * 把警報畫面叫出來。
         *
         *
         * 原本這裡是直接 `ctx.startActivity(...)`。廣播收到了、日誌也寫了
         *
         * ```
         * E/ActivityTaskManager: Background activity launch blocked!
         *     callingUidProcState: FOREGROUND_SERVICE
         *     result code=102 (BAL_BLOCK)
         * E/ActivityTaskManager: Abort background activity starts from 10170
         * ```
         *
         * **Android 14 起禁止背景啟動 Activity —— 前景服務也不行。**
         *
         *
         * 而且 app 這一端**完全看不出異常**：沒有例外、沒有錯誤回傳，
         *
         * ## 正解：全螢幕通知（系統留給鬧鐘與來電的合法管道）
         *
         * `setFullScreenIntent` ＋ `CATEGORY_ALARM` ＋ IMPORTANCE_HIGH 的頻道。
         * manifest 裡的 `USE_FULL_SCREEN_INTENT` 就是為這個宣告的。
         *
         * 保留通知本身也有好處：**萬一全螢幕那條被系統擋掉**（有些機型／設定會），
         * 至少還會留下一則高優先級通知，敲它一樣進得來 —— 不會什麼都沒有。
         */
        fun launch(ctx: android.content.Context) {
            val nm = ctx.getSystemService(android.app.NotificationManager::class.java)
            nm.createNotificationChannel(
                android.app.NotificationChannel(
                    CHANNEL, "緊急警報",
                    android.app.NotificationManager.IMPORTANCE_HIGH,
                ).apply {
                    description = "跌倒或生理異常時的求救畫面"
                    setBypassDnd(true)          // 勿擾模式擋不住求救
                    lockscreenVisibility = android.app.Notification.VISIBILITY_PUBLIC
                }
            )

            val pi = android.app.PendingIntent.getActivity(
                ctx, 0,
                Intent(ctx, AlarmActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
                android.app.PendingIntent.FLAG_UPDATE_CURRENT or
                    android.app.PendingIntent.FLAG_IMMUTABLE,
            )

            val n = android.app.Notification.Builder(ctx, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_warning)
                .setContentTitle("HELP　請幫我")
                .setContentText("敲螢幕三下停止警報")
                .setCategory(android.app.Notification.CATEGORY_ALARM)
                .setPriority(android.app.Notification.PRIORITY_MAX)
                .setOngoing(true)
                .setAutoCancel(false)
                .setFullScreenIntent(pi, true)   // ← true = 就算螢幕亮著也要接管
                .build()

            nm.notify(NOTIF_ID, n)
            Log.i(TAG, "警報：已送出全螢幕通知")
        }

        /** 警報結束後把那則通知收掉，不然它會一直掛在錶上。 */
        fun clear(ctx: android.content.Context) {
            runCatching {
                ctx.getSystemService(android.app.NotificationManager::class.java)
                    .cancel(NOTIF_ID)
            }
        }
    }
}
