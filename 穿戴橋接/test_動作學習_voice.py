"""不碰錶、不寫真資料：事件檔、50Hz 檔都寫在暫存資料夾。

跑法：python -X utf8 test_動作學習_voice.py"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import 動作學習 as m

TMP = Path(tempfile.mkdtemp())
ok = fail = 0


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1; print(f"PASS {name}")
    else:
        fail += 1; print(f"FAIL {name}  {info}")


def ev(t, phase, label, heard=""):
    return {"t": t, "kind": "教動作", "phase": phase, "label": label, "heard": heard, "sent": True}


EVENTS = TMP / "哨兵事件.jsonl"
rows = [
    {"t": "2026-11-07T05:00:00", "kind": "短答", "heard": "我在家"},                    # 別的事件不影響
    ev("2026-11-07T05:25:20", "開始", "吹頭髮", "阿寶，我在吹頭髮"),                    # 哨兵比錶晚 20 秒
    ev("2026-11-07T05:30:05", "結束", "吹頭髮", "吹完了"),
    ev("2026-11-07T21:10:40", "開始", "吹头", "我在吹头"),                              # 簡體，同一類
    ev("2026-11-07T21:14:00", "開始", "沙發上滑手機", "記一下我在沙發上滑手機"),        # 換下一個動作
    ev("2026-11-07T21:20:00", "結束", "沙發上滑手機", "好了"),
    ev("2026-11-07T23:00:00", "開始", "摺衣服", "我正在摺衣服"),                        # 沒說好了 → 10 分鐘
]
EVENTS.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
ses = m.voice_sessions(EVENTS, days=3650)

check("切出 4 段", len(ses) == 4, [(x["label"], x["start"], x["end"]) for x in ses])
check("吹頭／吹頭髮合成一類，類名用「吹頭髮」", {x["cls"] for x in ses if "吹" in x["label"]} == {"吹頭髮"}, [x["cls"] for x in ses])
s3 = next(x for x in ses if x["label"] == "吹头")
check("換下一個動作：吹头那段在 21:14 收", s3["end"] == dt.datetime(2026, 11, 7, 21, 14) and s3["how"] == "換下一個動作", s3)
s4 = next(x for x in ses if x["label"] == "摺衣服")
check("沒說好了：算 10 分鐘", s4["end"] - s4["start"] == dt.timedelta(minutes=10), s4)


def f(stamp):
    return TMP / f"{stamp}_voice.jsonl"


cases = [
    ("20261107_052500", "阿寶，我在吹頭髮", "錶比哨兵早 20 秒開錄 → 配到吹頭髮"),
    ("20261107_052930", "阿寶，我在吹頭髮", "錄音中段 → 同一段"),
    ("20261107_211035", "我在吹头", "簡體那句也配得到"),
    ("20261107_211355", "記一下我在沙發上滑手機", "兩段交界（吹头 21:10:40／沙發 21:14）→ 挑開始最近的沙發"),
    ("20261107_225900", "我正在摺衣服", "沒說好了的那段也配得到"),
]
for stamp, heard, why in cases:
    got = m.voice_match(f(stamp), ses)
    check(f"{why}", got is not None and got["heard"] == heard, got and got["heard"])

for stamp, why in [("20261107_120000", "白天沒有任何教動作事件 → 對不到"),
                   ("20261107_051900", "早於開始 6 分鐘 → 對不到（超過 120 秒容許）"),
                   ("20261107_231100", "10 分鐘自動收之後 → 對不到"),
                   ("abc_voice", "檔名不是時間 → 對不到")]:
    p = TMP / f"{stamp}_voice.jsonl" if stamp[0].isdigit() else TMP / f"{stamp}.jsonl"
    check(why, m.voice_match(p, ses) is None)
check("對不到的檔標 voice?，不拿來學", m.voice_tag_for(f("20261107_120000"), ses) == "voice?")
check("對到的檔標成類名", m.voice_tag_for(f("20261107_211035"), ses) == "吹頭髮")

# 端到端：一個真的 50Hz voice 檔經過 load()，標籤要是類名（load 用預設事件檔，這裡暫時換掉）
m.EVENTS = EVENTS
orig_vs = m.voice_sessions
m.voice_sessions = lambda events=EVENTS, days=14: orig_vs(EVENTS, 3650)
wave = f("20261107_052505")
with wave.open("w", encoding="utf-8") as fh:
    fh.write(json.dumps({"header": True, "tag": "voice"}) + "\n")
    for i in range(300):
        ns = i * 20_000_000
        fh.write(json.dumps({"ns": ns, "s": "acc", "v": [0.1, 9.8, 0.2]}) + "\n")
        fh.write(json.dumps({"ns": ns, "s": "gyro", "v": [0.5, 0.1, 0.0]}) + "\n")
        fh.write(json.dumps({"ns": ns, "s": "grav", "v": [0.0, 9.8, 0.0]}) + "\n")
r = m.load(wave)
check("load()：voice 檔標籤變成「吹頭髮」", r["tag"] == "吹頭髮", r["tag"])
check("label_windows：口頭教的動作當連續型，每個窗都標", len(m.label_windows(r)) > 0 and all(l == "吹頭髮" for _, _, l in m.label_windows(r)))

print(f"\n{ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
