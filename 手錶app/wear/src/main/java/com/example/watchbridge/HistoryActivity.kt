package com.example.watchbridge

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 *
 */
class HistoryActivity : Activity() {

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        val fmt = SimpleDateFormat("MM/dd HH:mm", Locale.US)
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(26, 40, 26, 56)
            addView(TextView(this@HistoryActivity).apply {
                text = "💬 阿寶說過的話"; textSize = 15f; setTextColor(Color.parseColor("#C9B6F2"))
                gravity = android.view.Gravity.CENTER; setPadding(0, 0, 0, 10)
            })
        }
        val cards = CardLog.read(this)
        if (cards.isEmpty()) {
            box.addView(TextView(this).apply { text = "還沒有（新版裝好之後的卡才會留在這裡）"; textSize = 13f; setTextColor(Color.LTGRAY); gravity = android.view.Gravity.CENTER })
        }
        for (c in cards) {
            val text = c.optString("text"); val ask = c.optBoolean("ask", false); val t = c.optLong("t")
            box.addView(TextView(this).apply {
                this.text = "${fmt.format(Date(t))}${if (ask) " ❓" else ""}\n${text}"
                textSize = 13f; setTextColor(Color.WHITE); setLineSpacing(0f, 1.15f)
                setPadding(22, 16, 22, 16)
                background = android.graphics.drawable.GradientDrawable().apply { cornerRadius = 26f; setColor(Color.parseColor("#2A1B4D")) }
                setOnClickListener {
                    startActivity(Intent(this@HistoryActivity, SayCardActivity::class.java)
                        .putExtra("text", text).putExtra("spoken", false).putExtra("ask", ask)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP))
                }
            }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply { setMargins(0, 0, 0, 10) })
        }
        setContentView(ScrollView(this).apply { setBackgroundColor(Color.BLACK); isVerticalScrollBarEnabled = false; addView(box) })
    }
}

/** 卡片流水帳：append-only，留最近 50 張。 */
object CardLog {
    private const val FILE = "阿寶說過.jsonl"
    private const val KEEP = 50

    @Synchronized
    fun add(ctx: Context, text: String, ask: Boolean) {
        runCatching {
            val f = File(ctx.filesDir, FILE)
            f.appendText(JSONObject().put("t", System.currentTimeMillis()).put("text", text).put("ask", ask).toString() + "\n", Charsets.UTF_8)
            val lines = f.readLines(Charsets.UTF_8).filter { it.isNotBlank() }
            if (lines.size > KEEP * 2) f.writeText(lines.takeLast(KEEP).joinToString("\n") + "\n", Charsets.UTF_8)
        }
    }

    fun read(ctx: Context): List<JSONObject> {
        val f = File(ctx.filesDir, FILE)
        if (!f.exists()) return emptyList()
        return f.readLines(Charsets.UTF_8).mapNotNull { runCatching { JSONObject(it) }.getOrNull() }.takeLast(KEEP).reversed()
    }
}
