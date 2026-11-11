import tempfile
import json
import os
from datetime import datetime
import unittest
from pathlib import Path
from unittest.mock import patch

import 今日報告
import 摘要層


HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "日報-2026-09-02-片段.jsonl"


class DailyReportCoverageRegressionTest(unittest.TestCase):
    def test_real_0902_fragment_is_insufficient_not_eighteen_hour_sleep(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            bio = root / "手錶橋接"
            summary = root / "摘要"
            voice = root / "語音筆記"
            buzz = root / "震動史"
            for path in (bio, summary, voice, buzz):
                path.mkdir(parents=True)
            (bio / "2026-09-02.jsonl").write_text(
                FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
            )
            with (
                patch.object(今日報告, "BIO", bio),
                patch.object(今日報告, "SUM", summary),
                patch.object(今日報告, "VOICE", voice),
                patch.object(今日報告, "BUZZ", buzz),
                patch.object(今日報告, "VERDICT", bio / "判斷日誌.jsonl"),
                patch.object(摘要層, "BIO", bio),
                patch.object(摘要層, "OUT", summary),
            ):
                text = 今日報告.report("2026-09-02")
            self.assertTrue(text.splitlines()[0].startswith("資料覆蓋"))
            self.assertIn("資料不足", text)
            self.assertNotIn("18.0 小時", text)
            self.assertNotIn("心率推估連續低心率段", text)


    def test_high_coverage_sleep_fallback_does_not_bridge_separated_low_hours(self):
        # Branch test: the real two sensor samples anchor the hour medians.
        # Coverage and summary are injected to reach the fallback independently
        # of the low-coverage and stale-snapshot guards in the first test.
        anchor = datetime(2026, 9, 3, 12)
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return anchor
        rows = [json.loads(x) for x in FIXTURE.read_text(encoding='utf-8').splitlines()]
        hours = [dict(t=r['t'][:13]+':00:00', hr=dict(p50=r['bpm'], lo=r['bpm']),
                      cover=dict(worn_minutes=45), verdict='ok') for r in rows]
        coverage = dict(minutes=1440, valid_minutes=1200, off_minutes=0,
                        unknown_minutes=240, quality='sufficient')
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            bio = root / '手錶橋接'
            bio.mkdir()
            (bio / '2026-09-02.jsonl').write_text(FIXTURE.read_text(encoding='utf-8'), encoding='utf-8')
            sleep = root / '睡眠.json'
            sleep.write_text(json.dumps(dict(ok=True, days=2, sessions=[])), encoding='utf-8')
            os.utime(sleep, (anchor.timestamp(), anchor.timestamp()))
            with patch.object(今日報告, 'BIO', bio), patch.object(今日報告, 'SUM', root/'摘要'), patch.object(
                今日報告, 'VOICE', root/'語音筆記'
            ), patch.object(今日報告, 'BUZZ', root/'震動史'), patch.object(
                今日報告, 'VERDICT', root/'判斷日誌.jsonl'
            ), patch.object(今日報告, 'datetime', FrozenDateTime), patch.object(
                今日報告, 'classify', return_value=coverage
            ), patch.object(摘要層, 'build', return_value=(hours, None)), patch.object(今日報告, '_hist', return_value=[]):
                text = 今日報告.report('2026-09-02')
        self.assertIn('心率推估連續低心率段 1.0 小時', text)
        self.assertNotIn('18.0 小時', text)
        self.assertIn('尚不能當作實睡', text)


if __name__ == "__main__":
    unittest.main()
