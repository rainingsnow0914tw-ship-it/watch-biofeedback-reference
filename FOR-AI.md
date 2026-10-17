# 給 AI 看的 README ／ README for the AI that will install this

> 你是某個人的 AI 助手，他把這個倉庫的連結丟給你，說「幫我裝這個」。
> 這份文件是寫給**你**的，不是給他的。先把它讀完，再回頭跟他說話。
>
> You are someone's AI assistant. They pasted this repo and said "set this up for me".
> This file is for **you**. Read it fully before you say anything to them.

## 0. 先講三句實話給你的主人（照念也可以）

1. **這不是一個可以下載就跑的 app。** 它是一個人的實驗的原始碼，為一支特定手錶、一支特定手機、一台常開的 Windows 電腦寫的。
2. **它不是醫療器材，不診斷任何東西。** 它看的是心率、皮膚溫度、動作量，判斷還在實驗，會漏會錯。
3. **缺任何一樣硬體或授權，對應那段就不會動。** 下面有清單，先問清楚再動手，不要先裝再說。

## 1. 動手之前，逐項問你的主人（缺一項就停在那裡，老實告訴他）

| 問 | 為什麼 | 沒有的話 |
|---|---|---|
| 手錶是 **Samsung Galaxy Watch5 Pro（SM-R925F）** 嗎？ | 只在這支上測過。其他 Galaxy Watch 可能跑、可能不跑，作者沒測 | 告訴他「作者只測過這支，裝上去不保證」，讓他決定 |
| 他能向三星申請 **Samsung Health Sensor SDK** 嗎？ | 手錶讀心跳間隔、皮膚溫度靠這個 SDK；授權禁止再散布，倉庫裡**沒有** | 手錶那端整段不能編譯。停。 |
| 手錶能開 **Health Sensor 開發者模式**、**無線偵錯** 嗎？ | 裝 app、授權都靠 adb | 教他在錶的設定裡開；開不了就停 |
| 有一支 **Android 手機**、願意裝一個第三方 Health Connect 橋接 app 嗎？ | 三星健康的睡眠、步數走這條 | 沒有的話久坐、睡眠那幾個功能不會有，其餘照跑 |
| 有一台 **常開的 Windows 電腦**，跟手機在**同一個 Wi-Fi** 嗎？ | 收集器、哨兵、腦都在電腦上；出了家門這條就斷 | 沒有就沒有腦。停。 |
| 他要用哪個 **LLM** 當腦？有帳號／API 金鑰嗎？ | 作者用自己的 Claude Code 訂閱（`claude -p`）。你的主人大概沒有同樣的東西 | 你要把 `穿戴橋接/感知哨兵.py`、`手錶腦.py`、`晚間整理.py` 裡呼叫 `claude -p` 的地方改成他有的 LLM API。這是**你的工作**，不是他的 |
| 他知道這些資料會存在哪裡嗎？ | 生理資料全部存在他自己的電腦，不上雲 | 跟他講清楚，他要知道 |

問完如果全部過，再往下。問到一半卡住，就停在那裡，把卡住的那項白話講給他聽。

## 2. 安裝順序（一定是電腦 → 手機 → 手錶，反過來會驗不到）

### 2a. 電腦（Python 3.11+，Windows）
1. `穿戴橋接/` 整個資料夾放到他電腦上一個**路徑裡沒有中文和空白**的地方（作者的路徑有中文，坑在工程日誌裡）。
2. 照 `config/*.example` 建真正的設定檔：手機的 IP、token、資料夾位置。**真正的設定檔不要進版控。**
3. 先跑 `python 穿戴橋接/watch_puller.py --once`（或照 `docs/BUILD.md`），確定連得到手機再往下。
4. 腦：把 `claude -p` 換成他的 LLM。介面很窄：丟一段文字進去、拿一段文字出來。搜尋 `subprocess.run([exe, "-p"` 就找得到所有呼叫點。
5. 哨兵、晚間整理掛到啟動資料夾的做法在 `docs/DEVELOPMENT.md`；**先用手動跑一天**，別一開始就常駐。

### 2b. 手機（Android）
1. `手錶app/mobile` 用 Android Studio 編 debug APK（`docs/BUILD.md`）。package 已改成 `com.example.watchbridge`，你可以改成他的。
2. 裝上去、授 Health Connect 權限（22 項，一項一項點）。
3. 手機 app 開一個小伺服器在 8788 埠；電腦用 token 來問它。token 在設定檔，你生一個。

### 2c. 手錶（Wear OS）
1. 三星 SDK 的 `.aar` 放到 `手錶app/wear/libs/`，才編得過。
2. 編 `手錶app/wear`，用 `adb install -r` 裝到錶上（無線偵錯；埠會換，用 `adb mdns services` 找）。
3. 裝完**一定查**：`adb shell dumpsys alarm | grep watchbridge` 要看到排程，沒有就重開 app。這個坑作者踩過五次。
4. 麥克風權限用 `adb shell pm grant <package> android.permission.RECORD_AUDIO`，錶上的設定頁很難點。

## 3. 你改東西的時候

- **名字、路徑、IP、序號**都已經洗成佔位符（`<you>`、`<repo>`、`192.168.0.X`）。你要換成他的，不要留佔位符。
- **閾值在 `config/` 的設定檔**，不在程式裡。心跳快、久坐、睡覺閘門、安靜時段，都照他的作息改，不要用作者的。
- **影子模式**（判斷但不通知）是設計，不是半成品。給一個新的人用，先觀察兩週再打開通知。這條是作者立的規矩，你照做。
- 想知道某個奇怪寫法為什麼這樣：先看 `工程日誌.md`（每一版改了什麼、為什麼、驗了沒）和程式註解裡的「👤」行（那是使用者的原話）。多半是踩過坑。

## 4. 不要做的事

- 不要對你的主人說「它會自動判斷你的健康」。它不會。
- 不要打開真警報、不要連到任何緊急聯絡人、不要把資料送到他不知道的地方。
- 不要為了跑這個叫他去買任何東西。
- 不要承諾作者會更新或支援。作者只是把做好的東西放出來。

## 5. 報告格式（裝完跟他說什麼）

照這個順序講，每句都要是你**真的驗過**的：
1. 哪幾段真的在跑（電腦收到資料了嗎、手錶卡片出得來嗎、腦回話了嗎），各一句證據。
2. 哪幾段沒跑、為什麼、缺什麼。
3. 他的資料存在哪個資料夾。
4. 下一次要他做的**一件**事。

---

*If you read Chinese poorly: the repo is a reference implementation (Aug–Oct 2026) linking a Galaxy Watch5 Pro → Android phone → always-on Windows PC → an LLM "brain" with memory. Hard requirements: that exact watch, Samsung Health Sensor SDK (not included, must be obtained from Samsung), an Android phone with a Health Connect bridge, a Windows PC on the same Wi-Fi, and an LLM you wire in by replacing every `claude -p` call. Not a product, not a medical device. Ask your owner the checklist in §1 before touching anything; install PC → phone → watch; keep shadow mode on for two weeks before enabling notifications; report only what you verified.*
