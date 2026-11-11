package com.example.watchbridge;

/** Pure time policy for a user-marked off-wrist event. No sensor value is fabricated. */
public final class UserOffPolicy {
    public static final long MAX_AGE_MS = 12L * 60L * 60L * 1000L;

    private UserOffPolicy() {}

    public static long restore(long savedAt, long now) {
        if (savedAt <= 0 || savedAt > now || now - savedAt > MAX_AGE_MS) {
            return 0L;
        }
        return savedAt;
    }

    public static boolean isExplicitlyOff(long savedAt, long lastPulseAt, long now) {
        long offAt = restore(savedAt, now);
        return offAt > 0 && lastPulseAt <= offAt;
    }
}
