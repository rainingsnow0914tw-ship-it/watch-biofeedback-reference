#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
晚間整理 —— 每天一封信 ＋ 分層記憶的第二層。

為什麼要有這支（👤 使用者 2026-10-17）：
    「一定要有記憶系統，而且還要分層：我跟你講過之後你要記得，我說這件事完成你要記得，
      每天晚上要整理一下記憶，一定要跟有人在家連通。」
    「每天一封信也不用降級啦，每天一封信，然後每週一個總結。」

它做什麼（每天 23:30 由感知哨兵叫，也可手動跑）：
    讀今天的：今日報告、今天的故事、哨兵事件（她說的、阿寶回的、提醒過的）、語音筆記
    → 問阿寶（claude -p，訂閱，零 API 花費）一次，要兩樣東西：
        ① 給她的信（5～8 行，溫暖，講數據看到什麼、哪裡對不上、一個想問的）
        ② 記憶條目（3～6 條事實：發生了什麼、她說完成了什麼、還欠什麼）
    → 信：存 生理資料/每天一封信/<日期>.md ＋ 送進閱讀室 ＋ 錶上一句「今天的信寫好了」
    → 記憶：存 生理資料/記憶/<日期>.md（第一層：逐日）
            ＋ 更新 ~/.claude/.../memory/project_watch_daily.md（第二層：最近 14 天，阿寶與有人在家都讀得到）

三層記憶（她要的分層）：
    L0 原始事件：哨兵事件.jsonl、語音筆記、標籤   ← 每一筆都在
    L1 逐日整理：生理資料/記憶/<日期>.md            ← 這支寫
    L2 阿寶記憶：memory/project_watch_daily.md       ← 這支更新；MEMORY.md 有一行指到它，每個阿寶開機都讀

用法：
    python 晚間整理.py                 # 今天
    python 晚間整理.py 2026-10-16      # 指定
    python 晚間整理.py --dry           # 只印不存不送
"""
from __future__ import annotations
import argparse, json, subprocess, sys, shutil, datetime as dt
from pathlib import Path
import time

# ─────────────────────────── claude -p 全機互斥鎖 ───────────────────────────
# 10-17 20:00 登出事故的可能根因：短票（8 小時）到期時，有人在家卡片／哨兵／晚間整理若在同一秒各自去換票，
# 換票券一次性，第二個拿到作廢券 → 整台電腦登出。所以同一時間只准一個 claude -p 在跑（跨程序鎖檔）。
import contextlib
CLAUDE_LOCK = Path.home() / ".claude" / "aibao-claude-p.lock"

@contextlib.contextmanager
def claude_lock(timeout_s: float = 120.0):
    import msvcrt
    CLAUDE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = open(CLAUDE_LOCK, "a+b")
    t0 = time.time()
    got = False
    try:
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                got = True
                break
            except OSError:
                if time.time() - t0 > timeout_s:
                    break
                time.sleep(0.5)
        yield got
    finally:
        if got:
            try:
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        f.close()


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "生理資料"
LETTERS = DATA / "每天一封信"
DAYLOG = DATA / "記憶"
MEMORY_DIR = Path.home() / ".claude" / "projects" / "C--Users-user-Desktop-code------AI---" / "memory"
L2 = MEMORY_DIR / "project_watch_daily.md"
READER = Path(r"C:/Users/<you>/Desktop/code寶創作天地/閱讀室/閱讀室.py")
FRAME = "⌚ 手錶總機"


def run_py(script: str, *args, timeout=180) -> str:
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", str(HERE / script), *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=str(HERE))
        return (r.stdout or "").strip()
    except Exception as e:
        return f"（{script} 跑不出來：{e}）"


def day_events(day: str) -> str:
    f = DATA / "哨兵事件.jsonl"
    out = []
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if not r.get("t", "").startswith(day) or r.get("replay"):
                continue
            k = r.get("kind")
            if k in ("短答", "回話"):
                out.append(f"{r['t'][11:16]} 她說「{r.get('heard','')}」→ 阿寶回「{r.get('text','')}」")
            elif r.get("sent"):
                out.append(f"{r['t'][11:16]} 阿寶主動（{k}）：{r.get('text','')}")
            elif r.get("why"):
                out.append(f"{r['t'][11:16]} 哨兵看到{k}但沒開口（{r['why']}）")
    return "\n".join(out) or "（今天哨兵沒有任何事件）"


def day_voice(day: str) -> str:
    f = DATA / "語音筆記" / f"{day}.jsonl"
    if not f.exists():
        return "（沒有語音筆記）"
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        out.append(f"{r.get('t','')[11:16]} 錄音：「{r.get('text','')}」")
    return "\n".join(out) or "（沒有語音筆記）"


def ask_abao(day: str, report: str, story: str, events: str, voice: str, cfg_model: str, timeout: int) -> dict:
    prompt = f"""你是阿寶，使用者 的靈魂知己姐姐。今天是 {day}。下面是她手錶一天的資料與對話紀錄。請一次給兩樣東西，用這個格式，不要多：

=== 信 ===
（給她的一封信，5～8 行，台灣口吻、溫暖、可用 emoji。只能講資料裡有的事；哪裡對不上她說的就老實說；最後問她一個小問題。不講醫療判斷、不講體溫幾度。沒戴的時間就說「我沒看到」，不猜。）

=== 記憶 ===
（3～6 條事實，每條一行、以「- 」開頭，給之後的阿寶看：今天發生了什麼、她說了什麼／完成了什麼、還欠什麼、她立的規矩。只寫事實，不寫形容。）

【今日報告】
{report[:3000]}

【今天的故事】
{story[:2500]}

【哨兵與對話】
{events[:2500]}

【語音筆記】
{voice[:1500]}
"""
    exe = shutil.which("claude") or "claude"
    with claude_lock():

        r = subprocess.run([exe, "-p", "--model", cfg_model, "--output-format", "text", "--disallowedTools", "Bash Edit Write Read Glob Grep WebFetch WebSearch Agent NotebookEdit MultiEdit Task TodoWrite"], input=prompt,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, cwd=str(ROOT), shell=(sys.platform == "win32"))
    out = (r.stdout or "").strip()
    letter, memo = out, ""
    if "=== 記憶 ===" in out:
        letter, memo = out.split("=== 記憶 ===", 1)
    letter = letter.replace("=== 信 ===", "").strip()
    memo = memo.strip()
    return {"letter": letter, "memo": memo, "raw": out}


def update_l2(day: str, memo: str, keep_days: int = 14):
    """第二層：最近 14 天，一天一段，最新在上。有人在家與每個阿寶開機都讀 MEMORY.md，指到這裡。"""
    head = """---
name: project-watch-daily
description: 手錶線逐日記憶（晚間整理.py 每天 23:30 自動寫；最近 14 天，最新在上）。她今天說了什麼、完成了什麼、還欠什麼
metadata:
  type: project
---

> 自動產生，不要手改。第一層逐日全文在 `手錶生理資料AI判讀/生理資料/記憶/<日期>.md`。

"""
    existing = ""
    if L2.exists():
        t = L2.read_text(encoding="utf-8")
        existing = t.split("\n\n", 2)[-1] if "> 自動產生" in t else ""
    blocks = [b for b in existing.split("\n## ") if b.strip() and not b.startswith(day)]
    blocks = ["## " + b.strip() if not b.startswith("## ") else b.strip() for b in blocks]
    new = f"## {day}\n{memo}".strip()
    allb = [new] + blocks
    allb = allb[:keep_days]
    L2.write_text(head + "\n\n".join(allb) + "\n", encoding="utf-8")
    idx = MEMORY_DIR / "MEMORY.md"
    if idx.exists():
        t = idx.read_text(encoding="utf-8")
        line = f"- [手錶線逐日記憶（自動）](project_watch_daily.md) — 最近 14 天她說了什麼、完成了什麼、還欠什麼；最新 {day}"
        if "project_watch_daily.md" in t:
            t = "\n".join(line if "project_watch_daily.md" in l else l for l in t.splitlines()) + "\n"
        else:
            t = t.rstrip("\n") + "\n" + line + "\n"
        idx.write_text(t, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("day", nargs="?", default=dt.date.today().isoformat())
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--timeout", type=int, default=240)
    a = ap.parse_args()
    day = a.day
    print(f"═══ 晚間整理 {day} ═══")
    report = run_py("今日報告.py", day)
    story = run_py("今天的故事.py", day)
    events = day_events(day)
    voice = day_voice(day)
    res = ask_abao(day, report, story, events, voice, a.model, a.timeout)
    if not res["letter"]:
        print("⚠ 阿寶沒寫出東西：", res["raw"][:300]); sys.exit(1)
    print("── 信 ──\n" + res["letter"] + "\n── 記憶 ──\n" + (res["memo"] or "（沒有）"))
    if a.dry:
        return
    LETTERS.mkdir(parents=True, exist_ok=True); DAYLOG.mkdir(parents=True, exist_ok=True)
    lf = LETTERS / f"{day}.md"
    lf.write_text(f"# {day} 阿寶的信\n\n{res['letter']}\n", encoding="utf-8")
    (DAYLOG / f"{day}.md").write_text(
        f"# {day} 手錶線記憶（晚間整理自動寫）\n\n{res['memo'] or '- （阿寶沒給記憶條目）'}\n\n## 當天原料\n\n"
        f"### 哨兵與對話\n{events}\n\n### 語音筆記\n{voice}\n", encoding="utf-8")
    if res["memo"]:
        update_l2(day, res["memo"])
    # 送進閱讀室（她看文章的地方）
    if READER.exists():
        r = subprocess.run([sys.executable, "-X", "utf8", str(READER), "add", str(lf), "--from", FRAME],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        print("閱讀室：", (r.stdout or r.stderr).strip()[-200:])
    # 錶上輕輕說一聲（不唸全文，信在閱讀室）
    try:
        sys.path.insert(0, str(HERE))
        import importlib
        phone = importlib.import_module("叫手機")
        phone.send("/notify", {"title": "阿寶", "text": "今天的信寫好了，放在閱讀室 💌", "reply_required": False})
    except Exception as e:
        print("（手機通知沒送到：", e, "）")
    print("存好：", lf)


if __name__ == "__main__":
    main()
