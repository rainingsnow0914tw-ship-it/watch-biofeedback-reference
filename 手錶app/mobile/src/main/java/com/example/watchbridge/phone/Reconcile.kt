package com.example.watchbridge.phone

import org.json.JSONObject
import java.io.File

/**
 * 把一天的原始資料，壓成「可以拿去跟現實對照」的幾句話。
 *
 * ## 這一支存在的理由（2026-08-16 使用者 提的）
 *
 * 她的原話：「我自己記錄我晚上 11 點睡覺，然後早上 6 點起床…
 * 你在手機上的數據要記錄，我們要對一堆是否正確，**減少幻覺**。」
 *
 * 所以這裡輸出的每一句話都必須**可以被現實否證**。
 * 「你今天狀態不錯」不行 —— 那種話對不上也看不出來。
 * 「23:41 到 06:12 之間幾乎沒動」可以 —— 她翻開筆記本就知道對不對。
 *
 * ## 三條寫死的規矩
 *
 * 1. **只講觀察到的，不講推論出來的。**
 *    講「這段時間動作量很低」不講「你睡著了」——
 *    睡著是推論，而她可能是躺著看手機。推論要她自己下。
 *
 * 2. **沒有資料就說沒有資料。** 不要因為那段沒訊號就跳過不提，
 *    那會讓「我不知道」看起來像「沒事發生」。這是 triage.py 原則三的同一條。
 *
 * 3. **每一句都附時間。** 沒有時間的句子對不了。
 */
object Reconcile {

    data class Fact(val 標題: String, val 內容: String)

    private const val GAP_MIN = 10          // 中斷超過這麼久就要講出來
    private const val STILL_RUN_MIN = 20    // 連續不動超過這麼久才值得報

    /**
     * 讀 `on_body`，同時吃得下新舊兩種格式，回 "on" / "off" / "unknown"。
     * 舊資料是布林；沒有這個欄位一律當 "unknown"（**不是 "on"**）。
     */
    private fun onBodyOf(o: JSONObject): String = when {
        o.opt("on_body") is String -> o.optString("on_body")
        o.has("on_body") -> if (o.optBoolean("on_body")) "on" else "off"
        else -> "unknown"
    }

    /** 讀一天的檔，回一串可以對照的事實。檔案不存在回空。 */
    fun facts(dir: File, day: String): List<Fact> {
        val f = File(dir, "$day.jsonl")
        if (!f.exists()) return emptyList()

        // 只挑三種記錄。hr_ibi 佔了 98% 的行數但對「跟現實對照」沒用，
        // 先用字串比對篩掉再解析 —— 在手機上解析 18 MB 的 JSON 太慢。
        val hr = ArrayList<Pair<String, Int>>()          // 時間, bpm
        val motion = ArrayList<Triple<String, Double, Double>>()  // 時間, 動作量, 靜止比例
        val states = ArrayList<Triple<String, Boolean, Boolean>>() // 時間, 在手上, 充電中
        var motionVersions = HashSet<Int>()

        f.forEachLine { line ->
            if (line.length < 20) return@forEachLine
            val isHr = line.contains("\"type\":\"hr\"")
            val isMo = line.contains("\"type\":\"motion\"")
            val isAv = line.contains("\"type\":\"availability\"")
            if (!isHr && !isMo && !isAv) return@forEachLine
            val o = runCatching { JSONObject(line) }.getOrNull() ?: return@forEachLine
            val t = o.optString("t").takeIf { it.length >= 19 } ?: return@forEachLine
            when {
                isHr -> o.optInt("bpm", 0).takeIf { it > 0 }?.let { hr += t to it }
                isMo -> {
                    motion += Triple(t, o.optDouble("activity", -1.0), o.optDouble("still_ratio", -1.0))
                    motionVersions += o.optInt("mv", 1)
                }
                // ⚠ on_body 在 2026-08-16 從布林改成三態字串（on/off/unknown）。
                //   `optBoolean` 對字串一律回 false —— 會把所有新資料判成「沒戴」，
                //   而且不會報錯。所以一定要兩種格式都讀。
                isAv -> states += Triple(t, onBodyOf(o) == "on", o.optBoolean("charging", false))
            }
        }

        if (hr.isEmpty() && motion.isEmpty()) {
            return listOf(Fact("這一天沒有資料", "$day 的檔案在，但裡面沒有可以對照的記錄"))
        }

        val out = ArrayList<Fact>()
        fun hhmm(t: String) = t.substring(11, 16)

        // ── 涵蓋範圍 ──────────────────────────────────────────
        val all = (hr.map { it.first } + motion.map { it.first }).sorted()
        out += Fact("資料涵蓋", "${hhmm(all.first())} 到 ${hhmm(all.last())}")

        // ── 中斷 ──────────────────────────────────────────────
        // 這一段刻意放在最前面。**沒收到資料的時段，任何其他結論都不適用**，
        // 她要先知道哪幾段是「我不知道」。
        val gaps = ArrayList<String>()
        for (i in 1 until all.size) {
            val a = minutesOf(all[i - 1]); val b = minutesOf(all[i])
            if (b - a >= GAP_MIN) gaps += "${hhmm(all[i - 1])}→${hhmm(all[i])}（${b - a} 分鐘）"
        }
        out += if (gaps.isEmpty()) Fact("沒有中斷", "整天沒有超過 $GAP_MIN 分鐘的空白")
        else Fact("這幾段我沒有資料", gaps.joinToString("、") + "\n這幾段發生什麼事我不知道，別當成沒事")

        // ── 錶不在手上的時段 ──────────────────────────────────
        val off = states.filter { !it.second }
        if (off.isNotEmpty()) {
            val charging = off.count { it.third }
            out += Fact("錶離開手腕",
                "${off.size} 次，其中 $charging 次在充電\n" +
                    "最早 ${hhmm(off.first().first)}，最晚 ${hhmm(off.last().first)}")
        }

        // ── 心率 ──────────────────────────────────────────────
        if (hr.isNotEmpty()) {
            val bpms = hr.map { it.second }.sorted()
            val lo = hr.minByOrNull { it.second }!!
            val hi = hr.maxByOrNull { it.second }!!
            out += Fact("心率",
                "中位 ${bpms[bpms.size / 2]}　範圍 ${bpms.first()}–${bpms.last()}\n" +
                    "最低那一筆在 ${hhmm(lo.first)}，最高在 ${hhmm(hi.first)}")

            // 每小時最低心率的那一小時 —— 通常對應最深的睡眠，但**不下這個結論**
            val byHour = hr.groupBy { it.first.substring(11, 13) }
                .mapValues { e -> e.value.map { it.second }.sorted().let { it[it.size / 2] } }
            val quietest = byHour.minByOrNull { it.value }
            if (quietest != null && byHour.size >= 4) {
                out += Fact("心率最低的那一小時",
                    "${quietest.key}:00 那個小時，中位 ${quietest.value}\n" +
                        "（只是觀察，不代表那時候睡著了）")
            }
        }

        // ── 動作 ──────────────────────────────────────────────
        if (motion.isNotEmpty()) {
            if (motionVersions.size > 1) {
                out += Fact("⚠ 這一天混了兩種動作量",
                    "指標版本 ${motionVersions.sorted().joinToString("、")} 都有。" +
                        "**不同版本的數值不能互相比較**，這一天的動作結論要小心看")
            }
            val top = motion.sortedByDescending { it.second }.take(3)
            out += Fact("動作最大的三分鐘",
                top.joinToString("\n") { "${hhmm(it.first)}　動作量 ${it.second.toInt()}" })

            // 最長的一段「幾乎沒動」。用相對門檻（當天中位數的一半）而不是絕對值 ——
            // 絕對門檻在換過取樣率之後就失效了，而且每個人尺度不同。
            val med = motion.map { it.second }.sorted().let { it[it.size / 2] }
            val thr = med * 0.5
            var bestStart = ""; var bestEnd = ""; var best = 0
            var curStart = ""; var prev = ""
            for ((t, act, _) in motion) {
                if (act in 0.0..thr) {
                    if (curStart.isEmpty()) curStart = t
                    prev = t
                } else {
                    if (curStart.isNotEmpty()) {
                        val len = minutesOf(prev) - minutesOf(curStart)
                        if (len > best) { best = len; bestStart = curStart; bestEnd = prev }
                    }
                    curStart = ""
                }
            }
            if (curStart.isNotEmpty()) {
                val len = minutesOf(prev) - minutesOf(curStart)
                if (len > best) { best = len; bestStart = curStart; bestEnd = prev }
            }
            out += if (best >= STILL_RUN_MIN)
                Fact("最長一段幾乎沒動",
                    "${hhmm(bestStart)} 到 ${hhmm(bestEnd)}，共 $best 分鐘\n" +
                        "（門檻是當天中位數的一半 = ${thr.toInt()}）")
            else Fact("沒有長時間不動", "整天沒有連續 $STILL_RUN_MIN 分鐘以上幾乎不動的時段")
        } else {
            out += Fact("沒有動作量資料", "這一天完全沒有 motion 記錄 —— 動作相關的都對不了")
        }

        return out
    }

    /** 從 ISO 時間字串取「當天第幾分鐘」。跨日不處理 —— 一個檔就是一天。 */
    private fun minutesOf(t: String): Int =
        t.substring(11, 13).toInt() * 60 + t.substring(14, 16).toInt()
}
