import unittest
import json
import tempfile
from datetime import datetime
from pathlib import Path

from 覆蓋率 import classify, headline
from 摘要層 import hour_summary
import 校準進度 as calibration


class CoverageEvidenceTest(unittest.TestCase):
    def test_missing_and_conflicting_minutes_stay_unknown(self):
        day = "2026-10-01"
        records = [
            {"t": day + "T00:00:02", "type": "hr_ibi", "on_body": "on", "hr": 70, "status": 1},
            {"t": day + "T00:01:02", "type": "availability", "on_body": "off",
             "state": "UNAVAILABLE_DEVICE_OFF_BODY"},
            {"t": day + "T00:03:01", "type": "motion", "on_body": "off", "activity": 0, "trust": False},
            {"t": day + "T00:03:50", "type": "hr_ibi", "on_body": "on", "hr": 75, "status": 1},
        ]
        c = classify(records, day, datetime(2026, 10, 1, 0, 4))
        self.assertEqual((c["minutes"], c["valid_minutes"], c["off_minutes"],
                          c["unknown_minutes"], c["conflict_minutes"]), (5, 1, 1, 3, 1))
        self.assertEqual(c["states"], ["valid", "off", "unknown", "unknown", "unknown"])

    def test_false_or_untrusted_heart_rate_does_not_create_coverage(self):
        day = "2026-09-30"
        rows = [
            {"t": day + "T12:01:00", "type": "hr_ibi", "on_body": "off", "hr": 80, "status": 1},
            {"t": day + "T12:02:00", "type": "hr_ibi", "on_body": "inferred_on", "hr": 0, "status": 1},
            {"t": day + "T12:03:00", "type": "motion", "on_body": "on", "activity": 1, "trust": False},
        ]
        c = classify(rows, day, datetime(2026, 10, 1, 12, 0))
        self.assertEqual((c["valid_minutes"], c["off_minutes"], c["unknown_minutes"]), (0, 0, 1440))
        self.assertIn("有效 0.0/24.0 小時", headline(c, day, datetime(2026, 10, 1, 12, 0)))

    def test_one_explicit_off_minute_does_not_label_whole_hour_off(self):
        day = "2026-09-30"
        one = [{"t": day + "T12:00:00", "type": "motion", "on_body": "off",
                "charging": False, "activity": 0, "trust": False}]
        self.assertEqual(hour_summary(day, 12, one)["verdict"], "unknown")
        many = [{**one[0], "t": day + f"T12:{m:02d}:00"} for m in range(30)]
        self.assertEqual(hour_summary(day, 12, many)["verdict"], "off")

    def test_in_progress_day_is_a_partial_report(self):
        day = "2026-10-01"
        rows = [{"t": day + f"T00:{m:02d}:00", "type": "motion",
                 "on_body": "on", "activity": 1, "trust": True} for m in range(8)]
        c = classify(rows, day, datetime(2026, 10, 1, 0, 9))
        self.assertEqual(c["quality"], "partial")

    def test_calibration_only_loads_samples_from_valid_days(self):
        with tempfile.TemporaryDirectory() as dirname:
            data = Path(dirname)
            valid_day = "2026-09-02"
            low_day = "2026-09-30"
            with (data / f"{valid_day}.jsonl").open("w", encoding="utf-8") as fh:
                for minute in range(1100):
                    row = {"t": f"{valid_day}T{minute // 60:02d}:{minute % 60:02d}:00",
                           "type": "motion", "on_body": "on", "trust": True, "activity": 1}
                    fh.write(json.dumps(row) + "\n")
                fh.write(json.dumps({"t": valid_day + "T12:00:01", "type": "mark", "tag": "exercise"}) + "\n")
            (data / f"{low_day}.jsonl").write_text(
                json.dumps({"t": low_day + "T12:00:01", "type": "mark", "tag": "exercise"}) + "\n",
                encoding="utf-8")
            old = calibration.BIO
            try:
                calibration.BIO = data
                records, days, total = calibration.load_all()
            finally:
                calibration.BIO = old
            self.assertEqual((days, total), ({valid_day}, 2))
            self.assertEqual([r["tag"] for r in records if r["type"] == "mark"], ["exercise"])


if __name__ == "__main__":
    unittest.main()
