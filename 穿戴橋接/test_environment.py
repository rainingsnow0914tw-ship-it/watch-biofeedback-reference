"""光線與氣壓門檻：所有輸入皆為合成資料。"""
import datetime as dt
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

import 環境 as environment


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((Path(__file__).parent / "fixtures" / "環境-單元.json").read_text(encoding="utf-8"))
        self.start = dt.datetime(2026, 10, 21, 10)

    def rows(self, section, name):
        key = "lux" if section == "lights" else "hpa"
        return [{"dt": self.start + dt.timedelta(minutes=minute), key: value, "hpa_n": 60} for minute, value in self.data[section][name]]

    def test_bright_then_three_minutes_dark(self):
        self.assertEqual(environment.lights_off_at(self.rows("lights", "sustained")), [{"t": self.start + dt.timedelta(minutes=1), "lux_before": 20, "lux_after": 1}])

    def test_always_dark_is_not_a_transition(self):
        self.assertEqual(environment.lights_off_at(self.rows("lights", "always_dark")), [])

    def test_less_than_three_minutes_does_not_count(self):
        self.assertEqual(environment.lights_off_at(self.rows("lights", "too_short")), [])

    def test_exactly_three_lux_interrupts_darkness(self):
        self.assertEqual(environment.lights_off_at(self.rows("lights", "interrupted")), [])

    def test_two_separate_lights_off_events(self):
        events = environment.lights_off_at(self.rows("lights", "twice"))
        self.assertEqual([x["t"] for x in events], [self.start + dt.timedelta(minutes=n) for n in (1, 6)])

    def test_empty_inputs(self):
        self.assertEqual(environment.lights_off_at([]), [])
        self.assertEqual(environment.floor_changes([]), [])

    def test_pressure_drop_is_one_floor_up_in_elevator(self):
        steps = Mock(return_value=0)
        events = environment.floor_changes(self.rows("floors", "up"), steps_fn=steps)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], {"t0": self.start, "t1": self.start + dt.timedelta(minutes=4), "delta_hpa": -0.36, "floors": 1, "direction": "上", "how": "電梯", "steps": 0})
        steps.assert_called_once_with(self.start, self.start + dt.timedelta(minutes=4))

    def test_pressure_rise_is_down_with_unknown_transport(self):
        event, = environment.floor_changes(self.rows("floors", "down"), steps_fn=lambda *_: None)
        self.assertEqual((event["direction"], event["floors"], event["how"]), ("下", 1, "不知道"))

    def test_enough_steps_indicate_stairs(self):
        event, = environment.floor_changes(self.rows("floors", "up"), steps_fn=lambda *_: 200)
        self.assertEqual(event["how"], "樓梯")

    def test_threshold_and_four_minute_window(self):
        self.assertEqual(len(environment.floor_changes(self.rows("floors", "at_threshold"))), 1)
        self.assertEqual(environment.floor_changes(self.rows("floors", "below_threshold")), [])
        self.assertEqual(environment.floor_changes(self.rows("floors", "too_slow")), [])

    def test_same_direction_extensions_merge(self):
        event, = environment.floor_changes(self.rows("floors", "merge"), steps_fn=lambda *_: 200)
        self.assertEqual((event["delta_hpa"], event["floors"], event["direction"]), (-0.72, 2, "上"))
        self.assertEqual(event["t1"], self.start + dt.timedelta(minutes=4))

    def test_opposite_directions_remain_separate(self):
        events = environment.floor_changes(self.rows("floors", "reverse"), steps_fn=lambda *_: 0)
        self.assertEqual([event["direction"] for event in events], ["上", "下"])


if __name__ == "__main__":
    unittest.main()


class FloorChangesRegression(unittest.TestCase):
    """ """

    def _rows(self, seq):
        import datetime as dt
        t0 = dt.datetime(2026, 10, 22, 10, 0)
        return [{"dt": t0 + dt.timedelta(minutes=m), "hpa": v, "hpa_n": 60, "lux": 20} for m, v in seq]

    def test_float_delta_exactly_threshold(self):
        import 環境
        out = 環境.floor_changes(self._rows([(0, 1000.0), (1, 1000.0), (2, 999.85), (3, 999.75), (4, 999.7)]))
        self.assertEqual([(x["delta_hpa"], x["direction"]) for x in out], [(-0.3, "上")])

    def test_up_then_down_gives_two_changes(self):
        import 環境
        out = 環境.floor_changes(self._rows([(0, 1000.0), (2, 999.64), (4, 1000.0)]))
        self.assertEqual([(x["delta_hpa"], x["direction"]) for x in out], [(-0.36, "上"), (0.36, "下")])


class PressureTrendAndFloors(unittest.TestCase):
    """ """

    T0 = dt.datetime(2026, 11, 7, 0, 0)

    def _rows(self, fn, hours=30, step=1):
        import math
        return [{"dt": self.T0 + dt.timedelta(minutes=m), "hpa": round(fn(m), 2), "hpa_n": 60} for m in range(0, hours * 60, step)]

    @staticmethod
    def _tide(m):
        import math
        return 1.2 * math.cos(2 * math.pi * (m / 60 - 10) / 12)   # 每天兩次起落，10 點、22 點最高

    def test_elevator_jump_removed_from_series(self):
        rows = self._rows(lambda m: 1015.0 if m < 60 else (1015.0 - 7.2 * min(1, (m - 60) / 3)), hours=2)
        s = environment.pressure_series(rows)
        self.assertLess(max(abs(c - 1015.0) for _, c in s), 0.4)

    def test_gap_jump_absorbed(self):
        rows = [r for r in self._rows(lambda m: 1015.0 if m < 60 else 1008.0, hours=2) if not (60 <= (r["dt"] - self.T0).total_seconds() / 60 < 100)]
        s = environment.pressure_series(rows)
        self.assertLess(max(abs(c - 1015.0) for _, c in s), 0.4)

    def test_tide_only_is_steady(self):
        rows = self._rows(lambda m: 1013 + self._tide(m))
        r = environment.pressure_trend(self.T0 + dt.timedelta(hours=29, minutes=59), rows)
        self.assertEqual(r["level"], "平穩")
        self.assertLess(abs(r["d24"]), 0.6)

    def test_rising_three_over_a_day(self):
        rows = self._rows(lambda m: 1013 + self._tide(m) + 3.0 * m / 1440)
        r = environment.pressure_trend(self.T0 + dt.timedelta(hours=29, minutes=59), rows)
        self.assertEqual(r["level"], "明顯在升")
        self.assertIn("冷空氣", r["line"])

    def test_falling_three_over_a_day(self):
        rows = self._rows(lambda m: 1013 + self._tide(m) - 3.0 * m / 1440)
        r = environment.pressure_trend(self.T0 + dt.timedelta(hours=29, minutes=59), rows)
        self.assertEqual(r["level"], "明顯在掉")
        self.assertIn("變天", r["line"])

    def test_not_enough_history(self):
        rows = self._rows(lambda m: 1013.0, hours=4)
        r = environment.pressure_trend(self.T0 + dt.timedelta(hours=3, minutes=59), rows)
        self.assertIsNone(r["d24"])
        self.assertEqual(r["level"], "看不出來")

    def test_floor_jump_not_counted_as_weather(self):

        rows = self._rows(lambda m: 1013 + self._tide(m) + (7.2 * min(1, max(0, (m - 1200) / 3)) if m >= 1200 else 0))
        r = environment.pressure_trend(self.T0 + dt.timedelta(hours=29, minutes=59), rows)
        self.assertEqual(r["level"], "平穩")

    def test_daily_floors_summary(self):
        seq = [(0, 1015.0), (1, 1015.0), (4, 1007.8), (5, 1007.8), (300, 1007.8), (301, 1008.5), (302, 1008.5), (600, 1008.5), (603, 1015.3)]
        rows = [{"dt": self.T0 + dt.timedelta(minutes=m), "hpa": v, "hpa_n": 60} for m, v in seq]
        steps = lambda t0, t1: 60 if (t0 - self.T0).total_seconds() / 60 >= 290 and (t0 - self.T0).total_seconds() / 60 < 400 else 0
        d = environment.daily_floors("2026-11-07", rows=rows, steps_fn=steps)
        self.assertEqual((d["電梯"], d["樓梯"], d["上"], d["下"]), (2, 1, 20, 21))   # 7.8→8.5 是 2 層、8.5→15.3 是 19 層
        self.assertIn("電梯 2 次", d["line"])

    def test_daily_floors_unknown_steps(self):
        rows = [{"dt": self.T0 + dt.timedelta(minutes=m), "hpa": v, "hpa_n": 60} for m, v in [(0, 1015.0), (3, 1014.3)]]
        d = environment.daily_floors("2026-11-07", rows=rows, steps_fn=lambda *_: None)
        self.assertEqual(d["不知道"], 1)
        self.assertIn("不知道怎麼", d["line"])

    def test_daily_floors_none(self):
        d = environment.daily_floors("2026-11-07", rows=[], steps_fn=lambda *_: None)
        self.assertIn("沒抓到", d["line"])
