package com.example.watchbridge

import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.os.PowerManager
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView

/**
 * 警報畫面 —— **用系統覆蓋視窗畫，不開 Activity**。
 *
 * ## 為什麼非得這樣做（2026-08-17 05:19–05:22 三次實測）
 *
 * 這支錶（Watch5 Pro / Android 14）**完全禁止背景啟動 Activity**：
 *
 * | 做法 | 結果 |
 * |---|---|
 * | 前景服務直接 `startActivity` | `BAL_BLOCK` |
 * | 全螢幕通知讓系統代發（Android 官方指定給鬧鐘／來電的正規做法） | **一樣 `BAL_BLOCK`** |
 *
 * 第二次的日誌特別值得留著，因為它看起來明明應該過：
 * ```
 * balAllowedByPiCreator: BSP.ALLOW_BAL
 * balAllowedByPiSender:  BSP.ALLOW_BAL
 * resultIfPiSenderAllowsBal: BAL_BLOCK      ← 兩邊都說可以，結果還是擋
 * realCallingPackage: com.samsung.android.wearable.sysui
 * ```
 * **連 Samsung 自己的系統介面代發都被否決。**
 *
 * 而 `USE_FULL_SCREEN_INTENT` 的 appop 查出來是
 * `allow; rejectTime=+38s ago` —— **它確實被拒絕過**，補授之後仍然擋。
 *
 * ### 這件事的嚴重性
 *
 * 沒有這次演練的話，真的出事時會是：**聲音響、震動震、畫面永遠不出現**。
 * 而畫面正是「扶起他的人怎麼把警報關掉」的唯一說明 ——
 * 也就是 使用者 最擔心的「一路響進急診室，長者醒來社死」。
 *
 * 而且 app 這一端**完全看不出異常**：沒有例外、沒有錯誤回傳，
 * 自己的日誌還寫著「已送出全螢幕通知」。
 * 要去撈系統的 `ActivityTaskManager` 日誌才看得到 `BAL_BLOCK`。
 * **又一個「綠燈但不能用」。**
 *
 * ### 代價，要寫清楚
 *
 * 覆蓋視窗需要 `SYSTEM_ALERT_WINDOW`，而它**要手動授予**：
 * ```
 * adb shell appops set com.example.watchbridge SYSTEM_ALERT_WINDOW allow
 * ```
 * 恢復原廠或重新配對之後要重下一次。
 * 裝到家人的錶上時這是設定步驟之一 ——
 * 反正三星感測器 SDK 本來就需要開發者模式，這一步併在一起做。
 */
object AlarmOverlay {

    const val TAG = "WatchBridge"

    private var view: View? = null
    private var wake: PowerManager.WakeLock? = null
    private var taps = 0
    private var firstTapAt = 0L
    /** 已經被停止過了嗎。防止第四下之後重複觸發、重複記錄。 */
    @Volatile private var dismissed = false
    private var hint: TextView? = null

    fun isShowing(): Boolean = view != null

    fun show(ctx: Context) {
        if (view != null) return    // 已經在了，不要疊第二層

        val wm = ctx.getSystemService(WindowManager::class.java)
        val t0 = System.currentTimeMillis()

        // ⚠ 2026-08-17 05:35 使用者 實測抓到的問題，順序**不可以再調回去**：
        //
        //   她的原話：「這一次是先出現螢幕是黑的，過了幾秒才開始出現我們的紅黃警告屏。
        //   然後在黑色屏幕的時候敲它，它不會停止響。但是警告屏出現的時候敲它三下就會停止。」
        //
        //   原本的順序是：點亮螢幕 → 貼畫面 → 開聲音。日誌顯示 addView 花了 2.5 秒：
        //       05:34:29.518  警報演練：開畫面
        //       05:34:31.981  警報畫面已貼上      ← 隔了 2.5 秒
        //   那 2.5 秒裡螢幕已經被喚醒鎖點亮（黑畫面／錶面），
        //   **而停止按鈕還不存在** —— 觸控落在底下的錶面上，什麼都沒發生。
        //
        //   **原則：絕對不要發出一個當下關不掉的聲音。**
        //   對急著要關掉它的人來說，「敲了沒反應」跟「這東西關不掉」是同一件事，
        //   而他只會試幾下就放棄。那正好是最不該發生的事 ——
        //   使用者 要的就是「扶起他的人關得掉」。
        //
        //   現在的順序：**先貼畫面 → 點亮螢幕 → 等畫面真的畫好了 → 才開聲音。**

        // ⚠ 0.33 試過「喚醒鎖放最前面讓它跟貼畫面並行」，**實測更差，已撤回**：
        //       0.32（先貼畫面）  已貼上 378ms  → 就緒 3031ms
        //       0.33（先點螢幕）  已貼上 2596ms → 就緒 4015ms
        //   等待沒有被並行掉，只是從外面搬進了 addView 裡面，總時間反而多一秒。
        //   **不要再試一次。**
        val root = build(ctx)

        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            // NOT_FOCUSABLE 不能加 —— 加了就收不到觸控，敲三下停不掉。
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD,
            PixelFormat.OPAQUE,
        )

        runCatching {
            wm.addView(root, lp)
            view = root
            Log.i(TAG, "警報畫面已貼上（覆蓋視窗，耗時 ${System.currentTimeMillis() - t0}ms）")

            // ⚠ 2026-08-17 06:08：**震動立刻開始，不等螢幕；只有聲音要等。**
            //
            //   實測螢幕從全黑到亮：3276ms、5692ms —— 差異很大而且會逼近上限。
            //   單純放寬上限的話，最壞情況會有十秒鐘完全沒有任何反應。
            //
            //   但「等螢幕」這條規矩的理由只適用於**聲音**：
            //   聲音是吵給旁邊的人聽的，而旁邊的人需要看得到「怎麼關」，
            //   否則就是 使用者 說的「一路響進急診室」。
            //   **震動只有戴著的人感覺得到** —— 它不會造成「關不掉的噪音」問題，
            //   而且它是最快能通知本人的通道。
            //
            //   所以拆開：震動 0 秒開始，聲音等螢幕亮。
            //   最壞情況變成「手腕先震、幾秒後才開始叫」，這完全可以接受。
            Buzzer.buzz(ctx, "chloe")

            // 畫面在了，現在點亮螢幕。這個鎖有時限，時間到自己放掉，不會漏電。
            runCatching {
                val pm = ctx.getSystemService(PowerManager::class.java)
                @Suppress("DEPRECATION")
                wake = pm.newWakeLock(
                    PowerManager.SCREEN_BRIGHT_WAKE_LOCK or
                        PowerManager.ACQUIRE_CAUSES_WAKEUP or
                        PowerManager.ON_AFTER_RELEASE,
                    "watchbridge:alarm",
                ).apply { acquire(2 * 60 * 1000L) }
            }.onFailure { Log.w(TAG, "點不亮螢幕：${it.javaClass.simpleName}") }

            // ⚠ 2026-08-17 05:55 使用者 第二次回饋，**推翻了我上一版的修法**：
            //   「第一次有點問題耶，前面黑屏很長一段時間一直叫，叫完才出現有字的屏幕。
            //     第二次正常。」
            //
            //   第二、三次「正常」是因為**螢幕本來就亮著**。
            //   出問題的只有「螢幕全黑時的第一次」—— 而那正是真實情境：
            //   **半夜倒下的時候，錶一定是黑的。**
            //
            //   我上一版用 `root.post{}` 當閘門，以為那代表「畫出來了」。**不是。**
            //   post 只保證程式這邊量測、排版、繪製完成，
            //   **螢幕的實體背光是另一套非同步流程，還沒亮。**
            //   於是聲音照樣在黑屏時開始 —— 修了個寂寞。
            //
            //   正確的閘門是**顯示器的電源狀態真的變成 STATE_ON**。
            //   那是唯一能代表「人的眼睛看得到東西」的訊號。
            //
            //   上限六秒：萬一某些機型永遠不回報 ON，也要出聲 ——
            //   **有聲音但沒畫面，still 比完全安靜好。**但要記進日誌，
            //   不然這種降級會變成沒人知道的預設行為。
            val dm = ctx.getSystemService(android.hardware.display.DisplayManager::class.java)
            val gate = object : Runnable {
                override fun run() {
                    val lit = runCatching {
                        dm.getDisplay(android.view.Display.DEFAULT_DISPLAY)?.state ==
                            android.view.Display.STATE_ON
                    }.getOrDefault(true)
                    val drawn = root.isShown && root.width > 0
                    val waited = System.currentTimeMillis() - t0
                    when {
                        lit && drawn -> {
                            Log.i(TAG, "螢幕已亮、畫面已顯示（${waited}ms），開始鳴叫")
                            Siren.start(ctx, maxSeconds = SIREN_SECONDS)
                        }
                        waited > SCREEN_WAIT_MAX_MS -> {
                            Log.w(TAG, "⚠ 等了 ${waited}ms 螢幕還沒亮（lit=$lit drawn=$drawn），先出聲")
                            Siren.start(ctx, maxSeconds = SIREN_SECONDS)
                        }
                        else -> root.postDelayed(this, 80)
                    }
                }
            }
            root.post(gate)
            // 貼上去了就把備援通知收掉。
            // ⚠ 2026-08-17 使用者 的實測回饋：**「敲三下停聲音，再敲三下停畫面？」**
            //   她敲了不只一組。原因是前面兩次 BAL_BLOCK 的嘗試各留下一則
            //   `setOngoing(true)` 的常駐通知 —— 畫面沒起來，
            //   所以負責清掉它的 onDestroy **永遠沒被執行到**，通知就孤兒在錶上。
            //   累積下來變成好幾個要各自關掉的東西。
            //   **一個警報應該只有一個「關掉」的動作。** 多一個都是負擔，
            //   而現場那個人正在慌。
            AlarmActivity.clear(ctx)
        }.onFailure {
            // 最可能的原因是 SYSTEM_ALERT_WINDOW 沒授予。
            // **一定要記下來** —— 這是整個警報唯一會安靜失敗的地方。
            Log.e(TAG, "⚠ 警報畫面貼不上（多半是沒有覆蓋視窗權限）：${it.javaClass.simpleName}", it)
            release()
            // 只有在覆蓋視窗失敗時才發通知當備援。
            // **成功時不要發** —— 那會變成第二個要關的東西。
            Log.i(TAG, "改用通知當備援")
            AlarmActivity.launch(ctx)
            // 畫面貼不上的時候還是要叫 —— 有聲音總比什麼都沒有好。
            // 這種情況下沒有停止鍵是已知的缺點，所以時間縮短一半，
            // 免得變成一個真的關不掉的東西。
            Siren.start(ctx, maxSeconds = SIREN_SECONDS / 2)
            Buzzer.buzz(ctx, "chloe")
        }
    }

    fun hide(ctx: Context) {
        Siren.stop()
        // ⚠ clear() 放在 view 檢查**之前**：就算畫面不在，
        //   也要保證把可能殘留的通知收掉。
        //   之前正是因為清理跟畫面綁在一起，畫面沒起來 → 通知變孤兒。
        AlarmActivity.clear(ctx)
        val v = view ?: return
        view = null
        runCatching { ctx.getSystemService(WindowManager::class.java).removeView(v) }
        release()
        Log.i(TAG, "警報畫面已移除")
    }

    private fun release() {
        runCatching { wake?.takeIf { it.isHeld }?.release() }
        wake = null
    }

    /** 版面。跟 AlarmActivity 的設計理由相同，寫在那支的檔頭。 */
    private fun build(ctx: Context): View {
        taps = 0; firstTapAt = 0L; dismissed = false

        val root = object : LinearLayout(ctx) {
            override fun onTouchEvent(e: MotionEvent): Boolean {
                if (e.action == MotionEvent.ACTION_DOWN) onTap(ctx)
                return true
            }
        }.apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(RED)
            setPadding(28, 28, 28, 28)   // 圓形錶面要留白，不然字被切掉
            isClickable = true
        }

        // HELP 用英文：救護員、外籍看護、任何人都認得，不需要翻譯。
        val help = TextView(ctx).apply {
            text = "HELP"
            setTextColor(YELLOW)
            setTypeface(Typeface.DEFAULT_BOLD)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 54f)
            gravity = Gravity.CENTER
        }
        root.addView(help)

        root.addView(TextView(ctx).apply {
            text = "請幫我"
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 19f)
            gravity = Gravity.CENTER
            setPadding(0, 2, 0, 10)
        })

        // ⚠ 這一行是整個畫面**最重要**的部分，不是裝飾。
        //   沒有它，扶起人的那個陌生人只能眼睜睜看著錶一直叫。
        //   覆蓋視窗拿不到焦點，marquee 跑不動 —— 所以改成兩行靜態大字，
        //   反而比跑馬燈好：**慌張的人不會等一句話跑完。**
        hint = TextView(ctx).apply {
            text = "敲螢幕三下\n停止警報"
            setTextColor(YELLOW)
            setTypeface(Typeface.DEFAULT_BOLD)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
            gravity = Gravity.CENTER
        }
        root.addView(hint)

        // 閃爍用縮放不用透明度 —— 變淡的那半個週期會降低可讀性，
        // 而這個字在最壞的情況下是隔著一段距離被人瞄到的。
        for (axis in listOf("scaleX", "scaleY")) {
            ObjectAnimator.ofFloat(help, axis, 1f, 1.18f).apply {
                duration = 420
                repeatCount = ValueAnimator.INFINITE
                repeatMode = ValueAnimator.REVERSE
                start()
            }
        }
        return root
    }

    /**
     * 敲三下停止。
     *
     * 為什麼是三下而不是一下：**一下太容易誤觸**（袖子、水、抱一下都會碰到），
     * 而誤觸的代價是警報在真正需要的時候已經被關掉了。
     *
     * 為什麼不是長按：長按要先知道「要按住」才做得到，
     * 而「按住」這個動作很難用一句話在小螢幕上講清楚。
     * 「敲螢幕三下」講得清楚，任何人第一次看到就會做。
     */
    private fun onTap(ctx: Context) {
        // ⚠ 2026-08-17 06:04 實測抓到的：**第四下會再觸發一次，寫進第二筆紀錄。**
        //   ```
        //   06:04:21.812  旁人敲三下 → 停止警報
        //   06:04:22.339  記一筆
        //   06:04:23.103  警報畫面被敲 第 4 下
        //   06:04:23.103  旁人敲三下 → 停止警報     ← 又來一次
        //   06:04:23.614  記一筆                     ← 重複的紀錄
        //   ```
        //   `taps` 到 3 之後沒有歸零也沒有上鎖，第四下一樣滿足 `>= 3`。
        //
        //   為什麼這不是小事：**現場扶起人的那個人一定會多敲幾下**（他在慌，
        //   而且畫面還會停留兩秒才消失）。結果是 使用者 收到重複通知，
        //   看起來像發生了兩次求救 —— 而在警報這件事上，
        //   **「發生兩次」跟「發生一次」是完全不同的判斷。**
        if (dismissed) return
        val now = System.currentTimeMillis()
        if (now - firstTapAt > TAP_WINDOW_MS) { taps = 0; firstTapAt = now }
        taps++
        Log.i(TAG, "警報畫面被敲 第 $taps 下")

        if (taps >= 3) {
            dismissed = true          // ← 上鎖，之後再敲都不算
            Log.i(TAG, "旁人敲三下 → 停止警報")

            // ⚠ 停聲音**不代表**這件事結束了。通知照樣送，而且一定送。
            //   現場的人只看得到「這個人被扶起來了」，
            //   看不到「他三天前血氧就在往下掉」。判斷不該由現場的人做。
            //   走既有的 mark 管線（已驗證會跟著心率一起傳到手機），
            //   **不另外開一條路** —— 少一條路就少一個會壞的地方。
            ctx.sendBroadcast(
                Intent(MarkActivity.ACTION).setPackage(ctx.packageName)
                    .putExtra("tag", "alarm_dismissed")
                    .putExtra("label", "旁人敲三下停止警報")
            )

            Siren.stop()
            // 讓停的人看到「有效」，不然他會懷疑沒敲成功而繼續敲。
            hint?.text = "已停止\n已通知家人"
            view?.postDelayed({ hide(ctx) }, 2200)
        } else {
            hint?.text = "再敲 ${3 - taps} 下"
            Buzzer.buzz(ctx, "tick")
        }
    }

    private val RED = Color.parseColor("#C1121F")
    private val YELLOW = Color.parseColor("#FFE100")
    private const val TAP_WINDOW_MS = 2500L

    /**
     * 最多等螢幕亮多久才放棄、直接出聲。
     *
     * 六秒是「寧可畫面沒到也要叫」跟「不要在黑屏時亂叫」之間的取捨。
     * 實測從全黑到亮 3276ms / 5692ms，變異很大，所以留到十秒。
     * 拉長不會讓人等更久 —— **震動在第 0 秒就開始了**，等的只有聲音。
     */
    private const val SCREEN_WAIT_MAX_MS = 10_000L

    /**
     * 一次警報最多響多久。
     *
     * 使用者：「總不能一直警報到手機跟手錶沒電為止吧，那家屬打電話來反而沒電了。」
     * **留電是為了打電話。** 時間到不是「算了」，是上層要換一條路
     * （手機也響 → 打給 使用者）。那一層還沒做。
     */
    private const val SIREN_SECONDS = 90
}
