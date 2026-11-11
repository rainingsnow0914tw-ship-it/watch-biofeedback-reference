package com.example.watchbridge

object SayPolicyTest {
    @JvmStatic
    fun main(args: Array<String>) {
        var passed = 0
        fun case(name: String, block: () -> Unit) { block(); passed += 1; println("PASS $name") }

        // 一般訊息：照舊
        case("normal speak, nothing quiet -> speak") { check(SayPolicy.shouldSpeak(true, false, false, false)) }
        case("normal speak, app quiet -> text only") { check(!SayPolicy.shouldSpeak(true, false, true, false)) }
        case("normal speak, system DND/sleep -> text only") { check(!SayPolicy.shouldSpeak(true, false, false, true)) }
        case("speak=false stays silent") { check(!SayPolicy.shouldSpeak(false, false, false, false)) }
        case("wake + system DND/sleep -> speak") { check(SayPolicy.shouldSpeak(true, true, false, true)) }
        case("wake + app quiet -> speak") { check(SayPolicy.shouldSpeak(true, true, true, false)) }
        case("wake + both quiet -> speak") { check(SayPolicy.shouldSpeak(true, true, true, true)) }
        case("wake even if speak=false -> speak") { check(SayPolicy.shouldSpeak(false, true, true, true)) }
        // 震動
        case("wake pattern is strongest") {
            check(SayPolicy.pattern("", true, true).contentEquals(SayPolicy.WAKE_PATTERN))
            check(SayPolicy.pattern("strong", true, false).contentEquals(SayPolicy.WAKE_PATTERN))
            check(SayPolicy.WAKE_PATTERN.sum() > SayPolicy.STRONG_PATTERN.sum())
        }
        case("non-wake patterns unchanged") {
            check(SayPolicy.pattern("strong", false, true).contentEquals(longArrayOf(0, 400, 120, 400, 120, 400, 120, 800)))
            check(SayPolicy.pattern("", false, true).contentEquals(longArrayOf(0, 80, 80, 80)))
            check(SayPolicy.pattern("", false, false).contentEquals(longArrayOf(0, 140, 90, 140, 90, 220)))
        }
        case("wake uses full amplitude + alarm usage") {
            check(SayPolicy.fullAmplitude("", true)); check(SayPolicy.alarmUsage(true))
        }
        case("normal messages keep old vibration category") {
            check(!SayPolicy.alarmUsage(false)); check(!SayPolicy.fullAmplitude("", false)); check(SayPolicy.fullAmplitude("strong", false))
        }
        println("ALL $passed PASSED")
    }
}
