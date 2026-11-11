package com.example.watchbridge.phone

import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 *
 *
 * **兩邊互相污染之後，對得再準都證明不了任何事。**
 *
 * 所以每一筆都記 `blind`：
 *
 * **只有 blind=true 的筆記可以拿來當驗證用。**
 * blind=false 的照樣存 —— 那是有用的紀錄，只是不能當證據。
 * 這個欄位不是道德判斷，是**證據等級**。
 *
 * ## 為什麼不能改
 *
 * 檔案只 append。改過的筆記沒有驗證價值 ——
 * 不是因為誰不誠實，而是人的記憶本來就會被新資訊重寫。
 * 唯一的防法是**寫下去就不能動**。
 *
 *
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
