package com.example.watchbridge.phone

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 手機端的畫面。
 *
 * ## 2026-08-16 大改：從「狀態顯示器」變成「核對台」
 *
 * 使用者 的原話：「你最需要的是做任何東西都要讓我有能夠核查的機制…
 * 我們要對一堆是否正確，**減少幻覺**。」
 *
 * ## 畫面的順序就是驗證的順序，不能調
 *
 *   ① 先寫妳記得的
 *   ② 存檔（存了不能改）
 *   ③ **然後才**顯示數據怎麼說
 *
 * 「先記後看」是這整個機制唯一的價值來源。
 * 反過來就只是「拿數據餵給記憶」，對得再準都不算驗證。
 *
 * 所以數據那一段預設是收起來的，而且**一旦展開，這一天之後寫的筆記
 * 全部標記成 blind=false**（照樣存，但不能當證據）。
 *
 * 這個限制刻意做得沒有辦法繞過 —— 包括她自己。
 * 不是不信任她，是**人的記憶被新資訊重寫這件事不受意志控制**。
 */
class MainActivity : Activity() {

    private lateinit var box: LinearLayout
    private lateinit var dataDir: File
    private var revealedToday = false
    private var bridgeStartupError: String? = null

    private val prefs by lazy { getSharedPreferences("reconcile", MODE_PRIVATE) }

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        dataDir = File(filesDir, "手錶資料").apply { mkdirs() }
        revealedToday = prefs.getString("revealed_day", "") == NotesStore.today()

        box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 64, 48, 96)
        }
        setContentView(ScrollView(this).apply {
            setBackgroundColor(BG); addView(box)
        })

        val missing = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) missing += Manifest.permission.POST_NOTIFICATIONS
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED
        ) missing += Manifest.permission.BLUETOOTH_CONNECT
        if (missing.isNotEmpty()) requestPermissions(missing.toTypedArray(), 1)
        startBridgeWithGrantedPermissions()
    }

    override fun onResume() {
        super.onResume()
        startBridgeWithGrantedPermissions()
        rebuild()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1) {
            startBridgeWithGrantedPermissions()
            rebuild()
        }
    }

    private fun startBridgeWithGrantedPermissions() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED
        ) {
            bridgeStartupError = "橋接尚未啟動：請允許附近裝置權限，再回到 app。"
            return
        }
        runCatching { BridgeServer.start(this) }
            .onSuccess { bridgeStartupError = null }
            .onFailure {
                bridgeStartupError = "橋接啟動失敗（${it.javaClass.simpleName}）：請重新開啟 app。"
            }
    }

    // ── 畫面 ────────────────────────────────────────────────────
    private fun rebuild() {
        box.removeAllViews()
        val day = NotesStore.today()

        title("阿寶橋接")
        small("port ${BridgeServer.PORT}　${statusLine()}")
        bridgeStartupError?.let { small(it) }

        // ⓪ 機器現在有沒有在工作
        //
        // ⚠ **這一段刻意放在盲記錄閘門的外面。**（2026-08-30 接上）
        //   👤 使用者 08-26 04:55 問：「為什麼戴在手上沒有心率呢？
        //   你不是在手機上寫了一個監測程式嗎？」——手錶那邊心率好得不能再好，
        //   她看不到是因為「② 數據怎麼說」預設收起來。
        //
        //   兩個需求本來被同一個開關綁住了：
        //     · 我昨晚睡得好不好、心率多少　→ **要藏**（看過就不能拿來驗證記憶）
        //     · 這台機器現在有沒有在運作　　→ **不該藏**（跟她的記憶無關）
        //
        //   所以 Health 只回「感測器活不活」，一個生理數值都不講。
        //   「感測器活著」是關於機器的事實，「心率 93」是關於她的事實。
        header("⓪ 機器現在的狀況")
        Health.rows(dataDir, day).forEach { r ->
            fact(Reconcile.Fact(r.標題, r.內容))
        }

        // ① 記一筆
        header("① 先寫妳記得的")
        body("幾點睡、幾點醒、什麼時候運動、有沒有哪裡不舒服。\n" +
             "**寫具體的時刻比寫感覺有用十倍** —— 「3:15 起來上廁所」對得上，「睡不好」對不上。")

        val input = EditText(this).apply {
            hint = "例：23:40 上床　03:15 起來上廁所　06:20 醒　下午兩點爬了六樓"
            setHintTextColor(Color.parseColor("#666666"))
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.parseColor("#161A19"))
            inputType = InputType.TYPE_CLASS_TEXT or
                InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            minLines = 3
            setPadding(28, 28, 28, 28)
            textSize = 15f
        }
        box.addView(input, lp(topMargin = 16))

        button(if (revealedToday) "存起來（今天已看過數據，不算驗證）" else "存起來（存了不能改）") {
            val t = input.text.toString().trim()
            if (t.isEmpty()) { toast("還沒寫東西"); return@button }
            NotesStore.add(dataDir, day, t, blind = !revealedToday)
            input.setText("")
            toast(if (revealedToday) "存好了（標記為非盲）" else "存好了 ✓ 盲記錄")
            rebuild()
        }

        // ② 數據
        header("② 數據怎麼說")
        if (!revealedToday) {
            body("**還沒展開。** 展開之後，今天之後寫的筆記都會被標成「非盲」——\n" +
                 "不是懲罰，是因為看過數據的記憶已經不能拿來驗證了。")
            button("我記完了，展開今天的數據") {
                prefs.edit().putString("revealed_day", day).apply()
                revealedToday = true
                rebuild()
            }
        } else {
            val facts = Reconcile.facts(dataDir, day)
            if (facts.isEmpty()) body("今天還沒有資料")
            else facts.forEach { fact(it) }
        }

        // ③ 已經記過的
        val notes = NotesStore.forDay(dataDir, day)
        if (notes.isNotEmpty()) {
            header("③ 今天記過的（${notes.size} 筆）")
            notes.reversed().forEach { n ->
                val mark = if (n.blind) "🔒 盲" else "👁 已看過數據"
                fact(Reconcile.Fact("${n.寫的時間.substring(11, 16)}　$mark", n.內容))
            }
        }

        // 檔案狀態
        header("資料檔")
        val files = dataDir.listFiles().orEmpty().filter { it.name.endsWith(".jsonl") }.sortedBy { it.name }
        val fmt = SimpleDateFormat("MM-dd HH:mm", Locale.US)
        small(if (files.isEmpty()) "還沒收到手錶的資料"
              else files.joinToString("\n") {
                  "${it.nameWithoutExtension}　${it.length() / 1024} KB　${fmt.format(Date(it.lastModified()))}"
              })
    }

    private fun statusLine(): String {
        val newest = dataDir.listFiles().orEmpty()
            .filter { it.name.endsWith(".jsonl") }.maxByOrNull { it.lastModified() }
            ?: return "還沒有資料"
        val min = (System.currentTimeMillis() - newest.lastModified()) / 60_000
        return if (min <= 15) "資料 $min 分鐘前更新" else "⚠ 已經 $min 分鐘沒有新資料"
    }

    // ── 小元件 ──────────────────────────────────────────────────
    private fun lp(topMargin: Int = 0) =
        LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { setMargins(0, topMargin, 0, 0) }

    private fun title(s: String) = box.addView(TextView(this).apply {
        text = s; textSize = 24f; setTextColor(ACCENT); gravity = Gravity.CENTER
    })

    private fun header(s: String) = box.addView(TextView(this).apply {
        text = s; textSize = 17f; setTextColor(ACCENT)
        setPadding(0, 56, 0, 12)
    })

    private fun body(s: String) = box.addView(TextView(this).apply {
        text = s.replace("**", ""); textSize = 14f
        setTextColor(Color.parseColor("#B8C4C0")); setLineSpacing(6f, 1f)
    })

    private fun small(s: String) = box.addView(TextView(this).apply {
        text = s; textSize = 12f; setTextColor(Color.parseColor("#7C8985"))
        gravity = Gravity.CENTER; setPadding(0, 8, 0, 0)
    })

    private fun fact(f: Reconcile.Fact) = box.addView(LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setBackgroundColor(Color.parseColor("#121614"))
        setPadding(28, 24, 28, 24)
        addView(TextView(context).apply {
            text = f.標題; textSize = 14f; setTextColor(ACCENT)
        })
        addView(TextView(context).apply {
            text = f.內容.replace("**", ""); textSize = 15f
            setTextColor(Color.WHITE); setPadding(0, 8, 0, 0); setLineSpacing(6f, 1f)
        })
    }, lp(topMargin = 14))

    private fun button(label: String, onClick: () -> Unit) =
        box.addView(Button(this).apply {
            text = label; textSize = 15f
            setTextColor(Color.BLACK); setBackgroundColor(ACCENT)
            setOnClickListener { onClick() }
        }, lp(topMargin = 20))

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    companion object {
        private val BG = Color.parseColor("#0B0F0E")
        private val ACCENT = Color.parseColor("#4FC4AC")
    }
}
