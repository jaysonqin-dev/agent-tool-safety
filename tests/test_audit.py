import json
import os
import random
import tempfile
import unittest

from helpers import FakeClock
from agent_tool_safety import AuditLog
from agent_tool_safety.audit import GENESIS


class AuditTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "audit.jsonl")
        self.log = AuditLog(self.path, clock=FakeClock())

    def fill(self, n):
        for i in range(n):
            self.log.append("event", "run-1", {"i": i})

    def lines(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read().splitlines()

    def write_lines(self, lines):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def test_empty_log_verifies(self):
        result = self.log.verify()
        self.assertTrue(result.ok)
        self.assertEqual(result.entries, 0)

    def test_entries_chain_to_their_predecessor(self):
        self.fill(3)
        entries = self.log.entries()
        self.assertEqual(entries[0]["prev_hash"], GENESIS)
        self.assertEqual(entries[1]["prev_hash"], entries[0]["hash"])
        self.assertEqual(entries[2]["prev_hash"], entries[1]["hash"])
        self.assertEqual(self.log.head, entries[2]["hash"])

    def test_intact_log_verifies(self):
        self.fill(5)
        result = self.log.verify()
        self.assertTrue(result.ok)
        self.assertEqual(result.entries, 5)

    def test_editing_an_entry_is_detected(self):
        self.fill(4)
        lines = self.lines()
        entry = json.loads(lines[2])
        entry["data"]["i"] = 999
        lines[2] = json.dumps(entry, sort_keys=True)
        self.write_lines(lines)
        result = self.log.verify()
        self.assertFalse(result.ok)
        self.assertIn("entry 2", result.problem)

    def test_deleting_a_middle_entry_is_detected(self):
        self.fill(4)
        lines = self.lines()
        del lines[1]
        self.write_lines(lines)
        self.assertFalse(self.log.verify().ok)

    def test_deleting_the_first_entry_is_detected(self):
        self.fill(3)
        self.write_lines(self.lines()[1:])
        self.assertFalse(self.log.verify().ok)

    def test_reordering_entries_is_detected(self):
        self.fill(4)
        lines = self.lines()
        lines[1], lines[2] = lines[2], lines[1]
        self.write_lines(lines)
        self.assertFalse(self.log.verify().ok)

    def test_truncating_the_tail_is_only_caught_against_a_known_head(self):
        self.fill(4)
        head = self.log.head
        self.write_lines(self.lines()[:-1])
        self.assertTrue(self.log.verify().ok)
        result = self.log.verify(expected_head=head)
        self.assertFalse(result.ok)
        self.assertIn("head", result.problem)

    def test_expected_head_passes_on_an_intact_log(self):
        self.fill(2)
        self.assertTrue(self.log.verify(expected_head=self.log.head).ok)

    def test_garbage_line_is_detected(self):
        self.fill(2)
        self.write_lines(self.lines() + ["not json"])
        self.assertFalse(self.log.verify().ok)

    def test_reopening_continues_the_chain(self):
        self.fill(2)
        reopened = AuditLog(self.path, clock=FakeClock())
        reopened.append("event", None, {"i": 2})
        result = reopened.verify()
        self.assertTrue(result.ok)
        self.assertEqual(result.entries, 3)

    def test_entries_record_undo_information_verbatim(self):
        self.log.append("call_applied", "run-1", {"undo": {"row_id": 7, "previous": {"Hours": None}}})
        self.assertEqual(self.log.entries()[0]["data"]["undo"], {"row_id": 7, "previous": {"Hours": None}})

    def test_random_tampering_is_always_detected(self):
        rng = random.Random(20261031)
        for trial in range(40):
            if os.path.exists(self.path):
                os.remove(self.path)
            log = AuditLog(self.path, clock=FakeClock())
            for i in range(rng.randint(3, 8)):
                log.append("event", "run-1", {"i": i, "pad": rng.random()})
            lines = self.lines()
            kind = rng.choice(["edit", "delete", "swap"])
            if kind == "edit":
                k = rng.randrange(len(lines))
                entry = json.loads(lines[k])
                entry["data"]["i"] += 1
                lines[k] = json.dumps(entry, sort_keys=True)
            elif kind == "delete":
                del lines[rng.randrange(len(lines) - 1)]  # never the last: that needs a known head
            else:
                k = rng.randrange(len(lines) - 1)
                lines[k], lines[k + 1] = lines[k + 1], lines[k]
            self.write_lines(lines)
            self.assertFalse(log.verify().ok, "trial %d (%s) went undetected" % (trial, kind))


if __name__ == "__main__":
    unittest.main()
