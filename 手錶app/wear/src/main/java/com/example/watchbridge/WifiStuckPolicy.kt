package com.example.watchbridge

/**
 *
 *
 * 一般 App 在 Android 10+ 不能直接開關 Wi-Fi。能做的：
 *   A. 沒充電時：放掉我們的 Wi-Fi 請求 → 藍牙連著的話 WearWifiMediator 會把 radio 關掉 → 20 秒後再要 → radio 重起
 *
 */
object WifiStuckPolicy {
    const val STUCK_AFTER_MS = 15 * 60_000L
    const val CYCLE_GAP_MS = 15 * 60_000L
    const val MAX_CYCLES = 2
    const val CYCLE_PAUSE_MS = 20_000L

    enum class Act { NONE, CYCLE, NOTIFY }

    fun isStuck(radioOn: Boolean, keepWanted: Boolean, onWifiNetwork: Boolean, noWifiForMs: Long): Boolean =
        radioOn && keepWanted && !onWifiNetwork && noWifiForMs >= STUCK_AFTER_MS

    fun decide(stuck: Boolean, charging: Boolean, cyclesThisEpisode: Int, sinceLastCycleMs: Long, notifiedThisEpisode: Boolean): Act = when {
        !stuck -> Act.NONE
        !charging && cyclesThisEpisode < MAX_CYCLES -> if (sinceLastCycleMs >= CYCLE_GAP_MS) Act.CYCLE else Act.NONE
        !notifiedThisEpisode -> Act.NOTIFY
        else -> Act.NONE
    }
}
