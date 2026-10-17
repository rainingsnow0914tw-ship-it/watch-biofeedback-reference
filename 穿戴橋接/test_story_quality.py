import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import 今天的故事 as story


class StoryQualityTest(unittest.TestCase):
    def test_off_body_status_one_heart_rate_is_not_body_evidence(self):
        day = "2026-09-01"
        start = dt.datetime(2026, 9, 1, 1, 0)
        rows = [{"t": day + "T01:01:00", "type": "hr_ibi", "hr": 90,
                 "status": 1, "on_body": "off"}]
        result = story.身體怎麼樣(rows, start, start + dt.timedelta(minutes=20),
                            states=["valid"] * 1440, day=day)
        self.assertEqual(result["hr_n"], 0)

    def test_sparse_window_keeps_event_but_refuses_body_comparison(self):
        day = "2026-09-01"
        start = dt.datetime(2026, 9, 1, 1, 0)
        rows = [{"t": day + "T01:01:00", "type": "hr_ibi", "hr": 90,
                 "status": 1, "on_body": "on"}]
        states = ["unknown"] * 1440
        states[61] = "valid"
        before = story.身體怎麼樣(rows, start - dt.timedelta(minutes=10), start,
                               states=states, day=day)
        after = story.身體怎麼樣(rows, start, start + dt.timedelta(minutes=20),
                              states=states, day=day)
        message = story.一句話(before, after, None)
        self.assertIn("資料不足", message)
        self.assertNotIn("心率 90", message)

    def test_low_coverage_story_headline_precedes_event(self):
        day = "2026-09-01"
        rows = [
            {"t": day + "T01:00:00", "type": "mark", "tag": "other"},
            {"t": day + "T01:01:00", "type": "hr_ibi", "hr": 90,
             "status": 1, "on_body": "on"},
        ]
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            bio = root / "watch"
            voice = root / "voice"
            bio.mkdir()
            voice.mkdir()
            (bio / (day + ".jsonl")).write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            original_bio, original_voice = story.BIO, story.VOICE
            story.BIO, story.VOICE = bio, voice
            try:
                output = story.故事(day)
            finally:
                story.BIO, story.VOICE = original_bio, original_voice
        lines = output.splitlines()
        self.assertIn("資料覆蓋", lines[1])
        self.assertIn("全日覆蓋不足", output)
        self.assertIn("身體反應資料不足", output)


if __name__ == "__main__":
    unittest.main()
