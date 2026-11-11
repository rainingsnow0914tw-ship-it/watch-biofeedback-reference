package com.example.watchbridge.phone

/** Pure request policy kept free of Android APIs so the remote routes can be unit-tested offline. */
object RemoteCommandPolicy {
    const val DEFAULT_RING_SECONDS = 5
    const val MAX_RING_SECONDS = 30

    enum class Route {
        RING,
        NOTIFY,
        SAY,
        CONTEXT_CONFIG,
        WATCH_DIAG,
        RELAY_CONFIG,
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
            "/relay_config" -> if (method.equals("POST", ignoreCase = true)) Route.RELAY_CONFIG else Route.METHOD_NOT_ALLOWED
            "/watch_diag" -> Route.WATCH_DIAG
            "/context_config" -> if (method.equals("POST", ignoreCase = true)) Route.CONTEXT_CONFIG else Route.METHOD_NOT_ALLOWED
            else -> Route.OTHER
        }
    }

    /** 錶診斷白名單：唯讀的 GET/POST 都行；會改東西的只收 POST。不在名單裡＝null（拒絕）。 */
    val DIAG_READ = setOf("status", "log")
    val DIAG_ACTION = setOf("restart_collector", "wifi_on", "wifi_off", "adb_on", "adb_keep_on", "adb_keep_off", "send_now")

    fun diagCommand(method: String, target: String): String? {
        val query = target.substringAfter('?', "")
        val raw = query.split('&').firstOrNull { it.startsWith("cmd=") }?.removePrefix("cmd=")
        val cmd = raw ?: "status"   // 沒帶 cmd＝問狀態；帶了但不在名單（含奇怪字元）→ 下面一律 null
        val post = method.equals("POST", ignoreCase = true)
        val get = method.equals("GET", ignoreCase = true)
        return when {
            cmd in DIAG_READ && (get || post) -> cmd
            cmd in DIAG_ACTION && post -> cmd
            else -> null
        }
    }

    fun sayNeedsJson(speak: Boolean, buzz: String, ask: Boolean, wake: Boolean): Boolean =
        !speak || buzz.isNotBlank() || ask || wake

    /** Invalid/non-positive values fail; valid values above the safety ceiling are capped. */
    fun ringSeconds(raw: String?): Int? {
        if (raw == null) return DEFAULT_RING_SECONDS
        val parsed = raw.toIntOrNull() ?: return null
        if (parsed <= 0) return null
        return parsed.coerceAtMost(MAX_RING_SECONDS)
    }
}
