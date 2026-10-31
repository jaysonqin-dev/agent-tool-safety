import os
import tempfile
import unittest

from helpers import FakeClock, make_sheet, task_columns
from agent_tool_safety import AuditLog
from agent_tool_safety.sheets.diagnose import diagnose_payload, orphan_audit
from agent_tool_safety.sheets.mock import ColumnSpec, ColumnType, MockSheetService


class SpyService(MockSheetService):
    """Records writes and can be told to fail the Nth update."""

    def __init__(self, fail_on_update=None):
        super().__init__(clock=FakeClock())
        self.adds = 0
        self.updates = 0
        self.fail_on_update = fail_on_update

    def add_row(self, sheet_id, cells):
        self.adds += 1
        return super().add_row(sheet_id, cells)

    def update_row(self, sheet_id, row_id, cells):
        self.updates += 1
        if self.updates == self.fail_on_update:
            raise RuntimeError("network down")
        return super().update_row(sheet_id, row_id, cells)


def spy_sheet(fail_on_update=None):
    service = SpyService(fail_on_update)
    sheet = service.create_sheet("Release Tasks", task_columns())
    return service, sheet


GOOD = {"Task": "Draft test plan", "Status": "In Progress", "Hours": 6}


class DiagnoseTests(unittest.TestCase):
    def test_finds_the_disguised_column(self):
        service, sheet = spy_sheet()
        finding = diagnose_payload(service, sheet, dict(GOOD, Areas="Design, QA"))
        self.assertEqual(finding.offending, ["Areas"])

    def test_finds_every_offending_column(self):
        service, sheet = spy_sheet()
        finding = diagnose_payload(service, sheet, dict(GOOD, Areas="Design", Hours="six", Owner="x@example.com"))
        self.assertEqual(sorted(finding.offending), ["Areas", "Hours", "Owner"])

    def test_clean_payload_reports_nothing_offending(self):
        service, sheet = spy_sheet()
        finding = diagnose_payload(service, sheet, dict(GOOD, Areas=["QA"]))
        self.assertEqual(finding.offending, [])
        self.assertIn("accepted", finding.note)

    def test_uses_exactly_one_test_row_and_removes_it(self):
        service, sheet = spy_sheet()
        before = service.get_rows(sheet)
        diagnose_payload(service, sheet, dict(GOOD, Areas="Design, QA"))
        self.assertEqual(service.adds, 1)
        self.assertEqual(service.get_rows(sheet), before)

    def test_bisects_with_updates_not_inserts(self):
        service, sheet = spy_sheet()
        diagnose_payload(service, sheet, dict(GOOD, Areas="Design"))
        self.assertEqual(service.adds, 1)
        self.assertGreater(service.updates, 1)

    def test_probe_count_is_logarithmic_for_one_bad_column(self):
        service = MockSheetService()
        sheet = service.create_sheet("wide", [ColumnSpec("n%d" % i, ColumnType.NUMBER) for i in range(16)])
        payload = {"n%d" % i: i for i in range(16)}
        payload["n11"] = "bad"
        finding = diagnose_payload(service, sheet, payload)
        self.assertEqual(finding.offending, ["n11"])
        self.assertLessEqual(finding.probes, 9)

    def test_cleans_up_when_the_bisection_raises_part_way(self):
        for failing_probe in (1, 2, 3, 4):
            service, sheet = spy_sheet(fail_on_update=failing_probe)
            with self.assertRaises(RuntimeError):
                diagnose_payload(service, sheet, dict(GOOD, Areas="Design", Hours="six"))
            self.assertEqual(len(service.get_rows(sheet)), 0, "probe %d left a row" % failing_probe)

    def test_a_failed_test_row_creation_leaves_nothing_to_clean(self):
        service, sheet = spy_sheet()
        service.add_row = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
        with self.assertRaises(RuntimeError):
            diagnose_payload(service, sheet, GOOD)
        self.assertEqual(len(service.get_rows(sheet)), 0)

    def test_audit_records_creation_and_deletion_of_the_test_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLog(os.path.join(tmp, "audit.jsonl"))
            service, sheet = spy_sheet()
            diagnose_payload(service, sheet, dict(GOOD, Areas="Design"), audit)
            events = [e["event"] for e in audit.entries()]
            self.assertEqual(events, ["diagnose_test_row_created", "diagnose_test_row_deleted"])
            self.assertTrue(audit.verify().ok)


class OrphanAuditTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.service, self.sheet, self.seed = make_sheet(self.clock)
        self.since = self.clock.now + 0.5

    def add(self, cells):
        return self.service.add_row(self.sheet, cells)["id"]

    def suspects(self):
        return {s.row_id: s.reasons for s in orphan_audit(self.service, self.sheet, self.since)}

    def test_healthy_rows_are_not_flagged(self):
        self.add({"Task": "Write release notes", "Status": "Done", "Owner": "ana@example.com", "Hours": 2})
        self.assertEqual(self.suspects(), {})

    def test_mostly_blank_row_is_flagged(self):
        row = self.add({"Task": "x"})
        self.assertEqual(self.suspects(), {row: ["mostly blank"]})

    def test_empty_row_is_flagged(self):
        row = self.add({})
        self.assertIn("mostly blank", self.suspects()[row])

    def test_duplicate_of_an_older_row_is_flagged_but_the_original_is_not(self):
        copy_id = self.add(dict({"Task": "Draft test plan", "Status": "In Progress", "Owner": "farid@example.com",
                                 "Reviewer": "Priya Shah", "Hours": 6, "Areas": ["Design", "QA"]}))
        self.assertEqual(self.suspects(), {copy_id: ["duplicate of an earlier row"]})

    def test_error_valued_formula_is_flagged(self):
        row = self.add({"Status": "Done", "Owner": "ana@example.com", "Reviewer": "Review team", "Hours": 3, "Areas": ["QA"]})
        self.assertEqual(self.suspects(), {row: ["formula output is an error value"]})

    def test_rows_created_before_the_window_are_ignored(self):
        self.service.add_row(self.sheet, {})
        self.since = self.clock.now + 0.5
        self.assertEqual(self.suspects(), {})

    def test_audit_only_reports_and_never_deletes(self):
        self.add({})
        count = len(self.service.get_rows(self.sheet))
        self.suspects()
        self.assertEqual(len(self.service.get_rows(self.sheet)), count)

    def test_diagnosis_leaves_zero_orphans(self):
        diagnose_payload(self.service, self.sheet, dict(GOOD, Areas="Design, QA"))
        self.assertEqual(self.suspects(), {})


if __name__ == "__main__":
    unittest.main()
