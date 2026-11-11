package com.example.watchbridge

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.sin

object PpgHrTest {
    /** 合成一段綠光 PPG：每拍＝收縮峰＋重搏切跡小峰，疊上大 DC、呼吸漂移、雜訊；時間戳 25Hz 加抖動。 */
    private fun synth(
        bpm: Double, seconds: Double = 30.0, seed: Long = 1, noise: Double = 0.0, drift: Double = 0.0,
        notch: Double = 0.3, hrv: Double = 0.0, jitterMs: Int = 4, shuffleBatches: Boolean = false,
    ): Pair<LongArray, DoubleArray> {
        val rnd = java.util.Random(seed)
        // 先排好每一拍的起點（可帶心率變異）
        val beats = ArrayList<Double>(); var tb = 0.0
        while (tb < seconds + 2) { beats += tb; tb += 60.0 / bpm * (1 + hrv * (rnd.nextDouble() * 2 - 1)) }
        val n = (seconds * 25).toInt()
        val t = LongArray(n); val v = DoubleArray(n)
        val t0 = 1_794_000_000_000L
        for (i in 0 until n) {
            val ts = i / 25.0 + (rnd.nextInt(2 * jitterMs + 1) - jitterMs) / 1000.0
            var pulse = 0.0
            for (b in beats) {
                val d = ts - b
                if (d < -0.5 || d > 1.5) continue
                pulse += exp(-((d - 0.12) * (d - 0.12)) / (2 * 0.05 * 0.05))
                pulse += notch * exp(-((d - 0.40) * (d - 0.40)) / (2 * 0.06 * 0.06))
            }
            val breath = drift * sin(2 * PI * 0.25 * ts) + drift * 0.02 * ts
            t[i] = t0 + (ts * 1000).toLong()
            v[i] = 2_000_000.0 - 1000.0 * pulse + 1000.0 * breath + 1000.0 * noise * rnd.nextGaussian()
        }
        if (shuffleBatches) {   // 批次送達：每 10 筆一批，偶爾兩批對調（時間戳倒退）
            for (s in 0 until n - 20 step 40) {
                for (k in 0 until 10) { val a = s + k; val b = s + 10 + k
                    val tt = t[a]; t[a] = t[b]; t[b] = tt; val vv = v[a]; v[a] = v[b]; v[b] = vv }
            }
        }
        return t to v
    }

    @JvmStatic
    fun main(args: Array<String>) {
        var passed = 0; var total = 0
        fun case(name: String, block: () -> Unit) { total++; block(); passed += 1; println("PASS $name") }
        fun near(bpm: Double, tol: Double, e: PpgHr.Est?) {
            check(e != null) { "null" }
            check(e.ok) { "not ok: $e" }
            check(abs(e.bpm - bpm) <= tol) { "want $bpm got $e" }
        }

        for (b in listOf(45.0, 60.0, 72.0, 90.0, 110.0, 130.0, 150.0, 180.0)) {
            case("clean $b bpm within ±2") { val (t, v) = synth(b); near(b, 2.0, PpgHr.estimate(t, v)) }
        }
        case("72 bpm + respiration drift + noise within ±3") {
            val (t, v) = synth(72.0, noise = 0.25, drift = 3.0, seed = 7); near(72.0, 3.0, PpgHr.estimate(t, v))
        }
        case("strong dicrotic notch 60 bpm is not doubled to 120") {
            val (t, v) = synth(60.0, notch = 0.65, seed = 3); near(60.0, 3.0, PpgHr.estimate(t, v))
        }
        case("heart-rate variability ±5% at 65 bpm within ±3") {
            val (t, v) = synth(65.0, hrv = 0.05, seed = 11); near(65.0, 3.0, PpgHr.estimate(t, v))
        }
        case("batched / out-of-order timestamps still fine (80 bpm)") {
            val (t, v) = synth(80.0, shuffleBatches = true, seed = 5); near(80.0, 3.0, PpgHr.estimate(t, v))
        }
        case("12 s window (minimum-ish) at 75 bpm within ±3") {
            val (t, v) = synth(75.0, seconds = 12.0, seed = 9); near(75.0, 3.0, PpgHr.estimate(t, v))
        }
        case("pure noise is NOT a heart rate") {
            for (seed in 1L..20L) {
                val rnd = java.util.Random(seed)
                val t = LongArray(750) { 1_794_000_000_000L + it * 40L }
                val v = DoubleArray(750) { 2_000_000.0 + 1000.0 * rnd.nextGaussian() }
                val e = PpgHr.estimate(t, v)
                check(e == null || !e.ok) { "noise seed $seed judged ok: $e" }
            }
        }
        case("flat signal is NOT a heart rate") {
            val t = LongArray(750) { 1_794_000_000_000L + it * 40L }
            val e = PpgHr.estimate(t, DoubleArray(750) { 2_000_000.0 })
            check(e == null || !e.ok)
        }
        case("too short (5 s) -> null") { val (t, v) = synth(70.0, seconds = 5.0); check(PpgHr.estimate(t, v) == null) }
        case("mismatched arrays -> null") { check(PpgHr.estimate(LongArray(100), DoubleArray(99)) == null) }

        // 真實綠光（code104）：錶上 PPG_NOW 量的、同 30 秒系統心率 ref_hr 當標準答案。
        val realDir = java.io.File("../生理資料/ppg_raw")
        val real = realDir.listFiles { f -> f.name.endsWith(".json") }?.sortedBy { it.name }.orEmpty()
        if (real.isEmpty()) println("SKIP real PPG windows（${realDir.path} 沒有檔案）")
        for (f in real) {
            val s = f.readText()
            fun arr(key: String): List<Double> =
                Regex("\"$key\":\\[([^\\]]*)\\]").find(s)?.groupValues?.get(1)?.split(',')?.mapNotNull { it.trim().toDoubleOrNull() }.orEmpty()
            val t0 = Regex("\"t0\":(\\d+)").find(s)!!.groupValues[1].toLong()
            val ref = arr("ref_hr").sorted()
            if (ref.isEmpty()) continue
            val want = ref[ref.size / 2]
            case("real ${f.name} vs system HR $want within ±5") {
                val e = PpgHr.estimate(arr("dt").map { t0 + it.toLong() }.toLongArray(), arr("v").toDoubleArray())
                near(want, 5.0, e)
            }
        }

        // 什麼時候量
        val M = 60_000L
        case("policy: HR dead 10 min, never tried -> try (only if ENABLED)") { check(PpgHrPolicy.shouldTry(10 * M, Long.MAX_VALUE, false, false, true) == PpgHrPolicy.ENABLED) }
        case("code104: PPG off by default (sensors.sec crashes when PPG tracker closes)") { check(!PpgHrPolicy.ENABLED) }
        case("policy: HR fresh -> no") { check(!PpgHrPolicy.shouldTry(9 * M, Long.MAX_VALUE, false, false, true)) }
        case("policy: tried 5 min ago -> wait") { check(!PpgHrPolicy.shouldTry(30 * M, 5 * M, false, false, true)) }
        case("policy: tried 10 min ago -> try again (only if ENABLED)") { check(PpgHrPolicy.shouldTry(30 * M, 10 * M, false, false, true) == PpgHrPolicy.ENABLED) }
        case("policy: charging / confidently off / unsupported -> no") {
            check(!PpgHrPolicy.shouldTry(30 * M, 30 * M, true, false, true))
            check(!PpgHrPolicy.shouldTry(30 * M, 30 * M, false, true, true))
            check(!PpgHrPolicy.shouldTry(30 * M, 30 * M, false, false, false))
        }
        case("policy: duty cycle ≤ 5%") { check(PpgHrPolicy.WINDOW_S * 1000.0 / PpgHrPolicy.EVERY_MS <= 0.05) }

        println("PpgHrTest: $passed/$total passed")
    }
}
