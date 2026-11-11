package com.example.watchbridge

import com.example.watchbridge.VoiceTeachPolicy.Act

object VoiceTeachPolicyTest {
    @JvmStatic
    fun main(args: Array<String>) {
        var passed = 0
        fun expect(text: String, current: String?, want: Act) {
            val got = VoiceTeachPolicy.decide(text, current)
            check(got == want) { "「$text」(current=$current) 要 $want，得到 $got" }
            passed += 1
            println("PASS 「$text」 current=$current -> $want")
        }
        val V = VoiceTeachPolicy.TAG_V

        // 開錄：繁體、簡體、有客套、教你／學一下開頭
        expect("阿寶，我在吹頭髮", null, Act.START)
        expect("我在吹头发", null, Act.START)
        expect("记一下我在马桶上滑手机", null, Act.START)
        expect("記一下我在馬桶上滑手機喔", null, Act.START)
        expect("我現在在摺衣服", null, Act.START)
        expect("我现在在叠衣服", null, Act.START)
        expect("我正在刷牙", null, Act.START)
        expect("教你 這個是刷牙", null, Act.START)
        expect("学一下 我在拖地", null, Act.START)
        expect("我在床上滑手機", null, Act.START)
        // 不理：問句
        expect("我在吹頭髮嗎", null, Act.IGNORE)
        expect("你猜我在幹嘛？", null, Act.IGNORE)
        expect("我在坐著還是躺著", null, Act.IGNORE)
        expect("好了嗎", V, Act.IGNORE)                    // 問句不停錄
        // 不理：只講地點／在想
        expect("我在家", null, Act.IGNORE)
        expect("我在家啦", null, Act.IGNORE)
        expect("阿寶，我在公司了", null, Act.IGNORE)
        expect("我在诊所", null, Act.IGNORE)
        expect("我在路上喔", null, Act.IGNORE)
        expect("我在想晚餐要吃什麼", null, Act.IGNORE)
        expect("我在等你回我", null, Act.IGNORE)
        expect("我在", null, Act.IGNORE)
        // 不理：一般聊天
        expect("今天好累", null, Act.IGNORE)
        expect("", null, Act.IGNORE)
        // 停錄：只在 voice 錄音中、短句
        expect("好了", V, Act.STOP)
        expect("吹完了", V, Act.STOP)
        expect("结束", V, Act.STOP)
        expect("做完啦", V, Act.STOP)
        expect("好了", null, Act.IGNORE)                   // 沒在錄就不用停
        expect("我覺得今天工作好了很多但是還是很累", V, Act.IGNORE)
        expect("我在刷牙", V, Act.START)
        // 錄動作頁正在錄別的：一律不搶
        expect("我在吹頭髮", "phone", Act.IGNORE)
        expect("好了", "drink", Act.IGNORE)
        println("ALL $passed PASSED")
    }
}
