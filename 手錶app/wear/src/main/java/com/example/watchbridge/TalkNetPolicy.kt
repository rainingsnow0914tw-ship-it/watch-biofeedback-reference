package com.example.watchbridge

/**
 *
 * 兩個原因疊在一起：
 *
 * 新規則：看當下有沒有藍牙這條路。
 *   沒藍牙（手機不在）  → 不放 Wi-Fi，主動要 Wi-Fi 並等它接上再聽（WAIT_WIFI），最多等 WAIT_MAX_MS，最多 MAX_WAITS 輪。
 */
object TalkNetPolicy {
    const val WAIT_MAX_MS = 30_000L
    const val MAX_WAITS = 2

    enum class Act { BLUETOOTH, WAIT_WIFI, GIVE_UP }

    /** net＝TalkLog.netNow 的字串（"none"、"wifi"、"wifi+未驗證"、"bluetooth"…）；waits＝這次按下去已經等過幾輪 Wi-Fi。 */
    fun onOffline(net: String, waits: Int): Act = when {
        net.contains("bluetooth") -> Act.BLUETOOTH
        waits < MAX_WAITS -> Act.WAIT_WIFI
        else -> Act.GIVE_UP
    }

    /** 網路能用了嗎：有任何一條路而且驗證過（未驗證＝剛連上 AP、還上不了網）。 */
    fun ready(net: String): Boolean = net != "none" && net != "讀不到" && !net.contains("未驗證")

    fun timedOut(waitedMs: Long): Boolean = waitedMs >= WAIT_MAX_MS
}
