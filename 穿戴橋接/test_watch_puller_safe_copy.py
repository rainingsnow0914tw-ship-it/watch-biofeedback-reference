import tempfile
import unittest
import json
import os
import time
import io
from contextlib import redirect_stdout
from pathlib import Path

import watch_puller


class WatchPullerSafeCopyTest(unittest.TestCase):
    def quiet(self, fn, *args, **kwargs):
        with redirect_stdout(io.StringIO()):
            return fn(*args, **kwargs)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_root = watch_puller.ROOT
        self.old_get = watch_puller._get
        watch_puller.ROOT = Path(self.temp.name) / "手錶橋接"
        watch_puller.ROOT.mkdir()
        self.path = watch_puller.ROOT / "2026-10-01.jsonl"

    def tearDown(self):
        watch_puller.ROOT = self.old_root
        watch_puller._get = self.old_get
        self.temp.cleanup()

    def test_shorter_phone_prefix_cannot_erase_verified_watch_tail(self):
        self.path.write_bytes(b'{"a":1}\n{"b":2}\n')
        watch_puller._get = lambda *args, **kwargs: b'{"a":1}\n'
        self.assertEqual(self.quiet(watch_puller.pull_day, "phone", "2026-10-01"), self.path)
        self.assertEqual(self.path.read_bytes(), b'{"a":1}\n{"b":2}\n')

    def test_longer_phone_prefix_extends_local_file(self):
        self.path.write_bytes(b'{"a":1}\n')
        watch_puller._get = lambda *args, **kwargs: b'{"a":1}\n{"b":2}\n'
        self.assertEqual(self.quiet(watch_puller.pull_day, "phone", "2026-10-01"), self.path)
        self.assertEqual(self.path.read_bytes(), b'{"a":1}\n{"b":2}\n')

    def test_divergence_is_reported_without_overwrite(self):
        self.path.write_bytes(b'{"a":1}\n')
        watch_puller._get = lambda *args, **kwargs: b'{"x":9}\n'
        self.assertIsNone(self.quiet(watch_puller.pull_day, "phone", "2026-10-01"))
        self.assertEqual(self.path.read_bytes(), b'{"a":1}\n')

    def test_same_size_hole_is_repaired_only_from_valid_phone_jsonl(self):
        clean = b'{"a":1}\n'
        broken = b'\x00' * len(clean)
        self.path.write_bytes(broken)
        watch_puller._get = lambda *args, **kwargs: clean
        self.assertEqual(self.quiet(watch_puller.pull_day, "phone", "2026-10-01",
                                    allow_valid_refresh=True), self.path)
        self.assertEqual(self.path.read_bytes(), clean)
        backup = watch_puller.ROOT.parent / "備份" / "同大小修補" / self.path.name
        self.assertEqual(backup.read_bytes(), broken)

    def test_catch_up_notices_newer_same_size_phone_file(self):
        clean = b'{"a":1}\n'
        self.path.write_bytes(b'\x00' * len(clean))
        os.utime(self.path, (1000, 1000))
        status = json.dumps({"now": int(time.time() * 1000),
                             "files": [{"day": "2026-10-01", "bytes": len(clean),
                                        "modified": int(time.time() * 1000)}]}).encode()
        watch_puller._get = lambda ip, path, **kwargs: status if path == "/status" else clean
        self.assertEqual(self.quiet(watch_puller.catch_up, "phone"), 1)
        self.assertEqual(self.path.read_bytes(), clean)


if __name__ == "__main__":
    unittest.main()
