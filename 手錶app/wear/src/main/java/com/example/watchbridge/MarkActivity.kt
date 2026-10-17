package com.example.watchbridge

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * 手腕上的「標記這一刻」。
 *
 * ## 為什麼入口一定要在手錶上（2026-08-16）
 *
 * 使用者 說「手錶上面很難看東西，最好能打到手機的 APP 裡面讓我核實」——
 * **看**確實是手機好，但**記**剛好相反。
 *
 * 她覺得暈的那一刻，如果要先找到手機、解鎖、開 app、打字，
 * **那一筆就不會被記下來**。而那一筆正是最有價值的一筆 ——
 * 因為它對應著資料裡一個很銳利的指紋，可以拿來驗證判讀對不對。
 *
 * 一按就記，兩秒鐘結束。**能被記下來的紀錄才有用。**
 *
 * ## 怎麼叫出來
 *
 * 綁在錶冠按兩下（設定 → 進階功能 → 自訂按鍵 → 按兩下）。
 * 長按只能綁 Bixby/Gemini，按兩下可以綁任意 app —— 2026-08-12 查證過。
 *
 * ## 標記怎麼傳到手機
 *
 * **不另外開一條路。** 標記直接寫進當天的資料檔，
 * 跟心率、動作量走同一條藍牙管線到手機、再到電腦。
 * 少一條路就少一個會壞的地方，而且它自動享有既有的補送與斷點續傳。
 *
 * ## 為什麼是預設選項不是打字
 *
 * 手錶上打字很痛苦，而**痛苦的東西不會被用**。
 * 八個按鈕涵蓋大部分要記的事；細節可以事後在手機上補，
 * 但**時間戳必須當下就準**，那是事後補不回來的東西。
 */
class MarkActivity : Activity() {

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)

        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 28, 24, 40)
            addView(TextView(this@MarkActivity).apply {
                text = "記一筆"
                textSize = 15f
                setTextColor(Color.parseColor("#4FC4AC"))
                gravity = Gravity.CENTER
                setPadding(0, 0, 0, 8)
            })
        }
        // 2026-10-17「跟阿寶說」排最上面：它是萬用入口（不舒服／吃藥了／叫司機都能用一句話講），
        // 而且是警報階梯的方向盤 —— 她倒下時一根手指要按得到。八顆標記鈕往下擠一格。
        box.addView(Button(this).apply {
            text = "🎤 跟阿寶說"
            textSize = 14f
            setTextColor(Color.BLACK)
            setBackgroundColor(Color.parseColor("#9B6DD6"))   // 紫色：👤 10-17 金色跟記一筆撞色，她要紫
            setOnClickListener {
                startActivity(Intent(this@MarkActivity, TalkActivity::class.java))
                finishAndRemoveTask()
            }
        }, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { setMargins(0, 6, 0, 0) })
        for ((label, tag) in OPTIONS) {
            box.addView(Button(this).apply {
                text = label
                // 14sp + 較小的間距：實測從三顆變成四顆放得下。
                // 手錶上每多一顆看得到的按鈕，就少一次「算了不記了」。
                textSize = 14f
                setTextColor(Color.BLACK)
                setBackgroundColor(Color.parseColor("#4FC4AC"))
                setOnClickListener { mark(tag, label) }
            }, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { setMargins(0, 6, 0, 0) })
        }
        setContentView(ScrollView(this).apply {
            setBackgroundColor(Color.BLACK); addView(box)
        })
    }

    private fun mark(tag: String, label: String) {
        if (tag == "weight") 每週體重.記下已量(this)
        // 走廣播交給收集服務寫檔 —— 那支才是唯一在寫這個檔的東西。
        // 兩個地方同時 append 同一個檔遲早會交錯出壞行，而且很難重現。
        sendBroadcast(Intent(ACTION).apply {
            setPackage(packageName)
            putExtra("tag", tag)
            putExtra("label", label)
        })
        // 震一下 = 確認收到。手錶螢幕很小，**觸覺回饋比文字可靠** ——
        // 她可能已經把手放下了。
        runCatching {
            getSystemService(Vibrator::class.java)
                .vibrate(VibrationEffect.createOneShot(60, VibrationEffect.DEFAULT_AMPLITUDE))
        }
        // ⚠ 2026-08-20：`finish()` 只關掉「這一個」畫面。
        //   實測 使用者 按完之後**又跳出來一次** —— 因為同一個 task 裡
        //   疊了不只一個實例（dumpsys 看到 Hist #0 跟 #1 兩個）。
        //   `finishAndRemoveTask()` 把整個 task 收掉，不留殘骸給下次疊。
        //   配合 manifest 的 launchMode="singleTask" 兩邊一起修。
        finishAndRemoveTask()
    }

    companion object {
        const val ACTION = "com.example.watchbridge.MARK"

        /**
         * 八個選項。**刻意不多** —— 選項一多就要滑，滑就會懶得記。
         *
         * ## 順序是照「按的時候有多不方便」排的，不是照使用頻率
         *
         * 2026-08-16 實測：圓形錶面一次只放得下**三顆**，其餘要滑。
         * 而原本的順序把「不舒服」排在第四 —— **要滑才按得到**。
         *
         * 那是最糟的設計：不舒服正是妳最沒力氣摸索介面的時候，
         * 而且它是唯一一個**對應「判讀該不該報警」**的標記。
         * 需要它的時候找不到，等於這個功能不存在。
         *
         * 所以：**最急的排最前面，方便的排後面。**
         *   不舒服 → 最重要，而且按的時候狀況最差
         *   起床／睡覺 → 每天都按，但按的時候人是清醒從容的，滑一下無所謂
         *   吃藥／其他 → 補充性質
         *
         * 挑選標準（為什麼是這八個）：這件事在資料上有沒有銳利的指紋可以對照。
         *   起床／睡覺 → 心率與動作量會轉折
         *   運動       → 動作量與心率同時衝高
         *   不舒服     → 對應警報判讀
         *   吃藥       → 有些藥影響心率，之後要能排除掉
         *   生理期     → 同時推動心率／HRV／體溫，**目前最大的已知干擾源**
         *   震了       → 對照系統震動史，抓「她有感覺／系統沒記錄」與反向
         *   其他       → 先記時間，細節事後在手機補
         */
        val OPTIONS = listOf(
            "不舒服" to "unwell",
            // ⚠ 2026-08-19 使用者 提的，**排第二是有理由的**。
            //
            //   她的原話：「只要他一震我就點一下就記錄在手機裡面，
            //   就跟吃飯睡覺起床那個是一起的，這樣比較方便」。
            //
            //   為什麼不是排在後面：**這一顆的全部價值在於「按得夠快」。**
            //   時間戳就是它的內容 —— 隔十分鐘才想起來按，那筆就對不上任何東西了。
            //   而且亂震發生在不可預測的時刻（08-19 那兩次是凌晨 01:12、01:13），
            //   她那時候是半睡的，沒有力氣滑螢幕找按鈕。
            //   所以它跟「不舒服」同一級：**要按的時候，人的狀態最差。**
            //
            //   ## 這顆按鈕在驗證上做什麼
            //
            //   系統自己有震動史（`dumpsys vibrator_manager`，看得到是哪個套件震的），
            //   所以「誰震的」其實查得到 —— 08-19 那兩次查出來是
            //   `com.google.android.apps.wearable.settings`（Wear OS 設定，2.17 秒），
            //   **不是我們**。我們的 app 整份歷史只震過一次，是她 23:27 按「起床」的 87 毫秒。
            //
            //   那為什麼還要這顆按鈕？三個理由：
            //     ① `dumpsys` 只留最近一段，過了就沒了 —— 這顆會寫進永久資料
            //     ② 查 dumpsys 需要 adb，**家人那邊不會有電腦**
            //     ③ 最重要：**她感覺到的震動 ≠ 系統記錄的震動。**
            //        兩份記錄分開存、事後對照，才看得出有沒有「她覺得震了但系統沒記錄」
            //        （錶帶滑動、心跳誤認）或反過來（震了但她沒感覺 —— 那對警報是致命的）。
            "震了" to "buzz",

            // ★ 2026-08-26 新增，**這一顆是九天下來最需要的那一格**。
            //
            //   使用者 自己提的：「以後不管是要把手錶拿去充電還是脫下來，
            //   當手腕休息，或者做任何奇怪的動作，我都先在錄音裡面記一筆」。
            //
            //   為什麼值得佔一個格子（九天資料算的）：
            //   ```
            //     「不是戴著」的記錄共 99,300 筆，其中
            //       充電中             8.0%   ✅ 電路上的物理事實，分得出來
            //       明確離腕           4.0%   ✅ 裝置主動說的
            //       🔴 說沒戴卻有脈搏  76.6%   ← **這格永遠不知道真相**
            //       🟡 說不準          11.3%   ← 這格也不知道
            //   ```
            //   那 76,107 筆是所有佩戴判斷麻煩的來源 ——
            //   08-25/26 整晚在調的門檻（+0.15、否決票、有效期 45 分鐘）
            //   全部都是在猜這一格。**按一下就不用猜了。**
            //
            //   ⚠ 為什麼沒有配一顆「戴上」：脈搏回來就代表戴上了，那個偵測得到。
            //     真正偵測不到的是「脫下來了，但 PPG 還在吐假脈搏」。
            //     有了「脫錶」，**沒有標記的時段就預設是戴著** —— 一顆就夠。
            //
            //   ⚠ 充電不用按。錶接上充電座是電路上的事實，我百分之百分得出來。
            //     這一顆是給「脫下來但沒去充電」用的 —— 手腕休息、洗澡、錶帶調鬆。
            //
            //   排第三：不舒服和震了的位置**刻意不動**（她按最多的是震了，28 次），
            //   動位置會讓肌肉記憶按錯。
            "脫錶" to "off_wrist",
            "起床" to "wake",
            "睡覺" to "sleep",
            "運動" to "exercise",
            "吃藥" to "medicine",
            // ⚠ 2026-08-18 使用者 提的，而且**這一顆比它看起來重要得多**。
            //
            //   她的原話：「我這兩天生理期來了，我們要不要加一個按鈕」——
            //   她當成一個記事，但它其實是這套系統目前**最大的已知干擾源**。
            //
            //   生理週期會同時推動我們三個判斷訊號往同一個方向跑：
            //     · 靜息心率      黃體期／經期會升高（文獻多半落在 2~5 bpm）
            //     · 心率變異度    同期下降
            //     · 皮膚溫度      黃體期偏高，來潮時回落
            //
            //   **這三個正好就是 triage 拿來判斷「該不該擔心」的東西。**
            //   不知道的話會有兩種壞法，而第二種比第一種糟：
            //     ① 把正常的黃體期心率上升當成異常 → 誤報
            //     ② 系統慢慢「習慣」了那個偏高的基線 → **真的出事時反而不叫**
            //
            //   ⚠ 但標記 ≠ 自動放寬門檻。
            //     我們手上還沒有任何一個完整週期的資料，
            //     現在去調數字就是憑文獻猜她的身體。
            //     **現階段只做一件事：讓判斷結果帶上「這天在經期窗內」這個註記**，
            //     等累積到真實資料再談要不要動門檻。
            //     （這條是為了不要重蹈 08-16 那個「挑好看的數字當門檻」。）
            //
            //   按一次 = 開始。她不會每天記得按，所以下游用「起算 N 天」當窗，
            //   細節事後在手機的核對台改。排在後面是因為按的時候人是從容的。
            "生理期" to "period",
            // 2026-09-17：一週一次。按下去＝「量過了」，這週就不再提醒。
            //   數字用錄音講（「體重五十二點三」），錄音本來就會自己上傳。
            "體重" to "weight",
            // ⚠ 2026-08-26 拿掉「其他」，換成「脫錶」。
            //
            //   「其他」九天下來**一次都沒按過**，而它的用途（記下按鈕沒有的怪事）
            //   已經被更好的東西取代了：敲門手勢 → 錄音。
            //   錄音同時給時間**和內容**，「其他」只給時間。
        )
    }
}
