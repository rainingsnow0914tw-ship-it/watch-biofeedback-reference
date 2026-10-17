package com.example.watchbridge

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.TextView
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * v0.7 —— 畫面只負責兩件事：要權限、啟動服務。
 *
 * 為什麼畫面不再自己讀感測器（2026-08-12 的教訓）：
 *     上一版畫面和服務都會去註冊感測器，兩邊會搶。
 *     血氧那次就是搶出來的 —— 一個會期沒收乾淨，
 *     整組心率和皮膚溫度被餓死，最後只能重開手錶才救回來。
 *     **感測器只能有一個主人。** 那個主人是 CollectorService。
 *
 * 所以這頁只是個開關兼儀表板。關掉它不影響收集，
 * 因為真正在跑的是背景服務（通知列上那個常駐圖示）。
 */
class MainActivity : Activity() {

    private lateinit var big: TextView
    private lateinit var small: TextView

    private val needed = arrayOf(
        Manifest.permission.BODY_SENSORS,
        Manifest.permission.POST_NOTIFICATIONS,
        Manifest.permission.ACTIVITY_RECOGNITION,
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val title = TextView(this).apply {
            text = "阿寶・生理資料"
            textSize = 13f
            setTextColor(Color.parseColor("#4FC4AC"))
            gravity = Gravity.CENTER
        }
        big = TextView(this).apply {
            text = "--"
            textSize = 30f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
        }
        small = TextView(this).apply {
            text = "準備中"
            textSize = 11f
            setTextColor(Color.parseColor("#9BAAA7"))
            gravity = Gravity.CENTER
        }

        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.BLACK)
            setPadding(20, 30, 20, 30)
            addView(title)
            addView(big)
            addView(small)
        })

        val missing = needed.filter {
            checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) launch() else requestPermissions(missing.toTypedArray(), 1)
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        // 通知權限被拒也照跑 —— 少了它服務會比較容易被系統凍，
        // 但感測器權限才是真正的必要條件。
        if (checkSelfPermission(Manifest.permission.BODY_SENSORS) ==
            PackageManager.PERMISSION_GRANTED
        ) launch() else small.text = "沒有感測器權限，量不到"
    }

    private fun launch() {
        CollectorService.start(this)
        showStatus()
    }

    override fun onResume() {
        super.onResume()
        showStatus()
    }

    /** 直接數今天那個檔有幾行 —— 不跟服務通訊，少一個會壞掉的地方。 */
    private fun showStatus() {
        val day = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
        val f = File(File(filesDir, "生理資料"), "$day.jsonl")
        if (!f.exists()) {
            big.text = "0"
            small.text = "服務已啟動\n等第一筆資料"
            return
        }
        val n = runCatching { f.readLines().size }.getOrDefault(0)
        big.text = "$n"
        small.text = "今天已記錄\n${f.length() / 1024} KB"
    }
}
