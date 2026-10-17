package com.example.watchbridge

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * 開機後自動把收集服務叫起來。
 *
 * 為什麼非有不可（2026-08-15 稽核查出來的漏洞）：
 *     在這之前，**手錶只要重開機一次，收集就永遠停在那裡** ——
 *     一直到 使用者 自己想到要去點開 app 為止。
 *
 *     而這是最壞的一種壞法：**完全看不出來**。
 *     錶面正常、app 圖示在、電量正常、沒有任何錯誤訊息，
 *     手機那頭只是「今天的檔案沒有長大」—— 而沒有人會盯著檔案大小。
 *
 *     重開機不是罕見事件：
 *       · 系統更新（三星會自己排）
 *       · 電池耗盡後重新充電
 *       · 卡住時手動重開（我自己就叫她重開過一次，為了清掉血氧的 session）
 *
 *     `START_STICKY` 擋不住這個 —— 那個只管「服務被系統殺掉」，
 *     重開機之後整個行程根本不存在，沒有東西可以 sticky。
 *
 * ⚠️ 這個接收器**不保證一定會被叫到**：
 *     Android 從 3.1 起，app 安裝後如果從來沒被打開過，
 *     會停在 stopped state，收不到任何廣播。
 *     所以裝完之後**一定要手動開一次 app**，這條路才會生效。
 *     （**沒有第二道防線。** 2026-08-16 稽核查證：我曾經在這裡寫
       「手機端另外有心跳監看」—— **那個東西不存在，是我自己編的。**
       電腦端的 hc_puller.py 確實有 staleness 警報，但它盯的是
       Samsung Health 那條管線（port 8787），完全不碰手錶橋接這條。
       所以這個接收器沒被叫到的時候，**沒有任何東西會發現**。
       補一個真的監看是待辦事項，在那之前不要再假裝它存在。）
 *
 * 為什麼順便收 MY_PACKAGE_REPLACED：
 *     app 更新之後系統也會把行程殺掉，跟重開機同一種病。
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(ctx: Context, intent: Intent) {
        val act = intent.action ?: return
        if (act != Intent.ACTION_BOOT_COMPLETED &&
            act != Intent.ACTION_MY_PACKAGE_REPLACED
        ) return

        Log.i(TAG, "收到 $act，重新啟動收集服務")
        runCatching {
            // 一定要用 startForegroundService —— 開機當下 app 不在前景，
            // 用 startService 會被 Android 8 以上直接擋掉。
            ctx.startForegroundService(Intent(ctx, CollectorService::class.java))
        }.onFailure {
            // 這裡失敗最可能的原因是開機瞬間系統還在忙。
            //
            // ⚠ 這裡本來寫「不做重試 —— 手機端的心跳監看會發現」。
            //   **那個心跳監看不存在，是我編的**（2026-08-16 稽核抓到）。
            //   現在誠實講：失敗就是失敗了，沒有人會發現。
            //   之所以還是不做重試迴圈，是因為在 BroadcastReceiver 裡睡覺會被系統砍，
            //   要做得對必須用 WorkManager —— 那是待辦，不是已經有的東西。
            Log.e(TAG, "開機啟動失敗", it)
        }
    }

    companion object {
        const val TAG = "WatchBridge"
    }
}
