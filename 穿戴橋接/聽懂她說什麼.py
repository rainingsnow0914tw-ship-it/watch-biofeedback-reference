#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""進度條還是 1/20。**那等於白講。**

## ⚠ 這支最危險的地方：關鍵字比對很會亂認

所以三條防線，一條都不能拿掉：

**一、否定與未來式要擋掉。**
```
```

**二、猜到的標籤一律標記來源。** 資料裡寫 `from:"voice"`，
之後如果發現語音判讀有系統性偏差，可以整批拿掉重來 ——
混在一起就再也分不開了。

**三、永遠把原話印在標籤旁邊。**

## 一句話裡可以有好幾件事

—— 全部都收，因為它們真的都發生了。"""

from __future__ import annotations

import re

# 標籤 → (關鍵詞, 說明)


RULES: list[tuple[str, list[str]]] = [
    ("off_wrist", ["脫下手錶", "脫手錶", "拿下手錶", "手錶拿下", "脫錶", "拿下來充電",
                   "去洗澡", "洗澡", "拔下來"]),
    ("wake", ["起床", "醒來", "醒了", "睡醒"]),
    ("sleep", ["去睡", "睡覺", "上床", "躺下來睡", "回去睡", "要睡了"]),
    ("exercise", ["運動", "走路", "散步", "跑步", "爬樓梯", "爬山", "健身",
                  "走回來", "走去", "騎車"]),


    #   那句話有轉錄、有進故事，卻沒算進校準進度。
    #

    #   隔一天又中。所以不能只補這一個詞 ——

    #   用**兩個字之間允許間隔**的規則寫，比一個一個列窮舉可靠。
    ("meal", ["吃飯", "吃東西", "肚子餓", "餓了", "吃完", "用餐", "吃午餐",
              "吃晚餐", "吃早餐", "吃宵夜", "吃過飯", "吃了飯", "吃一頓",
              "用過餐", "吃飽"]),
    ("toilet", ["上廁所", "尿尿", "廁所"]),
    ("medicine", ["吃藥", "服藥"]),


    ("unwell", ["不舒服", "難受", "很累", "好累", "疲憊", "疲勞", "沒力氣", "沒力",
                "累壞", "撐不住", "頭暈", "頭痛", "胸悶",
                "心悸", "想吐", "噁心", "很喘", "冒汗", "出冷汗", "怪怪的"]),
    ("period", ["生理期", "月經", "經期", "мс"]),
    ("buzz", ["震了", "震動", "又震"]),
]

# 鬆散比對：(前字, 後字)，中間最多可以插五個字。

# 而窮舉列詞永遠列不完。**列不完的症狀是安靜的**：那句話照樣進故事，
# 只是沒算進校準進度，沒人會發現。
LOOSE: list[tuple[str, list[tuple[str, str]]]] = [
    ("meal", [("吃", "飯"), ("吃", "東西"), ("用", "餐"), ("吃", "餐"), ("吃", "麵")]),
    ("exercise", [("走", "路"), ("跑", "步"), ("爬", "樓梯"), ("散", "步")]),
    ("sleep", [("睡", "覺"), ("上", "床")]),
    ("wake", [("起", "床"), ("醒", "過來")]),
    ("toilet", [("上", "廁所"), ("尿", "尿")]),
    ("medicine", [("吃", "藥"), ("服", "藥")]),
]

NAME = {"unwell": "不舒服", "buzz": "震了", "wake": "起床", "sleep": "睡覺",
        "exercise": "運動", "medicine": "吃藥", "period": "生理期",
        "off_wrist": "脫錶", "meal": "吃飯", "toilet": "上廁所", "other": "其他"}


NEG_WINDOW = 8
NEGATE = ["等一下", "待會", "等會", "準備要", "打算", "想要去", "要去", "要不要",
          "沒有", "還沒", "不想", "不要", "如果", "以後", "明天", "剛剛想到",
          "應該要", "本來要"]
# 這幾個雖然是過去式，但**事情確實發生了**，不可以擋掉。
KEEP = ["剛", "已經", "剛才", "剛剛"]


def _被否定(text: str, pos: int) -> str | None:
    """關鍵詞前面那一小段有沒有否定／未來式。回擋掉的原因，沒有回 None。"""
    left = text[max(0, pos - NEG_WINDOW):pos]
    for k in KEEP:
        if k in left:
            return None
    for n in NEGATE:
        if n in left:
            return n
    return None


def _人工標記() -> dict[str, list[str]]:
    """而那種失敗是**安靜的**（那句話照樣進故事，只是沒算進進度）。

    那些理由現在還成立。**但它不該是唯一的一層** ——
    我每天都在現場讀那些句子，我讀完的判斷才是最準的。

    所以分成兩層：
    ```
    第一層  關鍵字比對    自動、跑得快、跑得過歷史資料
    ```"""
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent / "生理資料" / "語音筆記" / "阿寶親自標的.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def 抽標籤(text: str, when: str | None = None) -> tuple[list[str], list[str]]:
    """回（認出來的標籤, 擋掉的理由）。**兩個都要回** ——"""
    t = re.sub(r"\s+", "", text or "")

    #


    #

    #   **語音是留給複雜的事的 → 用 if/else 去理解語音，
    #     等於拿最笨的工具去處理最難的輸入。**
    #
    #   所以關鍵字比對**不再有投票權**：


    #   關鍵字的結果只當**提示**放在 hint 裡，給我看，不進校準進度。
    if when:
        manual = _人工標記().get(when)
        if manual is not None:
            return list(manual), ["（阿寶親自讀過標的）"]
    tags: list[str] = []
    skipped: list[str] = []
    for tag, words in RULES:
        for w in words:
            i = t.find(w)
            if i < 0:
                continue
            why = _被否定(t, i)
            if why:
                skipped.append(f"{w}（前面有「{why}」，當成不是現在）")
                continue
            if tag not in tags:
                tags.append(tag)
            break
    # ── 鬆散比對：兩個字之間可以插字 ──────────────────────────
    #

    #     吃**過**飯　吃**了**飯　吃**一頓**飯　走**了很久的**路
    #   一個一個列窮舉永遠列不完，**而漏掉的症狀是安靜的**
    #   （那句話照樣進故事、只是沒算進校準進度，沒人會發現）。

    for tag, pairs in LOOSE:
        if tag in tags:
            continue
        for a, b in pairs:
            m = re.search(re.escape(a) + r".{0,5}?" + re.escape(b), t)
            if not m:
                continue
            why = _被否定(t, m.start())
            if why:
                skipped.append(f"{a}…{b}（前面有「{why}」，當成不是現在）")
                continue
            tags.append(tag)
            break

    # 關鍵字猜到的東西只當提示回去，**不當標籤**（呼叫端不會拿它去算進度）。
    hint = "、".join(NAME.get(x, x) for x in tags) if tags else "猜不出來"
    return [], [f"🔸 這一筆阿寶還沒親自讀（關鍵字猜：{hint}）"] + skipped


def _demo() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cases = [
        "起床尿尿然後再回去睡覺。",
        "躺在床上滑手機。",
        "我脫下手錶去洗澡。",
        "等一下要去運動。",
        "我剛走路回來，有點喘。",
        "今天沒有運動。",
        "肚子餓了，準備去吃飯。",
        "我剛吃完飯，坐下來休息。",
        "阿寶我現在很累很累很累。",
        "下班走路回來，走了二十分鐘。",
    ]
    print("═══ 關鍵字判讀實測 ═══\n")
    for c in cases:
        tags, sk = 抽標籤(c)
        line = f"  「{c}」"
        print(line)
        print("      → " + ("、".join(NAME.get(t, t) for t in tags) if tags else "（沒有認出標籤）"))
        for s in sk:
            print(f"      ⊘ 擋掉：{s}")
        print()


if __name__ == "__main__":
    _demo()
