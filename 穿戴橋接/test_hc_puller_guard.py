import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import hc_puller


class HcForegroundGuardTest(unittest.TestCase):
    def setUp(self):
        self.old_run = hc_puller.subprocess.run
        self.old_foreground = hc_puller.前台
        self.old_can = hc_puller.可以動前台
        self.old_journal = hc_puller.延後紀錄
        self.old_deferred = hc_puller._延後過
        self.old_adb = hc_puller.ADB
        self.old_sleep = hc_puller.time.sleep
        self.temp = tempfile.TemporaryDirectory()
        hc_puller.延後紀錄 = Path(self.temp.name) / "deferred.jsonl"
        hc_puller._延後過 = set()
        hc_puller.ADB = Path(self.temp.name) / "adb.exe"
        hc_puller.ADB.write_bytes(b"stub")

    def tearDown(self):
        hc_puller.subprocess.run = self.old_run
        hc_puller.前台 = self.old_foreground
        hc_puller.可以動前台 = self.old_can
        hc_puller.延後紀錄 = self.old_journal
        hc_puller._延後過 = self.old_deferred
        hc_puller.ADB = self.old_adb
        hc_puller.time.sleep = self.old_sleep
        self.temp.cleanup()

    def test_adb_failure_is_unknown_foreground_not_screen_off(self):
        hc_puller.subprocess.run = lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="device not found")
        self.assertEqual(hc_puller.前台(), (True, None))

    def test_deferred_receipt_waits_until_command_succeeds(self):
        hc_puller.前台 = lambda: (True, "com.taobao.taobao")
        with redirect_stdout(io.StringIO()):
            self.assertFalse(hc_puller.可以動前台("叫醒 HC"))
            self.assertFalse(hc_puller.可以動前台("叫醒 HC"))
        rows = [json.loads(x) for x in hc_puller.延後紀錄.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["狀態"] for r in rows], ["延後"])
        hc_puller.前台 = lambda: (False, None)
        self.assertTrue(hc_puller.可以動前台("叫醒 HC"))
        self.assertIn("叫醒 HC", hc_puller._延後過)
        with redirect_stdout(io.StringIO()):
            hc_puller.完成延後("叫醒 HC")
        rows = [json.loads(x) for x in hc_puller.延後紀錄.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["狀態"] for r in rows], ["延後", "補做"])

    def test_failed_adb_wake_does_not_claim_success(self):
        hc_puller.可以動前台 = lambda action: True
        hc_puller._延後過.add("叫醒 HC")
        hc_puller.subprocess.run = lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="device not found")
        self.assertFalse(hc_puller.wake_app())
        self.assertIn("叫醒 HC", hc_puller._延後過)
        self.assertFalse(hc_puller.延後紀錄.exists())

    def test_failed_restart_launch_never_taps_screen(self):
        hc_puller.可以動前台 = lambda action: True
        calls = []
        def failed_run(*args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"device not found")
        hc_puller.subprocess.run = failed_run
        hc_puller.time.sleep = lambda seconds: self.fail("must not wait or tap after failed launch")
        self.assertFalse(hc_puller.restart_local_server())
        self.assertEqual(len(calls), 1)


    def test_restart_stops_if_foreground_changes_between_taps(self):
        hc_puller.可以動前台 = lambda action: True
        seen = iter([(True, hc_puller.HCWEBHOOK),
                     (True, hc_puller.HCWEBHOOK),
                     (True, "com.taobao.taobao")])
        hc_puller.前台 = lambda: next(seen)
        commands = []

        def run(*args, **kwargs):
            commands.append(args[0])
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        hc_puller.subprocess.run = run
        hc_puller.time.sleep = lambda seconds: None
        self.assertFalse(hc_puller.restart_local_server())
        taps = [cmd for cmd in commands if "input" in cmd]
        self.assertEqual(len(taps), 1)


if __name__ == "__main__":
    unittest.main()
