import copy
import os
import tempfile
import unittest

from helpers import KV_TOOLS
from agent_tool_safety import AuditLog, Executor, Plan, Policy, RiskLevel, ToolCall
from agent_tool_safety.executor import AuditIntegrityError, UnknownRun
from agent_tool_safety.policy import ApprovalMismatch, ApprovalRequired, DryRunRequired
from agent_tool_safety.risk import Outcome, ToolDefinitionError, ToolRegistry, tool


def set_call(key, value):
    return ToolCall("kv.set", {"key": key, "value": value})


class ExecutorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "audit.jsonl")
        self.store = {"a": 1, "b": 2}
        self.audit = AuditLog(self.path)
        self.executor = Executor(self.store, KV_TOOLS, Policy(), self.audit)

    def run_approved(self, plan):
        self.assertTrue(self.executor.dry_run(plan).ok)
        return self.executor.execute(plan, self.executor.approve(plan, "reviewer"))

    def events(self, run_id=None):
        return [e["event"] for e in self.audit.entries() if run_id is None or e["run_id"] == run_id]


class DryRunTests(ExecutorTestCase):
    def test_dry_run_has_no_side_effects(self):
        before = copy.deepcopy(self.store)
        self.executor.dry_run(Plan((set_call("a", 10), set_call("c", 3))))
        self.assertEqual(self.store, before)

    def test_dry_run_reports_before_and_after(self):
        report = self.executor.dry_run(Plan((set_call("a", 10),)))
        change = report.previews[0].change
        self.assertEqual((change.before, change.after), ({"value": 1}, {"value": 10}))
        self.assertIn("before", report.render())

    def test_dry_run_flags_a_call_that_would_fail(self):
        failing = ToolRegistry()

        @tool(failing, "w", RiskLevel.WRITE_IRREVERSIBLE)
        def w(ctx, args):
            return Outcome({})

        @w.preview
        def _preview(ctx, args):
            raise ValueError("would be rejected")

        executor = Executor({}, failing, Policy(), self.audit)
        report = executor.dry_run(Plan((ToolCall("w"),)))
        self.assertFalse(report.ok)
        self.assertIn("WOULD FAIL", report.render())

    def test_plan_that_failed_its_dry_run_cannot_execute(self):
        failing = ToolRegistry()

        @tool(failing, "w", RiskLevel.WRITE_IRREVERSIBLE)
        def w(ctx, args):
            return Outcome({})

        w.preview(lambda ctx, args: 1 / 0)
        executor = Executor({}, failing, Policy(), self.audit)
        plan = Plan((ToolCall("w"),))
        executor.dry_run(plan)
        with self.assertRaises(DryRunRequired):
            executor.execute(plan)

    def test_write_tool_without_preview_fails_closed(self):
        broken = ToolRegistry()
        tool(broken, "w", RiskLevel.WRITE_IRREVERSIBLE)(lambda ctx, args: Outcome({}))
        with self.assertRaises(ToolDefinitionError):
            Executor({}, broken, Policy(), self.audit).dry_run(Plan((ToolCall("w"),)))

    def test_dry_run_is_logged(self):
        self.executor.dry_run(Plan((set_call("a", 10),)))
        self.assertEqual(self.events(), ["dry_run"])


class ApprovalFlowTests(ExecutorTestCase):
    def test_cannot_approve_without_a_dry_run(self):
        with self.assertRaises(DryRunRequired):
            self.executor.approve(Plan((set_call("a", 10),)), "reviewer")

    def test_execute_without_dry_run_is_refused_and_logged(self):
        with self.assertRaises(DryRunRequired):
            self.executor.execute(Plan((set_call("a", 10),)))
        self.assertEqual(self.store["a"], 1)
        self.assertEqual(self.events(), ["execute_refused"])

    def test_execute_without_token_is_refused(self):
        plan = Plan((set_call("a", 10),))
        self.executor.dry_run(plan)
        with self.assertRaises(ApprovalRequired):
            self.executor.execute(plan)
        self.assertEqual(self.store["a"], 1)

    def test_plan_edited_after_approval_is_refused(self):
        plan = Plan((set_call("a", 10),))
        self.executor.dry_run(plan)
        token = self.executor.approve(plan, "reviewer")
        plan.calls[0].args["value"] = 99  # the swap an approval must not survive
        with self.assertRaises(DryRunRequired):
            self.executor.execute(plan, token)
        self.assertEqual(self.store["a"], 1)

    def test_token_for_plan_a_cannot_run_plan_b(self):
        plan_a, plan_b = Plan((set_call("a", 10),)), Plan((set_call("a", 99),))
        self.executor.dry_run(plan_a)
        self.executor.dry_run(plan_b)
        token = self.executor.approve(plan_a, "reviewer")
        with self.assertRaises(ApprovalMismatch):
            self.executor.execute(plan_b, token)
        self.assertEqual(self.store["a"], 1)
        self.assertIn("execute_refused", self.events())

    def test_one_dry_run_and_one_token_authorize_one_execution(self):
        plan = Plan((set_call("a", 10),))
        self.assertEqual(self.run_approved(plan).status, "ok")
        with self.assertRaises(DryRunRequired):
            self.executor.execute(plan)

    def test_read_only_plan_runs_without_ceremony(self):
        result = self.executor.execute(Plan((ToolCall("kv.get", {"key": "a"}),)))
        self.assertEqual(result.results, [{"value": 1}])


class ExecutionAndUndoTests(ExecutorTestCase):
    def test_execution_applies_and_logs_each_call_with_undo_data(self):
        run = self.run_approved(Plan((set_call("a", 10), set_call("c", 3))))
        self.assertEqual((run.status, self.store["a"], self.store["c"]), ("ok", 10, 3))
        applied = [e for e in self.audit.entries() if e["event"] == "call_applied"]
        self.assertEqual(len(applied), 2)
        self.assertEqual(applied[0]["data"]["undo"]["previous"], 1)
        self.assertEqual(self.events(run.run_id)[0], "run_started")

    def test_run_started_records_the_approver(self):
        run = self.run_approved(Plan((set_call("a", 10),)))
        started = [e for e in self.audit.entries() if e["event"] == "run_started"][0]
        self.assertEqual((started["run_id"], started["data"]["approver"]), (run.run_id, "reviewer"))

    def test_undo_restores_the_exact_prior_state(self):
        before = copy.deepcopy(self.store)
        run = self.run_approved(Plan((set_call("a", 10), set_call("c", 3), set_call("a", 11))))
        self.assertNotEqual(self.store, before)
        report = self.executor.undo(run.run_id)
        self.assertEqual(self.store, before)
        self.assertEqual(report.undone, [2, 1, 0])

    def test_undo_is_idempotent(self):
        run = self.run_approved(Plan((set_call("a", 10),)))
        self.executor.undo(run.run_id)
        self.assertEqual(self.executor.undo(run.run_id).undone, [])
        self.assertEqual(self.store, {"a": 1, "b": 2})

    def test_undo_works_from_the_log_alone(self):
        run = self.run_approved(Plan((set_call("a", 10), set_call("c", 3))))
        fresh = Executor(self.store, KV_TOOLS, Policy(), AuditLog(self.path))
        fresh.undo(run.run_id)
        self.assertEqual(self.store, {"a": 1, "b": 2})

    def test_undo_of_unknown_run_raises(self):
        with self.assertRaises(UnknownRun):
            self.executor.undo("run-404")

    def test_undo_refuses_a_log_that_fails_verification(self):
        run = self.run_approved(Plan((set_call("a", 10),)))
        with open(self.path, encoding="utf-8") as fh:
            text = fh.read()
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text.replace('"previous": 1', '"previous": 666'))
        with self.assertRaises(AuditIntegrityError):
            self.executor.undo(run.run_id)
        self.assertEqual(self.store["a"], 10)

    def test_irreversible_calls_are_reported_not_undone(self):
        run = self.run_approved(Plan((set_call("c", 3), ToolCall("kv.drop", {"key": "b"}))))
        report = self.executor.undo(run.run_id)
        self.assertEqual((report.undone, report.irreversible), ([0], [1]))
        self.assertNotIn("b", self.store)
        self.assertNotIn("c", self.store)

    def test_undo_interrupted_part_way_can_be_resumed(self):
        run = self.run_approved(Plan((set_call("a", 10), set_call("c", 3))))
        original = KV_TOOLS.get("kv.set").undo_fn
        calls = []

        def flaky(ctx, undo):
            calls.append(undo)
            if len(calls) == 2:
                raise RuntimeError("connection dropped")
            original(ctx, undo)

        KV_TOOLS.get("kv.set").undo_fn = flaky
        try:
            with self.assertRaises(RuntimeError):
                self.executor.undo(run.run_id)
        finally:
            KV_TOOLS.get("kv.set").undo_fn = original
        self.assertEqual(self.executor.undo(run.run_id).undone, [0])
        self.assertEqual(self.store, {"a": 1, "b": 2})


class PartialFailureTests(ExecutorTestCase):
    def failing_plan(self):
        return Plan((set_call("a", 10), set_call("c", 3), ToolCall("kv.explode"), set_call("d", 4)))

    def test_execution_stops_at_the_first_error(self):
        run = self.run_approved(self.failing_plan())
        self.assertEqual(run.status, "failed")
        self.assertIn("boom", run.error)
        self.assertNotIn("d", self.store)
        self.assertEqual(len(run.results), 2)

    def test_partial_run_leaves_a_log_that_still_restores_everything(self):
        run = self.run_approved(self.failing_plan())
        self.assertEqual(self.store["a"], 10)
        self.executor.undo(run.run_id)
        self.assertEqual(self.store, {"a": 1, "b": 2})

    def test_failure_is_recorded_in_the_audit_log(self):
        run = self.run_approved(self.failing_plan())
        self.assertEqual(
            self.events(run.run_id),
            ["run_started", "call_applied", "call_applied", "call_failed", "run_finished"],
        )
        self.assertTrue(self.audit.verify().ok)

    def test_the_failed_call_itself_is_not_undone(self):
        run = self.run_approved(self.failing_plan())
        self.assertEqual(self.executor.undo(run.run_id).undone, [1, 0])  # kv.explode's undo would raise


if __name__ == "__main__":
    unittest.main()
