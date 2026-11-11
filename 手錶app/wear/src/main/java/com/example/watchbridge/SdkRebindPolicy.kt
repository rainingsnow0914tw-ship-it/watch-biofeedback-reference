package com.example.watchbridge

/**
 *
 * 證據：同一天兩次重裝 ——
 *            onConnectionSuccess／Failed 都沒回）→ 手動 restart_collector（＝重 connectService）23 秒後心率回來。
 * 每裝一次版本服務就重啟一次，不能每次都靠人盯。
 *
 * ⚠ 觸發不能靠動作窗口那個看門狗：動作（加速度）本身就是三星 SDK 給的，SDK 沒連上它根本不會跑。
 */
object SdkRebindPolicy {
    const val FIRST_AFTER_MS = 3 * 60_000L
    const val GAP_MS = 3 * 60_000L
    const val MAX_TRIES = 3

    fun shouldRebind(connected: Boolean, sinceStartMs: Long, sinceLastTryMs: Long, tries: Int): Boolean =
        !connected && tries < MAX_TRIES && sinceStartMs >= FIRST_AFTER_MS && sinceLastTryMs >= GAP_MS

    // ── code104：資料流停擺（SDK 連著、但什麼都不送）就重掛 ──
    //   init 會把它重開，但我們掛的 tracker 不會自己回來：動作、心率、皮溫全停 11 分鐘，直到人下 restart_collector。
    const val STALL_MS = 5 * 60_000L
    const val STALL_GAP_MS = 5 * 60_000L

    /** streamAgeMs＝三星資料流（加速度，正常每分鐘都有）多久沒進來。充電中照樣該有，所以不排除充電。 */
    fun shouldKickStall(connected: Boolean, streamAgeMs: Long, sinceLastKickMs: Long): Boolean =
        connected && streamAgeMs >= STALL_MS && sinceLastKickMs >= STALL_GAP_MS
}
