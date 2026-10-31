"""Diagnose a rejected write without littering the sheet.

The service's error does not say which column is wrong. Instead of retrying the
real sheet with guesses, this creates exactly one disposable test row, bisects
the payload's columns with updates to that single row, and always deletes it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..audit import AuditLog
from .mock import MockSheetService, ValidationError

BLANK_RATIO = 0.25  # a row with at most this share of its writable cells filled counts as "mostly blank"


@dataclass(frozen=True)
class Diagnosis:
    offending: List[str]  # columns whose values the service rejects
    probes: int
    note: str = ""


@dataclass(frozen=True)
class Suspect:
    row_id: int
    reasons: List[str]


def diagnose_payload(
    service: MockSheetService,
    sheet_id: int,
    payload: Dict[str, Any],
    audit: Optional[AuditLog] = None,
) -> Diagnosis:
    """Find which payload columns the service rejects. The test row is deleted on every path."""
    probes = 0

    def log(event: str, data: Dict[str, Any]) -> None:
        if audit is not None:
            audit.append(event, None, data)

    def rejected(test_row: int, titles: List[str]) -> bool:
        nonlocal probes
        probes += 1
        try:
            service.update_row(sheet_id, test_row, {t: payload[t] for t in titles})
        except ValidationError:
            return True
        return False

    def find(test_row: int, titles: List[str]) -> List[str]:
        """`titles` is known to be rejected as a group; narrow it to the culprits."""
        if len(titles) == 1:
            return titles
        mid = len(titles) // 2
        found: List[str] = []
        for half in (titles[:mid], titles[mid:]):
            if rejected(test_row, half):
                found += find(test_row, half)
        return found

    test_row = service.add_row(sheet_id, {})["id"]
    log("diagnose_test_row_created", {"sheet_id": sheet_id, "row_id": test_row})
    try:
        titles = list(payload)
        if not rejected(test_row, titles):
            return Diagnosis([], probes, "the whole payload was accepted on the test row; the failure is not a cell value")
        offending = find(test_row, titles)
        note = "" if offending else "the payload is rejected as a whole but no smaller group is"
        return Diagnosis(offending, probes, note)
    finally:
        service.delete_row(sheet_id, test_row)
        log("diagnose_test_row_deleted", {"sheet_id": sheet_id, "row_id": test_row})


def orphan_audit(service: MockSheetService, sheet_id: int, since: float) -> List[Suspect]:
    """Rows created at or after `since` that look like leftovers. Reports only; never deletes."""
    columns = service.get_columns(sheet_id)
    writable = [c["title"] for c in columns if c["type"] != "FORMULA"]
    rows = service.get_rows(sheet_id)
    formula_titles = [c["title"] for c in columns if c["type"] == "FORMULA"]

    def content(row: Dict[str, Any]) -> tuple:
        return tuple(repr(row["cells"].get(t)) for t in writable)

    def filled(row: Dict[str, Any]) -> int:
        return sum(1 for t in writable if row["cells"].get(t) not in (None, "", []))

    suspects = []
    for i, row in enumerate(rows):
        if row["created_at"] < since:
            continue
        reasons = []
        if writable and filled(row) <= BLANK_RATIO * len(writable):
            reasons.append("mostly blank")
        if filled(row) and any(content(other) == content(row) for other in rows[:i]):
            reasons.append("duplicate of an earlier row")
        if any(isinstance(row["cells"].get(t), str) and row["cells"][t].startswith("#") for t in formula_titles):
            reasons.append("formula output is an error value")
        if reasons:
            suspects.append(Suspect(row["id"], reasons))
    return suspects
