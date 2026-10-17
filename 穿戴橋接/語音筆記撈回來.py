#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
把手錶上的錄音撈回來、用**她自己電腦上的 Whisper** 轉成文字，變成可對照的標記。

## 這是 使用者 2026-08-19 提的（而且提得很好）

> 「如果有什麼狀況我們之前設計的按鍵沒有的，我就直接語音錄一段給你…
>   每個錄音應該都有時間戳吧，這樣會不會更好的做這個影子測試呢？」

**會。** 手腕上那八顆按鈕是固定分類，但影子模式最有價值的一筆，
往往正好是「發生了一件按鈕沒有的怪事」—— 那種事沒有格子可以放，
以前只能等她事後想起來跟我講，而事後回想的時間戳是不準的。

## ⚠ 但按鈕不能因此不按 —— 兩個要一起用

**先按按鈕，再錄音。**
  · 按鈕給的是**精確的時刻**（那才是資料對得上的關鍵）
  · 錄音給的是**內容**（按鈕沒有的分類）

錄音本身的時間戳是「檔案寫完的時間」減掉長度 —— 準到秒，
但她要先想、再舉手、再按錄音，中間可能過了半分鐘。
**半分鐘在心率資料上就是三十筆。**

## 隱私：轉錄完全在本機

用她自己 `<voice-venv>/venv` 裡的 faster-whisper（large-v3-turbo，跑她的 GPU）。
**錄音檔不上任何雲端。** 這條不是效能考量，是這個專案的前提 ——
資料裡有她父母的醫療脈絡，而他們沒有同意過任何事。

轉完的文字會套用她既有的 `corrections.json` 錯字表
（無線偵錯、摩斯密碼、Bixby 那些），跟她平常打字走同一套。

## ⚠ 寫到哪裡：**不要寫進 `手錶橋接/*.jsonl`**

那個檔每次 `watch_puller` 拉資料都是**整個覆蓋**（`out.write_bytes(data)`）。
寫進去下一輪就沒了，而且不會有任何錯誤訊息 —— 又是一次安靜的資料損失。
所以語音筆記存自己的資料夾。

用法：
    python 語音筆記撈回來.py           # 撈新的、轉錄
    python 語音筆記撈回來.py --列出     # 只看手錶上有什麼，不下載
"""

from __future__ import annotations

import json
import re
import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0  # 2026-09-23 不閃黑框（使用者 看到了）
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADB = "C:/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe"
WHISPER_PY = Path("C:/Users/<you>/Desktop/<voice-venv>/venv/Scripts/python.exe")
CORRECTIONS = Path("C:/Users/<you>/Desktop/<voice-venv>/corrections.json")

OUT_AUDIO = HERE.parent / "生理資料" / "語音筆記" / "原始錄音"
OUT_TEXT = HERE.parent / "生理資料" / "語音筆記"

# 手錶上錄音可能落腳的地方。Samsung 在不同版本放過不同資料夾，
# **兩個都掃** —— 少掃一個的代價是「她錄了但我沒看到」，那是最糟的失敗方向。
WATCH_DIRS = ["/storage/emulated/0/Recordings", "/storage/emulated/0/Sounds"]
AUDIO_EXT = (".m4a", ".mp3", ".amr", ".wav", ".3gp", ".ogg")



def _序號(line: str) -> str:
    """從 `adb devices -l` 的一行取序號。

    ⚠ **不可以用 `line.split()[0]`** —— mDNS 的序號含空格：
      `adb-<watch-serial>-sziG0j (2)._adb-tls-connect._tcp device product:...`
      切錯之後每一條 adb 指令都會失敗，而且是**安靜地**失敗 ——
      2026-08-26 因為這個，兩筆錄音躺在錶上沒撈到，程式卻印
      「手錶上目前沒有任何錄音檔」。
    """
    for state in (" device ", " offline ", " unauthorized ", " authorizing "):
        if state in line:
            return line.split(state)[0].strip()
    return line.split()[0]

def watch_serial() -> str | None:
    r = subprocess.run([ADB, "devices", "-l"], capture_output=True, text=True, creationflags=_NO_WINDOW)
    for line in r.stdout.splitlines()[1:]:
        if "model:SM_R" in line or "model:SM-R" in line:
            return _序號(line)   # ⚠ 不可以用 split()[0]：mDNS 序號含空格
    return None


def sh(serial: str, cmd: str) -> str:
    r = subprocess.run([ADB, "-s", serial, "shell", cmd],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    return r.stdout.replace("\r", "")


def phone_serial() -> str | None:
    r = subprocess.run([ADB, "devices", "-l"], capture_output=True, text=True, creationflags=_NO_WINDOW)
    for line in r.stdout.splitlines()[1:]:
        if " device " not in line:
            continue
        if "model:SM_S" in line or "model:SM-S" in line:
            return _序號(line)
    return None


# ── 2026-10-17：**第零條路：8788 走 Wi-Fi，不用線。** ────────────────
#
#   👤 使用者：「我偶爾會用錄音來跟你傳消息，在不方便用手機的時候。」
#   手機 app code5 起有 /recordings 路由（列出／下載／刪除），跟生理資料同一條 Wi-Fi。
#   USB（run-as）降為第二條，手錶無線偵錯第三條。
def _http():
    import importlib
    sys.path.insert(0, str(HERE))
    return importlib.import_module("叫手機")

def list_http_recordings() -> list[dict] | None:
    """回 None 表示這條路不通（手機不在同一個 Wi-Fi、app 舊版沒這路由…），不是「沒錄音」。"""
    import urllib.request, urllib.error
    try:
        base, token = _http().load_config()
        req = urllib.request.Request(base + "/recordings", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    out = []
    for f in d.get("files", []):
        name = f.get("name", "")
        if not name.lower().endswith(AUDIO_EXT):
            continue
        out.append({"path": name, "name": name, "mtime": int(f.get("modified", 0)) // 1000,
                    "size": int(f.get("bytes", 0)), "on_http": True})
    return sorted(out, key=lambda x: x["mtime"])

def http_download(name: str, local: Path) -> bool:
    import urllib.request, urllib.parse
    try:
        base, token = _http().load_config()
        req = urllib.request.Request(base + "/recordings/" + urllib.parse.quote(name),
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            local.write_bytes(resp.read())
        return local.exists()
    except Exception as e:
        print(f"  X HTTP 下載失敗 {name}：{e}")
        return False

def http_delete(name: str) -> bool:
    import urllib.request, urllib.parse
    try:
        base, token = _http().load_config()
        req = urllib.request.Request(base + "/recordings/" + urllib.parse.quote(name), method="DELETE",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok") is True
    except Exception:
        return False


PHONE_DIR = "files/錄音"


def list_phone_recordings(serial: str) -> list[dict]:
    """手機上那份 —— 是手錶走藍牙送過來的，**不需要無線偵錯**。

    路徑在 app 的私有目錄，所以要走 `run-as`。
    """
    out = []
    txt = sh(serial, f"run-as com.example.watchbridge sh -c \"ls -1 '{PHONE_DIR}' 2>/dev/null\"")
    for name in txt.splitlines():
        name = name.strip()
        if not name or not name.lower().endswith(AUDIO_EXT):
            continue
        st = sh(serial,
                f"run-as com.example.watchbridge sh -c \"stat -c '%Y %s' '{PHONE_DIR}/{name}'\"").strip()
        m = re.match(r"(\d+)\s+(\d+)", st)
        if not m:
            continue
        out.append({"path": f"{PHONE_DIR}/{name}", "name": name,
                    "mtime": int(m.group(1)), "size": int(m.group(2)),
                    "on_phone": True})
    return sorted(out, key=lambda x: x["mtime"])


def list_recordings(serial: str) -> list[dict]:
    """回 [{path, size, mtime}]。mtime 用 `stat` 拿 epoch，不要解析 ls 的文字 ——
    ls 的日期格式會隨語系變，而語系是會變的。"""
    out = []
    for d in WATCH_DIRS:
        txt = sh(serial, f"find '{d}' -type f 2>/dev/null")
        for p in txt.splitlines():
            p = p.strip()
            if not p or not p.lower().endswith(AUDIO_EXT):
                continue
            st = sh(serial, f"stat -c '%Y %s' '{p}' 2>/dev/null").strip()
            m = re.match(r"(\d+)\s+(\d+)", st)
            if not m:
                continue
            out.append({"path": p, "mtime": int(m.group(1)), "size": int(m.group(2))})
    return sorted(out, key=lambda x: x["mtime"])


def duration_sec(local: Path) -> float | None:
    """從 m4a/mp4 的 mvhd atom 讀長度。**不裝 ffmpeg** ——
    多一個外部相依就多一個家人那邊裝不起來的東西。"""
    try:
        b = local.read_bytes()
        i = b.find(b"mvhd")
        if i < 0:
            return None
        ver = b[i + 4]
        if ver == 0:
            scale = int.from_bytes(b[i + 16:i + 20], "big")
            dur = int.from_bytes(b[i + 20:i + 24], "big")
        else:
            scale = int.from_bytes(b[i + 24:i + 28], "big")
            dur = int.from_bytes(b[i + 28:i + 36], "big")
        return dur / scale if scale else None
    except Exception:
        return None


def load_corrections() -> dict:
    if not CORRECTIONS.exists():
        return {}
    try:
        return json.loads(CORRECTIONS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fix_text(text: str, corr: dict) -> str:
    """用她既有的錯字表。跟 voxpress 的 `_apply_corrections` 同一套邏輯。"""
    for wrong, right in corr.items():
        if wrong.startswith("_"):
            continue
        if wrong in text:
            text = text.replace(wrong, right)
    return text


TRANSCRIBE = r"""
import sys, os, json, glob
# ⚠ 2026-08-20 實測踩到：直接跑會噴
#     RuntimeError: Library cublas64_12.dll is not found or cannot be loaded
#   原因是 CUDA 的 DLL 裝在 `site-packages/nvidia/*/bin`，
#   voxpress 平常跑得動是因為它自己在啟動時加了 DLL 搜尋路徑。
#   我用 subprocess 直接叫 python 就沒有那一步 —— **同一個 venv、不同的結果**。
base = os.path.join(os.path.dirname(os.path.dirname(sys.executable)),
                    "Lib", "site-packages", "nvidia")
for d in glob.glob(os.path.join(base, "*", "bin")):
    try:
        os.add_dll_directory(d)
    except Exception:
        pass

from faster_whisper import WhisperModel
MODEL = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
# ⚠ GPU 失敗一定要退回 CPU，不要整個放棄。
#   22 秒的錄音在 CPU 上也就幾秒 —— **能轉出來遠比轉得快重要**。
# ⚠ 2026-08-20 第二次踩到：只包住「建模型」是不夠的。
#   CUDA 那顆 DLL 是**在真的開始編碼的時候**才載入，所以
#   `WhisperModel(device="cuda")` 會成功、`transcribe()` 才爆。
#   **退路必須包住整個轉錄流程，不是只包建構式。**
def run(dev, ct):
    m = WhisperModel(MODEL, device=dev, compute_type=ct)
    segs, info = m.transcribe(sys.argv[1], language="zh", beam_size=5,
                              initial_prompt="以下是繁體中文口語對話。")
    return "".join(s.text for s in segs).strip(), info, dev

try:
    out, info, dev = run("cuda", "float16")
except Exception:
    out, info, dev = run("cpu", "int8")
# ⚠ 2026-08-20 第三次踩到：**child process 的 stdout 預設用系統編碼（cp950）**，
#   中文寫出去就是一堆亂碼，而父行程用 errors="replace" 讀 —— 資訊當場毀掉、
#   而且**不會報錯**，只會得到一段看起來像字的垃圾。
#   解法：直接寫 bytes，明確指定 UTF-8，不經過任何文字層。
sys.stdout.buffer.write(json.dumps(
    {"text": out, "dur": info.duration, "dev": dev},
    ensure_ascii=False).encode("utf-8"))
"""


def transcribe(local: Path) -> tuple[str, float | None]:
    if not WHISPER_PY.exists():
        return "（找不到本機 Whisper，沒有轉錄）", None
    import os
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([str(WHISPER_PY), "-c", TRANSCRIBE, str(local)],
                       capture_output=True, env=env, creationflags=_NO_WINDOW)
    # 自己解 bytes，不讓 subprocess 用系統編碼去猜
    stdout = r.stdout.decode("utf-8", "replace")
    stderr = r.stderr.decode("utf-8", "replace")
    try:
        d = json.loads(stdout.strip().splitlines()[-1])
        return d["text"], d.get("dur")
    except Exception:
        return f"（轉錄失敗：{stderr.strip()[-200:]}）", None


def main() -> None:
    # Windows 主控台預設 cp950，印到轉錄出來的字就會 UnicodeEncodeError ——
    # 檔案已經寫好了卻在「印出來給人看」這一步炸掉，很容易被誤判成轉錄失敗。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # ── 2026-08-26：**先問手機，手錶當備援。** ───────────────────
    #
    #   手錶的無線偵錯**會自己關**，而錄音因此囤在錶上撈不到 ——
    #   她 08-25 錄的兩筆躺了三天，程式還印「沒有任何錄音檔」。
    #
    #   現在手錶 app 會把錄音走藍牙（DataClient + Asset）送到手機的
    #   `files/錄音/`，而**手機用 USB 接著就一直在**。
    #   端到端已驗證：375,265 bytes 逐位元組送達。
    #
    #   ⚠ 順序不能反。手錶那條要開無線偵錯、而且開了會自己關；
    #     手機那條只要線插著就成立。**先試穩的那條。**
    src = None
    p_serial = None
    http_recs = list_http_recordings()
    if http_recs is not None:
        src = ("http", None)
        recs = http_recs
        if recs:
            print(f"手機 8788 上有 {len(recs)} 個錄音（走 Wi-Fi，不用線）：")
        else:
            print("手機 8788 說：目前沒有錄音（這條路是通的，所以這句可信）。")
            return
    else:
        p_serial = phone_serial()
    if p_serial:
        recs = list_phone_recordings(p_serial)
        if recs:
            src = ("phone", p_serial)
            print(f"手機上有 {len(recs)} 個錄音（走藍牙同步過來的）：")
    if src is None:
        s_serial = watch_serial()
        if not s_serial:
            # ⚠ 這裡**不可以**說「沒有錄音」—— 兩台都連不上只代表我看不到。
            print("手機和手錶都連不上 —— **我沒辦法看，這不等於沒有錄音**。")
            print("  手機：USB 接上並允許偵錯　手錶：開無線偵錯")
            raise SystemExit(1)
        recs = list_recordings(s_serial)
        if not recs:
            print("手錶上目前沒有任何錄音檔。")
            print(f"（找過：{'、'.join(WATCH_DIRS)}）")
            return
        src = ("watch", s_serial)
        print(f"手錶上有 {len(recs)} 個錄音：")
    s_serial = src[1]
    for r in recs:
        t = datetime.fromtimestamp(r["mtime"])
        print(f"  {t:%m-%d %H:%M:%S}  {r['size']/1024:7.0f} KB  {r['path']}")
    if "--列出" in sys.argv:
        return

    OUT_AUDIO.mkdir(parents=True, exist_ok=True)
    OUT_TEXT.mkdir(parents=True, exist_ok=True)
    corr = load_corrections()

    for r in recs:
        name = Path(r["path"]).name
        local = OUT_AUDIO / name
        # ⚠ 已經撈過就跳過，但**用大小比對不是只看存在** ——
        #   錄一半被中斷的檔案會先出現、之後才長大。
        if local.exists() and local.stat().st_size == r["size"]:
            # ⚠ 2026-08-21 修：本來這裡直接 continue，結果
            #   **已經撈過、也轉錄過的錄音永遠不會從手錶上刪掉** ——
            #   刪除那段程式在下面，被這個 continue 跳過了。
            #   08-20 那筆錄音就這樣在手錶上多躺了一天。
            #   （症狀跟「補送餓死」同一形狀：後面的程式碼永遠排不到隊。）
            day0 = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d")
            m0 = re.search(r"(\d{8})_(\d{6})", name)
            if m0:
                day0 = f"{m0.group(1)[:4]}-{m0.group(1)[4:6]}-{m0.group(1)[6:]}"
            done = OUT_TEXT / f"{day0}.jsonl"
            if done.exists() and name.rsplit(".", 1)[0] in done.read_text(encoding="utf-8"):
                if r.get("on_http"):
                    http_delete(r["name"])
                elif r.get("on_phone"):
                    subprocess.run(
                        [ADB, "-s", s_serial, "shell",
                         f"run-as com.example.watchbridge rm -f '{r['path']}'"],
                        capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
                else:
                    subprocess.run([ADB, "-s", s_serial, "shell", "rm", f"'{r['path']}'"],
                                   capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
                print(f"  {name} 之前就撈過也轉錄過了 → 裝置上那份已刪")
            else:
                print(f"  {name} 撈過但找不到轉錄紀錄 → 手錶上那份保留")
            continue
        # 手機那份在 app 私有目錄（`files/錄音/`），**`adb pull` 拉不到** ——
        # 要走 `exec-out run-as … cat`。手錶那份在公開儲存區，pull 就好。
        if r.get("on_http"):
            http_download(r["name"], local)
        elif r.get("on_phone"):
            got = subprocess.run(
                [ADB, "-s", s_serial, "exec-out",
                 f"run-as com.example.watchbridge cat '{r['path']}'"],
                capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
            if got.stdout:
                local.write_bytes(got.stdout)
        else:
            subprocess.run([ADB, "-s", s_serial, "pull", r["path"], str(local)],
                           capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        if not local.exists():
            print(f"  X 撈不回來：{name}")
            continue

        dur = duration_sec(local)
        end = datetime.fromtimestamp(r["mtime"])
        # ★ 2026-08-20 實測：**三星把開始時間寫在檔名裡**，比推算的準。
        #   實際檔名：`語音 001_W_20260820_022941.m4a`
        #   `_W_` 是「在手錶上錄的」，後面是 YYYYMMDD_HHMMSS。
        #   優先用它；抓不到才退回「寫檔時間減長度」的推算。
        m = re.search(r"(\d{8})_(\d{6})", name)
        if m:
            start = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            t_src = "檔名"
        else:
            start = end - timedelta(seconds=dur) if dur else end
            t_src = "推算（寫檔時間減長度）"
        text, wdur = transcribe(local)
        text = fix_text(text, corr)

        day = start.strftime("%Y-%m-%d")
        rec = {
            "t": start.isoformat(timespec="seconds"),
            "type": "mark",
            "tag": "voice",
            "label": "語音筆記",
            "text": text,
            # ⚠ 兩個時間都留著。`t` 是**推算的開始**（寫檔時間減長度），
            #   `t_file` 是**檔案系統的事實**。推算會錯（例如錄音 app 邊錄邊寫），
            #   而事後查的時候，分得出「哪個是量到的、哪個是算的」很重要。
            "t_file": end.isoformat(timespec="seconds"),
            # 分得出「量到的」跟「算的」—— 事後查問題時差很多
            "t_src": t_src,
            "dur_s": round(dur or wdur or 0, 1),
            "src": r["path"],
        }
        f = OUT_TEXT / f"{day}.jsonl"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"\n  {start:%H:%M:%S}（長 {rec['dur_s']}s）→ {f.name}")
        print(f"    「{text}」")

        # ── 撈成功而且轉錄成功，就把手錶上那份刪掉 ────────────
        #
        # 使用者 2026-08-20 問的：「會不會越存越多後來爆掉？
        # 因為那只是我們做實驗快速用的東西而已」—— 她問得對。
        # 錄音約 1 MB/分鐘，手錶還要放一天 18 MB 的生理資料，不能讓它無限長。
        #
        # ⚠ 但**三個條件都成立才刪**，少一個就留著：
        #   ① 本機檔案大小跟手錶上一樣（撈完整了）
        #   ② 轉錄有結果、不是錯誤訊息
        #   ③ jsonl 那一行真的寫進去了
        # 刪錯的代價是她講的那段話**永遠回不來** —— 寧可手錶塞一點，不要刪掉證據。
        # 而且本機那份 .m4a **不刪**，留在 生理資料/語音筆記/原始錄音/。
        ok_size = local.exists() and local.stat().st_size == r["size"]
        ok_text = bool(text) and not text.startswith("（")
        ok_row = f.exists() and rec["t"] in f.read_text(encoding="utf-8")
        if ok_size and ok_text and ok_row:
            # 手機那份在 app 私有目錄，要走 run-as 才刪得掉。
            if r.get("on_http"):
                print("    （手機上那份已刪，本機留著）" if http_delete(r["name"]) else "    ⚠ 手機上那份刪不掉，保留")
            elif r.get("on_phone"):
                subprocess.run(
                    [ADB, "-s", s_serial, "shell",
                     f"run-as com.example.watchbridge rm -f '{r['path']}'"],
                    capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
                print("    （手機上那份已刪，本機留著）")
            else:
                subprocess.run([ADB, "-s", s_serial, "shell", "rm", f"'{r['path']}'"],
                               capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
                print("    （手錶上那份已刪，本機留著）")
        else:
            why = [t for t, ok in [("檔案沒撈完整", ok_size),
                                   ("轉錄沒成功", ok_text),
                                   ("紀錄沒寫進 jsonl", ok_row)] if not ok]
            print(f"    ⚠ 手錶上那份**保留**（{'、'.join(why)}）")


if __name__ == "__main__":
    main()
