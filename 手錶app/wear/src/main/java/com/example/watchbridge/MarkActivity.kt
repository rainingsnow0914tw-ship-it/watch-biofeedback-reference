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
 *
 *
 * **看**確實是手機好，但**記**剛好相反。
 *
 * **那一筆就不會被記下來**。而那一筆正是最有價值的一筆 ——
 * 因為它對應著資料裡一個很銳利的指紋，可以拿來驗證判讀對不對。
 *
 * 一按就記，兩秒鐘結束。**能被記下來的紀錄才有用。**
 *
 * ## 怎麼叫出來
 *
 * 綁在錶冠按兩下（設定 → 進階功能 → 自訂按鍵 → 按兩下）。
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
        box.addView(Button(this).apply {
            text = "🎤 跟阿寶說"
            textSize = 14f
            setTextColor(Color.BLACK)
            setBackgroundColor(Color.parseColor("#9B6DD6"))
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
                textSize = 14f
                setTextColor(Color.BLACK)
                setBackgroundColor(Color.parseColor("#4FC4AC"))
                setOnClickListener { mark(tag, label) }
            }, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { setMargins(0, 6, 0, 0) })
        }
        box.addView(Button(this).apply {
            text = "💬 阿寶說過的話"
            textSize = 14f
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.parseColor("#4A3A8A"))
            setOnClickListener { startActivity(Intent(this@MarkActivity, HistoryActivity::class.java)) }
        }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply { setMargins(0, 6, 0, 0) })
        box.addView(Button(this).apply {
            text = "🫀 量心電圖／體脂"
            textSize = 14f
            setTextColor(Color.BLACK)
            setBackgroundColor(Color.parseColor("#E8637A"))
            setOnClickListener {
                startActivity(Intent(this@MarkActivity, ProbeActivity::class.java))
                finishAndRemoveTask()
            }
        }, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { setMargins(0, 6, 0, 0) })
        setContentView(ScrollView(this).apply {
            setBackgroundColor(Color.BLACK); addView(box)
        })
    }

    private fun mark(tag: String, label: String) {
        // 走廣播交給收集服務寫檔 —— 那支才是唯一在寫這個檔的東西。
        // 兩個地方同時 append 同一個檔遲早會交錯出壞行，而且很難重現。
        sendBroadcast(Intent(ACTION).apply {
            setPackage(packageName)
            putExtra("tag", tag)
            putExtra("label", label)
        })
        // 震一下 = 確認收到。手錶螢幕很小，**觸覺回饋比文字可靠** ——
        runCatching {
            getSystemService(Vibrator::class.java)
                .vibrate(VibrationEffect.createOneShot(60, VibrationEffect.DEFAULT_AMPLITUDE))
        }
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
         *
         *
         * 需要它的時候找不到，等於這個功能不存在。
         *
         * 所以：**最急的排最前面，方便的排後面。**
         *   不舒服 → 最重要，而且按的時候狀況最差
         *
         * 挑選標準（為什麼是這八個）：這件事在資料上有沒有銳利的指紋可以對照。
         *   運動       → 動作量與心率同時衝高
         *   不舒服     → 對應警報判讀
         *   其他       → 先記時間，細節事後在手機補
         */
        val OPTIONS = listOf(
            "不舒服" to "unwell",
            //
            //
            //   時間戳就是它的內容 —— 隔十分鐘才想起來按，那筆就對不上任何東西了。
            //
            //   ## 這顆按鈕在驗證上做什麼
            //
            //   系統自己有震動史（`dumpsys vibrator_manager`，看得到是哪個套件震的），
            //   `com.google.android.apps.wearable.settings`（Wear OS 設定，2.17 秒），
            //
            //   那為什麼還要這顆按鈕？三個理由：
            //     ① `dumpsys` 只留最近一段，過了就沒了 —— 這顆會寫進永久資料
            "震了" to "buzz",

            //
            //
            //   為什麼值得佔一個格子（九天資料算的）：
            //   ```
            //       充電中             8.0%   ✅ 電路上的物理事實，分得出來
            //       明確離腕           4.0%   ✅ 裝置主動說的
            //       🔴 說沒戴卻有脈搏  76.6%   ← **這格永遠不知道真相**
            //       🟡 說不準          11.3%   ← 這格也不知道
            //   ```
            //   那 76,107 筆是所有佩戴判斷麻煩的來源 ——
            //   全部都是在猜這一格。**按一下就不用猜了。**
            //
            //
            //   ⚠ 充電不用按。錶接上充電座是電路上的事實，我百分之百分得出來。
            //
            //   動位置會讓肌肉記憶按錯。
            "脫錶" to "off_wrist",
            "起床" to "wake",
            "睡覺" to "sleep",
            "運動" to "exercise",
            "吃藥" to "medicine",
            //
            //
            //   生理週期會同時推動我們三個判斷訊號往同一個方向跑：
            //     · 心率變異度    同期下降
            //     · 皮膚溫度      黃體期偏高，來潮時回落
            //
            //   不知道的話會有兩種壞法，而第二種比第一種糟：
            //     ① 把正常的黃體期心率上升當成異常 → 誤報
            //
            //   ⚠ 但標記 ≠ 自動放寬門檻。
            //     我們手上還沒有任何一個完整週期的資料，
            //     等累積到真實資料再談要不要動門檻。
            //
            //   細節事後在手機的核對台改。排在後面是因為按的時候人是從容的。
            "生理期" to "period",
            "體重" to "weight",
            //
            //   已經被更好的東西取代了：敲門手勢 → 錄音。
        )
    }
}
