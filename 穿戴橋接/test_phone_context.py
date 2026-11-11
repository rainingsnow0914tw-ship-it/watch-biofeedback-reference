"""手機情境的純假資料回歸；不載入本機紀錄。"""
import datetime as dt
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import 手機情境 as phone


class PhoneContextTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((Path(__file__).parent / "fixtures" / "手機情境-單元.json").read_text(encoding="utf-8"))
        self.start = dt.datetime(2026, 10, 21, 10)
        self.end = self.start + dt.timedelta(hours=1)
        self.guard = patch.object(phone, "load_range", side_effect=AssertionError("不讀真資料"))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def rows(self, key="rows"):
        return [dict(r, dt=self.start + dt.timedelta(minutes=r["minute"])) for r in self.data[key]]

    def test_empty_is_unknown(self):
        self.assertEqual(phone.window_stats(self.start, self.end, rows=[]), {"known": False})

    def test_integrates_state_before_start_and_includes_end_event(self):
        st = phone.window_stats(self.start, self.end, rows=self.rows())
        self.assertTrue(st["known"])
        self.assertEqual(st["screen_min"], 30)
        self.assertEqual(st["home_ratio"], 0.5)
        self.assertEqual(st["unlocks"], 2)
        self.assertEqual(st["events"], 4)
        self.assertEqual(st["battery"], 70)
        self.assertTrue(st["home"])

    def test_unknown_opening_starts_at_first_record(self):
        st = phone.window_stats(self.start, self.end, rows=self.rows("late_rows"))
        self.assertEqual(st["screen_min"], 20)
        self.assertEqual(st["home_ratio"], 0.5)

    def test_last_prior_state_carries_through_window(self):
        st = phone.window_stats(self.start, self.end, rows=self.rows()[:1])
        self.assertEqual(st["screen_min"], 60)
        self.assertEqual(st["home_ratio"], 1)
        self.assertEqual(st["unlocks"], 0)

    def test_end_event_does_not_invent_prior_screen_minutes(self):
        st = phone.window_stats(self.start, self.end, rows=self.rows()[-1:])
        self.assertEqual(st["screen_min"], 0)
        self.assertEqual(st["home_ratio"], 0)
        self.assertEqual(st["events"], 1)

    def test_strictly_future_record_is_unknown(self):
        rows = [{"dt": self.end + dt.timedelta(minutes=1), "screen": True, "home": True}]
        self.assertEqual(phone.window_stats(self.start, self.end, rows=rows), {"known": False})

    def test_place_home_clinic_and_unknown(self):
        with patch.object(phone, "places", return_value=self.data["places"]):
            for ip, expected in [("192.168.0.X", "家"), ("10.0.0.X", "公司"), ("8.8.8.8", None), (None, None), ("", None)]:
                with self.subTest(ip=ip):
                    self.assertEqual(phone.place_of(ip), expected)

    def test_format_unknown(self):
        self.assertEqual(phone.fmt_hour({"known": False}), "手機 —")

    def test_format_known(self):
        st = phone.window_stats(self.start, self.end, rows=self.rows())
        self.assertEqual(phone.fmt_hour(st), "進出 亮30分 解鎖 2 🔋70%")


if __name__ == "__main__":
    unittest.main()
