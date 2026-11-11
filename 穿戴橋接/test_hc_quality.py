import unittest
from datetime import datetime, timedelta, timezone

import hc_baseline
from hc_quality import SAMSUNG_ORIGIN, heart_rate_evidence, sleep_evidence


META = {"data_origin": SAMSUNG_ORIGIN}


class HealthConnectQualityTest(unittest.TestCase):
    def test_heart_day_requires_spread_and_distinct_samples(self):
        rows = []
        for day, hours in ((10, 17), (11, 16)):
            for hour in range(hours):
                for minute in (0, 10, 20):
                    dt = datetime(2026, 9, day, hour, minute, tzinfo=timezone(timedelta(hours=8)))
                    rows.append({"_dt": dt, "bpm": 70, "metadata": META})
        rows.append(dict(rows[0]))  # duplicate timestamp cannot add coverage
        rows.append({"_dt": datetime(2026, 9, 12, tzinfo=timezone.utc), "bpm": 70})
        accepted, quality = heart_rate_evidence(
            rows, now=datetime(2026, 10, 2, tzinfo=timezone(timedelta(hours=8))))
        self.assertEqual((quality["source_days"], quality["valid_days"], len(accepted)), (2, 1, 51))
        self.assertEqual(quality["capture_ratio"], 0.5)

    def test_sleep_uses_sleeping_stages_and_deduplicates(self):
        start = datetime(2026, 9, 10, 22, tzinfo=timezone(timedelta(hours=8)))
        split = start + timedelta(minutes=30)
        end = start + timedelta(hours=2)
        def stage(kind, a, b):
            return {"stage": str(kind), "start_time": a.isoformat(),
                    "end_time": b.isoformat(), "duration_seconds": int((b-a).total_seconds())}
        record = {"session_end_time": end.isoformat(), "duration_seconds": 7200,
                  "metadata": META, "stages": [stage(1, start, split), stage(4, split, end)]}
        bad = {**record, "stages": [stage(0, start, end)]}
        sessions, quality = sleep_evidence([record, dict(record), bad])
        self.assertEqual((quality["source_sessions"], quality["valid_sessions"]), (3, 1))
        self.assertEqual((sessions[0]["span_hours"], sessions[0]["asleep_hours"]), (2.0, 1.5))

    def test_overlapping_sleep_stages_are_not_valid_evidence(self):
        start = datetime(2026, 9, 10, 22, tzinfo=timezone(timedelta(hours=8)))
        end = start + timedelta(hours=2)
        row = {"session_end_time": end.isoformat(), "duration_seconds": 7200,
               "metadata": META,
               "stages": [
                   {"stage": "1", "start_time": start.isoformat(),
                    "end_time": (start + timedelta(hours=1)).isoformat()},
                   {"stage": "4", "start_time": start.isoformat(),
                    "end_time": end.isoformat()},
               ]}
        sessions, quality = sleep_evidence([row])
        self.assertEqual((len(sessions), quality["excluded_sessions"]), (0, 1))

    def test_growing_baseline_cannot_say_normal(self):
        rows = []
        for day in (10, 11, 12):
            for hour in range(17):
                for minute in (0, 10, 20):
                    rows.append({"_dt": datetime(2026, 9, day, hour, minute,
                                                  tzinfo=timezone(timedelta(hours=8))),
                                 "bpm": 70, "metadata": META})
        info = hc_baseline.build_numeric("heart_rate", rows)
        self.assertEqual((info["days"], info["status"]), (3, "growing"))
        verdict = hc_baseline.assess("heart_rate", 70,
                                     baseline={"types": {"heart_rate": info}})
        self.assertEqual(verdict["status"], "insufficient")

    def test_unqualified_numeric_type_never_claims_maturity(self):
        rows = [{"_dt": datetime(2026, 9, day, tzinfo=timezone.utc),
                 "percentage": 97} for day in range(1, 25)]
        info = hc_baseline.build_numeric("oxygen_saturation", rows)
        self.assertEqual(info["status"], "quality_unverified")
        verdict = hc_baseline.assess("oxygen_saturation", 97,
                                     baseline={"types": {"oxygen_saturation": info}})
        self.assertEqual(verdict["status"], "insufficient")


if __name__ == "__main__":
    unittest.main()
