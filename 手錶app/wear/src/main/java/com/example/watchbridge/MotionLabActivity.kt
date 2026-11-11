package com.example.watchbridge

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 *
 * 第一批：📱 拿手機（做 30～50 次）、🍑 橋式（20 次，三星運動清單沒有）、
 *
 * 真正錄音的是 CollectorService 裡的 FallRecorder.startMotion（服務在背景，螢幕暗了也照錄）。
 * 檔案在錶上 files/motion_rec/<時間>_<標籤>.jsonl，電腦用 run-as 拉回來訓練。
 *
 * 不用每一下都按標記：電腦端看動作能量自動切成一次一次。
 */
class MotionLabActivity : Activity() {

    private val ui = Handler(Looper.getMainLooper())
    private lateinit var status: TextView
    private lateinit var stopBtn: Button
    private val pickBtns = mutableListOf<Button>()
    private var countdown = 0

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 34, 28, 48)
            gravity = Gravity.CENTER_HORIZONTAL
        }
        box.addView(TextView(this).apply {
            text = "🎬 錄動作"
            textSize = 15f
            setTextColor(Color.parseColor("#F7A8C4"))
            gravity = Gravity.CENTER
        })
        status = TextView(this).apply {
            textSize = 13f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(0, 6, 0, 6)
        }
        box.addView(status)

        for ((label, tag) in OPTIONS) {
            val b = Button(this).apply {
                text = label
                textSize = 14f
                setTextColor(Color.BLACK)
                setBackgroundColor(Color.parseColor("#9B6DD6"))
                setOnClickListener { begin(tag, label) }
            }
            pickBtns += b
            box.addView(b, lp())
        }
        stopBtn = Button(this).apply {
            text = "⏹ 停止"
            textSize = 16f
            setTextColor(Color.BLACK)
            setBackgroundColor(Color.parseColor("#F7A8C4"))
            setOnClickListener { end() }
        }
        box.addView(stopBtn, lp())

        setContentView(ScrollView(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(box)
        })
        tick()
    }

    private fun lp() = LinearLayout.LayoutParams(
        LinearLayout.LayoutParams.MATCH_PARENT,
        LinearLayout.LayoutParams.WRAP_CONTENT,
    ).apply { setMargins(0, 8, 0, 0) }

    private fun begin(tag: String, label: String) {
        if (FallRecorder.current != null || countdown > 0) return
        countdown = 3
        fun step() {
            if (countdown > 0) {
                status.text = "準備 $label\n${countdown}…"
                buzz(60)
                countdown--
                ui.postDelayed({ step() }, 1000)
            } else {
                sendBroadcast(Intent(FallRecorder.ACTION_START).setPackage(packageName)
                    .putExtra("tag", tag).putExtra("motion", true))
                buzz(400)
            }
        }
        step()
    }

    private fun end() {
        if (FallRecorder.current == null) return
        sendBroadcast(Intent(FallRecorder.ACTION_STOP).setPackage(packageName))
        buzzTwice()
    }

    private fun tick() {
        val cur = FallRecorder.current
        if (countdown == 0) {
            status.text = if (cur != null) {
                val s = (System.currentTimeMillis() - FallRecorder.startedAt) / 1000
                "● 錄音中：${OPTIONS.firstOrNull { it.second == cur }?.first ?: cur}\n" +
                    "%d:%02d　%d 筆".format(s / 60, s % 60, FallRecorder.count)
            } else "選一個動作開始"
        }
        val idle = cur == null && countdown == 0
        pickBtns.forEach { it.visibility = if (idle) Button.VISIBLE else Button.GONE }
        stopBtn.visibility = if (cur != null) Button.VISIBLE else Button.GONE
        ui.postDelayed({ tick() }, 1000)
    }

    private fun buzz(ms: Long) {
        getSystemService(Vibrator::class.java)
            ?.vibrate(VibrationEffect.createOneShot(ms, 255))
    }

    private fun buzzTwice() {
        getSystemService(Vibrator::class.java)?.vibrate(
            VibrationEffect.createWaveform(longArrayOf(0, 180, 150, 180), intArrayOf(0, 255, 0, 255), -1))
    }

    override fun onDestroy() {
        ui.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    companion object {
        // 拿起／放下錄同一段（拿起看→放下，連做 30 次），電腦用手腕角度切成兩類。
        val OPTIONS = listOf(
            "📱 拿手機看→放下" to "phone",
            "🥤 舉杯喝水" to "drink",
            "⌨️ 打字" to "typing",
            "✍️ 寫字" to "writing",
            "🙆 伸懶腰" to "stretch",
            "🌀 其他（不動、講話）" to "other",
            "🍑 橋式（回家再錄）" to "bridge",
        )
    }
}
