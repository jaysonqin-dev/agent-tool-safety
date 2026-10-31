import os
import tempfile
import unittest

from helpers import APPROVED, make_sheet, sheet_executor
from agent_tool_safety import AuditLog
from agent_tool_safety.sheets.duplicate import duplicate_row, prepare_duplicate
from agent_tool_safety.sheets.mock import NotFoundError, ValidationError


class DuplicateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.service, self.sheet, self.seed = make_sheet()
        self.log_path = os.path.join(self._tmp.name, "audit.jsonl")
        self.executor = sheet_executor(self.service, self.log_path)

    def duplicate(self, approve):
        return duplicate_row(self.executor, self.service, self.sheet, self.seed, APPROVED, approve)

    def test_naive_copy_of_the_displayed_row_is_rejected(self):
        source = self.service.get_rows(self.sheet)[0]["cells"]
        with self.assertRaises(ValidationError):
            self.service.add_row(self.sheet, source)

    def test_prepared_plan_is_a_single_insert_without_formula_or_flagged_columns(self):
        dup = prepare_duplicate(self.service, self.sheet, self.seed, APPROVED)
        call = dup.plan.calls[0]
        self.assertEqual(call.tool, "sheet.add_row")
        self.assertEqual(set(call.args["cells"]), {"Task", "Status", "Hours", "Areas"})
        self.assertEqual(call.args["cells"]["Areas"], ["Design", "QA"])

    def test_duplicate_adds_exactly_one_correct_row_through_the_executor(self):
        outcome = self.duplicate(lambda dup, report: "reviewer")
        self.assertEqual(outcome.run.status, "ok")
        rows = self.service.get_rows(self.sheet)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["cells"]["Task"], "Draft test plan")
        self.assertEqual(rows[1]["cells"]["Areas"], "Design, QA")
        self.assertIsNone(rows[1]["cells"]["Owner"])
        self.assertTrue(AuditLog(self.log_path).verify().ok)

    def test_declined_approval_writes_nothing(self):
        outcome = self.duplicate(lambda dup, report: None)
        self.assertIsNone(outcome.run)
        self.assertEqual(len(self.service.get_rows(self.sheet)), 1)

    def test_approver_sees_the_decision_table_and_the_diff(self):
        seen = []
        self.duplicate(lambda dup, report: seen.append((dup, report)))
        dup, report = seen[0]
        self.assertTrue(any(d.flagged for d in dup.decisions))
        self.assertEqual(report.previews[0].change.op, "add_row")

    def test_duplicate_can_be_undone(self):
        before = self.service.get_rows(self.sheet)
        outcome = self.duplicate(lambda dup, report: "reviewer")
        self.executor.undo(outcome.run.run_id)
        self.assertEqual(self.service.get_rows(self.sheet), before)

    def test_unknown_source_row(self):
        with self.assertRaises(NotFoundError):
            prepare_duplicate(self.service, self.sheet, 999, APPROVED)


if __name__ == "__main__":
    unittest.main()
