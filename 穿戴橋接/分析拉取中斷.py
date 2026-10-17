#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline analysis of 8787 pull failures and 8788 comparison evidence.

This script only reads local logs.  It does not probe the LAN, call ADB, edit
either bridge configuration, or control the running puller.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "生理資料"

START_RE = re.compile(r"^===== 啟動 (\S+) =====")
EVENT_RE = re.compile(r"^\[(\d\d:\d\d:\d\d)\]\s+(\S+):8787\s+→\s+(.*)$")
ALERT_RE = re.compile(r"(\S+)\s+連續 3 次拉不到手機")
FAIL_MARKERS = (
    "連不到手機", "手機沒回應", "讀取失敗", "手機拒絕",
    "HTTP 4", "HTTP 5", "回來的不是 JSON", "回來的 JSON 不是物件",
)


def alerts(path: Path) -> list[datetime]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = ALERT_RE.match(line)
        if match:
            try:
                out.append(datetime.fromisoformat(match.group(1)))
            except ValueError:
                pass
    return out


def pull_events(path: Path) -> list[dict]:
    """Recover absolute timestamps from startup date + time-of-day lines.

    Old logs lack a run id on each line.  Concurrent/restarted writers can make
    long durations approximate; the report labels this limitation explicitly.
    """
    run = 0
    current_date = None
    last_tod = None
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        start = START_RE.match(line)
        if start:
            run += 1
            try:
                stamp = datetime.fromisoformat(start.group(1))
                current_date = stamp.date()
                last_tod = stamp.time().replace(tzinfo=None)
            except ValueError:
                current_date = last_tod = None
            continue
        event = EVENT_RE.match(line)
        if not event or current_date is None:
            continue
        tod = datetime.strptime(event.group(1), "%H:%M:%S").time()
        if last_tod:
            old = last_tod.hour * 3600 + last_tod.minute * 60 + last_tod.second
            new = tod.hour * 3600 + tod.minute * 60 + tod.second
            if new + 6 * 3600 < old:
                current_date += timedelta(days=1)
        last_tod = tod
        message = event.group(3)
        out.append({
            "run": run,
            "t": datetime.combine(current_date, tod),
            "host": event.group(2),
            "message": message,
            "failed": any(marker in message for marker in FAIL_MARKERS),
        })
    return out


def failure_bursts(events: list[dict]) -> list[dict]:
    """Merge consecutive failure lines; any logged success ends the burst."""
    unique = []
    seen = set()
    for event in sorted(events, key=lambda item: item["t"]):
        key = (event["t"], event["failed"], event["host"], event["message"])
        if key not in seen:
            seen.add(key)
            unique.append(event)
    bursts = []
    active = None
    previous_success = None
    for event in unique:
        if event["failed"]:
            if active is None:
                active = {
                    "start": event["t"], "last": event["t"], "count": 1,
                    "hosts": {event["host"]}, "previous": previous_success,
                }
            else:
                active["last"] = event["t"]
                active["count"] += 1
                active["hosts"].add(event["host"])
        else:
            if active:
                active["recovery"] = event
                active["duration_min"] = (event["t"] - active["start"]).total_seconds() / 60
                bursts.append(active)
                active = None
            previous_success = event
    if active:
        active["recovery"] = None
        active["duration_min"] = None
        bursts.append(active)
    return bursts


def delayed_jobs(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
            row["_t"] = datetime.fromisoformat(row["t"])
            out.append(row)
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
    return out


def delay_waits(rows: list[dict]) -> tuple[list[float], dict[str, datetime]]:
    pending: dict[str, datetime] = {}
    waits = []
    for row in sorted(rows, key=lambda item: item["_t"]):
        action, state = row.get("動作"), row.get("狀態")
        if state == "延後":
            pending.setdefault(action, row["_t"])
        elif state == "補做" and action in pending:
            waits.append((row["_t"] - pending.pop(action)).total_seconds() / 60)
    return waits, pending


def verdict_times(path: Path) -> list[datetime]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(datetime.fromisoformat(json.loads(line)["t"]))
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
    return out


def failure_reason_counts(path: Path) -> Counter:
    counts = Counter()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "WinError 10061" in line:
            counts["10061 connection refused"] += 1
        elif "WinError 10060" in line:
            counts["10060 timeout"] += 1
        elif "401" in line and "→" in line:
            counts["401 auth"] += 1
        if "最新資料是" in line:
            counts["source stale notices"] += 1
        if "伺服器不見了，已自動叫醒 app" in line:
            counts["wake recovered"] += 1
    return counts


def _short(message: str | None, limit: int = 54) -> str:
    if not message:
        return "（本 run 無較早成功行）"
    text = message.replace("|", "／")
    return text if len(text) <= limit else text[:limit] + "…"


def render() -> str:
    alert_rows = alerts(DATA / "收集中斷.txt")
    events = pull_events(DATA / "puller.log")
    bursts = [item for item in failure_bursts(events) if item["count"] >= 3]
    resolved = [item for item in bursts if item["duration_min"] is not None]
    delays = delayed_jobs(DATA / "延後的工作.jsonl")
    waits, pending = delay_waits(delays)
    verdicts = verdict_times(DATA / "手錶橋接" / "判斷日誌.jsonl")
    reasons = failure_reason_counts(DATA / "puller.log")

    by_hour = Counter(item.hour for item in alert_rows)
    by_weekday = Counter(item.weekday() for item in alert_rows)
    weekday = ["一", "二", "三", "四", "五", "六", "日"]
    near_8788 = sum(
        any(abs((mark - alert.replace(tzinfo=None)).total_seconds()) <= 15 * 60 for mark in verdicts)
        for alert in alert_rows
    )
    recent = [item for item in bursts if item["start"].date() >= date(2026, 9, 14)]
    latest = max((DATA / name).stat().st_mtime for name in (
        "收集中斷.txt", "puller.log", "延後的工作.jsonl"
    ))
    cutoff = datetime.fromtimestamp(latest).astimezone()

    durations = sorted(item["duration_min"] for item in resolved)
    median = statistics.median(durations) if durations else 0
    p90 = durations[int((len(durations) - 1) * 0.9)] if durations else 0
    longest = max(durations, default=0)
    failures = sum(event["failed"] for event in events)
    successes = len(events) - failures
    delay_states = Counter(row.get("狀態") for row in delays)
    foreground = Counter(
        row.get("前台") for row in delays if row.get("狀態") == "延後" and row.get("前台")
    )

    lines = [
        "# 8787 拉取中斷時間分布與根因｜2026-10-04",
        "",
        f"- 快照截止：{cutoff:%Y-%m-%d %H:%M:%S %z}；全程只讀本機四份指定紀錄。",
        "- 8787 是第三方 Health Connect Webhook；8788 是自家手錶橋接。兩條不能互相代稱。",
        "- 舊 `puller.log` 每行只有時分秒、沒有 run id；以下長度是『第一個失敗行到下一個成功行』的觀測間隔，包含 puller 停跑／重啟空窗，不能冒充純網路斷線時長，也不保證是上界。",
        "",
        "## 分布",
        "",
        f"- `收集中斷.txt`：{len(alert_rows)} 次三連敗提醒，範圍 {alert_rows[0]:%Y-%m-%d}～{alert_rows[-1]:%Y-%m-%d}。",
        f"- `puller.log`：{len(events)} 個有時間的拉取結果；成功 {successes}、失敗 {failures}。",
        f"- 連續至少 3 個失敗行：{len(bursts)} 段；已見恢復 {len(resolved)} 段，截止時未見恢復 {len(bursts)-len(resolved)} 段。",
        f"- 已恢復段的觀測間隔：中位 {median/60:.1f} 小時、P90 {p90/60:.1f} 小時、最長 {longest/60:.1f} 小時；受舊 log／多 run 影響，只作重建時序線索，不保證是上下界。",
        "- 時段最高：" + "、".join(f"{hour:02d} 時 {count} 次" for hour, count in by_hour.most_common(6)) + "。",
        "- 00–03 時合計 " + str(sum(by_hour[h] for h in range(0, 4))) + " 次；16–18 時合計 " + str(sum(by_hour[h] for h in range(16, 19))) + " 次。",
        "- 星期分布：" + "、".join(f"週{weekday[d]} {by_weekday[d]}" for d in range(7)) + "；每一天都有，沒有單一星期根因。",
        "",
        "## 9/14 之後的主要失敗段",
        "",
        "| 開始 | 失敗行 | 主機 | 到下一成功 | 斷前最後成功內容 |",
        "|---|---:|---|---:|---|",
    ]
    for item in recent:
        duration = "仍未恢復" if item["duration_min"] is None else f"{item['duration_min']/60:.1f}h"
        previous = item["previous"]["message"] if item.get("previous") else None
        lines.append(
            f"| {item['start']:%m-%d %H:%M} | {item['count']} | {', '.join(sorted(item['hosts']))} | "
            f"{duration} | {_short(previous)} |"
        )

    total_transport = reasons["10061 connection refused"] + reasons["10060 timeout"] + reasons["401 auth"]
    lines += [
        "",
        "## 錯誤型態與已知因素",
        "",
        f"- 10061 connection refused：{reasons['10061 connection refused']} 行（{reasons['10061 connection refused']/max(total_transport,1):.1%}）。被連線的位址拒絕 TCP；可能是該位址沒有 listener 或主動拒絕，不能單憑錯誤確認那就是手機或 app 被殺。",
        f"- 10060 timeout：{reasons['10060 timeout']} 行（{reasons['10060 timeout']/max(total_transport,1):.1%}）。可由錯 IP、Wi‑Fi／VPN 擋區網、手機休眠或路徑不通造成，不能只選一個。",
        f"- 401：{reasons['401 auth']} 行（{reasons['401 auth']/max(total_transport,1):.1%}）。收到 HTTP 401；只能確認驗證被拒絕，不能定位是鑰匙、設定或打到錯服務。現行設定不可因此整份覆寫。",
        f"- 拉得到但來源過舊的提醒 {reasons['source stale notices']} 行；只證明收到的來源時間過舊，不能定位 Samsung Health／HC 哪一環節停滯；和 8787 transport 失敗是不同層。",
        f"- 日誌明記自動叫醒後恢復 {reasons['wake recovered']} 次，支持『app／服務生命週期』是主因之一。",
        f"- 前台保護：延後 {delay_states['延後']} 筆、補做 {delay_states['補做']} 筆；已配對等待中位 {statistics.median(waits):.0f} 分、最長 {max(waits):.0f} 分。這不是斷線根因，而是避免搶 使用者 畫面後刻意延長的恢復時間。",
        "- 延後時前台最多：" + "、".join(f"{name} {count}" for name, count in foreground.most_common(6)) + "。",
        f"- {len(alert_rows)} 次 8787 三連敗中，有 {near_8788} 次在前後 15 分鐘內仍有 8788 判斷日誌；因此不能把 8787 失敗說成手錶沒戴、手錶沒資料或整台手機必然離線。",
        "",
        "## 最可能原因／排除範圍",
        "",
        "1. **歷史候選：8787 listener／app 生命週期異常。** 10061 佔 transport 錯誤約七成，多次叫醒後恢復；但錯誤比例不是已確認根因比例，不能據此排成所有失聯的唯一主因。",
        "2. **另一類候選：IP／Wi‑Fi／VPN／休眠造成路徑 timeout。** 歷史 host 在 `.1.*`／`.100.*` 位址間切換；不能僅由本機 transport log 排除其他路徑因素，亦不能把每次 timeout 都算成 IP 問題。",
        "- 10/4 16:16 事件的手機快照對時見 `outputs/手機診斷-20261004.md`；該報告另有新 IP 與 listener 證據，不能沿用全史 10061 比例判該次原因。本工具不讀設定或手機快照。",
        "3. **恢復延遲：前台保護。** 9/25 後她在淘寶等 app 時不搶前台、不點座標是必要保護；代價是自救要等可打擾窗口。",
        "4. **可排除：『沒有新資料』本身。** 成功拉取但 0 新資料在 log 中是成功行，未被算成 transport 失敗。",
        "5. **不能排除：手機省電／Wi‑Fi 休眠、HC app 被殺、多個舊 puller 同時寫 log。** 現有 log 沒有 process id／run id，不能再往下硬猜。",
        "",
        "## 電腦端改善提案（本輪不啟用、不重啟）",
        "",
        "- 每行改成 JSONL：ISO 時間、run UUID、PID、host、錯誤類型、fail count、上次成功／資料時間；先解決時長不可精確的證據缺口。",
        "- 下一次正常重啟後加 single-instance lock／heartbeat，避免舊 puller 疑似並行造成不同 host 交錯與重複提醒。",
        "- live loop 在 fails=3／10／30 時做一次受控 rediscovery，而不是只在程式啟動時掃；成功後只更新 host 單一鍵，保留 token。此功能涉及區網探測，須另開允許手機／區網的驗收窗口，本輪只提案。",
        "- 把 transport 失敗、HC 資料來源 stale、前台延後分成三種事件與三種告警，不再共用『拉不到』一個桶。",
        "- 截止時待補做：" + ("、".join(pending) if pending else "無") + "；不在本輪替她操作手機。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="離線分析 8787 拉取中斷")
    parser.add_argument("--output", default="-")
    args = parser.parse_args()
    text = render()
    if args.output == "-":
        print(text, end="")
    else:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
