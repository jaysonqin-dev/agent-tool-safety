import random
import unittest

from helpers import APPROVED, make_sheet
from agent_tool_safety.sheets.classify import Action, build_payload, classify, format_table
from agent_tool_safety.sheets.mock import ColumnSpec, ColumnType, MockSheetService


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.service, self.sheet, self.seed = make_sheet()
        self.columns = self.service.get_columns(self.sheet)
        self.rows = self.service.get_rows(self.sheet)
        self.decisions = {d.column: d for d in classify(self.columns, self.rows[0]["cells"], APPROVED, self.rows)}

    def test_every_column_gets_exactly_one_decision_in_order(self):
        decisions = classify(self.columns, self.rows[0]["cells"], APPROVED, self.rows)
        self.assertEqual([d.column for d in decisions], [c["title"] for c in self.columns])

    def test_formula_column_is_skipped(self):
        self.assertEqual(self.decisions["Summary"].action, Action.SKIP)
        self.assertNotIn("Summary", build_payload(self.decisions.values()))

    def test_contact_outside_the_allow_list_is_left_blank_and_flagged(self):
        d = self.decisions["Owner"]  # farid is a workspace member but not approved
        self.assertEqual((d.action, d.flagged), (Action.BLANK, True))
        self.assertNotIn("Owner", build_payload(self.decisions.values()))

    def test_contact_on_the_allow_list_is_copied(self):
        source = dict(self.rows[0]["cells"], Owner="ana@example.com")
        d = {x.column: x for x in classify(self.columns, source, APPROVED, self.rows)}["Owner"]
        self.assertEqual((d.action, d.value), (Action.COPY, "ana@example.com"))

    def test_empty_contact_is_blank_but_not_flagged(self):
        source = dict(self.rows[0]["cells"], Owner=None)
        d = {x.column: x for x in classify(self.columns, source, APPROVED, self.rows)}["Owner"]
        self.assertEqual((d.action, d.flagged), (Action.BLANK, False))

    def test_name_only_text_in_a_person_column_needs_a_human(self):
        d = self.decisions["Reviewer"]
        self.assertEqual((d.action, d.flagged), (Action.NEEDS_HUMAN, True))
        self.assertNotIn("Reviewer", build_payload(self.decisions.values()))

    def test_ordinary_text_is_copied(self):
        self.assertEqual(self.decisions["Task"].value, "Draft test plan")

    def test_a_name_in_a_non_person_column_is_ordinary_text(self):
        columns = [{"title": "Notes", "type": "TEXT"}]
        d = classify(columns, {"Notes": "Priya Shah"}, APPROVED)[0]
        self.assertEqual(d.action, Action.COPY)

    def test_disguised_multi_select_is_detected_and_written_as_a_list(self):
        d = self.decisions["Areas"]
        self.assertEqual((d.action, d.value), (Action.COPY, ["Design", "QA"]))
        self.assertIn("reported as TEXT", d.kind)

    def test_text_with_options_but_mismatched_values_needs_a_human(self):
        columns = [{"title": "Tags", "type": "TEXT", "options": ["A", "B"]}]
        d = classify(columns, {"Tags": "free text here"}, APPROVED)[0]
        self.assertEqual(d.action, Action.NEEDS_HUMAN)

    def test_existing_rows_with_mismatched_values_also_block_the_guess(self):
        columns = [{"title": "Tags", "type": "TEXT", "options": ["A", "B"]}]
        existing = [{"cells": {"Tags": "something else"}}]
        d = classify(columns, {"Tags": "A, B"}, APPROVED, existing)[0]
        self.assertEqual(d.action, Action.NEEDS_HUMAN)

    def test_plain_text_without_options_is_never_treated_as_multi_select(self):
        d = classify([{"title": "Notes", "type": "TEXT"}], {"Notes": "A, B"}, APPROVED)[0]
        self.assertEqual((d.action, d.value), (Action.COPY, "A, B"))

    def test_picklist_value_outside_options_needs_a_human(self):
        source = dict(self.rows[0]["cells"], Status="Blocked")
        d = {x.column: x for x in classify(self.columns, source, APPROVED, self.rows)}["Status"]
        self.assertEqual(d.action, Action.NEEDS_HUMAN)

    def test_non_numeric_number_needs_a_human(self):
        d = classify([{"title": "Hours", "type": "NUMBER"}], {"Hours": "six"}, APPROVED)[0]
        self.assertEqual(d.action, Action.NEEDS_HUMAN)

    def test_unknown_column_type_needs_a_human(self):
        d = classify([{"title": "X", "type": "GANTT"}], {"X": 1}, APPROVED)[0]
        self.assertEqual(d.action, Action.NEEDS_HUMAN)

    def test_payload_contains_only_copy_decisions(self):
        payload = build_payload(self.decisions.values())
        self.assertEqual(set(payload), {n for n, d in self.decisions.items() if d.action is Action.COPY})

    def test_payload_from_the_fixture_is_accepted_by_the_service(self):
        self.service.add_row(self.sheet, build_payload(self.decisions.values()))

    def test_table_mentions_every_column_and_marks_flags(self):
        table = format_table(list(self.decisions.values()))
        for title in self.decisions:
            self.assertIn(title, table)
        self.assertIn("blank !", table)


WORDS = ["Design", "Engineering", "QA", "Ops", "Docs", "Legal"]
POOL = ["a@example.com", "b@example.com", "c@example.com", "d@example.com"]


def random_schema(rng):
    specs = []
    for i in range(rng.randint(1, 9)):
        kind = rng.choice(["text", "person", "number", "picklist", "multi", "disguised", "contact", "formula"])
        options = tuple(rng.sample(WORDS, rng.randint(1, 4)))
        title = "Owner %d" % i if kind == "person" else "Col %d" % i
        if kind in ("text", "person"):
            specs.append(ColumnSpec(title, ColumnType.TEXT))
        elif kind == "number":
            specs.append(ColumnSpec(title, ColumnType.NUMBER))
        elif kind == "picklist":
            specs.append(ColumnSpec(title, ColumnType.PICKLIST, options=options))
        elif kind in ("multi", "disguised"):
            specs.append(ColumnSpec(title, ColumnType.MULTI_PICKLIST, options=options, disguised=kind == "disguised"))
        elif kind == "contact":
            specs.append(ColumnSpec(title, ColumnType.CONTACT_LIST, contacts=tuple(POOL)))
        else:
            specs.append(ColumnSpec(title, ColumnType.FORMULA, formula=lambda cells: "x"))
    return specs


def random_value(rng, spec):
    junk = [None, "", 5, True, "zzz", "Priya Shah", ["Bogus"], "stranger@example.com", "#REF"]
    valid = {
        ColumnType.TEXT: ["Alpha", "Priya Shah"],
        ColumnType.NUMBER: [3, 2.5],
        ColumnType.PICKLIST: list(spec.options),
        ColumnType.MULTI_PICKLIST: [
            rng.sample(spec.options, rng.randint(0, len(spec.options))) if rng.random() < 0.5 else ", ".join(spec.options[:2])
        ],
        ColumnType.CONTACT_LIST: POOL,
        ColumnType.FORMULA: ["whatever"],
    }[spec.type]
    return rng.choice(valid + junk) if rng.random() < 0.6 else rng.choice(valid)


class ClassifyPropertyTests(unittest.TestCase):
    def test_random_schemas_never_produce_a_formula_write_or_a_rejected_payload(self):
        rng = random.Random(20261031)
        for trial in range(300):
            specs = random_schema(rng)
            service = MockSheetService()
            sheet = service.create_sheet("s", specs)
            columns = service.get_columns(sheet)
            source = {s.title: random_value(rng, s) for s in specs}
            existing = [{"cells": {s.title: random_value(rng, s) for s in specs}} for _ in range(rng.randint(0, 2))]
            approved = frozenset(rng.sample(POOL, rng.randint(0, len(POOL))))

            decisions = classify(columns, source, approved, existing)
            payload = build_payload(decisions)

            formulas = {s.title for s in specs if s.type is ColumnType.FORMULA}
            context = "trial %d" % trial
            self.assertEqual(len(decisions), len(specs), context)
            self.assertFalse(formulas & set(payload), context)
            self.assertTrue(all(d.action is Action.SKIP for d in decisions if d.column in formulas), context)
            self.assertTrue(all(v in approved for v in payload.values() if v in POOL), context)
            service.add_row(sheet, payload)  # raises ValidationError if the payload is not acceptable


if __name__ == "__main__":
    unittest.main()
