import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DemoTests(unittest.TestCase):
    def test_demo_runs_and_tells_the_whole_story(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "examples", "demo.py")],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        for expected in (
            "ApprovalMismatch",
            "verify: ok=True",
            "verify after edit: ok=False",
            "sheet identical to before the run: True",
            "offending columns: ['Areas']",
            "rows before/after diagnosis: 1/1",
            "orphan audit since diagnosis started: []",
        ):
            self.assertIn(expected, out)


if __name__ == "__main__":
    unittest.main()
