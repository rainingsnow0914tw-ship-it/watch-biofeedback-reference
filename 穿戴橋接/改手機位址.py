#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""改手機位址 —— 電腦找手機的位址只有兩個地方寫死，這支一次改兩個，而且只改那一個鍵。

    python 改手機位址.py                 # 看現在是什麼
    python 改手機位址.py 192.168.0.X  # 改回家裡 Wi-Fi

改完要重啟三支：watch_puller、hc_puller、sentinel（或重開機）。"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    (ROOT / "生理資料" / "手錶橋接" / "puller_config.json", "ip",   "橋接 8788（原始資料、對講機、卡片）"),
    (ROOT / "生理資料" / "puller_config.json",               "host", "HC Webhook 8787（三星健康）"),
]


def main():
    new = sys.argv[1].strip() if len(sys.argv) > 1 else None
    for f, key, what in FILES:
        try:
            cfg = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"✗ 讀不到 {f.name}：{e}"); continue
        old = cfg.get(key)
        if new is None or new == old:
            print(f"  {what}：{key}={old}  ({f.relative_to(ROOT)})")
            continue
        bak = f.with_name(f.name + ".bak-改位址")
        bak.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        cfg[key] = new
        f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"✓ {what}：{key} {old} → {new}  （留底 {bak.name}）")
    if new:
        print("接著重啟：watch_puller / hc_puller / sentinel。")


if __name__ == "__main__":
    main()
