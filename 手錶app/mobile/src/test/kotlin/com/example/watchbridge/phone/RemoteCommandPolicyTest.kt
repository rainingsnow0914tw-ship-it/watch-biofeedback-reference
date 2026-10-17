package com.example.watchbridge.phone

/** Standalone offline unit test: no Android runtime, network, phone, watch, or ADB. */
object RemoteCommandPolicyTest {
    @JvmStatic
    fun main(args: Array<String>) {
        var passed = 0

        fun case(name: String, block: () -> Unit) {
            block()
            passed += 1
            println("PASS $name")
        }

        case("POST ring route") {
            check(RemoteCommandPolicy.route("POST", "/ring") == RemoteCommandPolicy.Route.RING)
        }
        case("POST notify route") {
            check(RemoteCommandPolicy.route("post", "/notify") == RemoteCommandPolicy.Route.NOTIFY)
        }
        case("query does not change route") {
            check(RemoteCommandPolicy.route("POST", "/ring?source=pc") == RemoteCommandPolicy.Route.RING)
        }
        case("ring prefix is not a route") {
            check(RemoteCommandPolicy.route("POST", "/ringtone") == RemoteCommandPolicy.Route.OTHER)
        }
        case("GET ring rejected") {
            check(RemoteCommandPolicy.route("GET", "/ring") == RemoteCommandPolicy.Route.METHOD_NOT_ALLOWED)
        }
        case("missing token rejected") {
            check(!RemoteCommandPolicy.isAuthorized(null, "secret"))
        }
        case("substring token rejected") {
            check(!RemoteCommandPolicy.isAuthorized("Bearer xxsecretyy", "secret"))
        }
        case("exact bearer token accepted") {
            check(RemoteCommandPolicy.isAuthorized("Bearer secret", "secret"))
        }
        case("ring default") {
            check(RemoteCommandPolicy.ringSeconds(null) == 5)
        }
        case("ring explicit seconds") {
            check(RemoteCommandPolicy.ringSeconds("12") == 12)
        }
        case("ring upper bound") {
            check(RemoteCommandPolicy.ringSeconds("999") == 30)
        }
        case("ring invalid seconds") {
            check(RemoteCommandPolicy.ringSeconds("0") == null)
            check(RemoteCommandPolicy.ringSeconds("nope") == null)
        }

        case("GET notify rejected") {
            check(RemoteCommandPolicy.route("GET", "/notify") == RemoteCommandPolicy.Route.METHOD_NOT_ALLOWED)
        }
        case("blank expected token rejected") {
            check(!RemoteCommandPolicy.isAuthorized("Bearer ", ""))
        }
        case("wrong bearer form rejected") {
            check(!RemoteCommandPolicy.isAuthorized("secret", "secret"))
            check(!RemoteCommandPolicy.isAuthorized("Bearer secret extra", "secret"))
        }
        case("ring boundaries") {
            check(RemoteCommandPolicy.ringSeconds("1") == 1)
            check(RemoteCommandPolicy.ringSeconds("30") == 30)
            check(RemoteCommandPolicy.ringSeconds("31") == 30)
        }
        case("ring malformed values") {
            for (raw in listOf("-1", "1.5", "true", "", "999999999999999999999")) {
                check(RemoteCommandPolicy.ringSeconds(raw) == null)
            }
        }

        println("RemoteCommandPolicyTest: $passed/17 passed")
    }
}
