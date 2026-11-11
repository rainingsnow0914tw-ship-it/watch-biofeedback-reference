package com.example.watchbridge

/**
 *
 *
 * 規則（照順序）：
 *  1. 問句（嗎／？／還是）→ 不理
 *  2. 錄動作頁正在錄別的（current 不是 null 也不是 voice）→ 不理，不搶
 *  3. 有開始詞、而且不是只講地點／在想 → 開錄（正在 voice 也重開：換下一個動作）
 *  4. 正在 voice、短句（≤12 字）有結束詞 → 停錄
 *  5. 其他 → 不理
 */
object VoiceTeachPolicy {
    enum class Act { START, STOP, IGNORE }

    const val TAG_V = "voice"
    const val MAX_MS = 10 * 60_000L

    val START = listOf("我在", "我正在", "我現在在", "我现在在", "教你", "學一下", "学一下", "學這個", "学这个", "學起來", "学起来")
    val END = listOf("好了", "完了", "做完", "結束", "结束", "停了", "停一下", "不錄", "不录")
    private val ASK = listOf("嗎", "吗", "?", "？", "還是", "还是")
    private val PLACES = listOf("家", "家裡", "家里", "診所", "诊所", "公司", "外面", "路上", "醫院", "医院", "車上", "车上", "這裡", "这里", "那裡", "那里")
    private val NOT_MOTION = listOf("想", "等", "猜", "忙", "看你", "聽你", "听你", "問你", "问你")
    private val FILLER = Regex("[，,。.!！~～、\\s啦了喔哦呢耶唷呀啊嘛]+$")
    private val LEAD = Regex("^(阿寶|阿宝|寶寶|宝宝|欸|嘿)?[，,\\s]*(幫我|帮我)?(記一下|记一下)?\\s*")

    fun decide(text: String, current: String?): Act {
        val t = text.trim()
        if (t.isEmpty() || ASK.any { it in t }) return Act.IGNORE
        if (current != null && current != TAG_V) return Act.IGNORE
        if (START.any { it in t } && !onlyPlaceOrThinking(t)) return Act.START
        if (current == TAG_V && t.length <= 12 && END.any { it in t }) return Act.STOP
        return Act.IGNORE
    }

    private fun onlyPlaceOrThinking(t: String): Boolean {
        val body = t.replace(LEAD, "")
        val m = Regex("(我現在在|我现在在|我正在|我在)(.*)").find(body) ?: return false
        val rest = m.groupValues[2].replace(FILLER, "").trim()
        if (rest.isEmpty()) return true
        if (rest in PLACES) return true
        return NOT_MOTION.any { rest.startsWith(it) }
    }
}
