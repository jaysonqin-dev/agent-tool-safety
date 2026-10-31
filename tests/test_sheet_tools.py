import os
import tempfile
import unittest

from helpers import make_sheet, sheet_executor
from agent_tool_safety import Plan, ToolCall
from agent_tool_safety.policy import ApprovalRequired, DryRunRequired
from agent_tool_safety.sheets.tools import SHEET_TOOLS


class SheetToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.service, self.sheet, self.seed = make_sheet()
        self.executor = sheet_executor(self.service, os.path.join(self._tmp.name, "audit.jsonl"))

    def approved_run(self, *calls):
        plan = Plan(tuple(calls))
        self.assertTrue(self.executor.dry_run(plan).ok)
        return self.executor.execute(plan, self.executor.approve(plan, "reviewer"))

    def update(self, cells):
        return ToolCall("sheet.update_row", {"sheet_id": self.sheet, "row_id": self.seed, "cells": cells})

    def test_tools_are_registered_with_their_risk_tiers(self):
        risks = {d["name"]: d["risk"] for d in SHEET_TOOLS.describe()}
        self.assertEqual(
            risks,
            {
                "sheet.get_rows": "read",
                "sheet.add_row": "write_reversible",
                "sheet.update_row": "write_reversible",
                "sheet.delete_row": "write_irreversible",
            },
        )

    def test_reading_needs_no_approval(self):
        result = self.executor.execute(Plan((ToolCall("sheet.get_rows", {"sheet_id": self.sheet}),)))
        self.assertEqual(len(result.results[0]["rows"]), 1)

    def test_update_dry_run_shows_old_and_new_values(self):
        report = self.executor.dry_run(Plan((self.update({"Hours": 8}),)))
        change = report.previews[0].change
        self.assertEqual((change.before, change.after), ({"Hours": 6}, {"Hours": 8}))

    def test_update_undo_restores_a_disguised_multi_select_exactly(self):
        before = self.service.get_rows(self.sheet)
        run = self.approved_run(self.update({"Areas": ["Engineering"], "Status": "Done", "Hours": None}))
        self.assertNotEqual(self.service.get_rows(self.sheet), before)
        self.executor.undo(run.run_id)
        self.assertEqual(self.service.get_rows(self.sheet), before)

    def test_invalid_cell_is_caught_by_the_dry_run_and_nothing_is_written(self):
        before = self.service.get_rows(self.sheet)
        plan = Plan((self.update({"Hours": 9}), self.update({"Summary": "typed by hand"})))
        report = self.executor.dry_run(plan)
        self.assertFalse(report.ok)
        self.assertIsNotNone(report.previews[1].error)
        with self.assertRaises(DryRunRequired):
            self.executor.execute(plan)
        self.assertEqual(self.service.get_rows(self.sheet), before)

    def test_update_of_a_missing_row_fails_the_dry_run(self):
        call = ToolCall("sheet.update_row", {"sheet_id": self.sheet, "row_id": 999, "cells": {"Hours": 1}})
        self.assertFalse(self.executor.dry_run(Plan((call,))).ok)

    def test_delete_needs_approval_and_cannot_be_undone(self):
        call = ToolCall("sheet.delete_row", {"sheet_id": self.sheet, "row_id": self.seed})
        plan = Plan((call,))
        self.executor.dry_run(plan)
        with self.assertRaises(ApprovalRequired):
            self.executor.execute(plan)
        run = self.approved_run(call)
        report = self.executor.undo(run.run_id)
        self.assertEqual((report.undone, report.irreversible), ([], [0]))
        self.assertEqual(self.service.get_rows(self.sheet), [])

    def test_sheet_changing_after_approval_fails_mid_run_and_undo_restores_it(self):
        add = ToolCall("sheet.add_row", {"sheet_id": self.sheet, "cells": {"Task": "Review draft"}})
        plan = Plan((add, self.update({"Hours": 1})))
        self.executor.dry_run(plan)
        token = self.executor.approve(plan, "reviewer")
        self.service.delete_row(self.sheet, self.seed)  # the world changes between approval and execution
        run = self.executor.execute(plan, token)
        self.assertEqual(run.status, "failed")
        self.assertEqual(len(self.service.get_rows(self.sheet)), 1)  # the add went through
        self.executor.undo(run.run_id)
        self.assertEqual(self.service.get_rows(self.sheet), [])


if __name__ == "__main__":
    unittest.main()
