package com.example.watchbridge.phone

import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.math.BigDecimal
import java.math.RoundingMode
import java.time.LocalDateTime

/**
 * 摘要層 —— 一天 7 萬筆原始資料 → 24 筆小時摘要 + 1 筆日摘要。
 *
 * ## 為什麼要跑在手機上（使用者 2026-08-20 指出來的）
 *
 * > 「這個摘要層就是要寫在手錶跟手機裡面嘛，也許我們應該先做這個。」
 *
 * 她說得對，而且這一步同時解掉三件事：
 * ```
 * 家人家沒有電腦       → 判讀本來就非得搬進裝置不可
 * 只送摘要不送原始流   → 隱私（資料不出裝置）＋ 成本（小寶的 token）
 * 邏輯在裝置上         → 斷網也能判斷
 * ```
 * 一天 11 MB → 約 9 KB，**1300 分之一**。
 *
 * ## 這一版是從 `穿戴橋接/摘要層.py` 逐條搬過來的
 *
 * 刻意保持**輸出格式完全一樣**，這樣可以拿同一天的資料兩邊各跑一次、
 * 直接 diff。**「搬對了沒有」這件事要能被驗證，不能靠我說。**
 *
 * ## 三條最要命的規則（原因寫在 `摘要層-規格.md`）
 *
 * ### 一、`cover` 放最前面，沒有它就不准讀數值
 * ```
 * hr.p50 = 68 配 hr_samples = 1840 → 「她心率 68」
 * hr.p50 = 68 配 hr_samples = 3    → 「我幾乎不知道」
 * ```
 *
 * ### 二、沒資料的小時、沒資料的日子，**還是要有一筆**
 * 使用者：「正常的數據也不能全部刪除…**但是沒了、變斷層也很奇怪**。」
 * 那是這專案的頭號病：**「沒有資料」跟「一切正常」長得一樣。**
 *
 * ### 三、字彙裡**沒有「正常」**
 * 最好的結果叫 `ok` = 「沒有踩到任何固定門檻」，不等於「她很健康」。
 * 要說健康需要個人基線，而基線需要 30 天（Fitbit 的做法）—— 我們還沒有。
 *
 * ## ⚠ 記憶體：一定要串流，不能整檔讀進來
 *
 * 原始檔一天 11~14 MB、7 萬行。手機上把它整個 parse 成物件會很痛。
 * 這裡**一行一行讀、直接累加進 24 個桶子**，峰值記憶體只有那些數值陣列。
 */
object Summary {

    // ── 固定門檻（Apple 式：不用個人基線）─────────────────────
    // 個人基線是從歷史算的，而歷史可能已經是不正常的 ——
    // 一個心臟不好的人，他的「個人基線」本來就偏高。
    private const val HR_HIGH = 120      // Apple 高心率通知的預設值
    private const val HR_LOW = 40
    private const val STILL_MOTION = 25.0  // v4 指標，**尚未校準**

    private const val MIN_HR_SAMPLES = 30
    private const val MIN_WORN_MINUTES = 10
    private const val MIN_VALID_DAY_RATIO = 0.70 // 報告品質閘門，不是醫療門檻

    // ── 溫度第二意見 ────────────────────────────────────────────
    // ⚠ 2026-08-26 大改，跟 `穿戴橋接/摘要層.py` **必須保持一模一樣** ——
    //   這兩份當初就是刻意寫成輸出相同，好拿同一天的資料兩邊各跑一次直接 diff。
    //   （08-25/26 之間 Python 那邊改了三輪、Kotlin 沒跟上，已經漂移過一次。）
    //
    //   改的兩件事：
    //   ① **溫差是主訊號，絕對溫度只當離譜值的煞車。**
    //      舊版要求 `skin >= 32.5 && delta > 0.15` 兩個同時成立，
    //      結果她在冷氣餐廳（皮膚 32.01、溫差 +0.98）被判「不確定」——
    //      而她明明戴著。**絕對皮膚溫完全跟著環境跑**（實測跨 7 度），
    //      溫差不跟著跑，因為環境同時影響皮膚感測器跟錶身、會互相抵銷。
    //
    //   ② **DELTA_OFF 從 -0.3 放寬到 -1.5。** 九天實測：
    //         戴著（非剛充電）   n=7932   5% +0.10   中位 +0.51
    //         確定沒戴（非充電）  n= 759   5% -4.12   **中位 +0.17**
    //      門檻 -0.3 → 誤殺戴著的 0.90%；-1.5 → 0.09%，只少抓 4 個百分點。
    private const val SKIN_FLOOR = 30.0
    private const val DELTA_WORN = 0.15
    private const val DELTA_OFF = -1.5

    const val DIR = "摘要"

    private class Bucket {
        var motionMinutes = 0
        var chargingMinutes = 0
        var trustedMinutes = 0
        val wornMinutes = HashSet<String>()      // 跨所有型別，數「不同的分鐘」
        val explicitOffMinutes = HashSet<String>()
        val hr = ArrayList<Int>()
        val acts = ArrayList<Double>()
        val skin = ArrayList<Double>()
        val delta = ArrayList<Double>()
        val marks = ArrayList<JSONObject>()
    }

    private fun worn(ob: String?) = ob == "on" || ob == "inferred_on" || ob == "true" || ob == "True"

    /** 產生一天的摘要，寫檔，回傳檔案。找不到原始資料回 null。 */
    fun build(filesDir: File, day: String): File? {
        val src = File(File(filesDir, "手錶資料"), "$day.jsonl")
        if (!src.exists()) return null

        val buckets = Array(24) { Bucket() }
        // 最長連續配戴用的狀態（壓瘡那條線要看的數字）
        val runs = ArrayList<Double>()
        var runStart: Long = -1
        var runLast: Long = -1
        var battFirst = -1
        var battLast = -1
        var records = 0
        val onSignal = HashSet<Int>()
        val offEvidence = HashSet<Int>()
        val hrSignal = HashSet<Int>()
        val now = LocalDateTime.now()
        val today = now.toLocalDate().toString()
        val elapsed = when {
            day == today -> (now.hour * 60 + now.minute + 1).coerceIn(0, 1440)
            day > today -> 0
            else -> 1440
        }

        src.forEachLine { line ->
            if (line.isBlank()) return@forEachLine
            val o = try { JSONObject(line) } catch (e: Exception) { return@forEachLine }
            val t = o.optString("t")
            if (t.length < 16) return@forEachLine
            val hour = t.substring(11, 13).toIntOrNull() ?: return@forEachLine
            if (hour !in 0..23) return@forEachLine
            records++

            val b = buckets[hour]
            val type = o.optString("type")
            val ob = o.optString("on_body", "")
            val minute = t.substring(0, 16)
            if (worn(ob)) b.wornMinutes.add(minute)
            val explicitOff = (ob == "off" && (type == "motion" || type == "availability")) ||
                (type == "mark" && o.optString("tag") == "off_wrist")
            if (explicitOff) b.explicitOffMinutes.add(minute)

            // 與電腦覆蓋率.py 同一個「分鐘證據」規則：沒有紀錄不延伸狀態。
            val minuteNumber = t.substring(14, 16).toIntOrNull()
            if (t.startsWith("${day}T") && minuteNumber != null && minuteNumber in 0..59) {
                val index = hour * 60 + minuteNumber
                if (index < elapsed) {
                    val signal = when (type) {
                        "hr_ibi" -> o.optInt("status", -1) == 1 && o.optDouble("hr", 0.0) > 0
                        "hr" -> o.optDouble("bpm", 0.0) > 0
                        "skin_temp" -> o.opt("skin") is Number
                        "motion" -> o.optBoolean("trust") && (o.opt("activity") is Number)
                        else -> false
                    }
                    if (worn(ob) && signal) {
                        onSignal.add(index)
                        if (type == "hr_ibi" || type == "hr") hrSignal.add(index)
                    }
                    if (explicitOff) offEvidence.add(index)
                }
            }

            when (type) {
                "motion" -> {
                    b.motionMinutes++
                    if (o.optBoolean("charging")) b.chargingMinutes++
                    if (worn(ob) && o.optBoolean("trust")) {
                        b.trustedMinutes++
                        b.acts.add(o.optDouble("activity", 0.0))
                    }
                    val batt = o.optInt("batt", -1)
                    if (batt >= 0) { if (battFirst < 0) battFirst = batt; battLast = batt }

                    // ⚠ 用「明確 off」切段，`unknown` **不算脫下** ——
                    //   2026-08-19 我用 unknown 切，把 9.3 小時算成 1.7 小時，差了五倍。
                    val ms = minuteMillis(t)
                    if (worn(ob)) {
                        if (runStart < 0) runStart = ms
                        runLast = ms
                    } else if (ob == "off") {
                        if (runStart >= 0 && runLast >= 0)
                            runs.add((runLast - runStart) / 3_600_000.0)
                        runStart = -1; runLast = -1
                    }
                }
                "hr_ibi" -> {
                    val v = o.optInt("hr", 0)
                    if (v > 0 && o.optInt("status", -1) == 1) b.hr.add(v)
                }
                "hr" -> {
                    val v = o.optInt("bpm", 0)
                    if (v > 0) b.hr.add(v)
                }
                "skin_temp" -> {
                    if (o.has("skin")) {
                        val s = o.optDouble("skin", Double.NaN)
                        if (!s.isNaN()) {
                            b.skin.add(s)
                            if (o.has("watch_ref")) {
                                val r = o.optDouble("watch_ref", Double.NaN)
                                if (!r.isNaN()) b.delta.add(s - r)
                            }
                        }
                    }
                }
                "mark" -> b.marks.add(JSONObject()
                    .put("t", t.substring(11, minOf(19, t.length)))
                    .put("tag", o.optString("tag")))
            }
        }
        if (runStart >= 0 && runLast >= 0) runs.add((runLast - runStart) / 3_600_000.0)

        val hours = (0..23).map { hourJson(day, it, buckets[it]) }
        val dayJson = dayJson(day, hours, runs, battFirst, battLast, records,
            buckets, onSignal, offEvidence, hrSignal, elapsed, today)

        val outDir = File(filesDir, DIR).apply { mkdirs() }
        val out = File(outDir, "$day.jsonl")
        out.printWriter().use { w ->
            hours.forEach { w.println(it.toString()) }
            w.println(dayJson.toString())
        }
        Log.i(TAG, "摘要 $day：${src.length() / 1024}KB → ${out.length() / 1024}KB")
        return out
    }

    private fun minuteMillis(t: String): Long =
        // 只需要相對差，不需要真的時區換算；用「日內秒數」就夠，
        // 而且**刻意不跨日** —— 跨午夜的那一段會被切斷，這是已知的限制，
        // 因為日摘要本來就是以日為界。要看跨夜的連續配戴要另外算。
        (t.substring(11, 13).toLong() * 3600 +
         t.substring(14, 16).toLong() * 60) * 1000

    private fun hourJson(day: String, hour: Int, b: Bucket): JSONObject {
        val o = JSONObject()
        o.put("t", "%sT%02d:00".format(day, hour))
        o.put("layer", "hour")
        // cover 放第一個。沒有它就不准讀下面的數值。
        o.put("cover", JSONObject()
            .put("motion_minutes", b.motionMinutes)
            .put("worn_minutes", b.wornMinutes.size)
            .put("explicit_off_minutes", b.explicitOffMinutes.size)
            .put("charging_minutes", b.chargingMinutes)
            .put("trusted_minutes", b.trustedMinutes)
            .put("hr_samples", b.hr.size))
        if (b.hr.isNotEmpty()) {
            b.hr.sort()
            o.put("hr", JSONObject()
                .put("lo", b.hr.first()).put("p50", b.hr[b.hr.size / 2]).put("hi", b.hr.last()))
        }
        if (b.acts.isNotEmpty()) {
            b.acts.sort()
            o.put("motion", JSONObject()
                .put("p50", round1(b.acts[b.acts.size / 2])).put("hi", round1(b.acts.last())))
        }
        if (b.skin.isNotEmpty()) {
            b.skin.sort()
            val t = JSONObject().put("skin_p50", round2(b.skin[b.skin.size / 2]))
            if (b.delta.isNotEmpty()) {
                b.delta.sort()
                t.put("delta_ref_p50", round2(b.delta[b.delta.size / 2]))
            }
            o.put("temp", t)
        }
        if (b.marks.isNotEmpty()) o.put("marks", JSONArray(b.marks))
        judgeHour(o)
        return o
    }

    /**
     * ⚠ **字彙裡刻意沒有「正常 / green」。**
     * 最好的結果是 `ok` = 「沒有踩到任何固定門檻」，不等於「她很健康」。
     */
    private fun judgeHour(o: JSONObject) {
        val c = o.getJSONObject("cover")
        val motion = c.getInt("motion_minutes")
        val wornM = c.getInt("worn_minutes")
        val charging = c.getInt("charging_minutes")
        val explicitOff = c.getInt("explicit_off_minutes")
        val hrN = c.getInt("hr_samples")

        if (motion == 0 && hrN == 0) {
            o.put("verdict", "no_data")
            o.put("why", "這一小時完全沒有資料 —— **不是沒事，是不知道**"); return
        }
        if (wornM == 0 && charging >= 30) {
            o.put("verdict", "charging"); o.put("why", "在充電（$charging 分鐘），沒戴"); return
        }
        if (wornM < MIN_WORN_MINUTES) {
            // ⚠⚠ 「說沒戴、卻有一兩千筆有效心率」的小時真的存在（08-20 的 02 時、10 時）。
            //   用皮膚溫度查證之後：**`status == 1` 不等於真的有脈搏** ——
            //   離腕的 PPG 會吐出通過有效性旗標的假數字（95、96、63~94）。
            //   → 那正是最怕的失效方向的原料：「阿公把錶放床頭櫃 → 系統說一切正常」。
            //   → 擋住它的是 CollectorService 那條「availability 說 off 時優先於脈搏」。
            //     **那條規則是承重牆，不要拆。**
            var why = "戴著只有 $wornM 分鐘 —— 身體判斷這小時做不了（沒戴的錶比睡著的人還靜）"
            if (hrN >= 300) {
                val temp = o.optJSONObject("temp")
                val op = tempOpinion(
                    temp?.optDouble("skin_p50", Double.NaN),
                    temp?.optDouble("delta_ref_p50", Double.NaN),
                    // ⚠ 括號不能省：Kotlin 的 `?:` 優先級比 `>` 低，
                    //   少了括號會被解析成 `x ?: (0 > 0)`，型別直接對不上。
                    charged = (o.optJSONObject("cover")?.optInt("charging_minutes", 0) ?: 0) > 0)
                o.put("conflict", JSONObject()
                    .put("kind", "off_but_hr").put("temp_says", op.first).put("temp_why", op.second))
                why += "　⚠ 但同時有 $hrN 筆「有效」心率。溫度說：${op.second}"
                if (op.first == "off") why += "　→ **心率是離腕假訊號，不要當成脈搏**"
                else if (op.first == "worn") why += "　→ **其實戴著，是 availability 報錯**"
            }
            if (explicitOff < 30) {
                o.put("verdict", "unknown")
                o.put("why", "只有 $explicitOff 分鐘明確離腕；其餘資料不足，不能推成整小時沒戴。" +
                    if (hrN >= 300) " $why" else "")
                return
            }
            o.put("verdict", "off"); o.put("why", why); return
        }
        if (hrN < MIN_HR_SAMPLES) {
            o.put("verdict", "unknown")
            o.put("why", "戴著 $wornM 分鐘，但只有 $hrN 筆有效心率 —— 數值不可信"); return
        }

        val hr = o.optJSONObject("hr")
        val p50 = hr?.optInt("p50", 0) ?: 0
        val lo = hr?.optInt("lo", 999) ?: 999
        val still = (o.optJSONObject("motion")?.optDouble("p50", 999.0) ?: 999.0) < STILL_MOTION

        // Apple 式閘門：高心率只有在「人幾乎沒動」的時候才算異常。
        // 運動、爬樓梯、洗熱水澡都會讓心率上去 —— 那些不是問題。
        if (still && p50 > HR_HIGH) {
            o.put("verdict", "flag")
            o.put("why", "幾乎沒動，但心率中位 $p50 超過固定門檻 $HR_HIGH（Apple 高心率通知的預設值）")
            return
        }
        if (lo < HR_LOW) {
            o.put("verdict", "flag"); o.put("why", "出現 $lo 的心率，低於固定門檻 $HR_LOW"); return
        }
        o.put("verdict", "ok")
        o.put("why", "戴著 $wornM 分鐘、$hrN 筆心率，沒有踩到任何固定門檻" +
                if (still) "（幾乎沒動）" else "")
    }

    /**
     * ⚠ **兩個訊號必須同向，否則一律回「不確定」。**
     *
     * 2026-08-21 的教訓：有一小時 `skin 34.07`（明顯是體溫）但 `溫差 −0.03`。
     * 第一版寫成「skin 低 **或** 溫差為負 → 離腕」，於是 34 度被判成離腕 —— 因為用了 or。
     * 剛脫下來的錶，皮膚感測器跟錶身都還熱，溫差會收斂到 0 但絕對溫度還沒降。
     * **那個狀態本來就分不出來，正確答案是「不確定」，不是硬選一邊。**
     */
    private fun tempOpinion(skin: Double?, delta: Double?,
                            charged: Boolean = false): Pair<String, String> {
        if (skin == null || delta == null || skin.isNaN() || delta.isNaN())
            return "unknown" to "沒有溫度資料"
        // ⚠⚠ 2026-08-26：**這小時有充過電的話，溫度不能用。**
        //
        //   充電會把錶身加熱到 34~35°C，而手腕要十幾分鐘才把皮膚感測器暖回來。
        //   實測 09:39:56 她剛從充電座拿起來戴上：
        //       錶身 34.92°C  皮膚 28.97°C  → 溫差 **-5.95**，而她明明戴著。
        //   一直到 09:55:11 才轉正。**整整 15 分鐘的反向訊號。**
        //
        //   而「剛充完電就戴上」正是每天最常做的動作 ——
        //   不擋掉的話，等於在她最常戴錶的時刻讓溫度給出相反的判決。
        if (charged)
            return "unknown" to "這小時有充過電 —— 充電把錶身加熱了，溫度這時候說什麼都不算"
        if (skin < SKIN_FLOOR)
            return "off" to "皮膚只有 %.2f°C —— 那不是活人手腕的溫度".format(skin)
        if (delta > DELTA_WORN)
            return "worn" to "皮膚比錶身高 %+.2f（%.2f°C）—— 熱源在身體那側".format(delta, skin)
        if (delta < DELTA_OFF)
            return "off" to "皮膚比錶身低 %+.2f（%.2f°C）—— 熱源不在身體那側".format(delta, skin)
        // ⚠ 中間那條寬帶（-1.5 ~ +0.15）本來就分不出來 —— 誠實回不確定。
        return "unknown" to
            "溫差只有 %+.2f（皮膚 %.2f°C）—— 剛脫下來、或環境跟體溫差不多的時候就長這樣。不確定"
                .format(delta, skin)
    }

    private fun dayJson(day: String, hours: List<JSONObject>, runs: List<Double>,
                        battFirst: Int, battLast: Int, records: Int,
                        buckets: Array<Bucket>, onSignal: Set<Int>,
                        offEvidence: Set<Int>, hrSignal: Set<Int>,
                        elapsed: Int, today: String): JSONObject {
        val withData = hours.count { it.optString("verdict") != "no_data" }
        val wornMin = buckets.sumOf { it.wornMinutes.size }
        val valid = onSignal - offEvidence
        val off = offEvidence - onSignal
        val conflicts = onSignal intersect offEvidence
        val unknown = elapsed - valid.size - off.size
        val ratio = if (elapsed > 0) valid.size.toDouble() / elapsed else 0.0
        val quality = when {
            day == today && elapsed < 1440 && ratio >= MIN_VALID_DAY_RATIO -> "partial"
            elapsed == 1440 && ratio >= MIN_VALID_DAY_RATIO -> "sufficient"
            else -> "insufficient"
        }
        val o = JSONObject()
            .put("t", day).put("layer", "day")
            .put("cover", JSONObject()
                .put("hours_with_data", withData)
                .put("worn_minutes", wornMin)
                .put("records", records)
                .put("minutes", elapsed)
                .put("valid_minutes", valid.size)
                .put("off_minutes", off.size)
                .put("unknown_minutes", unknown)
                .put("conflict_minutes", conflicts.size)
                .put("hr_minutes", (hrSignal - offEvidence).size)
                .put("valid_ratio", round4(ratio))
                .put("quality", quality))
            .put("wear", JSONObject()
                // 醫學上壓瘡耐受約 2 小時。這裡只**報數字**，不出聲、不警報 ——
                // 因為系統的誘因跟皮膚安全是相反的（越緊訊號越乾淨），
                // 所以這一格永遠只描述，不建議調緊。
                .put("longest_run_h", round1(runs.maxOrNull() ?: 0.0))
                .put("runs_over_2h", runs.count { it > 2 }))
            .put("marks", JSONArray(hours.flatMap { h ->
                (0 until (h.optJSONArray("marks")?.length() ?: 0))
                    .map { h.getJSONArray("marks").getJSONObject(it) }
            }))
        if (battFirst >= 0) o.put("batt", JSONObject().put("first", battFirst).put("last", battLast))

        if (withData == 0) {
            o.put("verdict", "no_data")
            o.put("why", "這一天一筆資料都沒有 —— **不是沒事，是不知道**")
            return o
        }
        val flags = hours.filter { it.optString("verdict") == "flag" }
        o.put("verdict", if (flags.isNotEmpty()) "flag" else
            if (quality == "sufficient") "ok" else quality)
        o.put("why", if (flags.isEmpty())
            "有效資料 ${valid.size}/$elapsed 分鐘；" + when (quality) {
                "partial" -> "今日尚未結束，這是暫報"
                "insufficient" -> "覆蓋不足，不能作全日判斷"
                else -> "有資料的時段沒有踩到固定門檻"
            }
        else "${flags.size} 個小時踩到固定門檻：" +
            flags.joinToString("、") { it.getString("t").substring(11, 16) })
        return o
    }

    // 與 Python round_summary() 共用明確十進位 HALF_EVEN 規則。
    // Math.round 是 half-up，Python round(float,n) 又受二進位尾數影響。
    private fun round1(v: Double) = BigDecimal.valueOf(v).setScale(1, RoundingMode.HALF_EVEN).toDouble()
    private fun round2(v: Double) = BigDecimal.valueOf(v).setScale(2, RoundingMode.HALF_EVEN).toDouble()
    private fun round4(v: Double) = Math.round(v * 10000) / 10000.0

    const val TAG = "WatchBridge"
}
