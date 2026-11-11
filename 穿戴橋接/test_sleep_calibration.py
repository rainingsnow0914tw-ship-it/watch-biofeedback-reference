import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("睡眠偵測校準.py")
spec = importlib.util.spec_from_file_location("sleep_calibration", MODULE_PATH)
calibration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibration)


class SleepCalibrationTest(unittest.TestCase):
    def test_cross_midnight_minutes_require_half_minute_overlap(self):
        start = dt.datetime(2026, 9, 1, 23, 59, 30, tzinfo=calibration.LOCAL_TZ)
        end = dt.datetime(2026, 9, 2, 0, 1, 20, tzinfo=calibration.LOCAL_TZ)
        self.assertEqual(calibration._睡著分鐘(start, end), {
            ("2026-09-01", "23:59"), ("2026-09-02", "00:00")
        })

    def test_date_holdout_has_gap(self):
        dates = [f"2026-09-{d:02d}" for d in range(1, 11)]
        train, gap, holdout = calibration.日期分組(dates)
        self.assertEqual((len(train), len(gap), len(holdout)), (6, 1, 3))
        self.assertLess(train[-1], gap[0])
        self.assertLess(gap[0], holdout[0])

    def test_unknown_or_awake_stage_never_becomes_sleep_label(self):
        day = dt.datetime.now(calibration.LOCAL_TZ).date() - dt.timedelta(days=1)
        start = dt.datetime.combine(day, dt.time(0, 0), calibration.LOCAL_TZ)
        end = start + dt.timedelta(minutes=2)
        record = {
            "session_end_time": end.isoformat(),
            "duration_seconds": 120,
            "metadata": {"data_origin": "com.sec.android.app.shealth"},
            "stages": [
                {"stage": 3, "start_time": start.isoformat(),
                 "end_time": (start + dt.timedelta(minutes=1)).isoformat()},
                {"stage": 4, "start_time": (start + dt.timedelta(minutes=1)).isoformat(),
                 "end_time": end.isoformat()},
            ],
        }
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            (root / "series").mkdir()
            (root / "series" / "sleep.jsonl").write_text(
                json.dumps(record) + "\n", encoding="utf-8")
            original = calibration.BASE
            calibration.BASE = root
            try:
                labels = calibration.三星的答案(7)
            finally:
                calibration.BASE = original
        self.assertEqual(labels, {day.isoformat(): {"00:01"}})

    def test_off_body_heart_rate_does_not_set_daily_threshold(self):
        day = "2026-09-09"
        rows = [
            {"t": day + "T00:00:00+08:00", "type": "motion", "on_body": "on",
             "still_ratio": 0.9},
            {"t": day + "T00:00:01+08:00", "type": "hr_ibi", "on_body": "off",
             "status": 1, "hr": 150},
            {"t": day + "T00:00:02+08:00", "type": "hr_ibi", "on_body": "on",
             "status": 1, "hr": 60},
            {"t": day + "T00:00:03+08:00", "type": "hr", "on_body": "on",
             "bpm": 70},
        ]
        with tempfile.TemporaryDirectory() as dirname:
            original = calibration.橋
            calibration.橋 = Path(dirname)
            (calibration.橋 / (day + ".jsonl")).write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            calibration.每分鐘.cache_clear()
            try:
                self.assertEqual(calibration.每分鐘(day)["00:00"]["hr"], [60, 70])
            finally:
                calibration.橋 = original
                calibration.每分鐘.cache_clear()

    def test_sliding_window_does_not_bridge_a_long_gap(self):
        minutes = {
            "00:00": {"hr": [70], "still": 0.5, "worn": True},
            "01:00": {"hr": [120], "still": 0.5, "worn": True},
            "01:01": {"hr": [120], "still": 0.5, "worn": True},
        }
        result = calibration.滑動(minutes, 15)
        self.assertEqual(result["00:00"][0], 70)


if __name__ == "__main__":
    unittest.main()
