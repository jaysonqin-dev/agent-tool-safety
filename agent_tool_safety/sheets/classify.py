"""Classify every column from its metadata before any write is attempted.

The result is a per-column decision table. Only COPY decisions reach the payload.
The service's own errors are generic, so this step is where the reason for each
omitted column gets recorded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import AbstractSet, Any, Dict, List, Optional, Sequence

from .mock import is_number


class Action(Enum):
    COPY = "copy"
    SKIP = "skip"
    BLANK = "blank"
    NEEDS_HUMAN = "needs human"


@dataclass(frozen=True)
class Decision:
    column: str
    kind: str
    action: Action
    value: Any = None
    reason: str = ""
    flagged: bool = False  # something a person should look at


PERSON_COLUMN = re.compile(r"owner|assignee|assigned|reviewer|approver|lead|contact|manager", re.I)
PERSON_NAME = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z]+){0,2}$")


def parse_multi(display: str, options: Sequence[str]) -> Optional[List[str]]:
    """Split a displayed multi-select value; None if any part is not a known option."""
    parts = [p.strip() for p in display.split(",") if p.strip()]
    return parts if all(p in options for p in parts) else None


def classify(
    columns: Sequence[Dict[str, Any]],
    source: Dict[str, Any],
    approved_contacts: AbstractSet[str],
    existing_rows: Sequence[Dict[str, Any]] = (),
) -> List[Decision]:
    """Decide, per column, what may be written when duplicating `source` (a row's cells).

    `existing_rows` supplies displayed values used as evidence that a TEXT column
    with options is really a multi-select.
    """
    decisions = []
    for col in columns:
        title = col["title"]
        samples = [r["cells"].get(title) for r in existing_rows] + [source.get(title)]
        decisions.append(_decide(col, source.get(title), approved_contacts, samples))
    return decisions


def _decide(col: Dict[str, Any], value: Any, approved: AbstractSet[str], samples: List[Any]) -> Decision:
    title, ctype, options = col["title"], col["type"], col.get("options", [])
    if ctype == "FORMULA":
        return Decision(title, "formula", Action.SKIP, reason="computed by the sheet; direct writes are rejected")
    if ctype == "CONTACT_LIST":
        if _blank(value):
            return Decision(title, "contact", Action.BLANK, reason="source is empty")
        if isinstance(value, str) and value in approved:
            return Decision(title, "contact", Action.COPY, value)
        return Decision(title, "contact", Action.BLANK, reason="not in the approved contact list; left blank", flagged=True)
    if ctype == "PICKLIST":
        if _blank(value):
            return Decision(title, "picklist", Action.BLANK, reason="source is empty")
        if value in options:
            return Decision(title, "picklist", Action.COPY, value)
        return Decision(title, "picklist", Action.NEEDS_HUMAN, reason="value is not one of the options", flagged=True)
    if ctype == "MULTI_PICKLIST":
        return _decide_multi(title, "multi-picklist", value, options)
    if ctype == "NUMBER":
        if _blank(value):
            return Decision(title, "number", Action.BLANK, reason="source is empty")
        if is_number(value):
            return Decision(title, "number", Action.COPY, value)
        return Decision(title, "number", Action.NEEDS_HUMAN, reason="value is not numeric", flagged=True)
    if ctype == "TEXT" and options:
        return _decide_disguised(title, value, options, samples)
    if ctype == "TEXT":
        if _blank(value):
            return Decision(title, "text", Action.BLANK, reason="source is empty")
        if not isinstance(value, str):
            return Decision(title, "text", Action.NEEDS_HUMAN, reason="value is not text", flagged=True)
        if PERSON_COLUMN.search(title) and PERSON_NAME.match(value):
            return Decision(
                title, "text", Action.NEEDS_HUMAN, reason="a name without a contact record; cannot be resolved safely", flagged=True
            )
        return Decision(title, "text", Action.COPY, value)
    return Decision(title, ctype.lower(), Action.NEEDS_HUMAN, reason="unknown column type", flagged=True)


def _decide_multi(title: str, kind: str, value: Any, options: Sequence[str]) -> Decision:
    if _blank(value):
        return Decision(title, kind, Action.BLANK, reason="source is empty")
    items = value if isinstance(value, list) else parse_multi(str(value), options)
    if items is not None and all(i in options for i in items):
        return Decision(title, kind, Action.COPY, list(items))
    return Decision(title, kind, Action.NEEDS_HUMAN, reason="value contains something that is not an option", flagged=True)


def _decide_disguised(title: str, value: Any, options: Sequence[str], samples: List[Any]) -> Decision:
    kind = "multi-picklist (reported as TEXT)"
    evidence = [s for s in samples if not _blank(s)]
    if not evidence:
        return Decision(title, kind, Action.BLANK, reason="options present but nothing to copy")
    if not all(isinstance(s, str) and parse_multi(s, options) is not None for s in evidence):
        return Decision(
            title, kind, Action.NEEDS_HUMAN, reason="has options but existing values do not match them", flagged=True
        )
    if _blank(value):
        return Decision(title, kind, Action.BLANK, reason="source is empty")
    return Decision(title, kind, Action.COPY, parse_multi(value, options), reason="options plus matching values: treated as multi-select")


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == []


def build_payload(decisions: Sequence[Decision]) -> Dict[str, Any]:
    return {d.column: d.value for d in decisions if d.action is Action.COPY}


def format_table(decisions: Sequence[Decision]) -> str:
    rows = [("column", "kind", "action", "note")]
    for d in decisions:
        note = d.reason or ("value: %r" % (d.value,))
        rows.append((d.column, d.kind, d.action.value + (" !" if d.flagged else ""), note))
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    return "\n".join(
        "  ".join(r[i].ljust(widths[i]) for i in range(3)) + "  " + r[3] for r in rows
    )
