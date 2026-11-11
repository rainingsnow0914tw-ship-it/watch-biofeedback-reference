package com.example.watchbridge

/**
 *
 *  ② 裝新版後 READ_HEALTH_DATA_IN_BACKGROUND／BODY_SENSORS_BACKGROUND 被收回，服務又是背景啟動 → 沒有前景身分，
 *     Health Services 的心率掛號（只在 onCreate 做一次）就一直拿不到東西，而且**完全沒有任何紀錄**。
 *  所以：重掛 HS 不能只在 onCreate；權限缺了要大聲寫進資料（perm_missing），不能再無聲歸零。
 */
object HrRefreshPolicy {
    /** 心率要用到的權限。少任何一個，背景就可能收不到。 */
    val NEEDED = listOf(
        "android.permission.BODY_SENSORS",
        "android.permission.BODY_SENSORS_BACKGROUND",
        "android.permission.health.READ_HEART_RATE",
        "android.permission.health.READ_HEALTH_DATA_IN_BACKGROUND",
    )

    /** 缺哪些（照 NEEDED 的順序）。查不到的當缺。 */
    fun missing(granted: Map<String, Boolean>): List<String> = NEEDED.filter { granted[it] != true }

    const val PERM_REPORT_EVERY_MS = 60 * 60_000L

    fun shouldReportPerms(now: List<String>, lastReported: List<String>?, lastReportAt: Long, nowMs: Long): Boolean = when {
        lastReported == null -> now.isNotEmpty()
        now != lastReported -> true
        now.isEmpty() -> false
        else -> nowMs - lastReportAt >= PERM_REPORT_EVERY_MS
    }

    const val REREGISTER_MIN_GAP_MS = 2 * 60_000L

    /**
     * 要不要把 Health Services 的心率掛號拆掉重掛。
     * - remote：一律重掛（是人下的指令）。
     * - watchdog：跟 open 一樣。
     */
    fun shouldReregister(why: String, hrAgeMs: Long, sinceLastReregMs: Long): Boolean = when (why) {
        "remote" -> true
        else -> hrAgeMs > REREGISTER_MIN_GAP_MS && sinceLastReregMs >= REREGISTER_MIN_GAP_MS
    }
}
