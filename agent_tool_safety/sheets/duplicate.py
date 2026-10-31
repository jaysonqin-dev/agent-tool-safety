"""Duplicate a row as a new record without trial and error.

The service has no copy call, so the row is read, classified, and inserted as a
fresh row through the executor: dry run, approval, audited execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Callable, List, Optional

from ..executor import DryRunReport, Executor, RunResult
from ..plan import Plan, ToolCall
from .classify import Decision, build_payload, classify
from .mock import MockSheetService, NotFoundError


@dataclass(frozen=True)
class Duplication:
    decisions: List[Decision]
    plan: Plan


@dataclass(frozen=True)
class DuplicateOutcome:
    duplication: Duplication
    report: DryRunReport
    run: Optional[RunResult]  # None when the dry run failed or nobody approved


def prepare_duplicate(
    service: MockSheetService, sheet_id: int, row_id: int, approved_contacts: AbstractSet[str]
) -> Duplication:
    rows = service.get_rows(sheet_id)
    source = next((r for r in rows if r["id"] == row_id), None)
    if source is None:
        raise NotFoundError("row %s" % row_id)
    decisions = classify(service.get_columns(sheet_id), source["cells"], approved_contacts, rows)
    call = ToolCall("sheet.add_row", {"sheet_id": sheet_id, "cells": build_payload(decisions)})
    return Duplication(decisions, Plan((call,), goal="Duplicate row %d of sheet %d as a new record" % (row_id, sheet_id)))


def duplicate_row(
    executor: Executor,
    service: MockSheetService,
    sheet_id: int,
    row_id: int,
    approved_contacts: AbstractSet[str],
    approve: Callable[[Duplication, DryRunReport], Optional[str]],
) -> DuplicateOutcome:
    """`approve` sees the decision table and dry-run diff and returns an approver name, or None to decline."""
    dup = prepare_duplicate(service, sheet_id, row_id, approved_contacts)
    report = executor.dry_run(dup.plan)
    if not report.ok:
        return DuplicateOutcome(dup, report, None)
    approver = approve(dup, report)
    if approver is None:
        return DuplicateOutcome(dup, report, None)
    return DuplicateOutcome(dup, report, executor.execute(dup.plan, executor.approve(dup.plan, approver)))
