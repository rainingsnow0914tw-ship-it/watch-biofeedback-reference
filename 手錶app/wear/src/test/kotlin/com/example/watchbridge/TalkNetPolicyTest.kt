package com.example.watchbridge

object TalkNetPolicyTest {
    @JvmStatic
    fun main(args: Array<String>) {
        var passed = 0; var total = 0
        fun case(name: String, block: () -> Unit) { total++; block(); passed++; println("PASS $name") }
        val P = TalkNetPolicy

        case("11-10 08:31:59 real: net=none, phone out of range -> wait for Wi-Fi") { check(P.onOffline("none", 0) == TalkNetPolicy.Act.WAIT_WIFI) }
        case("11-10 08:32:09 real: wifi just up but unvalidated -> still wait, not ready") {
            check(P.onOffline("wifi+未驗證", 0) == TalkNetPolicy.Act.WAIT_WIFI); check(!P.ready("wifi+未驗證"))
        }
        case("phone nearby (bluetooth) -> old 11-06 path") { check(P.onOffline("bluetooth", 0) == TalkNetPolicy.Act.BLUETOOTH) }
        case("bluetooth unvalidated still counts as phone nearby") { check(P.onOffline("bluetooth+未驗證", 0) == TalkNetPolicy.Act.BLUETOOTH) }
        case("second wait allowed") { check(P.onOffline("none", 1) == TalkNetPolicy.Act.WAIT_WIFI) }
        case("after two waits -> give up") { check(P.onOffline("none", 2) == TalkNetPolicy.Act.GIVE_UP) }

        case("ready: validated wifi") { check(P.ready("wifi")) }
        case("ready: bluetooth") { check(P.ready("bluetooth")) }
        case("not ready: none / unreadable") { check(!P.ready("none")); check(!P.ready("讀不到")) }

        case("timeout at 30 s") { check(!P.timedOut(29_000)); check(P.timedOut(30_000)) }

        println("$passed/$total passed")
    }
}
