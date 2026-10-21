import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logpan


class TestLogpan(unittest.TestCase):
    def test_normal_match(self):
        lines = [
            "2026-10-13 10:00:01 INFO service started",
            "2026-10-13 10:00:02 DEBUG cache warm",
            "2026-10-13 10:00:03 ERROR disk full",
            "2026-10-13 10:00:04 INFO request ok",
        ]
        result = logpan.scan_lines(lines, "ERROR")
        self.assertEqual(len(result), 1)
        self.assertIn("disk full", result[0])

    def test_no_match(self):
        lines = [
            "2026-10-13 10:00:01 INFO service started",
            "2026-10-13 10:00:02 DEBUG cache warm",
        ]
        result = logpan.scan_lines(lines, "FATAL")
        self.assertEqual(result, [])

    def test_regex_mode(self):
        lines = [
            "2026-10-13 10:00:01 INFO service started",
            "2026-10-13 10:00:02 ERROR disk full",
            "2026-10-13 10:00:03 ERROR net down",
            "2026-10-13 10:00:04 WARN slow",
        ]
        result = logpan.scan_lines(lines, r"ERROR|WARN", regex=True)
        self.assertEqual(len(result), 3)
        for line in result:
            self.assertTrue(re.search(r"ERROR|WARN", line))

    def test_regex_anchored(self):
        lines = [
            "INFO start",
            "ERROR boom",
            "prefix ERROR boom",
        ]
        result = logpan.scan_lines(lines, r"^ERROR", regex=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "ERROR boom")

    def test_invalid_regex_raises(self):
        lines = ["INFO start"]
        with self.assertRaises(re.error):
            logpan.scan_lines(lines, r"([unclosed", regex=True)

    def test_empty_pattern_raises(self):
        lines = ["INFO start"]
        with self.assertRaises(ValueError):
            logpan.scan_lines(lines, "")

    def test_empty_lines_returns_empty(self):
        result = logpan.scan_lines([], "ERROR")
        self.assertEqual(result, [])

    def test_case_sensitive_by_default(self):
        lines = ["error lower", "ERROR upper"]
        result = logpan.scan_lines(lines, "ERROR")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "ERROR upper")

    def test_scan_file(self):
        import tempfile

        content = (
            "2026-10-13 10:00:01 INFO service started\n"
            "2026-10-13 10:00:02 ERROR disk full\n"
            "2026-10-13 10:00:03 INFO request ok\n"
        )
        fd, path = tempfile.mkstemp()
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            result = logpan.scan_file(path, "ERROR")
            self.assertEqual(len(result), 1)
            self.assertIn("disk full", result[0])
        finally:
            os.remove(path)

    def test_scan_file_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            logpan.scan_file("/nonexistent/path/does/not/exist.log", "ERROR")


if __name__ == "__main__":
    unittest.main()