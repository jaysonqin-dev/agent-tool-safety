"""Plans: an ordered list of tool calls with a stable content hash.

Approval is bound to this hash, so the thing a human approves is byte-for-byte
the thing that runs.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"tool": self.tool, "args": self.args}


@dataclass(frozen=True)
class Plan:
    calls: Tuple[ToolCall, ...]
    goal: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"goal": self.goal, "calls": [c.to_dict() for c in self.calls]}

    @property
    def hash(self) -> str:
        """Recomputed on every access so later mutation of args cannot go unnoticed."""
        return sha256_hex(canonical_json(self.to_dict()))
