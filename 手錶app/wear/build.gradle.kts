plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.example.watchbridge"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.example.watchbridge"
        minSdk = 30
        targetSdk = 35
        versionCode = 108
        versionName = "1.10-talk-sent-no-false-fail"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // 手錶 → 手機的藍牙通道。Wearable Data Layer 走的是系統既有的
    // 藍牙連線，不用自己配對、不用自己管重連。
    implementation("com.google.android.gms:play-services-wearable:18.2.0")

    // 三星 Health Sensor SDK —— 皮膚溫度／IBI／PPG 原始波形／ECG／BIA／EDA。
    // .aar 要從 developer.samsung.com 下載（擋在三星帳號登入後面），放進 wear/libs/。
    implementation(fileTree(mapOf("dir" to "libs", "include" to listOf("*.aar"))))

    // Wear OS 的公開健康 API。
    // **重點：這個不是三星的 Sensor SDK** —— 不用登入、不用申請合作夥伴，
    // 任何人都能用。拿得到即時心率、步數、卡路里等。
    implementation("androidx.health:health-services-client:1.0.0-rc01")

    // Health Services 的每一支 *Async（含 getCapabilitiesAsync）都回傳
    // Guava 的 ListenableFuture，而 health-services-client 只把 Guava 列為
    // compileOnly —— 不自己補就會編譯失敗：
    //     Cannot access class 'com.google.common.util.concurrent.ListenableFuture'
    //
    // **那個沒有用**（實測兩次都還是同一個錯），因為它帶的是
    // com.google.guava:listenablefuture 的空殼 artifact。要真的 Guava 本體。
    implementation("com.google.guava:guava:31.1-android")

    // 有了本體才能用 .await() 把 ListenableFuture 接進 coroutine，
    // 不然要手寫 addListener + Executor，很囉唆。
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-guava:1.8.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // Wear OS 的 Doze 照樣會把它凍住。要用 OngoingActivity 註冊，
    // 而且通知要標成 CATEGORY_WORKOUT —— 系統看到這個才知道
    implementation("androidx.wear:wear-ongoing:1.0.0")
    implementation("androidx.core:core-ktx:1.13.1")
}
