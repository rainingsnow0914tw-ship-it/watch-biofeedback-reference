package com.example.watchbridge.phone

import java.util.Calendar
import java.util.TimeZone

/**
 * 純函式（不碰 Android），離線測試在 src/test。
 *
 * 資料不夠就沒有時段 → 不響（不瞎猜）。
 *
 * 響不響：現在人在 A、到了提醒時間後 25 分鐘內、錶最後一筆是 30 分鐘內而且在充電或脫下、今天這個時段還沒響過。
 * 錶的資料太舊（不知道錶在幹嘛）→ 不響。
 */
object DeparturePolicy {
    data class Ev(val t: Long, val kind: String, val ip: String?)
    data class Departure(val t: Long, val from: String, val to: String)
    data class Slot(
        val from: String, val to: String,
        val medianMin: Int, val remindMin: Int, val earliestMin: Int, val latestMin: Int,
        val days: Int, val weekdays: Set<Int>, val n: Int,
    )

    const val MIN_TRIP_MS = 5 * 60_000L
    const val MAX_TRIP_MS = 90 * 60_000L
    const val LOOKBACK_DAYS = 14
    const val MIN_DAYS = 3
    const val GAP_MIN = 45
    const val LEAD_MIN = 10
    const val WINDOW_MIN = 25
    const val WATCH_FRESH_MIN = 30

    fun placeOf(ip: String?, prefixes: List<String>): String? =
        if (ip == null) null else prefixes.firstOrNull { it.isNotEmpty() && ip.startsWith(it) }

    /** events 要照時間排好。 */
    fun departures(events: List<Ev>, prefixes: List<String>): List<Departure> {
        val out = mutableListOf<Departure>()
        var at: String? = null
        for ((i, e) in events.withIndex()) {
            if (e.kind == "away") {
                val from = at ?: continue
                val next = events.subList(i + 1, events.size).firstOrNull { it.kind == "home" && placeOf(it.ip, prefixes) != null }
                if (next != null) {
                    val to = placeOf(next.ip, prefixes)!!
                    val dt = next.t - e.t
                    if (to != from && dt in MIN_TRIP_MS..MAX_TRIP_MS) out += Departure(e.t, from, to)
                }
                continue
            }
            placeOf(e.ip, prefixes)?.let { at = it }
        }
        return out
    }

    fun minuteOfDay(t: Long, tz: TimeZone): Int =
        Calendar.getInstance(tz).apply { timeInMillis = t }.let { it.get(Calendar.HOUR_OF_DAY) * 60 + it.get(Calendar.MINUTE) }
    fun weekday(t: Long, tz: TimeZone): Int = Calendar.getInstance(tz).apply { timeInMillis = t }.get(Calendar.DAY_OF_WEEK)
    fun dayKey(t: Long, tz: TimeZone): String =
        Calendar.getInstance(tz).apply { timeInMillis = t }.let { "%04d-%02d-%02d".format(it.get(Calendar.YEAR), it.get(Calendar.MONTH) + 1, it.get(Calendar.DAY_OF_MONTH)) }

    fun learn(deps: List<Departure>, nowMs: Long, tz: TimeZone): List<Slot> {
        val since = nowMs - LOOKBACK_DAYS * 86_400_000L
        return deps.filter { it.t in since..nowMs }.groupBy { it.from to it.to }.flatMap { (route, ds) ->
            // 依一天中的分鐘排序，切群；頭尾跨午夜的兩群合併
            val sorted = ds.map { minuteOfDay(it.t, tz) to it }.sortedBy { it.first }
            val groups = mutableListOf<MutableList<Pair<Int, Departure>>>()
            for (p in sorted) {
                val g = groups.lastOrNull()
                if (g != null && p.first - g.last().first <= GAP_MIN) g += p else groups += mutableListOf(p)
            }
            if (groups.size >= 2 && groups.first().first().first + 1440 - groups.last().last().first <= GAP_MIN) {
                val head = groups.removeAt(0)
                groups.last() += head.map { (m, d) -> (m + 1440) to d }
            }
            groups.mapNotNull { g ->
                val days = g.map { dayKey(it.second.t, tz) }.toSet().size
                if (days < MIN_DAYS) return@mapNotNull null
                val ms = g.map { it.first }.sorted()
                val med = ms[ms.size / 2]
                val p25 = ms[(ms.size - 1) / 4]
                Slot(route.first, route.second,
                    medianMin = med % 1440, remindMin = Math.floorMod(p25 - LEAD_MIN, 1440),
                    earliestMin = ms.first() % 1440, latestMin = ms.last() % 1440,
                    days = days, weekdays = g.map { weekday(it.second.t, tz) }.toSet(), n = g.size)
            }
        }.sortedBy { it.remindMin }
    }

    data class Watch(val ageMin: Long, val charging: Boolean, val offBody: Boolean)

    /** 回這次要用的去重 key（要響），或 null（不響）。 */
    fun shouldRemind(slot: Slot, nowMs: Long, tz: TimeZone, herPlace: String?, watch: Watch?, alreadySent: Set<String>): String? {
        if (herPlace != slot.from) return null
        val m = minuteOfDay(nowMs, tz)
        if (Math.floorMod(m - slot.remindMin, 1440) >= WINDOW_MIN) return null
        if (slot.weekdays.size < 5 && weekday(nowMs, tz) !in slot.weekdays) return null
        if (watch == null || watch.ageMin !in 0..WATCH_FRESH_MIN.toLong()) return null
        if (!watch.charging && !watch.offBody) return null
        val key = "dep|${slot.from}|${slot.to}|${slot.medianMin / 60}|${dayKey(nowMs, tz)}"
        return if (key in alreadySent) null else key
    }

    fun hhmm(min: Int) = "%02d:%02d".format(min / 60, min % 60)
}
