"""Tests for ../size_check.py: python -I test/size_check_test.py (from station/panel). Builds a partition table and
program files of known sizes, and runs the script as build.sh does."""
import os
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "size_check.py")
SLOT = 0x140000  # the default table's app0


def entry(ptype, subtype, offset, size, name):
    return struct.pack("<HBBII16sI", 0x50AA, ptype, subtype, offset, size, name, 0)


TABLE = (entry(1, 2, 0x9000, 0x5000, b"nvs") + entry(1, 0, 0xE000, 0x2000, b"otadata")
         + entry(0, 0x10, 0x10000, SLOT, b"app0") + entry(0, 0x11, 0x150000, SLOT, b"app1")
         + b"\xff" * 32)


def run(size, percent=None, table=TABLE):
    with tempfile.TemporaryDirectory() as d:
        fw, pt = os.path.join(d, "firmware.bin"), os.path.join(d, "partitions.bin")
        with open(fw, "wb") as f:
            f.write(b"\0" * size)
        with open(pt, "wb") as f:
            f.write(table)
        env = dict(os.environ)
        env.pop("FK_PANEL_MAX_PERCENT", None)
        if percent is not None:
            env["FK_PANEL_MAX_PERCENT"] = str(percent)
        return subprocess.run([sys.executable, "-I", SCRIPT, fw, pt], capture_output=True, text=True, env=env)


class SizeCheck(unittest.TestCase):
    def test_under_the_default_limit_passes_and_prints_the_percentage(self):
        r = run(1120312)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1120312 of 1310720 bytes", r.stdout)
        self.assertIn("85.5%", r.stdout)

    def test_over_95_percent_fails(self):
        r = run(int(SLOT * 0.951))
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stderr)
        self.assertIn("95.1%", r.stderr)

    def test_exactly_at_the_limit_passes(self):
        self.assertEqual(run(int(SLOT * 0.95)).returncode, 0)

    def test_lowered_limit_fails_the_same_program(self):
        r = run(1120312, percent=80)
        self.assertEqual(r.returncode, 1)
        self.assertIn("limit 80.0%", r.stderr)

    def test_raised_limit_passes_a_large_program(self):
        self.assertEqual(run(int(SLOT * 0.97), percent=98).returncode, 0)

    def test_slot_size_comes_from_the_table(self):
        small = entry(0, 0x10, 0x10000, 0x100000, b"app0") + b"\xff" * 32
        r = run(1000000, table=small)  # 1,000,000 of 1,048,576 = 95.4%
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_a_table_without_an_app_is_an_error(self):
        self.assertEqual(run(10, table=entry(1, 2, 0x9000, 0x5000, b"nvs") + b"\xff" * 32).returncode, 2)


if __name__ == "__main__":
    unittest.main()
