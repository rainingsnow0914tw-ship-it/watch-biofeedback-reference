"""存在錶的 files/motion_rec/<時間>_<標籤>.jsonl。這支負責後面全部：

    python 動作學習.py pull  [--serial <錶>]   # 從錶拉回 生理資料/動作錄音/
    python 動作學習.py look                     # 每段錄音的長度、筆數、切出幾次動作
    python 動作學習.py train [--depth 6]        # 訓練＋老實報準確率＋產生錶上的 MotionModel.kt
    python 動作學習.py selftest                 # 用假資料把整條管線跑一遍（不碰錶）

    檔名 <時間>_voice.jsonl，這裡用時間對回那段的標籤。

    錶上只要算 19 個數字、跑十幾個 if，不用載入任何函式庫、幾乎不耗電、
    而且電腦和錶的算法一模一樣（特徵算法在下面 features() 與 MotionModel.kt 各寫一份，
    準確率不夠才換 TFLite。

標籤（跟錶上 MotionLabActivity.OPTIONS 一致）：
    phone   拿手機看→放下（一段裡做 30 次，自動切成 phone_up／phone_down）
    drink   舉杯喝水（次數型）
    stretch 伸懶腰（次數型）
    typing  打字（連續型）
    writing 寫字（連續型）
    other   其他：不動、講話、比手勢（連續型，對照組）
    bridge  橋式（次數型，回家再錄）

停著的段落丟掉（不當成 other，因為停的姿勢可能跟 other 很像，硬標會教壞）。"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tarfile
import io
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "生理資料" / "動作錄音"
KT_OUT = ROOT / "手錶app" / "wear" / "src" / "main" / "java" / "com" / "example" / "watchbridge" / "MotionModel.kt"
REPORT = ROOT / "outputs" / "動作學習報告.md"
ADB = Path.home() / "AppData/Local/Android/Sdk/platform-tools/adb.exe"
PKG = "com.example.watchbridge"

WIN_S = 2.0     # 一個判斷窗 2 秒
HOP_S = 0.5     # 每 0.5 秒判一次
REP_LABELS = {"phone", "drink", "stretch", "bridge"}
EVENTS = ROOT / "生理資料" / "哨兵事件.jsonl"
BRIDGE = ROOT / "生理資料" / "手錶橋接"
VOICE_OUT = ROOT / "生理資料" / "口頭教動作.jsonl"
TEACH_MIN = 10
CONT_LABELS = {"typing", "writing", "other"}
GRAV = 9.80665

FEATURES = [
    "acc_mean", "acc_std", "acc_max", "acc_min",
    "gyr_mean", "gyr_std", "gyr_max",
    "gx_mean", "gy_mean", "gz_mean",
    "gx_delta", "gy_delta", "gz_delta",
    "ax_std", "ay_std", "az_std",
    "wx_abs", "wy_abs", "wz_abs",
]


# ── 讀檔 ──────────────────────────────────────────────────────
def load(path: Path) -> dict:
    """回傳 {"tag":…, "acc": (t秒, xyz), "gyro":…, "grav":…}。t 從 acc 第一筆起算。"""
    tracks: dict[str, list] = {"acc": [], "gyro": [], "grav": []}
    tag = path.stem.split("_", 2)[-1]
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue          # 錄到一半被關掉，最後一行可能是半行
            if r.get("header"):
                tag = r.get("tag", tag)
                continue
            if r.get("s") in tracks and len(r.get("v", [])) >= 3:
                tracks[r["s"]].append((r["ns"], *r["v"][:3]))
    if tag == "voice":
        tag = voice_tag_for(path)
    out = {"tag": tag, "file": path.name}
    if not tracks["acc"]:
        raise ValueError(f"{path.name} 沒有加速度資料")
    t0 = tracks["acc"][0][0]
    for k, rows in tracks.items():
        a = np.array(rows, dtype=float) if rows else np.zeros((0, 4))
        if len(a):

            # 起點差超過 10 秒＝基準不同 → 把這軌的起點對齊 acc。
            if abs(a[0, 0] - t0) > 10e9:
                a[:, 0] = a[:, 0] - a[0, 0] + t0
            a[:, 0] = (a[:, 0] - t0) / 1e9
        out[k] = a
    return out


# ── 特徵（⚠ 跟 MotionModel.kt 的 features() 一字不差地對應）──────────
def features(acc: np.ndarray, gyr: np.ndarray, grav: np.ndarray) -> list[float] | None:
    """三軌各自是 (n, 3)，已經是同一個 2 秒窗。資料太少回 None。"""
    if len(acc) < 20 or len(gyr) < 20 or len(grav) < 10:
        return None
    am = np.linalg.norm(acc, axis=1)
    gm = np.linalg.norm(gyr, axis=1)
    g = grav / GRAV
    k = max(1, len(g) // 10)          # 頭尾各取 1/10 平均，看手腕轉了多少
    g_start, g_end = g[:k].mean(axis=0), g[-k:].mean(axis=0)
    return [
        am.mean(), am.std(), am.max(), am.min(),
        gm.mean(), gm.std(), gm.max(),
        *g.mean(axis=0),
        *(g_end - g_start),
        *acc.std(axis=0),
        *np.abs(gyr).mean(axis=0),
    ]


def windows(rec: dict):
    """逐窗產生 (開始秒, 特徵)。"""
    acc, gyr, grav = rec["acc"], rec["gyro"], rec["grav"]
    if not len(acc):
        return
    end = acc[-1, 0]
    t = 0.0
    while t + WIN_S <= end:
        sel = lambda a: a[(a[:, 0] >= t) & (a[:, 0] < t + WIN_S), 1:4]
        f = features(sel(acc), sel(gyr), sel(grav))
        if f is not None:
            yield t, f
        t += HOP_S


def active_events(rec: dict, thr: float | None = None) -> tuple[list[tuple[float, float]], float]:
    """用陀螺儀大小（0.5 秒平滑）找出動作段。回傳 [(開始, 結束)], 門檻。"""
    gyr = rec["gyro"]
    if len(gyr) < 50:
        return [], 0.0
    t, m = gyr[:, 0], np.linalg.norm(gyr[:, 1:4], axis=1)
    n = max(1, int(round(0.5 / np.median(np.diff(t)))))
    sm = np.convolve(m, np.ones(n) / n, mode="same")
    if thr is None:
        # 門檻：安靜時的水準（20 百分位）跟動作時（90 百分位）中間偏下
        lo, hi = np.percentile(sm, 20), np.percentile(sm, 90)
        thr = lo + 0.3 * (hi - lo)
    on = sm > thr
    ev, start = [], None
    for i, v in enumerate(on):
        if v and start is None:
            start = t[i]
        elif not v and start is not None:
            ev.append((start, t[i]))
            start = None
    if start is not None:
        ev.append((start, t[-1]))
    # 合併間隔 < 0.4 秒的、丟掉短於 0.4 秒的抖動
    merged: list[list[float]] = []
    for s, e in ev:
        if merged and s - merged[-1][1] < 0.4:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged if e - s >= 0.4], float(thr)


def label_windows(rec: dict):
    """回傳 [(開始秒, 特徵, 標籤)]。次數型只留跟動作段重疊 ≥ 50% 的窗。"""
    tag = rec["tag"]
    out = []
    if tag not in REP_LABELS:   # 連續型；口頭教的動作（吹頭髮、馬桶上滑手機…）也當連續型
        for t, f in windows(rec):
            out.append((t, f, tag))
        return out
    ev, _ = active_events(rec)
    names = {}
    if tag == "phone":
        # 一段裡是 拿起、放下、拿起、放下…交替；第一個動作段＝拿起。

        for i, (s, e) in enumerate(ev):
            names[(s, e)] = "phone_up" if i % 2 == 0 else "phone_down"
    else:
        for s, e in ev:
            names[(s, e)] = tag
    for t, f in windows(rec):
        best, need, lab = 0.0, 0.0, None
        for (s, e), nm in names.items():
            ov = max(0.0, min(t + WIN_S, e) - max(t, s))
            if ov > best:
                # 動作本身可能不到 2 秒：重疊 ≥ 1 秒，或 ≥ 那次動作長度的 70%，就算
                best, need, lab = ov, min(1.0, 0.7 * (e - s)), nm
        if lab and best >= need:
            out.append((t, f, lab))
    return out


_S2T = str.maketrans("头发机厕马饭东书写电脑话讲们这时间刷着觉看衣服叠杯水喝", "頭髮機廁馬飯東書寫電腦話講們這時間刷著覺看衣服疊杯水喝")


def norm_label(s: str) -> str:
    return s.translate(_S2T).replace(" ", "").strip("，,。.!！~～")


def voice_classes(labels: list[str]) -> dict[str, str]:
    """同類合併：繁簡統一後，互相包含就是同一類（吹頭／吹頭髮），類名取最短的那個。其餘交給腦。"""
    from collections import Counter
    canon: dict[str, str] = {}
    for lab in sorted({norm_label(x) for x in labels}, key=len):
        hit = next((c for c in set(canon.values()) if c in lab or lab in c), None)
        canon[lab] = hit or lab

    cnt = Counter(norm_label(x) for x in labels)
    name = {}
    for g in set(canon.values()):
        mem = [l for l in canon if canon[l] == g]
        name[g] = max(mem, key=lambda l: (cnt[l], len(l)))
    return {x: name[canon[norm_label(x)]] for x in labels}


def voice_sessions(events: Path = EVENTS, days: int = 14) -> list[dict]:
    """哨兵事件裡的 教動作 開始／結束 → [{start, end, label, cls, how}]。沒結束的算 TEACH_MIN 分鐘。"""
    import datetime as dt
    try:
        lines = events.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    since = dt.datetime.now() - dt.timedelta(days=days)
    out, open_ = [], None
    for line in lines:
        if '"教動作"' not in line:
            continue
        try:
            r = json.loads(line)
            t = dt.datetime.fromisoformat(r["t"])
        except Exception:
            continue
        if t < since:
            continue
        if r.get("phase") == "開始":
            if open_:
                cap = open_["start"] + dt.timedelta(minutes=TEACH_MIN)
                open_["end"] = min(t, cap)
                if t < cap:
                    open_["how"] = "換下一個動作"
                out.append(open_)
            open_ = {"start": t, "label": r.get("label", "?"), "heard": r.get("heard", ""), "how": "沒說好了，算 10 分鐘"}
        elif r.get("phase") == "結束" and open_:
            open_["end"] = min(t, open_["start"] + dt.timedelta(minutes=TEACH_MIN)); open_["how"] = "她說結束"
            out.append(open_); open_ = None
    if open_:
        open_["end"] = open_["start"] + dt.timedelta(minutes=TEACH_MIN)
        out.append(open_)
    cls = voice_classes([x["label"] for x in out])
    for x in out:
        x["cls"] = cls[x["label"]]
    return out


def file_time(path: Path):
    import datetime as dt
    try:
        return dt.datetime.strptime("_".join(path.stem.split("_")[:2]), "%Y%m%d_%H%M%S")
    except ValueError:
        return None


VOICE_LEAD_S = 120


def voice_match(path: Path, sessions: list[dict] | None = None) -> dict | None:
    """規則：檔案開錄時間落在 [那段開始 − 120 秒, 那段結束] 裡；有好幾段都符合就挑開始時間最近的。"""
    t = file_time(path)
    if t is None:
        return None
    import datetime as dt
    ses = voice_sessions(days=3650) if sessions is None else sessions
    hits = [x for x in ses if x["start"] - dt.timedelta(seconds=VOICE_LEAD_S) <= t <= x["end"]]
    return min(hits, key=lambda x: abs((x["start"] - t).total_seconds())) if hits else None


def voice_tag_for(path: Path, sessions: list[dict] | None = None) -> str:
    m = voice_match(path, sessions)
    return m["cls"] if m else "voice?"


def slice_minutes(start, end, bridge: Path = BRIDGE) -> dict:
    """那段時間錶每分鐘傳回來的：動作量、峰值、靜止比例、傾角、心跳、手溫（不用改錶，錶本來就每分鐘傳）。"""
    rows = []
    for day in sorted({start.date(), end.date()}):
        for f in bridge.glob(f"{day:%Y-%m-%d}*.jsonl"):
            if "_" in f.stem.split("-")[-1]:
                continue
            with f.open(encoding="utf-8") as fh:
                for line in fh:
                    if '"type":"motion"' not in line and '"type":"hr"' not in line and '"type":"skin_temp"' not in line:
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    import datetime as dt
                    t = dt.datetime.fromisoformat(r["t"][:19])
                    if start <= t <= end:
                        rows.append(r)

    def col(typ, key):
        return [r[key] for r in rows if r.get("type") == typ and isinstance(r.get(key), (int, float))]

    def stat(v):
        return None if not v else {"中位": round(float(np.median(v)), 2), "最低": round(min(v), 2), "最高": round(max(v), 2), "n": len(v)}

    skin = [r.get("temp", r.get("skin", r.get("value"))) for r in rows if r.get("type") == "skin_temp"]
    return {"動作量": stat(col("motion", "activity")), "峰值": stat(col("motion", "peak")),
            "靜止比例": stat(col("motion", "still_ratio")), "傾角": stat(col("motion", "tilt")),
            "心跳": stat(col("hr", "bpm")), "手溫": stat([x for x in skin if isinstance(x, (int, float))])}


def cmd_voice(a):
    ses = voice_sessions(Path(a.events), a.days)
    if not ses:
        print("還沒有口頭教的動作。她對錶說「阿寶，我在吹頭髮」，做完說「好了」，就會出現在這裡。")
        return
    waves, wave_of = {}, {}
    for p in recordings():
        if p.stem.endswith("_voice"):
            m = voice_match(p, ses)
            waves.setdefault(m["cls"] if m else "voice?", []).append(p.name)
            if m:
                wave_of.setdefault(m["start"], []).append(p.name)
    out = []
    for x in ses:
        cut = slice_minutes(x["start"], x["end"], Path(a.bridge))
        mins = round((x["end"] - x["start"]).total_seconds() / 60, 1)
        out.append({"start": x["start"].isoformat(timespec="seconds"), "end": x["end"].isoformat(timespec="seconds"),
                    "分鐘": mins, "label": x["label"], "類": x["cls"], "她說": x["heard"], "怎麼結束": x["how"],
                    "50Hz檔": wave_of.get(x["start"], []), **cut})
    if not a.dry:
        VOICE_OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8")
    by: dict[str, list] = {}
    for r in out:
        by.setdefault(r["類"], []).append(r)
    print(f"口頭教動作：{len(out)} 段、{len(by)} 類" + ("" if a.dry else f" → {VOICE_OUT}"))
    for c, rs in by.items():
        print(f"\n【{c}】{len(rs)} 次（標籤：{'、'.join(sorted({r['label'] for r in rs}))}）"
              f"　50Hz 波形 {len(waves.get(c, []))} 段")
        for r in rs:
            m = r["動作量"]; tl = r["傾角"]; hr = r["心跳"]; sk = r["手溫"]
            print(f"  {r['start'][5:16].replace('T', ' ')} {r['分鐘']:>4} 分（{r['怎麼結束']}）"
                  f"  動作量 {m['中位'] if m else '—'}  傾角 {tl['中位'] if tl else '—'}°"
                  f"  心跳 {hr['中位'] if hr else '—'}  手溫 {sk['最高'] if sk else '—'}°")
    if "voice?" in waves:
        print(f"\n（錶自己開錄、但不是在教動作的 50Hz 檔 {len(waves['voice?'])} 段，不拿來學）")


# ── 指令 ──────────────────────────────────────────────────────
def find_watch(serial: str | None) -> str:
    if serial:
        return serial
    out = subprocess.run([str(ADB), "devices"], capture_output=True, text=True).stdout
    for line in out.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 2 and parts[1] == "device" and " " not in parts[0]:
            model = subprocess.run([str(ADB), "-s", parts[0], "shell", "getprop ro.product.model"],
                                   capture_output=True, text=True).stdout.strip()
            if model.startswith("SM-L"):
                return parts[0]
    sys.exit("找不到手錶（adb devices 裡沒有 SM-L 開頭的裝置）")


def cmd_pull(a):
    s = find_watch(a.serial)
    DATA.mkdir(parents=True, exist_ok=True)
    raw = subprocess.run([str(ADB), "-s", s, "exec-out", "run-as", PKG, "tar", "c", "files/motion_rec"],
                         capture_output=True).stdout
    n = 0
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        for m in tf.getmembers():
            if m.isfile() and m.name.endswith(".jsonl"):
                dst = DATA / Path(m.name).name
                data = tf.extractfile(m).read()
                if not dst.exists() or dst.stat().st_size != len(data):
                    dst.write_bytes(data)
                    n += 1
    print(f"拉回 {n} 個新檔 → {DATA}")


def recordings() -> list[Path]:
    return sorted(DATA.glob("*.jsonl"))


def cmd_look(a):
    for p in recordings():
        try:
            r = load(p)
        except ValueError as e:
            print(f"✗ {e}")
            continue
        dur = r["acc"][-1, 0] if len(r["acc"]) else 0
        hz = len(r["acc"]) / dur if dur else 0
        line = f"{p.name:40s} {r['tag']:8s} {dur:6.1f} 秒  acc {hz:4.0f} Hz  gyro {len(r['gyro'])}  grav {len(r['grav'])}"
        if r["tag"] in REP_LABELS:
            ev, thr = active_events(r)
            line += f"  → 切出 {len(ev)} 次（門檻 {thr:.2f} rad/s）"
        print(line)


def build_dataset():
    X, y, grp = [], [], []
    for p in recordings():
        try:
            r = load(p)
        except ValueError:
            continue
        if r["tag"] == "voice?":
            continue
        rows = label_windows(r)
        if not rows:
            continue
        tmax = max(t for t, _, _ in rows)
        for t, f, lab in rows:
            X.append(f)
            y.append(lab)
            # 時間切：每段錄音前 70% 訓練、後 30% 測試（同一段裡相鄰的窗太像，不能隨機切）
            grp.append("test" if t > 0.7 * tmax else "train")
    return np.array(X), np.array(y), np.array(grp)


def cmd_train(a):
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import confusion_matrix

    X, y, g = build_dataset()
    if len(set(y)) < 2:
        sys.exit(f"標籤不夠：{sorted(set(y))}（至少要兩種動作才能學）")
    tr, te = g == "train", g == "test"
    labels = sorted(set(y))
    lines = [f"# 動作學習報告（{len(X)} 個 2 秒窗，{len(labels)} 種動作）", ""]
    lines.append("| 動作 | 訓練窗 | 測試窗 |")
    lines.append("|---|---|---|")
    for l in labels:
        lines.append(f"| {l} | {(tr & (y == l)).sum()} | {(te & (y == l)).sum()} |")

    tree = DecisionTreeClassifier(max_depth=a.depth, min_samples_leaf=5, class_weight="balanced", random_state=0)
    tree.fit(X[tr], y[tr])
    acc_t = (tree.predict(X[te]) == y[te]).mean() if te.any() else float("nan")
    rf = RandomForestClassifier(200, min_samples_leaf=3, class_weight="balanced", random_state=0).fit(X[tr], y[tr])
    acc_rf = (rf.predict(X[te]) == y[te]).mean() if te.any() else float("nan")
    lines += ["", f"- **小決策樹（深度 {a.depth}，錶上用這個）測試準確率：{acc_t:.1%}**",
              f"- 隨機森林（對照，太大不放錶上）：{acc_rf:.1%}",
              "- 測試方式：每段錄音的**最後 30%** 當考題，訓練時沒看過。只有一段錄音時這個分數偏樂觀，"
              "要等她換天、換姿勢再錄一次才算真的準。", ""]
    if te.any():
        cm = confusion_matrix(y[te], tree.predict(X[te]), labels=labels)
        lines.append("混淆表（列＝真的，欄＝錶猜的）")
        lines.append("")
        lines.append("| 真＼猜 | " + " | ".join(labels) + " |")
        lines.append("|---|" + "---|" * len(labels))
        for l, row in zip(labels, cm):
            lines.append(f"| {l} | " + " | ".join(str(v) for v in row) + " |")
    imp = sorted(zip(FEATURES, tree.feature_importances_), key=lambda x: -x[1])[:6]
    lines += ["", "最有用的特徵：" + "、".join(f"{n} {v:.2f}" for n, v in imp if v > 0)]

    # 最終模型用全部資料重訓
    final = DecisionTreeClassifier(max_depth=a.depth, min_samples_leaf=5, class_weight="balanced", random_state=0).fit(X, y)
    write_kotlin(final, acc_t)
    lines += ["", f"錶上模型已寫進 `{KT_OUT.name}`（{final.tree_.node_count} 個節點）。"]
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def write_kotlin(tree, acc: float):
    t = tree.tree_
    classes = list(tree.classes_)

    def node(i, ind):
        sp = "    " * ind
        if t.children_left[i] == -1:
            k = int(np.argmax(t.value[i][0]))
            return f'{sp}return "{classes[k]}"\n'
        f, th = FEATURES[t.feature[i]], t.threshold[i]
        return (f"{sp}if (f[{t.feature[i]}] <= {th:.6f}f) {{  // {f}\n"
                + node(t.children_left[i], ind + 1)
                + f"{sp}}} else {{\n"
                + node(t.children_right[i], ind + 1)
                + f"{sp}}}\n")

    kt = f'''package com.example.watchbridge

// ⚠ 自動產生，不要手改 —— 由 穿戴橋接/動作學習.py train 產生（測試準確率 {acc:.1%}）。
// 特徵算法跟 動作學習.py 的 features() 一一對應，改一邊要改另一邊。
object MotionModel {{
    val LABELS = listOf({", ".join(f'"{c}"' for c in classes)})
    const val WIN_MS = {int(WIN_S * 1000)}L
    private const val GRAV = {GRAV}f

    /** acc／gyr／grav 各是同一個 2 秒窗的 [x,y,z] 列表。資料太少回 null。 */
    fun features(acc: List<FloatArray>, gyr: List<FloatArray>, grav: List<FloatArray>): FloatArray? {{
        if (acc.size < 20 || gyr.size < 20 || grav.size < 10) return null
        val am = acc.map {{ mag(it) }}; val gm = gyr.map {{ mag(it) }}
        val g = grav.map {{ floatArrayOf(it[0] / GRAV, it[1] / GRAV, it[2] / GRAV) }}
        val k = maxOf(1, g.size / 10)
        fun meanAx(l: List<FloatArray>, ax: Int) = l.sumOf {{ it[ax].toDouble() }}.toFloat() / l.size
        fun stdAx(l: List<FloatArray>, ax: Int): Float {{ val m = meanAx(l, ax); return kotlin.math.sqrt(l.sumOf {{ ((it[ax] - m) * (it[ax] - m)).toDouble() }}.toFloat() / l.size) }}
        val gs = g.take(k); val ge = g.takeLast(k)
        return floatArrayOf(
            am.average().toFloat(), std(am), am.max(), am.min(),
            gm.average().toFloat(), std(gm), gm.max(),
            meanAx(g, 0), meanAx(g, 1), meanAx(g, 2),
            meanAx(ge, 0) - meanAx(gs, 0), meanAx(ge, 1) - meanAx(gs, 1), meanAx(ge, 2) - meanAx(gs, 2),
            stdAx(acc, 0), stdAx(acc, 1), stdAx(acc, 2),
            gyr.sumOf {{ kotlin.math.abs(it[0]).toDouble() }}.toFloat() / gyr.size,
            gyr.sumOf {{ kotlin.math.abs(it[1]).toDouble() }}.toFloat() / gyr.size,
            gyr.sumOf {{ kotlin.math.abs(it[2]).toDouble() }}.toFloat() / gyr.size,
        )
    }}

    private fun mag(v: FloatArray) = kotlin.math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    private fun std(l: List<Float>): Float {{ val m = l.average(); return kotlin.math.sqrt(l.sumOf {{ (it - m) * (it - m) }} / l.size).toFloat() }}

    fun classify(f: FloatArray): String {{
{node(0, 2)}    }}
}}
'''
    KT_OUT.write_text(kt, encoding="utf-8")


# ── 自我測試：假資料跑整條管線 ──────────────────────────────────
def cmd_selftest(a):
    import tempfile
    global DATA, KT_OUT, REPORT
    tmp = Path(tempfile.mkdtemp(prefix="motion_selftest_"))
    DATA, KT_OUT, REPORT = tmp / "rec", tmp / "MotionModel.kt", tmp / "report.md"
    DATA.mkdir()
    rng = np.random.default_rng(0)

    def fake(tag, secs, kind):
        p = DATA / f"20261105_000000_{tag}.jsonl"
        with p.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"header": True, "tag": tag}) + "\n")
            for i in range(int(secs * 50)):
                t = i / 50
                ns = int(1e12 + t * 1e9)
                phase = (t % 4) / 4                      # 次數型：每 4 秒一次，動 1.2 秒
                moving = kind == "rep" and phase < 0.3
                if kind == "type":
                    w = rng.normal(0, 0.3, 3); acc = [0, 0, GRAV] + rng.normal(0, 0.8, 3)
                    gr = [0, 1, 9.75]
                elif kind == "still":
                    w = rng.normal(0, 0.02, 3); acc = [0, 0, GRAV] + rng.normal(0, 0.05, 3)
                    gr = [0, 0, GRAV]
                else:
                    up = (int(t // 4) % 2 == 0)
                    sp = 2.5 if moving else 0.02
                    w = rng.normal(0, 0.02, 3) + ([sp, 0, 0] if up else [-sp, 0, 0])
                    acc = [0, 0, GRAV] + rng.normal(0, 1.5 if moving else 0.05, 3)
                    ang = (phase / 0.3 if moving else 1.0) * (1 if up else -1) * 0.8 + (0 if up else 0.8)
                    gr = [0, GRAV * math.sin(ang), GRAV * math.cos(ang)]
                for s, v in (("acc", acc), ("gyro", w), ("grav", gr)):
                    f.write(json.dumps({"ns": ns, "s": s, "v": [round(float(x), 4) for x in v]}) + "\n")

    fake("phone", 120, "rep")
    fake("typing", 60, "type")
    fake("other", 60, "still")
    print("— look —"); cmd_look(a)
    print("— train —"); cmd_train(argparse.Namespace(depth=a.depth))
    kt = KT_OUT.read_text(encoding="utf-8")
    assert "fun classify" in kt and "return \"" in kt, "Kotlin 沒產生出來"
    print(f"\n✅ selftest 通過，假資料與產物在 {tmp}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull"); p.add_argument("--serial"); p.set_defaults(fn=cmd_pull)
    sub.add_parser("look").set_defaults(fn=cmd_look)
    p = sub.add_parser("train"); p.add_argument("--depth", type=int, default=6); p.set_defaults(fn=cmd_train)
    p = sub.add_parser("selftest"); p.add_argument("--depth", type=int, default=6); p.set_defaults(fn=cmd_selftest)
    p = sub.add_parser("voice"); p.add_argument("--days", type=int, default=14)
    p.add_argument("--events", default=str(EVENTS)); p.add_argument("--bridge", default=str(BRIDGE))
    p.add_argument("--dry", action="store_true", help="只印不寫檔"); p.set_defaults(fn=cmd_voice)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
