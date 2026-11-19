"""
Tests for seatalk_doctor.py's contract checks. Self-contained (temp files only).

Run:  python3 -m unittest scripts/test_seatalk_doctor.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seatalk_doctor as doc  # noqa: E402

READER = textwrap.dedent('''
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--last-hours", type=int)
    p.add_argument("--watch-groups", default="")
    p.parse_args()
''')

CALLER_TEMPLATE = textwrap.dedent('''
    import subprocess, sys
    def read_messages(hours):
        return subprocess.run(
            [sys.executable, "reader.py", "--last-hours", str(hours), {extra}],
            capture_output=True)
    def push_to_redis():
        cmd = ["--not-a-reader-flag"]
''')


class DoctorContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reader = self._write("reader.py", READER)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name: str, text: str) -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_reader_flags_supported_parses_help(self):
        flags = doc.reader_flags_supported(sys.executable, self.reader)
        self.assertTrue({"--last-hours", "--watch-groups"} <= flags)

    def test_flags_used_only_scans_read_messages(self):
        caller = self._write("caller.py", CALLER_TEMPLATE.format(extra='"--watch-groups", "x"'))
        self.assertEqual(doc.reader_flags_used(caller), {"--last-hours", "--watch-groups"})

    def test_detects_the_nov_2026_output_flag_bug(self):
        # The stale ~/.goog-assistant caller passed --output, which the reader never had.
        caller = self._write("caller.py", CALLER_TEMPLATE.format(extra='"--output", "/tmp/x.json"'))
        used = doc.reader_flags_used(caller)
        supported = doc.reader_flags_supported(sys.executable, self.reader)
        self.assertEqual(used - supported, {"--output"})

    def test_current_repo_callers_match_current_reader(self):
        repo_scripts = os.path.dirname(os.path.abspath(__file__))
        reader = os.path.expanduser(
            "~/My Projects/Working Efficiency/use-seatalk/scripts/redux_related_messages.py")
        if not os.path.exists(reader):
            self.skipTest("use-seatalk not present on this machine")
        supported = doc.reader_flags_supported("/usr/local/bin/python3.11", reader)
        if supported is None:
            self.skipTest("python3.11 cannot run the reader here")
        for name in ("seatalk_snapshot.py", "seatalk_summary.py"):
            used = doc.reader_flags_used(os.path.join(repo_scripts, name))
            self.assertTrue(used, f"{name}: no reader flags found")
            self.assertEqual(used - supported, set(), name)

    def test_dispatch_python_reads_default(self):
        sh = self._write("d.sh", 'PYTHON="${GOOG_PYTHON:-/usr/local/bin/python3.11}"\n')
        self.assertEqual(doc.dispatch_python(sh), "/usr/local/bin/python3.11")
        self.assertIsNone(doc.dispatch_python(self._write("e.sh", "exec /usr/bin/python3 x\n")))


if __name__ == "__main__":
    unittest.main()
