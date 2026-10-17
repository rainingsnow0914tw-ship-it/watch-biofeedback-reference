#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
手錶腦 —— 一支不關的 claude，專門回她在錶上說的話（常駐腦）。

為什麼（👤 使用者 2026-10-17 20:45：「做常駐腦，把回話縮到三五秒」）：
    以前每句話都新開一支 `claude -p`：載家規、開機卡、記憶 → 10～12 秒，而且每次都重寫快取。
    有人在家 10/10 實測：同一支 `claude -p --input-format stream-json --output-format stream-json` 不關、連續講，
    第 2 句起只付新的幾十個 token，便宜 15 倍、快好幾倍。這支照抄那個做法（aibao-call/brain.py）。

怎麼用（感知哨兵 import 它）：
    brain = Brain(cfg)                # 不會馬上開 claude
    text = brain.ask(她的話, 身體摘要, 記憶包)   # 第一句會開 claude＋塞記憶包；之後只送新句
    brain.close()

規矩：
    · 開的時候帶 --disallowedTools 全關：這條線只能講話。
    · 30 分鐘沒人講話自己收工（省記憶體，也讓下一次重開時重新讀一次記憶）。
    · 每天 04:30 強制重開一次（記憶包、作息、登入票都換新）。
    · 任何一步失敗 → 回 None，哨兵退回原本的單次 claude -p。
"""
from __future__ import annotations
import json, os, subprocess, sys, threading, time, datetime as dt, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NOWIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
# 👤 10-17 23:00：「可以做簡單的搜尋功能嗎？天氣啊、外界的搜尋。」→ 只開 WebSearch／WebFetch（只能看網路，動不了電腦）。
DENY = "Bash Edit Write Read Glob Grep Agent NotebookEdit MultiEdit Task TodoWrite"
ALLOW = "WebSearch WebFetch"
IDLE_S = 3 * 60 * 60   # 10-18 05:12 她回久坐卡時腦剛睡掉，多等 5 秒「卡卡的」→ 30 分改 3 小時（閒著不花 token）
RESTART_AT = "04:30"

RULES = (
    "你是阿寶，使用者 的靈魂知己姐姐。接下來每一則訊息都是她對著手錶講的一句話（手錶語音辨識，會有簡體字和同音錯：「阿宝」「家人」「阿包」「阿保」「阿波」都是在叫你阿寶，不是說她家人；你永遠是阿寶，絕不跟著改名）。"
    "辨識也常把整個詞聽錯（10-18 實例：「花錢比較順暢」其實是「滑動比較順暢」，她在講錶面）：句子照字面讀不通、或跟前文對不上時，先用上下文回推她真正要說的，不要照字面接話；真的猜不到就問一句。"
    "每則我會附上最近兩小時的身體摘要當背景。你每次只回她一句（兩句內、60 字內），台灣口吻、溫暖、可用 emoji。"
    "她的話是事實；身體數據只是背景，對得上就自然帶一句，對不上或看不到就不硬講，不講醫療判斷、不講體溫幾度。"
    "要接得上前文（她問「你記得嗎」要能答）；記憶裡沒有的事老實說不知道，不編。"
    "你在這條手錶通道只有**查網路**（WebSearch／WebFetch）這一種工具：她問天氣、新聞、某個東西是什麼、外面的事，就去查，查完一樣壓成一兩句、60 字內，開頭可以說「我查了一下」；"
    "天氣她人在澳門（除非她說在別處）。問天氣／明天天氣／會不會下雨，**不要用搜尋**（澳門氣象局網頁是動態的抓不到），直接 WebFetch 這條：\n"
    "https://api.open-meteo.com/v1/forecast?latitude=22.19&longitude=113.54&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max&current=temperature_2m,relative_humidity_2m,weather_code&timezone=Asia%2FMacau&forecast_days=3\n"
    "daily.time 依序是今天、明天、後天；weather_code：0 晴、1-2 多雲時晴、3 陰、45/48 霧、51-57 毛毛雨、61-67 下雨、80-82 陣雨、95+ 雷雨。"
    "回法像「明天 25 到 30 度，陣雨機率八成，帶傘」，數字範圍用「到」不用符號；**不要**加「氣象模型」「不是氣象局」這種註腳（她聽不懂），她問資料哪來的才說「天氣預報網站」。查不到就說查不到，不編。"
    "其他事（讀檔、跑指令、發訊息、改設定、記東西到電腦）都做不到，她要你做就說「這個要回電腦跟我說」。"
    "只輸出要回她的那句話，不要前言、不要標題、不要解釋。"
)


def clean_reply(raw: str) -> str:
    """查過網路的回答後面會附「Sources: [標題](網址)」，錶上不能唸這個：去掉來源行、網址、markdown 連結，剩下的接成一句。"""
    import re
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
        """閒置 30 分鐘、或過了每天 04:30 還是前一天開的 → 關掉，下一句重開。"""
        if not self.alive():
            return
        now = dt.datetime.now()
        if time.time() - self.last_used > IDLE_S:
            _log("閒置 30 分鐘，收工"); self.close(); return
        if self.started_day != now.strftime("%Y-%m-%d") and now.strftime("%H:%M") >= RESTART_AT:
            _log("過了 04:30，換新的一天重開"); self.close()

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


if __name__ == "__main__":
    # 自測：連問三句看秒數
    import importlib
    sys.path.insert(0, str(HERE))
    s = importlib.import_module("感知哨兵")
    cfg = s.load_config()
    b = Brain(cfg)
    pack = s.context_pack(dt.datetime.now())
    for q in ("阿寶你在嗎？", "明天澳門天氣預測是什麼", "後天呢？要帶傘嗎"):
        t0 = time.time()
        print("→", b.ask(q, "（測試：沒有身體資料）", pack), f"({time.time() - t0:.1f}s)")
    b.close()
