# 建置與本機設定

Python 離線測試需要 Python 3.11 以上與標準函式庫。BLE 使用 bleak；WebSocket 使用 websockets。額外套件未在本次安裝。

Android 原始碼設定使用 Java 17、compileSdk 35、wear／mobile 兩模組。Gradle 與外部相依套件依 build.gradle.kts 配置。草稿未附本機快取或 wrapper 二進位；可在 Android Studio 開啟手錶app/，設定 SDK 後獨立建置。

wear/libs/ 不含三星 SDK。編譯三星感測原始碼須自行從官方渠道取得相容 SDK 與權限，不能將 SDK 加入公開版本。本次未驗證帳號、Partner 核准或裝置上的 SDK 模式。

| 範例檔 | 自行另存的本機用途 |
|---|---|
| config/puller_config.example.json | 生理資料/puller_config.json |
| config/watch_puller_config.example.json | 生理資料/手錶橋接/puller_config.json |
| config/environment.example | 設定 ADB、PHONE_SERIAL 與可選通知／語音工具 |
| 手錶app/local.properties.example | 填 SDK 路徑，另存 local.properties |

本次沒有建立生理資料/或實際設定。環境範例需手動設定，不會自動載入。

namespace、manifest、程式與測試一致使用 com.example.watchbridge。Wear 與手機仍需自己的同簽章與連線驗證。
