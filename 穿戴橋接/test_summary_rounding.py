import unittest

import 摘要層


class SummaryRoundingTest(unittest.TestCase):
    def test_decimal_ties_use_shared_half_even_rule(self):
        self.assertEqual(摘要層.round_summary(564.65, 1), 564.6)
        self.assertEqual(摘要層.round_summary(187.85, 1), 187.8)
        self.assertEqual(摘要層.round_summary(110.05, 1), 110.0)
        self.assertEqual(摘要層.round_summary(415.85, 1), 415.8)
        self.assertEqual(摘要層.round_summary(0.155, 2), 0.16)

    def test_hourly_motion_uses_decimal_contract(self):
        row = {"t": "2026-09-02T09:00:00", "type": "motion", "on_body": "on",
               "activity": 564.65, "trust": True, "charging": False}
        hour = 摘要層.hour_summary("2026-09-02", 9, [row])
        self.assertEqual(hour["motion"], {"p50": 564.6, "hi": 564.6})


if __name__ == "__main__":
    unittest.main()
