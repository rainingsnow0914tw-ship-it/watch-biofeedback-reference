# 手錶生理資料 AI 判讀 ／ Watch Biofeedback → AI Companion

> **這不是產品，是一個人的實驗。** 這套程式是為作者自己的 Samsung Galaxy Watch5 Pro（SM-R925F）、自己的手機、家裡一台常開的 Windows 電腦寫的。別的 Galaxy Watch 型號、別的手機、別的作業系統**未必能用**，作者沒有測過。**請不要為了它去買任何裝置。**
>
> 它讀的是皮膚溫度、心率、心跳間隔、動作量這些訊號，**不是醫療器材，不做任何診斷**，沒有任何臨床驗證。它判斷「不對勁」的方式還在實驗（影子模式），會漏、會錯。
>
> 程式碼和設計紀錄照原樣分享，坑都寫在註解裡。拿去改成你自己的，歡迎；出了事請自己負責。

## 這是什麼

一個非工程師在 2026 年 8～10 月，跟自己的 AI 一起做出來的東西：

```
手錶（Wear OS app）──藍牙──▶ 手機（Android app，小伺服器）──Wi-Fi──▶ 電腦（Python 收集器＋哨兵＋腦）
手錶 ◀──藍牙── 手機 ◀──Wi-Fi── 電腦：震一下／顯示字／唸出來
```

- **眼睛**：手錶每秒收心率與心跳間隔、每分鐘動作量、皮膚溫度，存在錶上，走藍牙送到手機；電腦每 10 分鐘拉回來。三星 Health Connect 的睡眠、步數另一條線。
- **判斷**：電腦端摘要層（每小時一筆：涵蓋率、心率上下界、動作、溫差）＋ triage（急／不急），全程「沒資料≠沒事」。
- **活人感**：感知哨兵每 10 分鐘看三種事件（安靜卻心跳快、連戴太久、脫下後戴回），由一個會「想」的腦寫一句話，錶亮起來唸出來。
- **對講機**：錶上按一下、說一句 → 錶自己轉成文字 → 手機 → 電腦 → 腦回一句 → 錶唸。實測整圈 6～10 秒。
- **記憶**：每天一封信＋三層記憶（原始事件／逐日整理／腦的長期記憶）。

完整設計理由與每一版改了什麼：`地圖.md`、`工程日誌.md`、`docs/`。

## 跑起來需要什麼（老實說）

| 需要 | 說明 |
|---|---|
| Galaxy Watch5 Pro＋**三星 Health Sensor SDK** | SDK 授權禁止再散布，要自己向三星申請，放到 `手錶app/wear/libs/`；錶要開 Health Sensor 開發者模式 |
| Android 手機 | 裝 `手錶app/mobile`，另需第三方 Health Connect 橋接（或自己接 Health Connect）；22 個健康權限要授 |
| 一台常開的 Windows 電腦、跟手機同一個 Wi-Fi | `穿戴橋接/` 的收集器、哨兵掛在啟動資料夾 |
| **一顆會想的腦** | 作者用自己的 Claude Code 訂閱（`claude -p`）和自己的記憶檔。你得換成你的：`感知哨兵.py` 與 `晚間整理.py` 裡呼叫 `claude -p` 的地方，可改成任何 LLM API |
| （備援）faster-whisper＋GPU | 只有「錄音留言」那條需要 |

沒有上述任何一項，對應的那段就不會動。這不是「下載就能跑」的東西，是**可以讀、可以抄、可以改成你自己的**東西。

## 目錄

```
手錶app/        Wear OS（wear/）與手機（mobile/）原始碼，package 已改為 com.example.watchbridge
穿戴橋接/        電腦端 Python：收集、摘要、判讀、哨兵、對講機、晚間整理
docs/           架構、建置、資料品質、安全界線、發布
config/         設定檔範本（真正的設定不進版控）
tools/          離線驗證與隱私檢查
地圖.md          先讀這份
工程日誌.md      每一版改了什麼、為什麼、驗了沒
```

## 不在這個倉庫裡的

作者的生理資料、日記、家人的醫療脈絡、手機快照、腦的記憶檔、三星 SDK。永遠不會在。

## 想裝？先把 `FOR-AI.md` 丟給你的 AI

這東西不是人裝的，是你的 AI 幫你裝的。`FOR-AI.md` 是寫給它看的：該先問你什麼、缺什麼就停在哪、裝的順序、哪些事不准做。把倉庫連結和那份檔一起貼給它，然後聽它老實報告。

## 做到了什麼（2026-10-18 這一版）

- 手錶 → 手機 → 電腦的資料路：連續跑了兩個月，開機自動起，斷了會自己接回。
- 哨兵事件：安靜卻心跳快、連戴太久、脫下後戴回、久坐兩小時、起身後回饋、三星偵測到走路。
- 對講機：錶上一句話 → 腦回一句 → 錶唸，4～6 秒；她問才出聲，腦主動找她只震一下給字。
- 記憶：每天一封信、逐日記憶、腦的長期記憶，隔天記得昨天說過什麼。
- 可以查天氣和網路上的事；其餘工具全關，這條線只能講話。

這些是**在作者自己身上**跑出來的，不是在別人身上。

## 授權

MIT（見 `LICENSE`）。拿去改、拿去用，不用問；出了事自己負責。

---

*English summary*: A non-engineer's experiment (Aug–Oct 2026) connecting a Galaxy Watch5 Pro → phone → always-on Windows PC → an LLM "brain" with memory. The watch streams HR/IBI/skin-temp/motion; a sentinel notices events; the brain speaks back through the watch; the user answers with one tap and a sentence. Reference implementation only: not a product, not a medical device, tested on exactly one watch. See `docs/` for architecture and the honest list of what you'd need.
