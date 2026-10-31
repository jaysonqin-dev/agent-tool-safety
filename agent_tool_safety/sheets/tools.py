"""Spreadsheet tools, registered with risk tiers. The execution context is a MockSheetService."""
from __future__ import annotations

from typing import Any, Dict, List

from ..executor import Change
from ..risk import Outcome, RiskLevel, ToolRegistry, tool
from .classify import parse_multi
from .mock import MockSheetService, NotFoundError

SHEET_TOOLS = ToolRegistry()


def _row(service: MockSheetService, sheet_id: int, row_id: int) -> Dict[str, Any]:
    for row in service.get_rows(sheet_id):
        if row["id"] == row_id:
            return row
    raise NotFoundError("row %s" % row_id)


def _writable_values(service: MockSheetService, sheet_id: int, row_id: int, titles: List[str]) -> Dict[str, Any]:
    """Current values of `titles` in the shape a write accepts.

    Reads return a multi-select as one joined string, so it is split back into a list.
    """
    columns = {c["title"]: c for c in service.get_columns(sheet_id)}
    cells = _row(service, sheet_id, row_id)["cells"]
    values = {}
    for title in titles:
        value = cells.get(title)
        column = columns.get(title, {})
        if column.get("type") == "TEXT" and column.get("options") and isinstance(value, str):
            value = parse_multi(value, column["options"]) or value
        values[title] = value
    return values


@tool(SHEET_TOOLS, "sheet.get_rows", RiskLevel.READ, "Read all rows of a sheet.")
def get_rows(ctx: MockSheetService, args: Dict[str, Any]) -> Outcome:
    return Outcome({"rows": ctx.get_rows(args["sheet_id"])})


@tool(SHEET_TOOLS, "sheet.add_row", RiskLevel.WRITE_REVERSIBLE, "Append a new row. Undo deletes it.")
def add_row(ctx: MockSheetService, args: Dict[str, Any]) -> Outcome:
    row = ctx.add_row(args["sheet_id"], args["cells"])
    return Outcome({"row_id": row["id"]}, undo={"sheet_id": args["sheet_id"], "row_id": row["id"]})


@add_row.preview
def _preview_add(ctx: MockSheetService, args: Dict[str, Any]) -> Change:
    ctx.validate_cells(args["sheet_id"], args["cells"])
    return Change("add_row", "sheet %s" % args["sheet_id"], None, dict(args["cells"]))


@add_row.undo
def _undo_add(ctx: MockSheetService, undo: Dict[str, Any]) -> None:
    ctx.delete_row(undo["sheet_id"], undo["row_id"])


@tool(SHEET_TOOLS, "sheet.update_row", RiskLevel.WRITE_REVERSIBLE, "Update cells of one row. Undo restores the old values.")
def update_row(ctx: MockSheetService, args: Dict[str, Any]) -> Outcome:
    sheet_id, row_id, cells = args["sheet_id"], args["row_id"], args["cells"]
    previous = _writable_values(ctx, sheet_id, row_id, list(cells))
    ctx.update_row(sheet_id, row_id, cells)
    return Outcome({"row_id": row_id}, undo={"sheet_id": sheet_id, "row_id": row_id, "previous": previous})


@update_row.preview
def _preview_update(ctx: MockSheetService, args: Dict[str, Any]) -> Change:
    ctx.validate_cells(args["sheet_id"], args["cells"])
    current = _row(ctx, args["sheet_id"], args["row_id"])["cells"]
    before = {k: current.get(k) for k in args["cells"]}
    return Change("update_row", "sheet %s row %s" % (args["sheet_id"], args["row_id"]), before, dict(args["cells"]))


@update_row.undo
def _undo_update(ctx: MockSheetService, undo: Dict[str, Any]) -> None:
    ctx.update_row(undo["sheet_id"], undo["row_id"], undo["previous"])


@tool(SHEET_TOOLS, "sheet.delete_row", RiskLevel.WRITE_IRREVERSIBLE, "Delete a row. Cannot be undone: a re-created row gets a new id.")
def delete_row(ctx: MockSheetService, args: Dict[str, Any]) -> Outcome:
    ctx.delete_row(args["sheet_id"], args["row_id"])
    return Outcome({"deleted": args["row_id"]})


@delete_row.preview
def _preview_delete(ctx: MockSheetService, args: Dict[str, Any]) -> Change:
    row = _row(ctx, args["sheet_id"], args["row_id"])
    return Change("delete_row", "sheet %s row %s" % (args["sheet_id"], args["row_id"]), row["cells"], None)
