package com.example.watchbridge.phone

import com.example.watchbridge.phone.PhoneSpotPolicy.Reading
import com.example.watchbridge.phone.PhoneSpotPolicy.Spot

object PhoneSpotPolicyTest {
    @JvmStatic
    fun main(args: Array<String>) {
        var passed = 0
        fun case(name: String, want: Spot, r: Reading) {
            val got = PhoneSpotPolicy.classify(r)
            check(got.spot == want) { "$name：要 $want 拿到 ${got.spot}（${got.why}）" }
            passed += 1; println("PASS $name → ${got.spot.zh}（${got.why}）")
        }
        fun rd(near: Boolean? = false, lux: Float? = 200f, g: Triple<Float, Float, Float> = Triple(0f, 0f, 9.8f),
               std: Float = 0.03f, n: Int = 12, call: Boolean = false, screen: Boolean = false) =
            Reading(near, lux, g.first, g.second, g.third, std, n, call, screen)
        val up = Triple(0f, 0f, 9.8f); val down = Triple(0f, 0f, -9.8f)
        val upright = Triple(0f, 9.6f, 1.5f); val tilted45 = Triple(0f, 6.9f, 6.9f)

        case("桌上平放、通知點亮螢幕", Spot.TABLE, rd(g = up, screen = true))
        case("桌上平放、螢幕暗", Spot.TABLE, rd(g = up))
        case("桌上螢幕朝下（距離感測被桌面擋住也算桌上）", Spot.TABLE_DOWN, rd(near = true, lux = 0f, g = down))
        case("口袋：貼著＋暗＋直立微晃", Spot.POCKET, rd(near = true, lux = 1f, g = upright, std = 0.6f))
        case("口袋：貼著＋暗＋不動（坐著）", Spot.POCKET, rd(near = true, lux = 0f, g = upright))
        case("包包：沒距離感測、斜著不動、暗", Spot.POCKET, rd(near = null, lux = 2f, g = tilted45))
        case("拿在手上滑：斜 45°、在晃、螢幕亮", Spot.HAND, rd(g = tilted45, std = 0.4f, screen = true))
        case("躺著舉手機：很穩但螢幕亮", Spot.HAND, rd(g = tilted45, std = 0.08f, screen = true))
        case("拿在手上但平拿、在晃 → 不是桌上", Spot.HAND, rd(g = up, std = 0.5f, screen = true))
        case("講電話貼耳", Spot.EAR, rd(near = true, lux = 30f, g = upright, std = 0.3f, call = true))
        case("通話中但開擴音放桌上 → 桌上", Spot.TABLE, rd(near = false, g = up, call = true))
        case("立在手機架上、螢幕暗", Spot.PROPPED, rd(g = tilted45))
        case("沒量到加速度 → 不知道", Spot.UNKNOWN, rd(n = 0))
        case("走路握在手上、螢幕暗、亮 → 手上", Spot.HAND, rd(g = upright, std = 1.2f))
        println("ALL $passed PASSED")
    }
}
