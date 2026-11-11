package com.example.watchbridge.phone

import android.content.Context
import android.util.Log
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit

/**
 * 從 Health Connect 讀**三星自己判好的睡眠**。
 *
 *
 * ### 一、我們自己的睡眠偵測是壞的
 *
 *
 * ```
 * 只看『不動』     最長撐 9~34 分
 * 只看『心率低』   最長撐 2~3 分   ← 真兇：逐分鐘中位數抖太兇
 * ```
 *
 * 那是訊號特性，不是生理事實。而三星那邊有完整的睡眠階段（深眠、REM 都有），
 *
 * ### 二、這兩顆按鈕本來就該退休
 *
 *
 *
 * 這個洞不補，將來在他身上會裂得更大。
 *
 * ### 三、為什麼不繼續借第三方的 app
 *
 * **而每日健康檢查全程綠燈** —— 因為那支只驗了手錶那條管路。
 *
 *   兩支拉資料的程式共用 `生理資料/puller_config.json` 的**同一個 `token` 欄位**。
 *   **同一個坑三天內踩兩次，方向相反。** 兩把鑰匙現在放兩個檔案。
 *
 * 就算如此，自己讀還是對的：中間少一個第三方，就少一種安靜壞掉的方式。
 *
 * > 🔴 一個系統有兩條管路，健康檢查只查一條，就等於沒查。
 *
 * 中間少一個會自己換鎖的人，就少一種安靜壞掉的方式。
 *
 *
 * 這條跟 Reconcile 的規矩二是同一條。下面每個失敗分支都帶自己的原因字串，
 * **絕不回一個空陣列讓上層自己想像**。
 */
object HealthConnect {

    private const val TAG = "HC"

    /** 我們要的權限。少一個就少一種資料，而且 Health Connect 是**安靜地**少。 */
    val 要的權限: Set<String> = setOf(
        HealthPermission.getReadPermission(SleepSessionRecord::class),
    )

    /** Health Connect 這台機器現在到底能不能用。**三種狀態要分開講。** */
    sealed class 狀態 {
        object 可以 : 狀態()
        data class 不行(val 為什麼: String, val 怎麼修: String) : 狀態()
    }

    fun 檢查(ctx: Context): 狀態 = when (HealthConnectClient.getSdkStatus(ctx)) {
        HealthConnectClient.SDK_AVAILABLE -> 狀態.可以
        HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED ->
            狀態.不行("Health Connect 版本太舊", "去 Play 商店更新 Health Connect")
        else ->
            狀態.不行("這台手機沒有 Health Connect", "Android 14 以上內建；以下要裝 Health Connect app")
    }

    private fun client(ctx: Context): HealthConnectClient? =
        runCatching { HealthConnectClient.getOrCreate(ctx) }
            .onFailure { Log.w(TAG, "拿不到 client：${it.message}") }
            .getOrNull()

    /** 已經被授權的權限有哪些。**沒授權跟沒資料是兩件事，不可以混。** */
    suspend fun 授權了嗎(ctx: Context): Set<String> {
        val c = client(ctx) ?: return emptySet()
        return runCatching { c.permissionController.getGrantedPermissions() }
            .onFailure { Log.w(TAG, "查權限失敗：${it.message}") }
            .getOrDefault(emptySet())
    }

    /**
     * 讀最近 [days] 天的睡眠，整理成 JSON。
     *
     * 回傳一定含 `ok` 跟 `why`：
     *   · `ok=true`  → `sessions` 是真的讀到的（可能是空的，那就是真的沒有記錄）
     */
    suspend fun 睡眠(ctx: Context, days: Int = 7): JSONObject {
        val out = JSONObject()

        when (val s = 檢查(ctx)) {
            is 狀態.不行 -> return out.put("ok", false)
                .put("why", s.為什麼).put("fix", s.怎麼修)
            else -> Unit
        }
        val c = client(ctx) ?: return out.put("ok", false).put("why", "拿不到 HealthConnectClient")

        val granted = 授權了嗎(ctx)
        val 缺 = 要的權限 - granted
        if (缺.isNotEmpty()) {
            // ⚠ 這裡**刻意不回空陣列**。回空陣列的話，
            return out.put("ok", false)
                .put("why", "沒有讀取睡眠的授權")
                .put("missing", JSONArray(缺.toList()))
                .put("fix", "adb shell pm grant com.example.watchbridge android.permission.health.READ_SLEEP")
        }

        val 到 = Instant.now()
        val 從 = 到.minus(days.toLong(), ChronoUnit.DAYS)
        val recs = runCatching {
            c.readRecords(
                ReadRecordsRequest(
                    recordType = SleepSessionRecord::class,
                    timeRangeFilter = TimeRangeFilter.between(從, 到),
                )
            ).records
        }.getOrElse {
            Log.w(TAG, "讀睡眠失敗：${it.message}")
            return out.put("ok", false).put("why", "讀取時出錯：${it.message}")
        }

        val zone = ZoneId.systemDefault()
        // ⚠ **不要用 ZonedDateTime.toString()。**
        //   Python 的 datetime.fromisoformat 直接 ValueError。
        //   **壞在跨語言的邊界上**，而邊界永遠是最少人看的地方。
        val iso = DateTimeFormatter.ISO_OFFSET_DATE_TIME
        val arr = JSONArray()
        for (r in recs) {
            val 階段 = JSONArray()
            var 醒著秒 = 0L
            for (st in r.stages) {
                val 秒 = ChronoUnit.SECONDS.between(st.startTime, st.endTime)
                // stage 1 = AWAKE，4/5/6 = 淺/深/REM。醒著的時間不算進實睡。
                if (st.stage == SleepSessionRecord.STAGE_TYPE_AWAKE ||
                    st.stage == SleepSessionRecord.STAGE_TYPE_AWAKE_IN_BED
                ) 醒著秒 += 秒
                階段.put(
                    JSONObject()
                        .put("stage", st.stage)
                        .put("start", ZonedDateTime.ofInstant(st.startTime, zone).format(iso))
                        .put("seconds", 秒)
                )
            }
            val 全長 = ChronoUnit.SECONDS.between(r.startTime, r.endTime)
            arr.put(
                JSONObject()
                    .put("start", ZonedDateTime.ofInstant(r.startTime, zone).format(iso))
                    .put("end", ZonedDateTime.ofInstant(r.endTime, zone).format(iso))
                    .put("total_seconds", 全長)
                    .put("asleep_seconds", 全長 - 醒著秒)
                    .put("awake_seconds", 醒著秒)
                    .put("source", r.metadata.dataOrigin.packageName)
                    .put("stages", 階段)
            )
        }
        return out.put("ok", true).put("days", days).put("count", arr.length())
            .put("sessions", arr)
    }

    /**
     */
    fun 一句話(o: JSONObject): String {
        if (!o.optBoolean("ok")) return "⚠ 讀不到睡眠：${o.optString("why")}"
        val n = o.optInt("count")
        if (n == 0) return "○ 這幾天沒有睡眠記錄。**這是「沒記到」，不是「沒睡」。**"
        val ss = o.optJSONArray("sessions") ?: return "○ 沒有睡眠記錄"
        val last = ss.optJSONObject(ss.length() - 1) ?: return "○ 沒有睡眠記錄"
        val 秒 = last.optLong("asleep_seconds")
        return "✅ 最後一段：${last.optString("start").substring(5, 16)} 起，實睡 ${秒 / 3600}h${秒 % 3600 / 60}m（共 $n 段）"
    }
}
