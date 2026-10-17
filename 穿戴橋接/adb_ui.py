#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
adb_ui.py —— 用 adb 可靠地操作 Android 畫面

為什麼會有這支（2026-08-12 使用者 立的目標）：
    她說：「我不可能永遠都會點。等我八九十歲手指頭不靈的時候怎麼辦？
    而且我一次只能點一台手機，搞不好以後你一次可以點十台。」

    所以目標不是「少失敗」，是**變成真的會點**。
    那天我在她手機上連續誤觸五次以上——誤入相片選擇器、拉出快速面板、
    把「聲音與震動」點成首頁。原因全都一樣：**用記憶中的座標盲點**。

    這支把那天所有的教訓寫成可執行的規則，讓下一次不用靠運氣。

核心設計（每一條都對應一次真實的失敗）：
    · 永遠「先看再點」—— 每次操作前重新讀畫面，不用上一次的座標
    · 點完「再看一次」—— 確認畫面真的變了，沒變就報告失敗而不是繼續
    · dump 前先刪舊檔 —— 否則 uiautomator 失敗時會讀到幾分鐘前的畫面
    · 用文字找元素，不用座標 —— 清單捲動位置每次都不同
    · 開關比對「想要的狀態」才動作 —— 已經對了就不碰，避免手殘關掉

用法：
    from adb_ui import UI
    ui = UI()                      # 預設第一台裝置
    ui.open_app("com.hcwebhook.app")
    ui.tap_text("^Local HTTP Server$")
    ui.set_toggle("捏合兩下", True)
    print(ui.text_dump())          # 現在畫面上有什麼

    python adb_ui.py --show        # 直接看目前畫面的元素清單
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0  # 2026-09-23 不閃黑框（使用者 看到了）
import time
from dataclasses import dataclass
from pathlib import Path

ADB = Path(os.environ.get("LOCALAPPDATA", "")) / \
    "Android" / "Sdk" / "platform-tools" / "adb.exe"


@dataclass
class El:
    """畫面上的一個元素。cx/cy 已經是實機座標，可以直接拿去 tap。"""
    text: str
    desc: str
    cls: str
    cx: int
    cy: int
    x1: int
    y1: int
    x2: int
    y2: int
    checkable: bool
    checked: bool
    clickable: bool

    @property
    def label(self) -> str:
        return self.text or self.desc


class UIError(RuntimeError):
    pass


class UI:
    def __init__(self, serial: str | None = None, adb: Path | None = None):
        self.adb = Path(adb) if adb else ADB
        if not self.adb.exists():
            raise UIError(f"找不到 adb：{self.adb}")
        self.serial = serial or self._first_device()

    # ── 底層 ───────────────────────────────────────────────────────
    def _first_device(self) -> str:
        out = self._raw("devices")
        for line in out.splitlines()[1:]:
            if "\tdevice" in line:
                return line.split("\t")[0]
        raise UIError("沒有連線中的裝置")

    def _raw(self, *args: str, timeout: int = 30) -> str:
        r = subprocess.run([str(self.adb), *args], capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8",
                           errors="replace", creationflags=_NO_WINDOW)
        return r.stdout or ""

    def sh(self, *args: str, timeout: int = 30) -> str:
        return self._raw("-s", self.serial, "shell", *args, timeout=timeout)

    # ── 讀畫面 ─────────────────────────────────────────────────────
    def screenshot(self, path: str | Path) -> Path:
        """存一張截圖。有些畫面有 FLAG_SECURE，存出來會是全黑的——
        那不是螢幕關了，是防截圖，這種畫面不要盲點。"""
        path = Path(path)
        r = subprocess.run([str(self.adb), "-s", self.serial,
                            "exec-out", "screencap", "-p"],
                           capture_output=True, timeout=60, creationflags=_NO_WINDOW)
        path.write_bytes(r.stdout)
        return path

    def _dump_xml(self, tries: int = 3) -> str | None:
        """
        取畫面結構。

        兩個坑：
        ① Compose 寫的 app 有持續動畫，uiautomator 永遠等不到 idle，
           會回 "could not get idle state."
        ② **失敗時舊檔還在**，直接 cat 會讀到幾分鐘前的畫面，
           看起來像成功了 —— 所以每次都先刪掉。
        """
        remote = "/sdcard/_adbui.xml"
        for i in range(tries):
            self.sh("rm", "-f", remote)
            out = self.sh("uiautomator", "dump", "--compressed", remote,
                          timeout=25)
            if "dumped to" in out:
                xml = self.sh("cat", remote, timeout=25)
                if xml and "<node" in xml:
                    return xml
            time.sleep(1.2)
        return None

    def elements(self) -> list[El]:
        xml = self._dump_xml()
        if xml is None:
            return []
        out: list[El] = []
        for m in re.finditer(r"<node[^>]*>", xml):
            s = m.group(0)

            def attr(name: str) -> str:
                mm = re.search(rf'{name}="([^"]*)"', s)
                return mm.group(1) if mm else ""

            b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', s)
            if not b:
                continue
            x1, y1, x2, y2 = (int(g) for g in b.groups())
            out.append(El(
                text=attr("text"), desc=attr("content-desc"),
                cls=attr("class").split(".")[-1],
                cx=(x1 + x2) // 2, cy=(y1 + y2) // 2,
                x1=x1, y1=y1, x2=x2, y2=y2,
                checkable=attr("checkable") == "true",
                checked=attr("checked") == "true",
                clickable=attr("clickable") == "true",
            ))
        return out

    def text_dump(self) -> str:
        """給人看的畫面內容。看不到東西時先跑這個，不要直接點。"""
        els = self.elements()
        if not els:
            return "（讀不到畫面結構：可能是動畫中的 app 或防截圖畫面）"
        lines = []
        for e in els:
            if not e.label and not e.checkable:
                continue
            mark = f"  [開關 {'開' if e.checked else '關'}]" if e.checkable else ""
            lines.append(f"{e.cx:>5},{e.cy:<5} {e.label[:46]}{mark}")
        return "\n".join(lines)

    def find(self, pattern: str, *, checkable: bool | None = None) -> El | None:
        rx = re.compile(pattern)
        for e in self.elements():
            if checkable is not None and e.checkable != checkable:
                continue
            if e.label and rx.search(e.label):
                return e
        return None

    # ── 動作 ───────────────────────────────────────────────────────
    def tap(self, x: int, y: int) -> None:
        self.sh("input", "tap", str(x), str(y))

    def back(self) -> None:
        self.sh("input", "keyevent", "KEYCODE_BACK")
        time.sleep(1.2)

    def home(self) -> None:
        self.sh("input", "keyevent", "KEYCODE_HOME")
        time.sleep(1.2)

    def hide_keyboard(self) -> None:
        """
        鍵盤一開就吃掉螢幕下面約三分之一，y>2600 的開關會點到鍵盤按鍵。
        輸入完一定要收起來再點別的。
        """
        self.back()

    def open_app(self, pkg: str, wait: float = 8.0) -> None:
        """
        重開 app 而不是連續 swipe 回頂端 ——
        連續 swipe 會從螢幕頂端拉出快速面板（實際發生過）。
        """
        self.sh("am", "force-stop", pkg)
        time.sleep(1.5)
        self.sh("monkey", "-p", pkg, "-c",
                "android.intent.category.LAUNCHER", "1")
        time.sleep(wait)

    def scroll(self, direction: str = "down", amount: int = 1) -> None:
        """
        安全捲動：起點不高於 y=2300、終點不低於 y=1000。
        超出這個範圍會被系統當成「從邊緣拉」而叫出快速面板或返回手勢。
        """
        for _ in range(amount):
            if direction == "down":
                self.sh("input", "swipe", "720", "2300", "720", "1200", "300")
            else:
                self.sh("input", "swipe", "720", "1200", "720", "2300", "300")
            time.sleep(1.0)

    def tap_text(self, pattern: str, *, verify: bool = True,
                 wait: float = 3.0) -> bool:
        """
        用文字找元素再點 —— **不用記憶中的座標**。

        verify=True 時會比對點前點後的畫面，沒變化就當作失敗回 False，
        而不是假裝成功繼續往下做。
        """
        el = self.find(pattern)
        if el is None:
            return False
        before = self.text_dump()
        self.tap(el.cx, el.cy)
        time.sleep(wait)
        if not verify:
            return True
        return self.text_dump() != before

    def set_toggle(self, label_pattern: str, want: bool,
                   *, max_dist: int = 90) -> str:
        """
        把某一列的開關設成想要的狀態。

        先比對現況：已經是想要的就不動 —— 避免「點一下反而關掉」。
        改完重新讀一次確認，回傳結果字串。

        max_dist：開關要跟文字在同一列（y 距離內），
        否則會抓到別列的開關（實際踩過）。
        """
        els = self.elements()
        if not els:
            return "讀不到畫面，沒有動作"
        rx = re.compile(label_pattern)
        row = next((e for e in els if e.label and rx.search(e.label)
                    and not e.checkable), None)
        if row is None:
            return f"找不到「{label_pattern}」"
        sw = min((e for e in els if e.checkable),
                 key=lambda e: abs(e.cy - row.cy), default=None)
        if sw is None or abs(sw.cy - row.cy) > max_dist:
            return f"「{row.label}」旁邊沒有開關"

        if sw.checked == want:
            return f"「{row.label}」已經是{'開' if want else '關'}，沒有動作"

        self.tap(sw.cx, sw.cy)
        time.sleep(2.0)

        els2 = self.elements()
        row2 = next((e for e in els2 if e.label and rx.search(e.label)
                     and not e.checkable), None)
        if row2:
            sw2 = min((e for e in els2 if e.checkable),
                      key=lambda e: abs(e.cy - row2.cy), default=None)
            if sw2 and abs(sw2.cy - row2.cy) <= max_dist:
                ok = "成功" if sw2.checked == want else "失敗（狀態沒變）"
                return f"「{row.label}」→ {'開' if want else '關'}：{ok}"
        return f"「{row.label}」點了，但驗不到結果"

    def find_and_scroll_to(self, pattern: str, max_scrolls: int = 12) -> El | None:
        """一邊捲一邊找。找到就回傳，找不到回 None（不要瞎點）。"""
        for _ in range(max_scrolls):
            el = self.find(pattern)
            if el:
                return el
            self.scroll("down")
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="adb 畫面操作小工具")
    ap.add_argument("--show", action="store_true", help="印出目前畫面的元素")
    ap.add_argument("--shot", help="存一張截圖到這個路徑")
    ap.add_argument("--tap-text", help="用文字找元素並點它")
    ap.add_argument("--toggle", help="開關的名字，配合 --on/--off")
    ap.add_argument("--on", action="store_true")
    ap.add_argument("--off", action="store_true")
    args = ap.parse_args()

    ui = UI()
    if args.shot:
        print("已存：", ui.screenshot(args.shot))
    if args.toggle:
        print(ui.set_toggle(args.toggle, want=not args.off))
    elif args.tap_text:
        print("點到了" if ui.tap_text(args.tap_text) else "找不到或畫面沒變")
    if args.show or not any([args.shot, args.tap_text, args.toggle]):
        print(ui.text_dump())


if __name__ == "__main__":
    main()
