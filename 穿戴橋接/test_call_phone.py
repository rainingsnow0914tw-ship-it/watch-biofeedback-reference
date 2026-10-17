# -*- coding: utf-8 -*-
"""Offline client contract tests: mock transport only, no phone/ADB/network."""
import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("call_phone", pathlib.Path(__file__).with_name("叫手機.py"))
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)

class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = pathlib.Path(self.temp.name) / "config.json"
        self.write_config({"ip": "192.0.2.123", "token": "offline-test-token"})
        config_patch = patch.object(client, "CONFIG", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)

    def write_config(self, value):
        self.config.write_text(json.dumps(value), encoding="utf-8")

    def test_config_uses_8788_and_stays_read_only(self):
        before = self.config.read_bytes()
        self.assertEqual(client.load_config(), ("http://192.0.2.123:8788", "offline-test-token"))
        self.assertEqual(self.config.read_bytes(), before)

    def test_existing_8788_url(self):
        self.write_config({"ip": "http://192.0.2.123:8788/", "token": "t"})
        self.assertEqual(client.load_config()[0], "http://192.0.2.123:8788")

    def test_rejects_other_ports_and_redirect_targets_in_config(self):
        for ip in ["http://192.0.2.123:8787", "ftp://192.0.2.123", "http://user:pass@192.0.2.123", "http://192.0.2.123/path", "http://192.0.2.123?key=value"]:
            with self.subTest(ip=ip):
                self.write_config({"ip": ip, "token": "t"})
                with self.assertRaises(RuntimeError):
                    client.load_config()

    def test_missing_or_invalid_config(self):
        for value in [{}, [], {"ip": 1, "token": "t"}, {"ip": "192.0.2.123", "token": "t\nx"}]:
            self.write_config(value)
            with self.assertRaises(RuntimeError):
                client.load_config()

    def test_cli_bounds_and_mutual_exclusion(self):
        self.assertEqual(client.parse_args(["--ring", "30"]).ring, 30)
        for args in [["--ring", "0"], ["--ring", "31"], ["--ring", "1.5"], ["--ring", "5", "--reply"], ["--ring", "5", "--notify", "a", "b"], ["--notify", "", "body"]]:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                client.parse_args(args)
            self.assertEqual(result.exception.code, 2)

    def transport(self, payload=None, failure=None):
        class Opener:
            request = None
            def open(self, request, timeout):
                self.request, self.timeout = request, timeout
                if failure:
                    raise failure
                return contextlib.closing(io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
        return Opener()

    def test_ring_uses_exact_auth_post_and_json(self):
        opener = self.transport({"ok": True, "action": "ring", "seconds": 5})
        with patch.object(client.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(client.send("/ring", {"seconds": 5})["seconds"], 5)
        req = opener.request
        self.assertEqual(req.full_url, "http://192.0.2.123:8788/ring")
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer offline-test-token")
        self.assertEqual(json.loads(req.data), {"seconds": 5})
        self.assertEqual(opener.timeout, 15)

    def test_notify_unicode_and_reply_flag(self):
        opener = self.transport({"ok": True, "action": "notify"})
        with patch.object(client.urllib.request, "build_opener", return_value=opener), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(client.main(["--notify", "澄來了", "請回覆", "--reply"]), 0)
        self.assertEqual(json.loads(opener.request.data), {"title": "澄來了", "text": "請回覆", "reply_required": True})

    def test_http_permission_error_visible_and_exit_one(self):
        failure = urllib.error.HTTPError("http://test", 403, "denied", {}, io.BytesIO(b'{"error":"notification_permission_missing"}'))
        opener = self.transport(failure=failure)
        error = io.StringIO()
        with patch.object(client.urllib.request, "build_opener", return_value=opener), contextlib.redirect_stderr(error):
            self.assertEqual(client.main(["--notify", "a", "b"]), 1)
        self.assertIn("403", error.getvalue())
        self.assertIn("notification_permission_missing", error.getvalue())

    def test_timeout_is_runtime_error(self):
        opener = self.transport(failure=TimeoutError("offline timeout"))
        with patch.object(client.urllib.request, "build_opener", return_value=opener), self.assertRaises(RuntimeError):
            client.send("/ring", {"seconds": 5})

    def test_wrong_action_is_not_success(self):
        opener = self.transport({"ok": True, "action": "status"})
        with patch.object(client.urllib.request, "build_opener", return_value=opener), self.assertRaises(RuntimeError):
            client.send("/ring", {"seconds": 5})

    def test_no_redirect_or_proxy_credentials(self):
        self.assertIsNone(client.NoRedirect().redirect_request(None, None, 302, "", {}, "http://elsewhere"))
        seen = []
        opener = self.transport({"ok": True, "action": "ring"})
        def factory(*handlers):
            seen.extend(handlers)
            return opener
        with patch.object(client.urllib.request, "build_opener", side_effect=factory):
            client.send("/ring", {"seconds": 5})
        self.assertTrue(any(isinstance(h, client.NoRedirect) for h in seen))
        self.assertTrue(any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {} for h in seen))

if __name__ == "__main__":
    unittest.main(verbosity=2)
