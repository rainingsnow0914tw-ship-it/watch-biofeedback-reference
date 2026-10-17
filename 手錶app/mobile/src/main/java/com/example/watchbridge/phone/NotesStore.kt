package com.example.watchbridge.phone

import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 使用者 自己記的那一份 —— 用來跟數據對照的「現實」。
 *
 * ## 為什麼要有「盲」這件事（這一支最重要的設計）
 *
 * 如果她先看到數據再寫筆記，她的記憶會被數據帶著走；
 * 如果我先看到筆記再算數據，我會不自覺地把數據解釋成符合她的說法。
 * **兩邊互相污染之後，對得再準都證明不了任何事。**
 *
 * 所以每一筆都記 `blind`：
 *   true  = 她寫這筆之前，這一天的數據還沒在畫面上展開過
 *   false = 她已經看過數據了
 *
 * **只有 blind=true 的筆記可以拿來當驗證用。**
 * blind=false 的照樣存 —— 那是有用的紀錄，只是不能當證據。
 * 這個欄位不是道德判斷，是**證據等級**。
 *
 * ## 為什麼不能改
 *
 * 檔案只 append。改過的筆記沒有驗證價值 ——
 * 「我本來寫 11 點，看到數據說 11:40，就改成 11:40」這種事，
 * 不是因為誰不誠實，而是人的記憶本來就會被新資訊重寫。
 * 唯一的防法是**寫下去就不能動**。
 *
 * ## 也記「她記錯了」的情況
 *
 * 對不上的時候不預設誰對。她已經被數據打臉過四次（充電、擦頭髮、走十分鐘），
 * 而我也被她打臉過。**兩份都會錯的記錄互相校，才是這個機制的價值。**
 */
object NotesStore {

    const val FILE = "對照筆記.jsonl"

    data class Note(
        val 寫的時間: String,
        val 講的是哪天: String,
        val 內容: String,
        val blind: Boolean,
    )

    private fun stamp() =
        SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).format(Date())

    fun today(): String = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())

    /** 存一筆。只 append，存了就不能改。 */
    fun add(dir: File, day: String, text: String, blind: Boolean) {
        val o = JSONObject().apply {
            put("t", stamp())
            put("day", day)
            put("text", text)
            put("blind", blind)
        }
        File(dir, FILE).appendText(o.toString() + "\n")
    }

    fun all(dir: File): List<Note> {
        val f = File(dir, FILE)
        if (!f.exists()) return emptyList()
        return f.readLines().mapNotNull { line ->
            runCatching {
                val o = JSONObject(line)
                Note(o.getString("t"), o.getString("day"), o.getString("text"),
                     o.optBoolean("blind", false))
            }.getOrNull()
        }
    }

    fun forDay(dir: File, day: String): List<Note> = all(dir).filter { it.講的是哪天 == day }
}
