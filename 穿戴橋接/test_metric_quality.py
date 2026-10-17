import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import 今日報告 as report
import ibi_windows
import verify_claim


class MetricQualityTest(unittest.TestCase):
    def test_hrv_windows_are_scoped_to_observed_fragments(self):
        start = datetime(2026, 9, 30, 10).timestamp() * 1000
        windows = [SimpleNamespace(start_ms=start, duration_s=300),
                   SimpleNamespace(start_ms=start + 3600_000, duration_s=300)]
        low = report.format_hrv_evidence(windows, {"quality": "insufficient"})
        high = report.format_hrv_evidence(windows, {"quality": "sufficient"})
        self.assertIn("2 個可用 5 分鐘窗口", low)
        self.assertIn("只代表這些片段", low)
        self.assertIn("不給全日 HRV", high)
        self.assertIn("資料不足", report.format_hrv_evidence([], {"quality": "sufficient"}))

    def test_history_excludes_low_coverage_days(self):
        with tempfile.TemporaryDirectory() as dirname:
            old = report.SUM
            report.SUM = Path(dirname)
            try:
                for day, quality in (("2026-10-01", "insufficient"),
                                     ("2026-10-02", "sufficient")):
                    row = {"layer": "day", "cover": {"quality": quality},
                           "wear": {"longest_run_h": 3.0}}
                    (report.SUM / f"{day}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
                self.assertEqual([d for d, _ in report._hist("2026-10-03", 2)],
                                 ["2026-10-02"])
            finally:
                report.SUM = old

    def test_growing_baseline_is_not_a_comparison_band(self):
        info = {"status": "growing", "overall": {"n": 100, "p10": 50, "p50": 60, "p90": 70}}
        self.assertIsNone(verify_claim.baseline_band(
            {"types": {"heart_rate": info}}, "heart_rate", datetime.now().astimezone()))

    def test_hrv_loader_rejects_off_wrist_even_with_valid_sensor_status(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "2026-08-19.jsonl"
            base = {"type": "hr_ibi", "ibi": [800, 810], "sensor_t": 100000,
                    "status": 1}
            rows = [{**base, "on_body": body} for body in
                    ("on", "inferred_on", "off", "unknown")]
            rows.append({**base, "on_body": "on", "status": 0})
            path.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
            accepted = ibi_windows._load(path)
            self.assertEqual([r["on_body"] for r in accepted], ["on", "inferred_on"])

    def test_no_sleep_record_plus_high_hr_does_not_refute_user(self):
        now = datetime.now().astimezone()
        hr = [{"_dt": now - timedelta(minutes=30 + 60 * i), "bpm": 80}
              for i in range(6)]
        old = verify_claim.load_series
        verify_claim.load_series = lambda kind, *args, **kwargs: hr if kind == "heart_rate" else []
        try:
            baseline = {"types": {"heart_rate": {
                "status": "mature", "buckets": {},
                "overall": {"n": 100, "p10": 50, "p50": 60, "p90": 70}}}}
            answer = verify_claim.check_sleep(6, baseline)
        finally:
            verify_claim.load_series = old
        self.assertEqual(answer["verdict"], "說不準")
        self.assertIn("不能否定睡眠", answer["reason"])


if __name__ == "__main__":
    unittest.main()
