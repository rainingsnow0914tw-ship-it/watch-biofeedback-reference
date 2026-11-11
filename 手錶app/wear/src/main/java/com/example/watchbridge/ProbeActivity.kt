package com.example.watchbridge

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Color
import android.os.Bundle
import android.os.CountDownTimer
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 *
 * ## 這頁為什麼存在
 *
 * 但我們從沒開過：要手指按著錶框電極才量得到，背景跑不了。
 * 這頁就是**真的按一次**，看 SDK 回不回資料、status 是什麼，
 *
 * ## 感測器只能有一個主人
 *
 * 按鈕只送廣播給 CollectorService，由它手上那個 SamsungSensors 去開 tracker；
 * 進度一句話再用廣播送回來顯示。資料走 CollectorService.write 那條路，
 * 跟心率、標記同一個 jsonl，自動享有補送與斷點續傳。
 *
 * ## 手指放哪（三星官方 ECG／BIA 的按法）
 *
 *   ECG：前臂放桌上，另一手的手指**輕按住上方那顆鍵**（Home 鍵）30 秒，不要說話不要動。
 *   BIA：手臂抬到胸口高度，另一手的**中指按上鍵、無名指按下鍵**，兩指同時按住，手指不碰到彼此。
 *
 */
class ProbeActivity : Activity() {

    private lateinit var status: TextView
    private var timer: CountDownTimer? = null

    private val statusRx = object : BroadcastReceiver() {
        override fun onReceive(c: Context?, i: Intent?) {
            val text = i?.getStringExtra("text") ?: return
            status.text = text
            // 服務說結束了（量完／出錯／逾時），倒數就不用繼續騙人。
            if (text.contains("量完") || text.contains("結束") || text.contains("出錯") || text.contains("開不起來")) {
                timer?.cancel(); timer = null
            }
        }
    }

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        // 量的時候螢幕不能暗：暗掉 app 就凍結（wearos skill 鐵律），30 秒就白按了。
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 28, 24, 40)
            addView(TextView(this@ProbeActivity).apply {
                text = "🫀 量心電圖／體脂"
                textSize = 15f
                setTextColor(Color.parseColor("#E8637A"))
                gravity = Gravity.CENTER
                setPadding(0, 0, 0, 8)
            })
        }
        status = TextView(this).apply {
            text = "按一顆開始。量的時候螢幕會亮著。"
            textSize = 11f
            setTextColor(Color.parseColor("#9BAAA7"))
            gravity = Gravity.CENTER
            setPadding(0, 4, 0, 8)
        }
        box.addView(status)
        box.addView(button("🫀 量心電圖 30 秒", "#E8637A") {
            start("ecg", "手指輕按住上方那顆鍵，不動不說話")
        })
        box.addView(hint("ECG：前臂放桌上，另一手手指**按住上方按鍵**（錶框電極）30 秒。"))
        box.addView(button("⚖️ 量體脂", "#F0B64A") {
            start("bia", "中指按上鍵、無名指按下鍵，兩指同時按住")
        })
        box.addView(hint("BIA：手臂抬到胸口，另一手**中指按上鍵、無名指按下鍵**，兩指不要碰到彼此。"))
        box.addView(hint("只記 SDK 回的原始值與 status，不解讀。"))
        setContentView(ScrollView(this).apply {
            setBackgroundColor(Color.BLACK); addView(box)
        })
    }

    private fun start(kind: String, fingers: String) {
        sendBroadcast(Intent(ACTION).apply {
            setPackage(packageName)
            putExtra("kind", kind)
        })
        status.text = "$fingers\n送出請求…"
        timer?.cancel()
        timer = object : CountDownTimer(SamsungSensors.PROBE_SECONDS * 1000L, 1000L) {
            override fun onTick(ms: Long) {
                val cur = status.text.toString()
                val head = cur.substringBefore("\n")
                status.text = "$head\n${ms / 1000} 秒"
            }
            override fun onFinish() = Unit
        }.start()
    }

    private fun button(label: String, color: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        textSize = 14f
        setTextColor(Color.BLACK)
        setBackgroundColor(Color.parseColor(color))
        setOnClickListener { onClick() }
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { setMargins(0, 6, 0, 0) }
    }

    private fun hint(text: String) = TextView(this).apply {
        this.text = text.replace("**", "")
        textSize = 10f
        setTextColor(Color.parseColor("#9BAAA7"))
        setPadding(4, 2, 4, 6)
    }

    override fun onResume() {
        super.onResume()
        registerReceiver(statusRx, IntentFilter(STATUS), Context.RECEIVER_NOT_EXPORTED)
    }

    override fun onPause() {
        super.onPause()
        runCatching { unregisterReceiver(statusRx) }
    }

    override fun onDestroy() {
        timer?.cancel()
        super.onDestroy()
    }

    companion object {
        /** 畫面 → 服務：開始量。extra kind = "ecg" | "bia"。adb 也能打：
         *  adb shell am broadcast -a com.example.watchbridge.PROBE --es kind ecg */
        const val ACTION = "com.example.watchbridge.PROBE"
        /** 服務 → 畫面：進度一句話。extra text。 */
        const val STATUS = "com.example.watchbridge.PROBE_STATUS"
        /*
         *  adb shell am broadcast -a com.example.watchbridge.PROBE_PROFILE --ef height 160 --ef weight 55 --ei gender 0 --ei age 35 */
        const val PROFILE = "com.example.watchbridge.PROBE_PROFILE"
    }
}
