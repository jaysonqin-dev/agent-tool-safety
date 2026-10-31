"""Scripted walkthrough: safe row duplication, approval, audit, tamper detection, undo, diagnosis.

Run from the repository root:  python3 examples/demo.py
"""
from __future__ import annotations

import itertools
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_tool_safety import AuditLog, Executor, Plan, Policy, ToolCall  # noqa: E402
from agent_tool_safety.policy import PolicyError  # noqa: E402
from agent_tool_safety.sheets.classify import format_table  # noqa: E402
from agent_tool_safety.sheets.diagnose import diagnose_payload, orphan_audit  # noqa: E402
from agent_tool_safety.sheets.duplicate import prepare_duplicate  # noqa: E402
from agent_tool_safety.sheets.mock import ColumnSpec, ColumnType, MockSheetService, ServiceError  # noqa: E402
from agent_tool_safety.sheets.tools import SHEET_TOOLS  # noqa: E402

CONTACTS = ("ana@example.com", "ben@example.com", "chloe@example.com", "farid@example.com")
APPROVED = frozenset(CONTACTS[:3])  # farid is a workspace member the agent is not cleared to assign


def section(title: str) -> None:
    print("\n== %s ==" % title)


def build_sheet(service: MockSheetService) -> int:
    def summary(cells):
        return "#INVALID" if not cells.get("Task") else "%s [%s]" % (cells["Task"], cells.get("Status") or "-")

    sheet_id = service.create_sheet(
        "Release Tasks",
        [
            ColumnSpec("Task", ColumnType.TEXT),
            ColumnSpec("Status", ColumnType.PICKLIST, options=("Not Started", "In Progress", "Done")),
            ColumnSpec("Owner", ColumnType.CONTACT_LIST, contacts=CONTACTS),
            ColumnSpec("Reviewer", ColumnType.TEXT),
            ColumnSpec("Hours", ColumnType.NUMBER),
            ColumnSpec("Areas", ColumnType.MULTI_PICKLIST, options=("Design", "Engineering", "QA"), disguised=True),
            ColumnSpec("Summary", ColumnType.FORMULA, formula=summary),
        ],
    )
    service.add_row(
        sheet_id,
        {
            "Task": "Draft test plan",
            "Status": "In Progress",
            "Owner": "farid@example.com",
            "Reviewer": "Priya Shah",
            "Hours": 6,
            "Areas": ["Design", "QA"],
        },
    )
    return sheet_id


def main() -> None:
    service = MockSheetService()
    sheet_id = build_sheet(service)
    source = service.get_rows(sheet_id)[0]
    counter = itertools.count(1)

    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "audit.jsonl")
        audit = AuditLog(log_path)
        executor = Executor(service, SHEET_TOOLS, Policy(), audit, run_id_factory=lambda: "run-%d" % next(counter))

        section("1. The naive copy fails, and the error does not say why")
        print("source row:", source["cells"])
        try:
            service.add_row(sheet_id, source["cells"])
        except ServiceError as exc:
            print("add_row(source cells) ->", exc)

        section("2. Classify every column before writing")
        dup = prepare_duplicate(service, sheet_id, source["id"], APPROVED)
        print(format_table(dup.decisions))

        section("3. Dry run: what would change")
        report = executor.dry_run(dup.plan)
        print(report.render())

        section("4. Approval is bound to the exact plan")
        token = executor.approve(dup.plan, approver="jayson")
        edited_cells = dict(dup.plan.calls[0].args["cells"], Hours=60)
        edited = Plan((ToolCall("sheet.add_row", {"sheet_id": sheet_id, "cells": edited_cells}),), goal=dup.plan.goal)
        executor.dry_run(edited)
        try:
            executor.execute(edited, token)
        except PolicyError as exc:
            print("edited plan with the old token -> refused: %s: %s" % (type(exc).__name__, exc))
        print("rows in sheet:", len(service.get_rows(sheet_id)))

        section("5. Approved execution")
        before = service.get_rows(sheet_id)
        run = executor.execute(dup.plan, token)
        print("run %s: %s, new row id %s" % (run.run_id, run.status, run.results[0]["row_id"]))
        print("new row:", service.get_rows(sheet_id)[-1]["cells"])

        section("6. Audit log verifies")
        result = audit.verify()
        print("verify: ok=%s entries=%d" % (result.ok, result.entries))

        section("7. Tampering is detected, and undo refuses to trust a bad log")
        with open(log_path, encoding="utf-8") as fh:
            original = fh.read()
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(original.replace('"Hours": 6', '"Hours": 60', 1))
        result = audit.verify()
        print("verify after edit: ok=%s (%s)" % (result.ok, result.problem))
        try:
            executor.undo(run.run_id)
        except Exception as exc:
            print("undo ->", type(exc).__name__)

        section("8. Undo restores the sheet")
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(original)
        report_undo = executor.undo(run.run_id)
        print("undone calls:", report_undo.undone)
        print("sheet identical to before the run:", service.get_rows(sheet_id) == before)

        section("9. Diagnose a rejected write with one disposable row")
        payload = {
            "Task": "Draft test plan",
            "Status": "In Progress",
            "Hours": 6,
            "Areas": "Design, QA",
        }
        rows_before = len(service.get_rows(sheet_id))
        started = time.time()
        finding = diagnose_payload(service, sheet_id, payload, audit)
        print("offending columns:", finding.offending, "(%d probes)" % finding.probes)
        print("rows before/after diagnosis: %d/%d" % (rows_before, len(service.get_rows(sheet_id))))
        print("orphan audit since diagnosis started:", orphan_audit(service, sheet_id, started))
        print("audit log verifies:", audit.verify().ok)


if __name__ == "__main__":
    main()
