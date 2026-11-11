package com.example.watchbridge

/**
 *
 *
 *
 */
object WalkPolicy {
    const val WALK_ACTIVITY = 400.0
    const val WALK_STILL_MAX = 0.25

    fun isWalkingMinute(activity: Double, stillRatio: Double): Boolean =
        !activity.isNaN() && !stillRatio.isNaN() && activity >= WALK_ACTIVITY && stillRatio <= WALK_STILL_MAX

    // ── ① 計步器停擺 ──
    const val STALL_WALK_MIN = 5
    const val STALL_REREG_GAP_MS = 30 * 60_000L

    /**
     * walkStreak＝連續幾分鐘像走路；stepsSinceStreakStart＝這段期間計步器增加了幾步（沒收到事件＝0）。
     * sensorPresent＝這支錶有 TYPE_STEP_COUNTER；permitted＝ACTIVITY_RECOGNITION 有給（沒給的話停擺是權限問題，不是感測器）。
     */
    fun isStepStall(walkStreak: Int, stepsSinceStreakStart: Long, sensorPresent: Boolean, permitted: Boolean): Boolean =
        sensorPresent && permitted && walkStreak >= STALL_WALK_MIN && stepsSinceStreakStart <= 0

    fun shouldReregister(stall: Boolean, sinceLastReregMs: Long): Boolean =
        stall && sinceLastReregMs >= STALL_REREG_GAP_MS

    // ── ② 忘了帶手機 ──
    const val PHONE_AWAY_MS = 2 * 60_000L
    const val RECENT_WALK_MIN = 2   // 最近 3 分鐘裡至少 2 分鐘像走路

    fun shouldAlertPhoneLeft(
        phoneAwayMs: Long, recentWalkMinutes: Int, onWifi: Boolean, worn: Boolean, charging: Boolean,
        alreadyAlertedThisDisconnect: Boolean,
    ): Boolean =
        phoneAwayMs >= PHONE_AWAY_MS && recentWalkMinutes >= RECENT_WALK_MIN &&
            !onWifi && worn && !charging && !alreadyAlertedThisDisconnect
}
