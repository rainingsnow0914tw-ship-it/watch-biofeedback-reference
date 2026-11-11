package com.example.watchbridge

object WalkPolicyTest {
    @JvmStatic
    fun main(args: Array<String>) {
        var passed = 0; var total = 0
        fun case(name: String, block: () -> Unit) { total++; block(); passed++; println("PASS $name") }
        val M = 60_000L

        case("00:50 walking home 701/0.053 -> walking") { check(WalkPolicy.isWalkingMinute(701.0, 0.053)) }
        case("00:57 walking 471/0.156 -> walking") { check(WalkPolicy.isWalkingMinute(471.0, 0.156)) }
        case("00:40 sitting 17/0.881 -> not") { check(!WalkPolicy.isWalkingMinute(17.0, 0.881)) }
        case("00:43 shuffling 259/0.322 -> not") { check(!WalkPolicy.isWalkingMinute(259.0, 0.322)) }
        case("big arm move but mostly still 600/0.6 -> not") { check(!WalkPolicy.isWalkingMinute(600.0, 0.6)) }
        case("NaN -> not") { check(!WalkPolicy.isWalkingMinute(Double.NaN, 0.1)); check(!WalkPolicy.isWalkingMinute(500.0, Double.NaN)) }
        case("11-08 00:49-01:00 replay: 12 walking minutes in a row") {
            val acts = listOf(557, 701, 587, 522, 607, 540, 662, 615, 471, 452, 609, 542)
            val stills = listOf(0.044, 0.053, 0.056, 0.061, 0.049, 0.188, 0.035, 0.054, 0.156, 0.135, 0.041, 0.057)
            check(acts.zip(stills).all { (a, s) -> WalkPolicy.isWalkingMinute(a.toDouble(), s) })
        }

        // ① 計步器停擺
        case("stall: 5 walking minutes, 0 steps -> stall") { check(WalkPolicy.isStepStall(5, 0, true, true)) }
        case("stall: 4 minutes -> not yet") { check(!WalkPolicy.isStepStall(4, 0, true, true)) }
        case("stall: steps moved -> fine") { check(!WalkPolicy.isStepStall(12, 600, true, true)) }
        case("stall: no permission -> not a sensor stall") { check(!WalkPolicy.isStepStall(12, 0, true, false)) }
        case("stall: no sensor -> not") { check(!WalkPolicy.isStepStall(12, 0, false, true)) }
        case("rereg at most every 30 min") {
            check(WalkPolicy.shouldReregister(true, Long.MAX_VALUE))
            check(!WalkPolicy.shouldReregister(true, 10 * M))
            check(WalkPolicy.shouldReregister(true, 30 * M))
            check(!WalkPolicy.shouldReregister(false, Long.MAX_VALUE))
        }

        // ② 忘了帶手機
        fun alert(away: Long = 3 * M, walk: Int = 3, wifi: Boolean = false, worn: Boolean = true, chg: Boolean = false, done: Boolean = false) =
            WalkPolicy.shouldAlertPhoneLeft(away, walk, wifi, worn, chg, done)
        case("phone left: walking outside 3 min without phone -> alert") { check(alert()) }
        case("phone left: only 1 min away -> wait") { check(!alert(away = 1 * M)) }
        case("phone left: walking around at home on Wi-Fi -> silent") { check(!alert(wifi = true)) }
        case("phone left: not walking (sitting at cafe) -> silent") { check(!alert(walk = 1)) }
        case("phone left: watch off wrist / charging -> silent") { check(!alert(worn = false)); check(!alert(chg = true)) }
        case("phone left: already alerted this disconnect -> once only") { check(!alert(done = true)) }

        println("WalkPolicyTest: $passed/$total passed")
    }
}
