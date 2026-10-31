"""In-memory mock of a work-management spreadsheet API.

It reproduces the quirks that make agent writes fail:
  * validation errors are deliberately generic and never name the column
  * a multi-select column can report its type as TEXT (a "disguised" column)
  * contact columns only accept members of a workspace allow-list
  * formula columns reject direct writes
  * there is no copy-row call, so a duplicate must be read, modified, inserted
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

GENERIC_ERROR = "Value is not supported for this column type"


class ColumnType(Enum):
    TEXT = "TEXT"
    NUMBER = "NUMBER"
    PICKLIST = "PICKLIST"
    MULTI_PICKLIST = "MULTI_PICKLIST"
    CONTACT_LIST = "CONTACT_LIST"
    FORMULA = "FORMULA"


class ServiceError(Exception):
    pass


class ValidationError(ServiceError):
    pass


class NotFoundError(ServiceError):
    pass


class NotSupportedError(ServiceError):
    pass


@dataclass
class ColumnSpec:
    title: str
    type: ColumnType
    options: Tuple[str, ...] = ()
    contacts: Tuple[str, ...] = ()  # workspace members accepted by a CONTACT_LIST column
    disguised: bool = False  # a MULTI_PICKLIST that reports itself as TEXT
    formula: Optional[Callable[[Dict[str, Any]], Any]] = None


@dataclass
class _Row:
    id: int
    created_at: float
    cells: Dict[str, Any]


@dataclass
class _Sheet:
    name: str
    columns: List[ColumnSpec]
    rows: List[_Row] = field(default_factory=list)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class MockSheetService:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._sheets: Dict[int, _Sheet] = {}
        self._next_sheet = 1
        self._next_row = 1

    def create_sheet(self, name: str, columns: List[ColumnSpec]) -> int:
        sheet_id = self._next_sheet
        self._next_sheet += 1
        self._sheets[sheet_id] = _Sheet(name, list(columns))
        return sheet_id

    def get_columns(self, sheet_id: int) -> List[Dict[str, Any]]:
        """Column metadata as the API reports it. A disguised column says TEXT but still lists options."""
        out = []
        for c in self._sheet(sheet_id).columns:
            reported = ColumnType.TEXT if c.disguised else c.type
            meta: Dict[str, Any] = {"title": c.title, "type": reported.value}
            if c.options:
                meta["options"] = list(c.options)
            out.append(meta)
        return out

    def get_rows(self, sheet_id: int) -> List[Dict[str, Any]]:
        """Rows as displayed: formulas evaluated, multi-select values joined into one string."""
        sheet = self._sheet(sheet_id)
        return [
            {"id": r.id, "created_at": r.created_at, "cells": self._display(sheet, r)} for r in sheet.rows
        ]

    def validate_cells(self, sheet_id: int, cells: Dict[str, Any]) -> None:
        """Run the write validation without writing anything."""
        by_title = {c.title: c for c in self._sheet(sheet_id).columns}
        for title, value in cells.items():
            col = by_title.get(title)
            if col is None or not self._accepts(col, value):
                raise ValidationError(GENERIC_ERROR)

    def add_row(self, sheet_id: int, cells: Dict[str, Any]) -> Dict[str, Any]:
        sheet = self._sheet(sheet_id)
        self.validate_cells(sheet_id, cells)
        row = _Row(self._next_row, self._clock(), {c.title: None for c in sheet.columns if c.formula is None})
        self._next_row += 1
        row.cells.update(copy.deepcopy(cells))
        sheet.rows.append(row)
        return {"id": row.id, "created_at": row.created_at, "cells": self._display(sheet, row)}

    def update_row(self, sheet_id: int, row_id: int, cells: Dict[str, Any]) -> Dict[str, Any]:
        sheet = self._sheet(sheet_id)
        row = self._row(sheet, row_id)
        self.validate_cells(sheet_id, cells)
        row.cells.update(copy.deepcopy(cells))
        return {"id": row.id, "created_at": row.created_at, "cells": self._display(sheet, row)}

    def delete_row(self, sheet_id: int, row_id: int) -> None:
        sheet = self._sheet(sheet_id)
        sheet.rows.remove(self._row(sheet, row_id))

    def copy_row(self, sheet_id: int, row_id: int) -> Dict[str, Any]:
        raise NotSupportedError("copying a row within a sheet is not supported; read, modify, then insert")

    def _sheet(self, sheet_id: int) -> _Sheet:
        try:
            return self._sheets[sheet_id]
        except KeyError:
            raise NotFoundError("sheet %s" % sheet_id) from None

    @staticmethod
    def _row(sheet: _Sheet, row_id: int) -> _Row:
        for r in sheet.rows:
            if r.id == row_id:
                return r
        raise NotFoundError("row %s" % row_id)

    @staticmethod
    def _accepts(col: ColumnSpec, value: Any) -> bool:
        if col.type is ColumnType.FORMULA:
            return False
        if value is None:
            return True
        if col.type is ColumnType.TEXT:
            return isinstance(value, str)
        if col.type is ColumnType.NUMBER:
            return is_number(value)
        if col.type is ColumnType.PICKLIST:
            return value in col.options
        if col.type is ColumnType.MULTI_PICKLIST:
            return isinstance(value, list) and all(v in col.options for v in value)
        return value in col.contacts  # CONTACT_LIST

    @staticmethod
    def _display(sheet: _Sheet, row: _Row) -> Dict[str, Any]:
        cells: Dict[str, Any] = {}
        for col in sheet.columns:
            if col.formula is not None:
                cells[col.title] = col.formula(row.cells)
                continue
            value = copy.deepcopy(row.cells[col.title])
            if col.type is ColumnType.MULTI_PICKLIST and col.disguised and isinstance(value, list):
                value = ", ".join(value)
            cells[col.title] = value
        return cells
