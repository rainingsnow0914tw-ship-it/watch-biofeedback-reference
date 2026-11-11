package com.example.watchbridge

object SdkRebindPolicyTest {
    @JvmStatic
    fun main(args: Array<String>) {
        var passed = 0; var total = 0
        fun case(name: String, block: () -> Unit) { total++; block(); passed++; println("PASS $name") }
        val M = 60_000L
        val NEVER = Long.MAX_VALUE
        case("connected -> never rebind") { check(!SdkRebindPolicy.shouldRebind(true, 30 * M, NEVER, 0)) }
        case("not connected, 2 min after start -> wait (20:09 case took 4 min but first try at 3 is cheap)") {
            check(!SdkRebindPolicy.shouldRebind(false, 2 * M, NEVER, 0))
        }
        case("not connected, 3 min after start -> rebind") { check(SdkRebindPolicy.shouldRebind(false, 3 * M, NEVER, 0)) }
        case("just tried 1 min ago -> wait") { check(!SdkRebindPolicy.shouldRebind(false, 6 * M, 1 * M, 1)) }
        case("tried 3 min ago, 1 try so far -> rebind again") { check(SdkRebindPolicy.shouldRebind(false, 6 * M, 3 * M, 1)) }
        case("3 tries used -> stop (no endless loop)") { check(!SdkRebindPolicy.shouldRebind(false, 60 * M, 30 * M, 3)) }
        case("schedule: tries at 3, 6, 9 min then stop") {
            var tries = 0; var last = -NEVER / 2; val at = mutableListOf<Long>()
            for (m in 0..30) {
                val now = m * M
                if (SdkRebindPolicy.shouldRebind(false, now, now - last, tries)) { tries++; last = now; at += m.toLong() }
            }
            check(at == listOf(3L, 6L, 9L)) { "got $at" }
        }
        // code104：資料流停擺
        case("stall: stream silent 5 min while connected -> kick") { check(SdkRebindPolicy.shouldKickStall(true, 5 * M, NEVER)) }
        case("stall: stream fresh -> no") { check(!SdkRebindPolicy.shouldKickStall(true, 4 * M, NEVER)) }
        case("stall: kicked 2 min ago -> wait") { check(!SdkRebindPolicy.shouldKickStall(true, 20 * M, 2 * M)) }
        case("stall: not connected -> leave it to rebind") { check(!SdkRebindPolicy.shouldKickStall(false, 20 * M, NEVER)) }
        case("stall: 20:40 incident would self-heal by 20:45") {
            val firstKick = (0..20).first { SdkRebindPolicy.shouldKickStall(true, it * M, NEVER) }
            check(firstKick == 5) { "got $firstKick" }
        }
        println("SdkRebindPolicyTest: $passed/$total passed")
    }
}
