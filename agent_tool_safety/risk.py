"""Risk tiers and the tool registry.

Every tool an agent may call is registered with a risk level. The executor and
the policy decide what is allowed from that metadata, not from the agent's say-so.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class RiskLevel(Enum):
    READ = "read"
    WRITE_REVERSIBLE = "write_reversible"
    WRITE_IRREVERSIBLE = "write_irreversible"
    EXTERNAL_NOTIFY = "external_notify"

    @property
    def is_write(self) -> bool:
        return self is not RiskLevel.READ


class ToolDefinitionError(Exception):
    """A tool is registered in a way that cannot be executed safely."""


class UnknownTool(KeyError):
    pass


@dataclass
class Outcome:
    """What a tool returns: a JSON-serializable result and, if reversible, undo data."""

    result: Dict[str, Any]
    undo: Optional[Dict[str, Any]] = None


class ToolSpec:
    """A registered tool: metadata plus the callables the executor needs.

    func(ctx, args) -> Outcome        performs the action
    preview(ctx, args) -> Change      describes it without side effects
    undo(ctx, undo_data) -> None      reverses it from the recorded undo data
    """

    def __init__(self, name: str, risk: RiskLevel, description: str, func: Callable[..., Outcome]):
        self.name = name
        self.risk = risk
        self.description = description
        self.func = func
        self.preview_fn: Optional[Callable[..., Any]] = None
        self.undo_fn: Optional[Callable[..., None]] = None

    def preview(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        self.preview_fn = fn
        return fn

    def undo(self, fn: Callable[..., None]) -> Callable[..., None]:
        self.undo_fn = fn
        return fn

    def validate(self) -> None:
        """Fail closed: a write tool without a preview, or a reversible one without undo, is unusable."""
        if self.risk.is_write and self.preview_fn is None:
            raise ToolDefinitionError("%s: write tools must define a preview" % self.name)
        if self.risk is RiskLevel.WRITE_REVERSIBLE and self.undo_fn is None:
            raise ToolDefinitionError("%s: reversible tools must define undo" % self.name)

    def __call__(self, ctx: Any, args: Dict[str, Any]) -> Outcome:
        return self.func(ctx, args)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ToolDefinitionError("duplicate tool name: %s" % spec.name)
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownTool(name) from None

    def describe(self) -> List[Dict[str, str]]:
        """Model-facing listing of the tools and their risk tiers."""
        return [
            {"name": s.name, "risk": s.risk.value, "description": s.description}
            for s in sorted(self._tools.values(), key=lambda s: s.name)
        ]


def tool(registry: ToolRegistry, name: str, risk: RiskLevel, description: str = "") -> Callable[[Callable[..., Outcome]], ToolSpec]:
    """Register a function as a tool. Returns the ToolSpec, which stays callable."""

    def wrap(func: Callable[..., Outcome]) -> ToolSpec:
        text = description or (func.__doc__ or "").strip().split("\n")[0]
        return registry.register(ToolSpec(name, risk, text, func))

    return wrap
