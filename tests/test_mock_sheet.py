import unittest

from helpers import FakeClock, make_sheet
from agent_tool_safety.sheets.mock import (
    GENERIC_ERROR,
    ColumnSpec,
    ColumnType,
    MockSheetService,
    NotFoundError,
    NotSupportedError,
    ValidationError,
)


class MockSheetTests(unittest.TestCase):
    def setUp(self):
        self.service, self.sheet, self.seed = make_sheet()

    def assertRejected(self, cells):
        with self.assertRaises(ValidationError) as ctx:
            self.service.add_row(self.sheet, cells)
        return str(ctx.exception)

    def test_disguised_column_reports_text_but_lists_options(self):
        areas = {c["title"]: c for c in self.service.get_columns(self.sheet)}["Areas"]
        self.assertEqual(areas["type"], "TEXT")
        self.assertEqual(areas["options"], ["Design", "Engineering", "QA"])

    def test_disguised_column_accepts_a_list_and_rejects_a_plain_string(self):
        self.service.add_row(self.sheet, {"Areas": ["Design"]})
        self.assertRejected({"Areas": "Design"})

    def test_disguised_column_rejects_unknown_options(self):
        self.assertRejected({"Areas": ["Design", "Marketing"]})

    def test_reads_show_the_multi_select_as_one_joined_string(self):
        self.assertEqual(self.service.get_rows(self.sheet)[0]["cells"]["Areas"], "Design, QA")

    def test_errors_are_generic_and_never_name_the_column(self):
        titles = [c["title"] for c in self.service.get_columns(self.sheet)]
        for cells in ({"Hours": "six"}, {"Status": "Blocked"}, {"Owner": "x@example.com"}, {"Nope": 1}):
            message = self.assertRejected(cells)
            self.assertEqual(message, GENERIC_ERROR)
            self.assertFalse(any(t in message for t in titles))

    def test_formula_columns_reject_direct_writes(self):
        self.assertRejected({"Summary": "anything"})
        self.assertRejected({"Summary": None})

    def test_number_column_rejects_text_and_bool(self):
        self.assertRejected({"Hours": "6"})
        self.assertRejected({"Hours": True})

    def test_contact_column_only_accepts_workspace_members(self):
        self.service.add_row(self.sheet, {"Owner": "ana@example.com"})
        self.assertRejected({"Owner": "stranger@example.com"})

    def test_blank_is_always_accepted_except_for_formulas(self):
        row = self.service.add_row(self.sheet, {"Hours": None, "Owner": None, "Areas": None})
        self.assertIsNone(row["cells"]["Owner"])

    def test_rejected_write_leaves_no_row_behind(self):
        count = len(self.service.get_rows(self.sheet))
        self.assertRejected({"Task": "ok", "Hours": "bad"})
        self.assertEqual(len(self.service.get_rows(self.sheet)), count)

    def test_rejected_update_changes_nothing(self):
        before = self.service.get_rows(self.sheet)
        with self.assertRaises(ValidationError):
            self.service.update_row(self.sheet, self.seed, {"Task": "changed", "Hours": "bad"})
        self.assertEqual(self.service.get_rows(self.sheet), before)

    def test_formula_is_evaluated_on_read(self):
        row = self.service.add_row(self.sheet, {"Task": "Review draft", "Status": "Done"})
        self.assertEqual(row["cells"]["Summary"], "Review draft [Done]")

    def test_copy_within_sheet_is_not_supported(self):
        with self.assertRaises(NotSupportedError):
            self.service.copy_row(self.sheet, self.seed)

    def test_missing_row_and_sheet_raise_not_found(self):
        with self.assertRaises(NotFoundError):
            self.service.update_row(self.sheet, 999, {})
        with self.assertRaises(NotFoundError):
            self.service.delete_row(self.sheet, 999)
        with self.assertRaises(NotFoundError):
            self.service.get_rows(42)

    def test_rows_are_stamped_with_creation_time(self):
        service = MockSheetService(clock=FakeClock(50.0))
        sheet = service.create_sheet("s", [ColumnSpec("Task", ColumnType.TEXT)])
        first, second = service.add_row(sheet, {}), service.add_row(sheet, {})
        self.assertLess(first["created_at"], second["created_at"])

    def test_returned_rows_are_copies(self):
        self.service.get_rows(self.sheet)[0]["cells"]["Task"] = "mutated"
        self.assertEqual(self.service.get_rows(self.sheet)[0]["cells"]["Task"], "Draft test plan")


if __name__ == "__main__":
    unittest.main()
