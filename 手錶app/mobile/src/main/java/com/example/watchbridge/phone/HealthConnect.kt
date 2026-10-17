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
 * ## 為什麼要有這支（2026-08-30）
 *
 * ### 一、我們自己的睡眠偵測是壞的
 *
 * 電腦端寫過一個「找安靜＋心率低的連續區塊」的偵測器，八天全部判成「沒睡」。
 * **差一點就跟 👤 使用者 說「妳連續八天沒睡」。** 去量分佈才發現是自己的問題：
 *
 * ```
 * 只看『不動』     最長撐 9~34 分
 * 只看『心率低』   最長撐 2~3 分   ← 真兇：逐分鐘中位數抖太兇
 * ```
 *
 * 那是訊號特性，不是生理事實。而三星那邊有完整的睡眠階段（深眠、REM 都有），
 * **證明她確實戴著睡** —— 錯的是我們的偵測器。
 *
 * ### 二、這兩顆按鈕本來就該退休
 *
 * 手腕上有「睡覺」「起床」兩顆標記鈕。👤 使用者 2026-08-30：
 *
 * > 「我最近有點恍惚，老是忘記壓，要不是就是累到直接睡著，
 * >   不然就起床明明呼呼、沒有按起床。所以這是我的問題。」
 *
 * **不是她的問題。要求一個正在睡著和剛醒來的人按按鈕，是設計錯了。**
 * 那正好是人最按不到的兩個時刻。而**家人將來更按不到** ——
 * 這個洞不補，將來在他身上會裂得更大。
 *
 * ### 三、為什麼不繼續借第三方的 app
 *
 * 本來走「Health Connect Webhook」那支 app 的本機 HTTP，它 08-27 中午起躺了三天，
 * **而每日健康檢查全程綠燈** —— 因為那支只驗了手錶那條管路。
 *
 * ⚠ **真正的原因不是第三方改版，是我自己。**（08-30 查出來，前一版註解寫錯了，已更正）
 *   兩支拉資料的程式共用 `生理資料/puller_config.json` 的**同一個 `token` 欄位**。
 *   08-27 我把橋接的鑰匙輪替、寫進那個欄位，**當場把 HC 的鑰匙蓋掉**。
 *   然後我花了三天把它誤診成「第三方 app 自己換了鑰匙」——
 *   而 08-30 早上我又反方向蓋回去一次，橋接立刻 401。
 *   **同一個坑三天內踩兩次，方向相反。** 兩把鑰匙現在放兩個檔案。
 *
 * 就算如此，自己讀還是對的：中間少一個第三方，就少一種安靜壞掉的方式。
 *
 * > 🔴 一個系統有兩條管路，健康檢查只查一條，就等於沒查。
 *
 * 👤 使用者 自己看出問題：「你以後是要自動把睡眠拉到我們做的 APP 嗎？」——對。
 * 中間少一個會自己換鎖的人，就少一種安靜壞掉的方式。
 *
 * ## ⚠ 讀不到的時候要說「不知道」，不能說「沒睡」
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
     *   · `ok=false` → `why` 說明為什麼讀不到。**上層不准把這種當成「沒睡」。**
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
            //   上層畫出來會是「昨晚沒睡」—— 那是憑空捏造的事實。
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
        //   它會吐 `2026-08-23T13:28+08:00[Asia/Macau]` —— 後面那個方括號是 Java 專有的，
        //   Python 的 datetime.fromisoformat 直接 ValueError。
        //   2026-08-30 第一版就是這樣炸的：Kotlin 那邊看起來很正常，
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
                    // 實睡＝全長扣掉「醒著」的階段。三星的 session 常常含 20~50 分的醒著。
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
     * 給畫面用的一行話。**沒資料時說「不知道」，不說「沒睡」。**
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
