package com.example.watchbridge;

/** Standalone JVM assertions: javac + java, no Android device or JUnit download. */
public final class UserOffPolicyTest {
    private static void check(boolean value, String caseName) {
        if (!value) throw new AssertionError(caseName);
    }

    public static void main(String[] args) {
        long now = 1_800_000_000_000L;
        long recent = now - 60_000L;
        check(UserOffPolicy.restore(recent, now) == recent, "recent mark survives restart");
        check(UserOffPolicy.isExplicitlyOff(recent, 0L, now), "mark outranks no pulse");
        check(!UserOffPolicy.isExplicitlyOff(recent, now - 30_000L, now),
                "later pulse disables mark in running service");
        check(UserOffPolicy.restore(now - UserOffPolicy.MAX_AGE_MS, now)
                == now - UserOffPolicy.MAX_AGE_MS, "boundary remains valid");
        check(UserOffPolicy.restore(now - UserOffPolicy.MAX_AGE_MS - 1L, now) == 0L,
                "expired mark cannot stay forever");
        check(!UserOffPolicy.isExplicitlyOff(now - UserOffPolicy.MAX_AGE_MS - 1L, 0L, now),
                "expired mark is not off evidence");
        check(UserOffPolicy.restore(now + 1L, now) == 0L,
                "future timestamp after clock change is invalid");
        check(UserOffPolicy.restore(0L, now) == 0L, "unset is unknown");
        System.out.println("UserOffPolicyTest 8/8 passed");
    }
}
