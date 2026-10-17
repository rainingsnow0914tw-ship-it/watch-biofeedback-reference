import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from 運動摘要 import one_line


class ExerciseSummaryTest(unittest.TestCase):
    def write_day(self, root: Path, rows: list[dict]):
        (root / "2026-09-21.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    def regular_minutes(self):
        day = "2026-09-21"
        rows = []
        for minute in range(20):
            stamp = day + f"T14:{minute:02d}:00"
            rows.extend([
                {"t": stamp, "type": "motion", "on_body": "on",
                 "trust": True, "activity": 200},
                {"t": stamp, "type": "hr_ibi", "on_body": "on",
                 "status": 1, "hr": 60 if minute < 10 else 100},
            ])
        return rows

    def test_marked_window_has_weekly_button_rank(self):
        rows = self.regular_minutes()
        rows += [
            {"t": "2026-09-21T10:00:00", "type": "mark", "tag": "exercise"},
            {"t": "2026-09-21T14:00:00", "type": "mark", "tag": "exercise"},
        ]
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            self.write_day(root, rows)
            result = one_line(root, datetime(2026, 9, 21, 14, 0),
                              datetime(2026, 9, 21, 14, 20))
        self.assertIn("做了 20 分鐘", result)
        self.assertIn("心跳從 60 到 100", result)
        self.assertIn("本週第 2 次（按鈕）", result)

    def test_unmarked_window_is_candidate_not_weekly_exercise(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            self.write_day(root, self.regular_minutes())
            result = one_line(root, datetime(2026, 9, 21, 14, 0),
                              datetime(2026, 9, 21, 14, 20))
        self.assertIn("候選活動窗 20 分鐘", result)
        self.assertIn("本週次數未計入", result)
        self.assertNotIn("做了 20 分鐘", result)

    def test_off_and_unknown_minutes_are_not_exercise(self):
        rows = [
            {"t": "2026-09-21T14:00:00", "type": "motion",
             "on_body": "on", "trust": True, "activity": 200},
            {"t": "2026-09-21T14:01:00", "type": "motion",
             "on_body": "off", "trust": False, "activity": 0},
        ]
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            self.write_day(root, rows)
            result = one_line(root, datetime(2026, 9, 21, 14, 0),
                              datetime(2026, 9, 21, 14, 3))
        self.assertIn("資料有效 1／離腕 1／未知 1 分鐘", result)
        self.assertIn("起終心跳資料不足", result)


if __name__ == "__main__":
    unittest.main()
