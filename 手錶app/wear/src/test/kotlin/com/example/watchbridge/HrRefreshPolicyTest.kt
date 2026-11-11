package com.example.watchbridge

object HrRefreshPolicyTest {
    @JvmStatic
    fun main(args: Array<String>) {
        var passed = 0
        fun case(name: String, block: () -> Unit) { block(); passed += 1; println("PASS $name") }
        val P = HrRefreshPolicy
        val all = P.NEEDED.associateWith { true }
        val bg = "android.permission.health.READ_HEALTH_DATA_IN_BACKGROUND"
        val bsb = "android.permission.BODY_SENSORS_BACKGROUND"

        // missing
        case("all granted -> nothing missing") { check(P.missing(all).isEmpty()) }
        case("11-07 real case: two background perms revoked") {
            val g = all + mapOf(bg to false, bsb to false)
            check(P.missing(g) == listOf(bsb, bg)) { P.missing(g).toString() }
        }
        case("unknown perm counts as missing") { check(P.missing(emptyMap()) == P.NEEDED) }

        // shouldReportPerms
        case("first check, all fine -> no record") { check(!P.shouldReportPerms(emptyList(), null, 0, 1000)) }
        case("first check, missing -> record") { check(P.shouldReportPerms(listOf(bg), null, 0, 1000)) }
        case("same missing within an hour -> quiet") { check(!P.shouldReportPerms(listOf(bg), listOf(bg), 0, 30 * 60_000L)) }
        case("same missing after an hour -> record again") { check(P.shouldReportPerms(listOf(bg), listOf(bg), 0, 60 * 60_000L)) }
        case("list changed -> record now") { check(P.shouldReportPerms(listOf(bg, bsb), listOf(bg), 0, 1000)) }
        case("fixed (missing -> none) -> record now, so we see it recovered") { check(P.shouldReportPerms(emptyList(), listOf(bg), 0, 1000)) }
        case("still fine -> quiet") { check(!P.shouldReportPerms(emptyList(), emptyList(), 0, 99 * 60 * 60_000L)) }

        // shouldReregister
        val NEVER = Long.MAX_VALUE
        case("remote always re-registers") { check(P.shouldReregister("remote", 0, 0)) }
        case("open, hr fresh -> leave it") { check(!P.shouldReregister("open", 30_000, NEVER)) }
        case("open, hr stale, never re-registered -> re-register") { check(P.shouldReregister("open", NEVER, NEVER)) }
        case("open, hr stale, re-registered 1 min ago -> wait") { check(!P.shouldReregister("open", NEVER, 60_000)) }
        case("watchdog, hr stale 10 min, last rereg 5 min ago -> re-register") { check(P.shouldReregister("watchdog", 600_000, 300_000)) }
        case("watchdog, hr fresh -> leave it") { check(!P.shouldReregister("watchdog", 5_000, NEVER)) }
        println("ALL $passed PASSED")
    }
}
