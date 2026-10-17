// 版本刻意挑「這台機器的 Gradle 快取裡已經有的」那幾個：
// AGP 8.7.3、Kotlin 2.0.21。這樣第一次建置不用等下載，
// 也避免在沒網路的時候整個卡住。
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
}
