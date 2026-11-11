package com.example.watchbridge.phone

import org.json.JSONObject
import java.io.File
import java.io.RandomAccessFile
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 *
 *
 *
 * 去查才發現：**手錶那邊心率好得不能再好**（那十分鐘 2,080 筆有效、99.5%），
 *
 * 所以這不是壞掉，是**兩個需求被同一個開關綁在一起了**：
 *
 * | 需求 | 該不該藏 |
 * |---|---|
 * | 我昨晚睡得好不好、心率多少　　　| **要藏** —— 看過就不能拿來驗證記憶 |
 *
 * ⚠ 所以這個面板**刻意不顯示任何生理數值**：
 */
object Health {

    data class Row(val 標題: String, val 內容: String, val 好: Boolean)

    private const val TAIL_BYTES = 400_000L

    /** 只讀檔案尾巴。一天的檔會長到十幾 MB，整份讀進來會讓畫面卡住。 */
    private fun tail(f: File): List<String> = runCatching {
        RandomAccessFile(f, "r").use { raf ->
            val from = maxOf(0L, raf.length() - TAIL_BYTES)
            raf.seek(from)
            if (from > 0) raf.readLine()          // 丟掉被切一半的那行
            val out = ArrayList<String>(4000)
            while (true) out.add(raf.readLine() ?: break)
            out
        }
    }.getOrDefault(emptyList())

    private fun onBodyOf(o: JSONObject): String = when {
        o.opt("on_body") is String -> o.optString("on_body")
        o.has("on_body") -> if (o.optBoolean("on_body")) "on" else "off"
        else -> "unknown"
    }

    private fun 幾分鐘前(iso: String, now: Long): Double {
        val p = runCatching {
            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US)
                .parse(iso.substring(0, 19))!!.time
        }.getOrNull() ?: return -1.0
        return (now - p) / 60000.0
    }

    private fun 說多久(m: Double): String = when {
        m < 0 -> "？"
        m < 1 -> "${(m * 60).toInt()} 秒前"
        m < 60 -> "${m.toInt()} 分鐘前"
        else -> "${(m / 60).toInt()} 小時 ${(m % 60).toInt()} 分前"
    }

    /**
     *
     * 這條跟 Reconcile 的規矩二是同一條。
     */
    fun rows(dir: File, day: String): List<Row> {
        val f = File(dir, "$day.jsonl")
        if (!f.exists()) return listOf(Row("今天還沒有任何資料", "**這不代表沒事，是不知道。** 手錶上的 app 可能沒起來、或手錶沒連上手機。", false))

        val now = System.currentTimeMillis()
        val lines = tail(f)
        var 心率t: String? = null; var 心率有效 = 0; var 心率總 = 0
        var 動作t: String? = null; var 溫度t: String? = null
        var 佩戴 = "unknown"; var 佩戴t: String? = null
        var 電量 = -1; var 充電 = false; var 最後t: String? = null

        for (l in lines) {
            if (l.isEmpty() || l[0] != '{') continue
            val o = runCatching { JSONObject(l) }.getOrNull() ?: continue
            val t = o.optString("t"); if (t.isEmpty()) continue
            最後t = t
            if (o.has("batt") && o.optInt("batt", -1) >= 0) {
                電量 = o.optInt("batt"); 充電 = o.optBoolean("charging")
            }
            佩戴 = onBodyOf(o); 佩戴t = t
            when (o.optString("type")) {
                "hr_ibi" -> {
                    心率總++
                    if (o.optInt("status") == 1 && o.optInt("hr") > 40) { 心率有效++; 心率t = t }
                }
                "motion" -> 動作t = t
                "skin_temp" -> 溫度t = t
            }
        }

        val out = ArrayList<Row>()

        //
        // 而真正的原因只是**手錶放在桌上充完電還沒戴回去**。
        // 那個紅燈是對的資訊、錯的語氣。
        //
        //   狼來了不是修辭，是這種面板最常見的死法。
        //
        val 沒戴 = 佩戴 == "off" || 佩戴 == "unknown"

        // ① 最重要的一行：資料到底在不在動
        val age = 最後t?.let { 幾分鐘前(it, now) } ?: -1.0
        out.add(
            when {
                age in 0.0..3.0 ->
                    Row("✅ 正在收資料", "最後一筆是 ${說多久(age)}（${最後t?.substring(11, 19)}）。", true)
                沒戴 ->
                    Row("○ 沒在收（因為沒戴著）",
                        "最後一筆是 ${說多久(age)}。手錶不在手腕上的時候本來就不會有資料 —— " +
                        "**這一格不是警報。** 戴上去大概一分鐘就會恢復。", true)
                else ->
                    Row("🔴 資料停了（而且它應該是戴著的）",
                        "最後一筆是 ${說多久(age)}。**這代表我現在對身體狀況一無所知** —— 不代表沒事。\n" +
                        "這一格是紅的，就真的值得去看一眼。", false)
            }
        )

        // ② 三個感測器各自的死活。**只講活不活，不講量到什麼。**
        fun 感測器(名: String, t: String?, 容許分鐘: Double): Row {
            // ⚠ 沒戴的時候感測器本來就沒東西量。在那種情況下叫，
            if (t == null) return Row(
                (if (沒戴) "○ " else "🔴 ") + 名,
                if (沒戴) "沒戴的時候本來就沒有。" else "這段時間一筆都沒有。",
                沒戴
            )
            val a = 幾分鐘前(t, now)
            val 過期 = a > 容許分鐘
            return Row(
                (if (!過期) "✅ " else if (沒戴) "○ " else "⚠ ") + 名,
                "最後一筆 ${說多久(a)}" + when {
                    !過期 -> ""
                    沒戴 -> "（沒戴著，所以停了是正常的）"
                    else -> "（正常應該 ${容許分鐘.toInt()} 分鐘內）"
                },
                !過期 || 沒戴
            )
        }
        out.add(感測器("心率感測器", 心率t, 12.0))     // 三星安靜時的自然週期是 10 分鐘
        out.add(感測器("動作感測器", 動作t, 3.0))
        out.add(感測器("皮膚溫度", 溫度t, 60.0))       // 實測空檔中位數 8~52 分鐘，本來就稀疏

        // ③ 戴著沒
        out.add(
            when (佩戴) {
                "on" -> Row("✅ 判斷：戴著", "裝置主動回報在手腕上。", true)
                "inferred_on" -> Row("✅ 判斷：戴著（推論）", "裝置沒明講，但量得到脈搏。", true)
                "off" -> Row("○ 判斷：沒戴", "如果妳其實戴著，**那就是一個 bug，跟我說**。", true)
                else -> Row("⚠ 判斷：不知道", "既沒人講話，也沒有生命跡象。", false)
            }
        )

        // ④ 品質：多少比例的心率是量得到的
        if (心率總 > 0) {
            val pct = 心率有效 * 100 / 心率總
            out.add(Row(
                (if (pct >= 80) "✅ " else "⚠ ") + "訊號品質 $pct%",
                "最近 $心率總 筆裡有 $心率有效 筆量得到脈搏。低於 80% 多半是錶帶太鬆。",
                pct >= 80
            ))
        }

        // ⑤ 電
        if (電量 >= 0) out.add(Row(
            (if (充電 || 電量 >= 20) "✅ " else "⚠ ") + "電量 $電量%",
            if (充電) "充電中。" else "沒在充電。",
            充電 || 電量 >= 20
        ))
        return out
    }
}
