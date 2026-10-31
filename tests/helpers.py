"""Shared fixtures for the test suite."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_tool_safety import AuditLog, Executor, Outcome, Policy, RiskLevel, ToolRegistry, tool  # noqa: E402
from agent_tool_safety.executor import Change  # noqa: E402
from agent_tool_safety.sheets.mock import ColumnSpec, ColumnType, MockSheetService  # noqa: E402
from agent_tool_safety.sheets.tools import SHEET_TOOLS  # noqa: E402

CONTACTS = ("ana@example.com", "ben@example.com", "chloe@example.com", "farid@example.com")
APPROVED = frozenset(CONTACTS[:3])


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def summary_formula(cells: Dict[str, Any]) -> str:
    if not cells.get("Task"):
        return "#INVALID"
    return "%s [%s]" % (cells["Task"], cells.get("Status") or "-")


def task_columns() -> List[ColumnSpec]:
    return [
        ColumnSpec("Task", ColumnType.TEXT),
        ColumnSpec("Status", ColumnType.PICKLIST, options=("Not Started", "In Progress", "Done")),
        ColumnSpec("Owner", ColumnType.CONTACT_LIST, contacts=CONTACTS),
        ColumnSpec("Reviewer", ColumnType.TEXT),
        ColumnSpec("Hours", ColumnType.NUMBER),
        ColumnSpec("Areas", ColumnType.MULTI_PICKLIST, options=("Design", "Engineering", "QA"), disguised=True),
        ColumnSpec("Summary", ColumnType.FORMULA, formula=summary_formula),
    ]


SEED_ROW = {
    "Task": "Draft test plan",
    "Status": "In Progress",
    "Owner": "farid@example.com",
    "Reviewer": "Priya Shah",
    "Hours": 6,
    "Areas": ["Design", "QA"],
}


def make_sheet(clock: Optional[FakeClock] = None):
    """Return (service, sheet_id, seed_row_id)."""
    service = MockSheetService(clock=clock or FakeClock())
    sheet_id = service.create_sheet("Release Tasks", task_columns())
    seed = service.add_row(sheet_id, SEED_ROW)
    return service, sheet_id, seed["id"]


def sheet_executor(service: MockSheetService, audit_path: str, policy: Optional[Policy] = None) -> Executor:
    return Executor(service, SHEET_TOOLS, policy or Policy(), AuditLog(audit_path))


# A tiny key-value store with one tool per risk tier, for tests that are not about sheets.
KV_TOOLS = ToolRegistry()


@tool(KV_TOOLS, "kv.get", RiskLevel.READ, "Read a key.")
def kv_get(ctx: Dict[str, Any], args: Dict[str, Any]) -> Outcome:
    return Outcome({"value": ctx.get(args["key"])})


@tool(KV_TOOLS, "kv.set", RiskLevel.WRITE_REVERSIBLE, "Set a key.")
def kv_set(ctx: Dict[str, Any], args: Dict[str, Any]) -> Outcome:
    key = args["key"]
    undo = {"key": key, "existed": key in ctx, "previous": ctx.get(key)}
    ctx[key] = args["value"]
    return Outcome({"key": key}, undo=undo)


@kv_set.preview
def _kv_set_preview(ctx: Dict[str, Any], args: Dict[str, Any]) -> Change:
    return Change("set", args["key"], {"value": ctx.get(args["key"])}, {"value": args["value"]})


@kv_set.undo
def _kv_set_undo(ctx: Dict[str, Any], undo: Dict[str, Any]) -> None:
    if undo["existed"]:
        ctx[undo["key"]] = undo["previous"]
    else:
        ctx.pop(undo["key"], None)


@tool(KV_TOOLS, "kv.explode", RiskLevel.WRITE_REVERSIBLE, "Always fails when run.")
def kv_explode(ctx: Dict[str, Any], args: Dict[str, Any]) -> Outcome:
    raise RuntimeError("boom")


@kv_explode.preview
def _kv_explode_preview(ctx: Dict[str, Any], args: Dict[str, Any]) -> Change:
    return Change("explode", "n/a")


@kv_explode.undo
def _kv_explode_undo(ctx: Dict[str, Any], undo: Dict[str, Any]) -> None:
    raise AssertionError("never applied, so never undone")


@tool(KV_TOOLS, "kv.drop", RiskLevel.WRITE_IRREVERSIBLE, "Delete a key permanently.")
def kv_drop(ctx: Dict[str, Any], args: Dict[str, Any]) -> Outcome:
    ctx.pop(args["key"], None)
    return Outcome({"dropped": args["key"]})


@kv_drop.preview
def _kv_drop_preview(ctx: Dict[str, Any], args: Dict[str, Any]) -> Change:
    return Change("drop", args["key"], {"value": ctx.get(args["key"])}, None)


@tool(KV_TOOLS, "msg.send", RiskLevel.EXTERNAL_NOTIFY, "Send a message to a person.")
def msg_send(ctx: Dict[str, Any], args: Dict[str, Any]) -> Outcome:
    ctx.setdefault("_outbox", []).append(args["text"])
    return Outcome({"sent": True})


@msg_send.preview
def _msg_send_preview(ctx: Dict[str, Any], args: Dict[str, Any]) -> Change:
    return Change("send", "outbox", None, {"text": args["text"]})
