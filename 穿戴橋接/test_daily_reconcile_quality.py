import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import 每天對一下 as daily


class DailyReconcileQualityTest(unittest.TestCase):
    def run_reconcile(self, rows):
        day = "2026-09-01"
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            for name in ("bio", "voice", "buzz"):
                (root / name).mkdir()
            (root / "bio" / (day + ".jsonl")).write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            old = daily.BIO, daily.VOICE, daily.BUZZ
            daily.BIO, daily.VOICE, daily.BUZZ = (
                root / "bio", root / "voice", root / "buzz")
            output = io.StringIO()
            try:
                with redirect_stdout(output):
                    daily.reconcile(day)
            finally:
                daily.BIO, daily.VOICE, daily.BUZZ = old
        return output.getvalue()

    def test_sparse_mark_keeps_mark_but_suppresses_body_numbers(self):
        day = "2026-09-01"
        rows = [
            {"t": day + "T01:10:00", "type": "mark", "label": "test", "tag": "other"},
            {"t": day + "T01:11:00", "type": "hr_ibi", "hr": 90,
             "status": 1, "on_body": "on"},
        ]
        output = self.run_reconcile(rows)
        self.assertIn("資料覆蓋", output)
        self.assertIn("test", output)
        self.assertIn("資料不足，不能作生理對照", output)
        self.assertNotIn("心率中位 90", output)

    def test_valid_window_excludes_off_body_heart_rate(self):
        day = "2026-09-01"
        rows = [{"t": day + "T01:10:00", "type": "mark",
                 "label": "test", "tag": "other"}]
        for minute in range(20):
            stamp = day + f"T01:{minute:02d}:00"
            rows.extend([
                {"t": stamp, "type": "motion", "on_body": "on",
                 "trust": True, "activity": 12},
                {"t": stamp, "type": "hr_ibi", "on_body": "on",
                 "status": 1, "hr": 60},
            ])
        rows.append({"t": day + "T01:11:30", "type": "hr_ibi",
                     "on_body": "off", "status": 1, "hr": 200})
        output = self.run_reconcile(rows)
        self.assertIn("心率中位 60", output)
        self.assertNotIn("心率中位 200", output)


if __name__ == "__main__":
    unittest.main()
