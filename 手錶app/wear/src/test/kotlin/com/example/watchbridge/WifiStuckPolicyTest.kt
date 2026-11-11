package com.example.watchbridge

object WifiStuckPolicyTest {
    @JvmStatic
    fun main(args: Array<String>) {
        var passed = 0; var total = 0
        fun case(name: String, block: () -> Unit) { total++; block(); passed++; println("PASS $name") }
        val M = 60_000L
        val NEVER = Long.MAX_VALUE
        val P = WifiStuckPolicy

        case("stuck: radio on, keep wanted, no Wi-Fi 15 min") { check(P.isStuck(true, true, false, 15 * M)) }
        case("not stuck: only 10 min") { check(!P.isStuck(true, true, false, 10 * M)) }
        case("not stuck: connected") { check(!P.isStuck(true, true, true, 60 * M)) }
        case("not stuck: radio off (Mediator turned it off on purpose)") { check(!P.isStuck(false, true, false, 60 * M)) }
        case("not stuck: she doesn't want keep-Wi-Fi") { check(!P.isStuck(true, false, false, 60 * M)) }

        case("not charging -> cycle radio first") { check(P.decide(true, false, 0, NEVER, false) == WifiStuckPolicy.Act.CYCLE) }
        case("cycled 5 min ago -> wait") { check(P.decide(true, false, 1, 5 * M, false) == WifiStuckPolicy.Act.NONE) }
        case("second cycle after 15 min") { check(P.decide(true, false, 1, 15 * M, false) == WifiStuckPolicy.Act.CYCLE) }
        case("two cycles used -> quiet notify") { check(P.decide(true, false, 2, NEVER, false) == WifiStuckPolicy.Act.NOTIFY) }
        case("11-08 case: charging (Mediator keeps radio ON_CHARGING) -> notify, don't cycle") {
            check(P.decide(true, true, 0, NEVER, false) == WifiStuckPolicy.Act.NOTIFY)
        }
        case("notify only once per episode") { check(P.decide(true, true, 0, NEVER, true) == WifiStuckPolicy.Act.NONE) }
        case("not stuck -> nothing") { check(P.decide(false, false, 0, NEVER, false) == WifiStuckPolicy.Act.NONE) }
        case("11-08 replay: stuck from ~01:31, charging -> one notify by 01:46, then silent") {
            var notified = false; val acts = mutableListOf<Pair<Int, WifiStuckPolicy.Act>>()
            for (m in 0..23) {
                val stuck = P.isStuck(true, true, false, m * M)
                val a = P.decide(stuck, true, 0, NEVER, notified)
                if (a != WifiStuckPolicy.Act.NONE) acts += m to a
                if (a == WifiStuckPolicy.Act.NOTIFY) notified = true
            }
            check(acts == listOf(15 to WifiStuckPolicy.Act.NOTIFY)) { "got $acts" }
        }
        println("WifiStuckPolicyTest: $passed/$total passed")
    }
}
