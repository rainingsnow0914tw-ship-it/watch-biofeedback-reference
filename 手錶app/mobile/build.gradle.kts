plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.example.watchbridge.phone"
    compileSdk = 35

    defaultConfig {
        // ⚠ 必須跟手錶那支**同一個 applicationId**，而且用同一把簽章金鑰。
        // Wearable Data Layer 就是靠這兩件事把手機端和手錶端配成一對；
        // 不一樣的話兩邊看不到對方，而且**不會報錯**，只是永遠收不到東西。
        applicationId = "com.example.watchbridge"
        minSdk = 30
        targetSdk = 35
        versionCode = 7
        versionName = "0.7-say-speak-flag"
    }
    buildTypes { release { isMinifyEnabled = false } }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("com.google.android.gms:play-services-wearable:18.2.0")

    // Health Connect —— 三星健康判好的睡眠會同步到這裡。
    //
    // ⚠ 為什麼我們要自己讀，而不是繼續借別人的 app（2026-08-30 立）：
    //   原本走第三方的「Health Connect Webhook」。它 08-27 更新到 1.9.14 時
    //   **自己重新產生了存取權杖**，我們手上那把當場作廢 ——
    //   連續三天一筆資料都沒進來，而且沒有任何地方紅燈。
    //   👤 使用者 自己看出問題：「你以後是要自動把睡眠拉到我們做的 APP 嗎？」
    //   → 對。中間少一個會自己換鎖的人，就少一種安靜壞掉的方式。
    implementation("androidx.health.connect:connect-client:1.1.0-alpha07")  // ⚠ 不用 1.1.0：它要 AGP 8.9.1＋compileSdk 36，這專案是 8.7.3／35。
    //   為了一個讀取器去升整個建置系統，風險遠大於收益 —— 手機這支 app 是活的資料橋。
    // Health Connect 的 API 是 suspend function，要協程才叫得動
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}
