"""Run existing offline regressions with network and device process access blocked."""
from __future__ import annotations
import ast
import contextlib
import io
import json
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "穿戴橋接"
MODULES = [
    "test_daily_reconcile_quality", "test_hc_puller_guard", "test_hc_quality",
    "test_metric_quality", "test_sleep_calibration", "test_story_quality",
    "test_summary_rounding", "test_voice_window_quality",
    "test_watch_puller_safe_copy", "test_恢復手錶尾段", "test_日報覆蓋率晚間",
    "test_覆蓋率", "test_覆蓋率晚間", "test_運動摘要",
]

def guard(event, args):
    if event in {"subprocess.Popen", "socket.connect", "socket.connect_ex", "socket.bind"}:
        raise RuntimeError("Offline verification forbids device processes and networking")
    if event == "open":
        filename, mode, flags = args
        if isinstance(filename, (str, bytes)):
            p = Path(os.fsdecode(filename)).resolve()
            write = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if write and p.is_relative_to(ROOT):
                raise RuntimeError("Offline verification does not write project data")
            if "生理資料" in p.parts and not p.is_relative_to(ROOT):
                raise RuntimeError("Offline verification forbids external physiological data")
    if event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename"}:
        p = Path(os.fsdecode(args[0])).resolve()
        if p.is_relative_to(ROOT):
            raise RuntimeError("Offline verification does not mutate project files")

def main():
    errors = []
    syntax_count = 0
    xml_count = 0
    for file in sorted(ROOT.rglob("*.py")):
        ast.parse(file.read_text(encoding="utf-8"), filename=str(file.relative_to(ROOT)))
        syntax_count += 1
    for file in sorted(ROOT.rglob("*.xml")):
        ET.parse(file)
        xml_count += 1
    sys.path.insert(0, str(BRIDGE))
    sys.addaudithook(guard)
    loader = unittest.TestLoader()
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
        suite = loader.loadTestsFromNames(MODULES)
        result = unittest.TextTestRunner(stream=capture, verbosity=2).run(suite)
    for test, _ in result.errors:
        errors.append({"test": test.id(), "kind": "error"})
    for test, _ in result.failures:
        errors.append({"test": test.id(), "kind": "failure"})
    print(json.dumps({"python_syntax_files": syntax_count, "xml_files": xml_count,
                      "tests_run": result.testsRun, "success": result.wasSuccessful(),
                      "errors": errors, "device_processes": "blocked",
                      "network": "blocked", "project_writes": "blocked"}, ensure_ascii=False, indent=2))
    if not result.wasSuccessful():
        print(capture.getvalue(), file=sys.stderr)
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    raise SystemExit(main())
