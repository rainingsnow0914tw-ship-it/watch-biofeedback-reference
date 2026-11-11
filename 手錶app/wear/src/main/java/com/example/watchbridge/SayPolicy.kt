package com.example.watchbridge

/**
 *
 * 規則：只有 wake=true 才略過 🤫 安靜模式與勿擾，一定要唸＋強震（USAGE_ALARM，勿擾下也會震）；
 */
object SayPolicy {
    fun shouldSpeak(speakRequested: Boolean, wake: Boolean, appQuiet: Boolean, systemQuiet: Boolean): Boolean =
        wake || (speakRequested && !appQuiet && !systemQuiet)

    val WAKE_PATTERN = longArrayOf(0, 600, 150, 600, 150, 600, 150, 1000)
    val STRONG_PATTERN = longArrayOf(0, 400, 120, 400, 120, 400, 120, 800)
    val SPEAK_PATTERN = longArrayOf(0, 80, 80, 80)
    val TEXT_PATTERN = longArrayOf(0, 140, 90, 140, 90, 220)

    fun pattern(buzz: String, wake: Boolean, willSpeak: Boolean): LongArray = when {
        wake -> WAKE_PATTERN
        buzz == "strong" -> STRONG_PATTERN
        willSpeak -> SPEAK_PATTERN
        else -> TEXT_PATTERN
    }

    fun fullAmplitude(buzz: String, wake: Boolean) = wake || buzz == "strong"

    fun alarmUsage(wake: Boolean) = wake
}
