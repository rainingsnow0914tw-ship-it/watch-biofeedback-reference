import unittest
import io
import tempfile
from contextlib import redirect_stdout
from unittest.mock import patch
import 覆蓋率報表
from datetime import datetime
from pathlib import Path

from 覆蓋率 import classify, load_records
from 覆蓋率報表 import independent_tally


HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "覆蓋率-2026-09-30-片段.jsonl"


class CoverageEveningRegressionTest(unittest.TestCase):
    def test_real_fragment_keeps_conflict_unknown(self):
        rows = load_records(FIXTURE)
        cover = classify(rows, "2026-09-30", datetime(2026, 10, 4, 16, 30))
        self.assertEqual(
            (cover["valid_minutes"], cover["off_minutes"],
             cover["unknown_minutes"], cover["conflict_minutes"]),
            (1, 1, 1438, 1),
        )
        self.assertEqual(cover["states"][91], "unknown")

    def test_independent_tally_matches_classifier(self):
        rows = load_records(FIXTURE)
        cover = classify(rows, "2026-09-30", datetime(2026, 10, 4, 16, 30))
        tally = independent_tally(rows, "2026-09-30")
        self.assertEqual(
            (tally["valid_minutes"], tally["off_minutes"],
             tally["unknown_minutes"], tally["conflict_minutes"]),
            (cover["valid_minutes"], cover["off_minutes"],
             cover["unknown_minutes"], cover["conflict_minutes"]),
        )


    def test_regression_rejects_eighteen_hours_even_with_unknown_notice(self):
        for amount in ('18', '18.0', '18.00'):
            with self.subTest(amount=amount), patch(
                '今日報告.report',
                return_value='資料覆蓋（全日）\n三星紀錄實睡 '+amount+' 小時；資料不足',
            ):
                self.assertFalse(覆蓋率報表._report_0902()[0])

    def test_regression_requires_coverage_headline(self):
        with patch('今日報告.report', return_value='資料不足；有沒有睡不知道'):
            self.assertFalse(覆蓋率報表._report_0902()[0])

    def test_cli_failure_is_nonzero_and_does_not_claim_tally_matches(self):
        wrong = dict(on_signal=0, off_evidence=0, valid_minutes=1,
                     off_minutes=0, unknown_minutes=1439, conflict_minutes=0)
        with tempfile.TemporaryDirectory() as dirname, patch(
            'sys.argv', ['coverage', '--start', '2026-09-30', '--end', '2026-09-30',
                         '--manual-day', '2026-09-30', '--data-dir', dirname],
        ), patch.object(覆蓋率報表, 'independent_tally', return_value=wrong), patch.object(
            覆蓋率報表, '_report_0902', return_value=(False, ['資料不足']),
        ), redirect_stdout(io.StringIO()) as output:
            code = 覆蓋率報表.main()
        self.assertEqual(code, 1)
        self.assertIn('與正式分類不一致', output.getvalue())
        self.assertNotIn('與正式分類相同', output.getvalue())

    def test_cli_success_is_zero(self):
        with tempfile.TemporaryDirectory() as dirname, patch(
            'sys.argv', ['coverage', '--start', '2026-09-30', '--end', '2026-09-30',
                         '--manual-day', '2026-09-30', '--data-dir', dirname],
        ), patch.object(覆蓋率報表, '_report_0902', return_value=(True, ['資料覆蓋'])), redirect_stdout(io.StringIO()):
            self.assertEqual(覆蓋率報表.main(), 0)


if __name__ == "__main__":
    unittest.main()
