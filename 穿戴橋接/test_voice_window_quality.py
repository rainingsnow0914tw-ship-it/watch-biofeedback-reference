import datetime as dt
import io
import unittest
from contextlib import redirect_stdout

import 那句話前後發生什麼 as context


class VoiceWindowQualityTest(unittest.TestCase):
    def test_sparse_window_does_not_print_physio_values(self):
        day = "2026-09-01"
        start = dt.datetime(2026, 9, 1, 1, 0)
        rows = [{"t": day + "T01:01:00", "type": "hr_ibi", "hr": 90,
                 "status": 1, "on_body": "on"}]
        states = ["unknown"] * 1440
        states[61] = "valid"
        output = io.StringIO()
        with redirect_stdout(output):
            context.describe(rows, start, start + dt.timedelta(minutes=30),
                             "test", states, day)
        self.assertIn("有效 1/30", output.getvalue())
        self.assertIn("不能作身體對照", output.getvalue())
        self.assertNotIn("心率  中位", output.getvalue())

    def test_off_body_hr_is_excluded_and_skin_field_is_read(self):
        day = "2026-09-01"
        start = dt.datetime(2026, 9, 1, 1, 0)
        rows = [
            {"t": day + "T01:01:00", "type": "hr_ibi", "hr": 200,
             "status": 1, "on_body": "off"},
            {"t": day + "T01:02:00", "type": "hr_ibi", "hr": 60,
             "status": 1, "on_body": "on"},
            {"t": day + "T01:03:00", "type": "skin_temp", "skin": 33.2,
             "on_body": "on", "charging": False},
        ]
        output = io.StringIO()
        with redirect_stdout(output):
            context.describe(rows, start, start + dt.timedelta(minutes=30),
                             "test", ["valid"] * 1440, day)
        self.assertIn("心率  中位 60", output.getvalue())
        self.assertNotIn("200", output.getvalue())
        self.assertIn("皮膚感測值 中位 33.20", output.getvalue())


if __name__ == "__main__":
    unittest.main()
