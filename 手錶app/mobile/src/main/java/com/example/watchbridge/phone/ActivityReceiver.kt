package com.example.watchbridge.phone

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.google.android.gms.location.ActivityTransition
import com.google.android.gms.location.ActivityTransitionResult

/**
 *
 * 我們只把它翻成一行 {kind:"activity", state:"walking"} 寫進手機情境 jsonl，其餘交給電腦端判。
 * 不在這裡做任何判斷、不通知、不震。
 */
class ActivityReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (!ActivityTransitionResult.hasResult(intent)) return
        val res = ActivityTransitionResult.extractResult(intent) ?: return
        for (ev in res.transitionEvents) {
            val name = PhoneContext.activityName(ev.activityType)
            val enter = ev.transitionType == ActivityTransition.ACTIVITY_TRANSITION_ENTER
            Log.i("WatchBridgePhone", "活動：" + (if (enter) "進入 " else "離開 ") + name)
            PhoneContext.onActivity(ctx.applicationContext, name, enter)
        }
    }
}
