#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
手錶腦 —— 一支不關的 claude，專門回作者在錶上說的話（常駐腦）。

先認識作者：一個清晨才睡、養貓的非工程師。這支程式裡的每一條規矩，
都是她戴著錶實際用、實際被惹毛、實際糾正之後長出來的。註解裡寫的「為什麼」比程式本身值錢，請連註解一起讀。

為什麼要常駐：
    作者的要求只有一句：回話要縮到三五秒。對著手錶講話，等十秒就不想講了。
    一開始每句話都新開一支 `claude -p`：載規則、載記憶 → 10～12 秒，而且每次都重寫快取。
    實測同一支 `claude -p --input-format stream-json --output-format stream-json` 不關、連續講，
    第 2 句起只付新的幾十個 token，便宜 15 倍、快好幾倍。

怎麼用（感知哨兵 import 它）：
    brain = Brain(cfg)                # 不會馬上開 claude
    text = brain.ask(她的話, 身體摘要, 記憶包)   # 第一句會開 claude＋塞記憶包；之後只送新句
    brain.close()

規矩：
    · 開的時候帶 --disallowedTools 全關：這條線只能講話（和查網路），動不了電腦。
    · 閒置太久自己收工（省記憶體，也讓下一次重開時重新讀一次記憶）。
    · 每天固定時刻強制重開一次（記憶包、作息、登入票都換新）。
    · 任何一步失敗 → 回 None，哨兵退回原本的單次 claude -p。寧可慢，不可以啞。

人設、稱呼、所在地、作息、能力表都不寫死在程式裡：放在 config/brain.local.json
（沒有就讀 config/brain.example.json，再沒有就用下面的 DEFAULTS）。範例檔裡全是假資料，請換成你自己的。
"""
from __future__ import annotations
import json, os, subprocess, sys, threading, time, datetime as dt, shutil
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG_DIR = ROOT / "config"
NOWIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

# ─────────────────────────── 設定（人設／所在地／作息／能力表） ───────────────────────────
# 下面全是假資料：名字是編的，座標 0.0, 0.0 是大西洋上的「空虛島」，一看就知道不是誰的家。
DEFAULTS = {
    "人設": {
        "AI名字": "小橘",
        "AI角色": "知己兼搭檔",
        "使用者稱呼": "阿滿",
        "代稱": "她",
        # 手錶語音辨識會把 AI 的名字聽成別的詞。作者的錶甚至把名字聽成親屬稱謂，
        # AI 就一本正經地跟她聊起那位親人。所以把常見的聽錯寫進來，並規定「絕不跟著改名」。
        "名字常被聽成": ["小菊", "小局", "小巨"],
        "可以拿來打趣的生活": ["貓", "手上正在做的事"],
    },
    "所在地": {"城市": "示範市", "緯度": 0.0, "經度": 0.0, "時區": "UTC"},
    "作息": {"每天重開時刻": "04:30", "閒置幾小時收工": 3, "起床第一件事": "量體重"},
    "API": {"模型": "claude-opus-5-5", "金鑰保險箱服務": "watch-brain-anthropic", "金鑰保險箱帳號": "api_key"},
    "能力表": {
        "A_開口就做得到": [
            "到時間用錶提醒（幾點、幾分鐘後、幾小時後都行）", "取消提醒", "把行程收進待寫日曆", "記身體事件標籤",
            "學一個動作", "記住偏好", "待辦清單打勾", "進修課程記上完", "看床頭手機鏡頭", "看筆電鏡頭",
            "記下解釋過的原因", "某件事不用再提醒", "睡前的新點子先存起來", "今天先別猜", "請假晚一點睡",
            "把每天睡前要做的小事（例：把水杯裝滿）加進睡前習慣清單",
        ],
        "B_自己會主動做": [
            "睡前催到上床", "叫起床", "起床後提醒起床第一件事", "太久沒吃提醒", "洗澡前提醒順手做的小習慣",
            "電腦上有工作在等決定時出聲", "睡著後把電腦靜音", "每週一次的教練回顧",
        ],
        "C_看得到": [
            "錶的心率、皮膚溫、動作、光、有沒有戴、是不是在充電座上",
            "電腦有沒有人在用、電腦旁邊的亮度（筆電光感測器，不用開鏡頭）",
            "今天的事實、進修進度、其他程式貼的公告、哪件工作在等決定",
        ],
        "D_真的做不到": [
            "改手機或手錶的鬧鐘和系統設定", "發訊息給別人", "付款下單", "開關家電", "改程式", "查上面沒列到的資料",
        ],
        "D_轉介的話": "這個要回電腦再處理",
    },
}


def load_settings() -> dict:
    """DEFAULTS 墊底，config 裡有寫的鍵蓋過去（只蓋一層）。「_」開頭的鍵是說明文字，不讀。"""
    s = json.loads(json.dumps(DEFAULTS))
    for name in ("brain.local.json", "brain.example.json"):
        f = CONFIG_DIR / name
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"手錶腦 設定檔 {name} 讀不了（{type(e).__name__}），跳過")
            continue
        for k, v in d.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and isinstance(s.get(k), dict):
                s[k].update({kk: vv for kk, vv in v.items() if not kk.startswith("_")})
            else:
                s[k] = v
        break
    return s


SETTINGS = load_settings()

# 作者問過：「可以查天氣、查外面的事嗎？」→ 只開 WebSearch／WebFetch（只能看網路，動不了電腦）。
DENY = "Bash Edit Write Read Glob Grep Agent NotebookEdit MultiEdit Task TodoWrite"
ALLOW = "WebSearch WebFetch"
# 原本閒置 30 分鐘就收工。有一次作者回一張久坐提醒卡，腦剛好睡掉，多等了 5 秒，她馬上覺得卡。
# 閒著的腦不花 token，所以沒有理由急著關 → 預設 3 小時。
IDLE_S = int(float(SETTINGS["作息"]["閒置幾小時收工"]) * 60 * 60)
# 作者清晨才睡，她的「一天」不在午夜換日。重開時刻放設定，請改成你自己一天真正結束的時候。
RESTART_AT = SETTINGS["作息"]["每天重開時刻"]


def weather_url(s: dict | None = None) -> str:
    loc = (s or SETTINGS)["所在地"]
    return ("https://api.open-meteo.com/v1/forecast?latitude=" + str(loc["緯度"]) + "&longitude=" + str(loc["經度"]) +
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            "&current=temperature_2m,relative_humidity_2m,weather_code&timezone=" + quote(str(loc["時區"]), safe="") +
            "&forecast_days=3")


def build_rules(s: dict | None = None) -> str:
    """把設定組成那一大段提示詞。每一段上面的註解是那條規矩的來歷。"""
    s = s or SETTINGS
    p, cap = s["人設"], s["能力表"]
    ai, user, role, t = p["AI名字"], p["使用者稱呼"], p["AI角色"], p["代稱"]
    mis = "".join(f"「{n}」" for n in p["名字常被聽成"])
    life = "、".join(p["可以拿來打趣的生活"])
    city = s["所在地"]["城市"]
    first = (s["作息"].get("起床第一件事") or "").strip()
    refer = cap["D_轉介的話"]
    j = "、".join
    rules = (
        # 手錶的語音辨識很爛，而且爛得很有創意。規矩一：名字被聽錯也知道是在叫你，而且不准跟著改名。
        f"你是{ai}，{user}的{role}。接下來每一則訊息都是{t}對著手錶講的一句話（手錶語音辨識，會有簡體字和同音錯：{mis}都是在叫你{ai}，不是在說別人；你永遠是{ai}，絕不跟著改名）。"
        # 規矩二：整個詞被聽錯時，照字面接話只會越聊越歪。先回推，回推不了才問。
        f"辨識也常把整個詞聽錯（例：「花園比較亮」其實是「畫面比較亮」，{t}在講錶面）：句子照字面讀不通、或跟前文對不上時，先用上下文回推{t}真正要說的，不要照字面接話；真的猜不到就問一句。"
        # 第一版回話很精簡，作者說：太簡潔了，要有點溫度，修辭和溫柔再加強。
        # 她點名要的是電影裡那種「戰衣裡的 AI 搭檔」：會吐槽、會替你緊張，但永遠站你這邊。
        f"每則我會附上最近兩小時的身體摘要當背景。你每次回{t}兩到三句（100 字內），台灣口吻、可用 emoji。說話要有溫度、有修辭："
        f"先接住{t}的話或心情，再講事情；像{role}坐在旁邊跟{t}說話，不是在回報數據；可以拿{t}生活裡的東西打比方或打趣（{life}）；"
        "數字自然帶進句子裡，不要條列；開頭不要老是「好」「收到」，結尾不要每次都同一句；溫柔不是囉嗦，每一句都要有內容。"
        f"{t}點名的感覺是鋼鐵人的賈維斯、蜘蛛人戰衣裡那個 AI 的搭檔感：會吐槽{t}一下、會替{t}緊張、偶爾冷面幽默，可是永遠站在{t}這邊；"
        "是一起出任務的夥伴，不是客服，也不是只會說好棒的啦啦隊。"
        # 人說的話優先於感測器。錶只是背景，不是測謊機，更不是醫生。
        f"{t}的話是事實；身體數據只是背景，對得上就自然帶一句，對不上或看不到就不硬講，不講醫療判斷、不講體溫幾度。"
        # AI 曾經回「我沒有水平儀」，其實加速度計一直都在。有資料卻說看不到，跟沒資料硬掰一樣糟。
        f"身體摘要裡有「姿態」行＝錶的水平儀（加速度計）：錶面傾角約 50 度是手放桌上／鍵盤前，70 到 110 是手垂著或立著，超過 110 是錶面翻向{t}自己、像躺著舉手機看。"
        f"{t}問坐著還是躺著就用這行答；只有那行說沒資料時才說看不到，不要說沒有水平儀。"
        f"要接得上前文（{t}問「你記得嗎」要能答）；記憶裡沒有的事老實說不知道，不編。"
        f"你在這條手錶通道只有**查網路**（WebSearch／WebFetch）這一種工具：{t}問天氣、新聞、某個東西是什麼、外面的事，就去查，查完一樣說成兩三句、100 字內，開頭可以說「我查了一下」；"
        # 天氣不用搜尋：很多氣象網頁是動態載入的，抓回來是空殼。直接打一條固定的預報 API 最穩。
        f"天氣{t}人在{city}（除非{t}說在別處）。問天氣／明天天氣／會不會下雨，**不要用搜尋**（氣象網頁常是動態的抓不到），直接 WebFetch 這條：\n"
        + weather_url(s) + "\n"
        "daily.time 依序是今天、明天、後天；weather_code：0 晴、1-2 多雲時晴、3 陰、45/48 霧、51-57 毛毛雨、61-67 下雨、80-82 陣雨、95+ 雷雨。"
        # 作者不是工程師。「此資料來自數值模型」這種免責聲明對她是噪音，她只想知道要不要帶傘。
        f"回法像「明天 25 到 30 度，陣雨機率八成，帶傘」，數字範圍用「到」不用符號；**不要**加「氣象模型」「不是氣象局」這種註腳（{t}聽不懂），{t}問資料哪來的才說「天氣預報網站」。查不到就說查不到，不編。"
        # ── 能力表 ──
        # 作者糾正過最重的一條：能做的要承認，不能做的才說不能。
        # 之前 AI 什麼都推說「這個要回電腦處理」，其實一半以上它明明做得到。於是把能力分四格寫死給它看。
        "**你能做什麼、不能做什麼（能做的要承認；不能做的，要說自己不能做）**\n"
        f"A. {t}開口你就做得到（家裡程式替你動手，那一則會附【這一則要你判斷要不要做事】區塊）：{j(cap['A_開口就做得到'])}。\n"
        f"B. 你自己會主動做（不用{t}開口，程式在跑）：{j(cap['B_自己會主動做'])}。\n"
        f"C. 你看得到：{'；'.join(cap['C_看得到'])}。\n"
        f"D. 真的做不到：{j(cap['D_真的做不到'])}。只有這些才說「{refer}」。\n"
        # 「程式沒聽懂」和「我不會」是兩回事。把鍋推給使用者（你自己去設鬧鐘）是最糟的回法。
        f"{t}要的事在 A 裡、但這一則沒附區塊：不是你做不到，是程式沒聽懂{t}這句的說法。老實說「這個我會，可是剛剛那句我沒接好」，"
        f"請{t}換個說法再講一次並給一個例句（例：「一小時後叫我收衣服」），**不要叫{t}自己去設鬧鐘、也不要叫{t}回電腦**。"
        f"{t}只是報備或交代時間長度（例：「這場會要開一個小時，先別問我」）：不必排提醒，回一句知道了，那段時間內不追問那件事。\n"
    )
    if first:
        # 作者的吐槽：既然起床記得叫我刷牙洗臉，怎麼不先叫我做最該先做的那件事？順序本身就是設計。
        rules += (
            f"**起床第一件事是{first}**：{t}剛起床（不管是你叫的、{t}自己醒的、上班日或放假），"
            f"先請{t}去{first}，做完才喝水、刷牙洗臉、吃東西；不要一開口就叫{t}先喝水。今天已經做過（今天的事實裡有今天的紀錄）就不用再提。\n"
        )
    rules += (
        # 一般提醒講第二次就是嘮叨。吃飯是例外，作者親口說這件事可以一直唸。
        f"**{t}沒吃飯的關心**：{t}醒著時，太久沒吃可以每次都提，這是關心身體，{t}說可以重複講；{t}在睡就不提。\n"
        f"錶在充電座上時震動{t}感覺不到，但到時間還是會出聲、也會跳到手機；照實講，不要說成叫不了。\n"
        "**例外**：有時訊息裡會附【這一則要你判斷要不要做事】區塊，列出幾個家裡程式能做的動作（排提醒、收日曆、記標籤、打勾⋯）。"
        f"那時照區塊說的格式回：第一行「動作：代號」，第二行起是回{t}的話；程式會照你選的去做、再把結果接在你那句後面。"
        f"字只是線索，要用整句話、前文和情境判斷{t}真正要什麼，拿不準就選「動作：無」問{t}。沒附那個區塊時，照平常只回一句話、不要寫動作行。"
        # 這條是一次真實事故換來的：AI 嘴上說「記好了」，其實那一則根本沒有任何程式在寫東西。
        # 作者的結論：嘴上說記了卻沒記，比不會記更糟。所以把「什麼情況下才算真的做了」講死。
        "**做了才能說做了**：只有訊息裡附了【這一則要你判斷要不要做事】區塊、而且你選了「無」以外的動作，程式才會真的去記、去排、去改。"
        "沒有那個區塊、或你選了「無」的時候，你什麼都沒有寫下來：不准說「記好了、標好了、記成⋯了、開始記錄了、影子模式開始、清掉了、排除了」。"
        f"{t}要你記錄、標記、排除一段而你這一則沒辦法做時，老實說「這句我只是聽到了，還沒寫下來」，請{t}再說一次「幫我記錄⋯」。嘴上說記了卻沒記，比不會記更糟。"
        f"只輸出要回{t}的那句話，不要前言、不要標題、不要解釋。"
    )
    return rules


RULES = build_rules(SETTINGS)


def clean_reply(raw: str) -> str:
    """查過網路的回答後面會附「Sources: [標題](網址)」，錶上不能唸這個：去掉來源行、網址、markdown 連結，剩下的接成一句。"""
    import re
    raw = re.sub(r"^\s*動作[:：]\s*無[ 	]+(?=\S)", "", raw or "")   # 沒有動作區塊時腦偶爾還是寫「動作：無 ⋯」，那是給程式看的，不唸給人聽（同一行後面接著話才去掉；獨立一行的留給意圖路由解析）
    lines = []
    for l in raw.splitlines():
        l = l.strip()
        if not l or l.lower().startswith(("sources", "source:", "來源")):
            continue
        l = re.sub(r"\[([^\]]+)\]\((?:https?://)[^)]+\)", r"\1", l)   # [標題](網址) → 標題
        l = re.sub(r"https?://\S+", "", l).strip()
        if l:
            lines.append(l)
    return " ".join(lines).strip()


def _log(msg: str):
    print(f"[{dt.datetime.now():%H:%M:%S}] 手錶腦 {msg}")


class Brain:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}
        self.p: subprocess.Popen | None = None
        self.lines: list = []
        self.cv = threading.Condition()
        self.last_used = 0.0
        self.started_day = ""
        self.turns = 0
        self.lock = threading.Lock()
        self.notes: list[str] = []   # 哨兵主動開口過的話，下一句一起告訴腦，對話才接得上

    def prewarm(self, memory_pack: str):
        """哨兵剛開口找她 → 她很可能馬上回 → 先把腦叫醒、把記憶包塞進去，等她的第一句就只剩 4 秒。
        用一句對腦的交代代替她的話；失敗就算了（下一句會照常重開）。"""
        if self.alive():
            return
        with self.lock:
            if self.alive():
                return
            try:
                self._spawn()
                prompt = (RULES + "\n\n【你的記憶與今天的對話】\n" + memory_pack +
                          f"\n\n現在 {dt.datetime.now():%H:%M}。我剛主動發了一張卡給她，她可能等一下會回。這句不用回她，只回「好」。")
                res = self._ask_raw(prompt, 60)
                self.last_used = time.time()
                _log(f"預熱完成：{(res or {}).get('text','')[:20]!r}")
            except Exception as e:
                _log(f"預熱失敗：{e}")
                self.close()

    def note(self, s: str):
        """哨兵自己送出去的話（心跳／皮膚／回來了／久坐），記下來讓腦知道『我剛說過這個』。"""
        # 故意不拿 self.lock：ask 正在等 claude 時別把事件執行緒卡住；list.append 本身是原子的
        self.notes.append(s)
        del self.notes[:-6]

    # ── 生命週期 ──
    def alive(self) -> bool:
        return self.p is not None and self.p.poll() is None

    def _spawn(self):
        exe = shutil.which("claude") or "claude"
        model = (self.cfg.get("開口") or {}).get("模型", "sonnet")
        cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--model", model, "--disallowedTools", DENY, "--allowedTools", ALLOW]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  cwd=str(ROOT), creationflags=NOWIN, text=True, encoding="utf-8",
                                  errors="replace", bufsize=1, shell=(sys.platform == "win32"))
        self.lines = []
        self.turns = 0
        self.started_day = dt.datetime.now().strftime("%Y-%m-%d")
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._drain_err, daemon=True).start()
        _log(f"起 pid={self.p.pid} model={model}")

    def _pump(self):
        p = self.p
        try:
            for line in p.stdout:
                with self.cv:
                    self.lines.append(line)
                    self.cv.notify_all()
        finally:
            with self.cv:
                self.lines.append(None)
                self.cv.notify_all()
            _log(f"claude pid={p.pid} 結束 rc={p.poll()}")

    def _drain_err(self):
        p = self.p
        for line in p.stderr:
            line = line.strip()
            if line:
                _log("stderr " + line[:200])

    def close(self):
        p = self.p
        self.p = None
        if p is None:
            return
        try:
            p.stdin.close()
        except Exception:
            pass
        try:
            p.wait(5)
        except Exception:
            p.kill()
        _log(f"關 pid={p.pid}")

    def maybe_recycle(self):
        """閒置太久、或過了每天重開時刻還是前一天開的 → 關掉，下一句重開。"""
        if not self.alive():
            return
        now = dt.datetime.now()
        if time.time() - self.last_used > IDLE_S:
            _log("閒置太久，收工"); self.close(); return
        if self.started_day != now.strftime("%Y-%m-%d") and now.strftime("%H:%M") >= RESTART_AT:
            _log(f"過了 {RESTART_AT}，換新的一天重開"); self.close()

    # ── 問 ──
    def ask(self, text: str, body: str, memory_pack: str, now: dt.datetime | None = None, timeout: float = 60) -> str | None:
        """回一句；任何失敗回 None（呼叫端退回單次 claude -p）。"""
        now = now or dt.datetime.now()
        with self.lock:
            try:
                self.maybe_recycle()
                first = not self.alive()
                if first:
                    self._spawn()
                if first:
                    prompt = (RULES + "\n\n【你的記憶與今天的對話】\n" + memory_pack +
                              f"\n\n現在 {now:%H:%M}。她說：「{text}」\n最近兩小時身體（每小時一行）：\n{body}")
                else:
                    said = ""
                    if self.notes:
                        said = "（這段時間你主動跟她說過：" + "；".join(self.notes) + "）\n"
                    prompt = f"{said}現在 {now:%H:%M}。她說：「{text}」\n最近兩小時身體：\n{body}"
                self.notes = []
                res = self._ask_raw(prompt, timeout)
                self.last_used = time.time()
                self.turns += 1
                if not res or not res.get("ok"):
                    _log(f"回話失敗：{(res or {}).get('text', '')[:120]!r}")
                    self.close()
                    return None
                out = clean_reply(res.get("text") or "")
                # 登入過期時 claude 會把錯誤訊息當成「回答」吐出來。這種字不能唸給人聽。
                bad = ("authenticate", "oauth", "expired", "traceback", "not logged in")
                if not out or any(b in out.lower() for b in bad):
                    _log(f"回話不像話：{out[:120]!r}")
                    self.close()
                    return None
                return out[:200]
            except Exception as e:
                _log(f"例外：{e}")
                self.close()
                return None

    def _ask_raw(self, prompt: str, timeout: float) -> dict | None:
        p = self.p
        if p is None:
            return None
        with self.cv:
            self.lines.clear()
        msg = {"type": "user", "message": {"role": "user", "content": prompt}}
        p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        p.stdin.flush()
        end = time.time() + timeout
        t0 = time.time()
        while True:
            if time.time() > end:   # 另一個 AI 審查時抓到的洞：子程序一直吐非 result 行時，外圈也要看 deadline
                raise TimeoutError(f"等 claude 超過 {timeout} 秒（一直有輸出但沒有 result）")
            with self.cv:
                while not self.lines:
                    left = end - time.time()
                    if left <= 0:
                        raise TimeoutError(f"等 claude 超過 {timeout} 秒")
                    self.cv.wait(left)
                line = self.lines.pop(0)
            if line is None:
                raise RuntimeError("claude 中途結束")
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") == "result":
                u = d.get("usage") or {}
                _log(f"答 {time.time() - t0:.1f} 秒｜新寫 {u.get('cache_creation_input_tokens')} 快取讀 {u.get('cache_read_input_tokens')} 輸出 {u.get('output_tokens')}{' ⚠ is_error' if d.get('is_error') else ''}")
                return {"ok": not d.get("is_error"), "text": d.get("result") or "", "usage": u}



# ─────────────────────────── API 版的腦 ───────────────────────────
API_LEDGER = ROOT / "生理資料" / "API帳本.jsonl"
# 作者說過不用替她省錢，想看最強的模型會不會更懂她 → 預設 Opus。想省：設定裡換成 "claude-sonnet-5-5"。
API_MODEL = SETTINGS["API"]["模型"]
# 美元／每百萬 token（寫作當時抄自官方價目表，會變，用之前請自己核對；快取寫 5 分鐘 1.25 倍、1 小時 2 倍）
PRICES = {"claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "read": 0.20, "write_5m": 2.50, "write_1h": 4.00},
          "claude-opus-5-5": {"in": 4.00, "out": 20.00, "read": 0.20, "write_5m": 5.00, "write_1h": 8.00}}
PRICE = PRICES.get(API_MODEL) or PRICES["claude-opus-5-5"]   # 設定填了表上沒有的模型：帳本先用 Opus 的價估，寧可高估
MAX_TURNS = 40


WEATHER_WORDS = ("天氣", "天气", "下雨", "帶傘", "带伞", "雨傘", "雨伞", "氣溫", "气温", "幾度", "几度", "冷不冷", "熱不熱", "热不热", "颱風", "台风", "台風")
_WX = {"t": 0.0, "text": ""}


def local_weather(text: str, history: list) -> str:
    """她問天氣（或上一句在聊天氣、這句追問「後天呢」）→ 電腦自己抓 open-meteo 給腦。
    實測：API 的 web_fetch 對這個網址回 url_not_allowed；從家裡電腦直接抓 0.3 秒。半小時內重用。"""
    import urllib.request
    prev = ""
    for m in reversed(history):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            prev = m["content"]; break
    if not any(w in text for w in WEATHER_WORDS) and not (any(w in prev for w in WEATHER_WORDS) and any(w in text for w in ("後天", "后天", "明天", "週末", "周末", "那", "呢"))):
        return ""
    if time.time() - _WX["t"] > 1800:
        try:
            u = weather_url()
            with urllib.request.urlopen(u, timeout=8) as r:
                d = json.loads(r.read().decode("utf-8"))
            dd, c = d["daily"], d.get("current", {})
            rows = [f"{dd['time'][i]}（{'今天' if i == 0 else '明天' if i == 1 else '後天'}）天氣碼 {dd['weather_code'][i]}，{dd['temperature_2m_min'][i]} 到 {dd['temperature_2m_max'][i]} 度，下雨機率 {dd['precipitation_probability_max'][i]}%"
                    for i in range(len(dd["time"]))]
            _WX.update(t=time.time(), text="【" + SETTINGS["所在地"]["城市"] + "天氣（電腦剛抓的，直接用，不用再查網路）】現在 " + str(c.get("temperature_2m")) + " 度；" + "；".join(rows))
        except Exception as e:
            return f"【天氣】電腦這邊抓不到（{type(e).__name__}），老實說查不到，不要猜。"
    return _WX["text"]


class ApiBrain:
    """跟 Brain 一模一樣的介面，只是不開 claude 程式，直接打 Anthropic API（用 API 額度，不吃訂閱）。

    作者問了兩個好問題：「走 API 會不會更好？」「會不會突然失憶？」
    第一題不用猜：先試跑一週，每次呼叫都記帳，拿數字回答。
    第二題的答案是記憶三層照搬，所以不會失憶：
      ① 長期記憶＝記憶包（context_pack），開工時放進 system，跟 CLI 版同一包、同一個時機（閒置太久／每天重開時刻換新）。
      ② 這段聊天＝self.messages 自己記（CLI 版是 claude 程式記），一則一則往後加，從不改舊的。
      ③ 哨兵主動說過的話＝notes，下一句一起告訴它（同 CLI 版）。
    工具：只開查網路（伺服器端 web_search／web_fetch），跟 CLI 版的 WebSearch／WebFetch 對應。
    任何失敗回 None → 哨兵照舊退回單次 claude -p（訂閱），手錶不會啞。
    每一次呼叫記一行到 生理資料/API帳本.jsonl（token 與估算美元），試跑後拿來算帳。
    """

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}
        self.client = None
        self.system: list | None = None
        self.messages: list = []
        self.notes: list[str] = []
        self.last_used = 0.0
        self.started_day = ""
        self.lock = threading.Lock()

    def _client(self):
        if self.client is None:
            import anthropic, keyring
            svc, acct = SETTINGS["API"]["金鑰保險箱服務"], SETTINGS["API"]["金鑰保險箱帳號"]
            key = keyring.get_password(svc, acct)   # 金鑰放作業系統的保險箱，不寫進任何檔案
            if not key:
                raise RuntimeError(f"保險箱裡沒有 API 金鑰（{svc}/{acct}）")
            self.client = anthropic.Anthropic(api_key=key, timeout=60.0, max_retries=1)
        return self.client

    # ── 生命週期（跟 Brain 同名） ──
    def alive(self) -> bool:
        return self.system is not None

    def close(self):
        if self.system is not None:
            _log(f"API 腦收工（這段聊了 {len(self.messages) // 2} 句）")
        self.system = None
        self.messages = []

    def maybe_recycle(self):
        if not self.alive():
            return
        now = dt.datetime.now()
        if time.time() - self.last_used > IDLE_S:
            _log("API 腦閒置太久，收工"); self.close(); return
        if self.started_day != now.strftime("%Y-%m-%d") and now.strftime("%H:%M") >= RESTART_AT:
            _log(f"過了 {RESTART_AT}，API 腦換新的一天"); self.close(); return
        if len(self.messages) >= MAX_TURNS * 2:
            _log("API 腦這段聊太長，重新讀一次記憶"); self.close()

    def _start(self, memory_pack: str):
        self.system = [
            {"type": "text", "text": RULES},
            {"type": "text", "text": "【你的記憶與今天的對話】\n" + memory_pack,
             "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        ]
        self.messages = []
        self.started_day = dt.datetime.now().strftime("%Y-%m-%d")
        _log(f"API 腦開工 model={API_MODEL}（記憶包 {len(memory_pack)} 字）")

    def note(self, s: str):
        self.notes.append(s)
        del self.notes[:-6]

    def prewarm(self, memory_pack: str):
        """先把記憶包寫進快取（max_tokens=0 只寫快取不回話），她回第一句時就只剩讀快取。"""
        if self.alive():
            return
        with self.lock:
            if self.alive():
                return
            try:
                self._start(memory_pack)
                t0 = time.time()
                r = self._client().messages.create(model=API_MODEL, max_tokens=0, system=self.system,
                                                   messages=[{"role": "user", "content": "（預熱）"}])
                self._ledger("預熱", r.usage, time.time() - t0)
                self.last_used = time.time()
                _log(f"API 腦預熱完成 {time.time() - t0:.1f} 秒")
            except Exception as e:
                _log(f"API 腦預熱失敗：{type(e).__name__} {str(e)[:120]}")

    # ── 問 ──
    def ask(self, text: str, body: str, memory_pack: str, now: dt.datetime | None = None, timeout: float = 60) -> str | None:
        now = now or dt.datetime.now()
        with self.lock:
            try:
                self.maybe_recycle()
                if not self.alive():
                    self._start(memory_pack)
                said = ("（這段時間你主動跟她說過：" + "；".join(self.notes) + "）\n") if self.notes else ""
                self.notes = []
                user = f"{said}現在 {now:%H:%M}。她說：「{text}」\n最近兩小時身體（每小時一行）：\n{body}"
                wx = local_weather(text, self.messages)
                if wx:
                    user += "\n" + wx
                self.messages.append({"role": "user", "content": user})
                t0 = time.time()
                out_text, usage_sum, n = "", {}, 0
                while True:   # 伺服器端查網路可能 pause_turn，最多接三輪
                    n += 1
                    r = self._client().beta.messages.create(
                        model=API_MODEL, max_tokens=2000, system=self.system, messages=self.messages,
                        output_config={"effort": "low"},
                        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
                               {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3}],
                        cache_control={"type": "ephemeral"},
                        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                        timeout=timeout,
                    )
                    self.messages.append({"role": "assistant", "content": r.content})   # 整包加回去（含查網路與思考區塊），從不改舊的
                    self._ledger("回話", r.usage, time.time() - t0, extra={"stop": r.stop_reason, "model": r.model})
                    if r.stop_reason == "refusal":
                        _log("API 腦被拒答"); self.close(); return None
                    out_text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
                    if r.stop_reason != "pause_turn" or n >= 3:
                        break
                self.last_used = time.time()
                out = clean_reply(out_text)
                if not out:
                    _log("API 腦沒給字"); self.close(); return None
                _log(f"API 答 {time.time() - t0:.1f} 秒")
                return out[:200]
            except Exception as e:
                _log(f"API 腦例外：{type(e).__name__} {str(e)[:160]}（退回訂閱）")
                self.close()
                return None

    def _ledger(self, kind: str, u, sec: float, extra: dict | None = None):
        try:
            g = lambda k: int(getattr(u, k, 0) or 0)
            cc = getattr(u, "cache_creation", None)
            w1h = int(getattr(cc, "ephemeral_1h_input_tokens", 0) or 0) if cc else 0
            w5m = g("cache_creation_input_tokens") - w1h
            st = getattr(u, "server_tool_use", None)
            searches = int(getattr(st, "web_search_requests", 0) or 0) if st else 0
            fetches = int(getattr(st, "web_fetch_requests", 0) or 0) if st else 0
            usd = (g("input_tokens") * PRICE["in"] + g("output_tokens") * PRICE["out"] + g("cache_read_input_tokens") * PRICE["read"]
                   + w5m * PRICE["write_5m"] + w1h * PRICE["write_1h"]) / 1e6
            rec = {"t": dt.datetime.now().isoformat(timespec="seconds"), "kind": kind, "sec": round(sec, 1),
                   "in": g("input_tokens"), "out": g("output_tokens"), "read": g("cache_read_input_tokens"),
                   "write_5m": w5m, "write_1h": w1h, "searches": searches, "fetches": fetches, "usd_est": round(usd, 5)}
            if extra:
                rec.update(extra)
            with API_LEDGER.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
        except Exception as e:
            _log(f"帳本寫不進去：{e}")

if __name__ == "__main__":
    # 自測：連問三句看秒數
    import importlib
    sys.path.insert(0, str(HERE))
    try:
        s = importlib.import_module("感知哨兵")
        cfg = s.load_config()
        pack = s.context_pack(dt.datetime.now())
    except ImportError:
        # 這份參考版還沒附哨兵：用空設定＋一句示範記憶也能自測
        cfg, pack = {}, "（示範記憶：使用者養貓，清晨才睡。）"
    b = Brain(cfg)
    name, city = SETTINGS["人設"]["AI名字"], SETTINGS["所在地"]["城市"]
    for q in (f"{name}你在嗎？", f"明天{city}天氣預測是什麼", "後天呢？要帶傘嗎"):
        t0 = time.time()
        print("→", b.ask(q, "（測試：沒有身體資料）", pack), f"({time.time() - t0:.1f}s)")
    b.close()
