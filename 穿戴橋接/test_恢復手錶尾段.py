import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import 恢復手錶尾段 as recovery


class LocalTailRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.old_pc, self.old_backup, self.old_adb = recovery.PC, recovery.BACKUP, recovery.adb
        recovery.PC = self.base / "pc"
        recovery.PC.mkdir()
        recovery.BACKUP = self.base / "backup"
        self.day = "2026-10-01"
        self.target = recovery.PC / f"{self.day}.jsonl"
        self.old = b'{"a":1}\n'
        self.full = self.old + b'{"b":2}\n'
        self.target.write_bytes(self.old)

    def tearDown(self):
        recovery.PC, recovery.BACKUP, recovery.adb = self.old_pc, self.old_backup, self.old_adb
        self.temp.cleanup()

    def test_verified_tail_restores_and_preserves_old_file(self):
        digest = hashlib.sha256(self.full).hexdigest().encode()
        recovery.adb = lambda serial, command, *args, **kwargs: (
            self.full[len(self.old):] if command == "tail" else digest + b"  file\n")
        self.assertEqual(recovery.restore("watch", self.day, len(self.full)), "restored")
        self.assertEqual(self.target.read_bytes(), self.full)
        self.assertEqual((recovery.BACKUP / self.target.name).read_bytes(), self.old)

    def test_hash_mismatch_never_replaces_original(self):
        recovery.adb = lambda serial, command, *args, **kwargs: (
            b'{"wrong":9}\n' if command == "tail" else
            hashlib.sha256(self.full).hexdigest().encode() + b"  file\n")
        self.assertEqual(recovery.restore("watch", self.day, len(self.full)),
                         "prefix_or_hash_mismatch")
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertFalse(recovery.BACKUP.exists())

    def test_full_hash_verification_checks_closed_day(self):
        self.target.write_bytes(self.full)
        digest = hashlib.sha256(self.full).hexdigest().encode()
        recovery.adb = lambda *args, **kwargs: digest + b"  file\n"
        with redirect_stdout(io.StringIO()):
            checked, mismatches = recovery.verify_all("watch", {self.day: len(self.full)},
                                                      "2026-10-02")
        self.assertEqual((checked, mismatches), (1, []))


if __name__ == "__main__":
    unittest.main()
