# -*- coding: utf-8 -*-
""" """
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import 今日報告 as r


def day_rows(day, hours, rate, start_batt=100, still=0.95, on_body="on", charging_hours=(), gap_at=None):
    """每分鐘一筆 motion；rate＝每小時掉幾 %。charging_hours：哪幾個小時在充電。gap_at：第幾分鐘起斷 30 分鐘。"""
    t0 = datetime.strptime(day, "%Y-%m-%d").replace(hour=1)
    rows, batt, m = [], float(start_batt), 0
    while m < hours * 60:
        if gap_at is not None and m == gap_at:
            m += 30
            batt -= rate * 0.5        # 斷線期間照樣掉電，但不該算進去
            continue
        chg = (m // 60) in charging_hours
        rows.append({"t": (t0 + timedelta(minutes=m)).isoformat(), "type": "motion",
                     "batt": int(round(batt)), "charging": chg, "on_body": on_body, "still_ratio": still})
        batt += (5.0 / 60) if chg else -(rate / 60)
        m += 1
    return rows


def write(d: Path, day, rows):
    (d / f"{day}.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")


class BatteryTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_overall_and_still_rate(self):
        write(self.d, "2026-11-07", day_rows("2026-11-07", 10, 3.0))
        x = r.battery_drain(self.d / "2026-11-07.jsonl")
        self.assertAlmostEqual(x["rate"], 3.0, delta=0.15)
        self.assertAlmostEqual(x["still_rate"], 3.0, delta=0.15)

    def test_charging_and_gaps_are_skipped(self):
        write(self.d, "2026-11-07", day_rows("2026-11-07", 10, 2.0, start_batt=60, charging_hours=(3, 4), gap_at=400))
        x = r.battery_drain(self.d / "2026-11-07.jsonl")
        self.assertAlmostEqual(x["hours"], 10 - 2 - 0.5, delta=0.1, msg="充電兩小時、斷線半小時都不算")
        self.assertAlmostEqual(x["rate"], 2.0, delta=0.15, msg="充電時漲的電不能抵掉耗電")

    def test_uncertain_wear_is_not_counted_as_still(self):
        write(self.d, "2026-11-07", day_rows("2026-11-07", 6, 3.0, on_body="unknown"))
        x = r.battery_drain(self.d / "2026-11-07.jsonl")
        self.assertIsNone(x["still_rate"])
        self.assertIn("不猜", r.battery_line("2026-11-07", self.d))

    def test_inferred_on_counts_as_worn(self):
        write(self.d, "2026-11-07", day_rows("2026-11-07", 6, 3.0, on_body="inferred_on"))
        self.assertIsNotNone(r.battery_drain(self.d / "2026-11-07.jsonl")["still_rate"])

    def test_calibration_jump_up_is_ignored(self):
        rows = day_rows("2026-11-07", 6, 2.0)
        rows[100]["batt"] += 5     # 沒充電卻跳高
        write(self.d, "2026-11-07", rows)
        self.assertAlmostEqual(r.battery_drain(self.d / "2026-11-07.jsonl")["rate"], 2.0, delta=0.9)

    def test_compare_with_prior_ultra2_days_only(self):
        write(self.d, "2026-11-07", day_rows("2026-11-07", 10, 3.0))
        for day in ("2026-11-04", "2026-11-05", "2026-11-06"):
            write(self.d, day, day_rows(day, 10, 2.0))
        write(self.d, "2026-11-02", day_rows("2026-11-02", 10, 9.0))   # 舊錶：不能拿來比
        s = r.battery_line("2026-11-07", self.d)
        self.assertIn("前 7 天中位 −2.0%（3 天", s)
        self.assertIn("多 1.0", s)
        self.assertIn("滿電約撐 33 小時", s)

    def test_not_enough_data(self):
        write(self.d, "2026-11-07", day_rows("2026-11-07", 0.5, 3.0))
        self.assertIn("不夠算", r.battery_line("2026-11-07", self.d))
        self.assertIn("不夠算", r.battery_line("2026-11-08", self.d))   # 檔案不存在


if __name__ == "__main__":
    unittest.main(verbosity=2)
