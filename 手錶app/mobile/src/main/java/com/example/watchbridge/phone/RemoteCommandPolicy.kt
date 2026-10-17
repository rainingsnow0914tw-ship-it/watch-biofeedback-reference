package com.example.watchbridge.phone

/** Pure request policy kept free of Android APIs so the remote routes can be unit-tested offline. */
object RemoteCommandPolicy {
    const val DEFAULT_RING_SECONDS = 5
    const val MAX_RING_SECONDS = 30

    enum class Route {
        RING,
        NOTIFY,
        SAY,   // 2026-10-17：POST /say {text} → 轉給手錶唸出來
        METHOD_NOT_ALLOWED,
        OTHER,
    }

    fun isAuthorized(authorization: String?, expectedToken: String): Boolean =
        expectedToken.isNotBlank() && authorization == "Bearer $expectedToken"

    fun route(method: String, target: String): Route {
        val path = target.substringBefore('?')
        return when (path) {
            "/ring" -> if (method.equals("POST", ignoreCase = true)) Route.RING else Route.METHOD_NOT_ALLOWED
            "/notify" -> if (method.equals("POST", ignoreCase = true)) Route.NOTIFY else Route.METHOD_NOT_ALLOWED
            "/say" -> if (method.equals("POST", ignoreCase = true)) Route.SAY else Route.METHOD_NOT_ALLOWED
            else -> Route.OTHER
        }
    }

    /** Invalid/non-positive values fail; valid values above the safety ceiling are capped. */
    fun ringSeconds(raw: String?): Int? {
        if (raw == null) return DEFAULT_RING_SECONDS
        val parsed = raw.toIntOrNull() ?: return null
        if (parsed <= 0) return null
        return parsed.coerceAtMost(MAX_RING_SECONDS)
    }
}
