"""Executor: plan -> dry run -> approval -> execute with undo records -> undo.

The audit log is the source of truth for undo. Each applied call is logged with
its undo data immediately after it succeeds, so a run that stops half way still
has a complete log of what to reverse.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from .audit import AuditLog
from .plan import Plan
from .policy import ApprovalToken, DryRunRequired, Policy, PolicyError
from .risk import RiskLevel, ToolRegistry


class AuditIntegrityError(Exception):
    """The audit log failed verification, so its undo data cannot be trusted."""


class UnknownRun(KeyError):
    pass


@dataclass(frozen=True)
class Change:
    """One tool call as it would affect the world: what is there now, what would be there."""

    op: str
    target: str
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class CallPreview:
    index: int
    tool: str
    risk: RiskLevel
    change: Optional[Change] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class DryRunReport:
    plan_hash: str
    previews: List[CallPreview]

    @property
    def ok(self) -> bool:
        return all(p.error is None for p in self.previews)

    def render(self) -> str:
        lines = ["dry run of plan %s (no changes made)" % self.plan_hash[:12]]
        for p in self.previews:
            head = "  [%d] %s (%s)" % (p.index, p.tool, p.risk.value)
            if p.error:
                lines.append("%s -> WOULD FAIL: %s" % (head, p.error))
                continue
            c = p.change
            lines.append("%s -> %s %s" % (head, c.op, c.target))
            for label, cells in (("before", c.before), ("after", c.after)):
                if cells is not None:
                    lines.append("        %-6s %s" % (label, cells))
        return "\n".join(lines)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str  # "ok" or "failed"
    results: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass(frozen=True)
class UndoReport:
    run_id: str
    undone: List[int]
    irreversible: List[int]


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


class Executor:
    def __init__(
        self,
        ctx: Any,
        registry: ToolRegistry,
        policy: Policy,
        audit: AuditLog,
        run_id_factory: Callable[[], str] = _short_id,
    ):
        self._ctx = ctx
        self._registry = registry
        self._policy = policy
        self._audit = audit
        self._new_run_id = run_id_factory
        self._dry_runs: Set[str] = set()

    def dry_run(self, plan: Plan) -> DryRunReport:
        """Preview every call without side effects. Only a clean dry run unlocks execution."""
        previews = []
        for i, call in enumerate(plan.calls):
            spec = self._registry.get(call.tool)
            spec.validate()
            try:
                if spec.preview_fn is None:
                    change = Change("read", call.tool, None, None)
                else:
                    change = spec.preview_fn(self._ctx, call.args)
                previews.append(CallPreview(i, call.tool, spec.risk, change=change))
            except Exception as exc:  # a preview that raises means the call would too
                previews.append(CallPreview(i, call.tool, spec.risk, error=str(exc)))
        report = DryRunReport(plan.hash, previews)
        if report.ok:
            self._dry_runs.add(plan.hash)
        self._audit.append("dry_run", None, {"plan_hash": plan.hash, "ok": report.ok})
        return report

    def approve(self, plan: Plan, approver: str, ttl_seconds: float = 300.0) -> ApprovalToken:
        """Human-side step. Refuses to approve a plan nobody has seen a clean dry run of."""
        if plan.hash not in self._dry_runs:
            raise DryRunRequired("cannot approve plan %s without a clean dry run" % plan.hash[:12])
        return self._policy.issue_token(plan, approver, ttl_seconds)

    def execute(self, plan: Plan, token: Optional[ApprovalToken] = None) -> RunResult:
        try:
            self._policy.authorize(plan, self._registry, self._dry_runs, token)
        except PolicyError as exc:
            self._audit.append(
                "execute_refused", None, {"plan_hash": plan.hash, "reason": type(exc).__name__, "detail": str(exc)}
            )
            raise
        self._dry_runs.discard(plan.hash)  # one dry run authorizes one execution

        run_id = self._new_run_id()
        self._audit.append(
            "run_started",
            run_id,
            {"plan_hash": plan.hash, "plan": plan.to_dict(), "approver": token.approver if token else None},
        )
        results: List[Dict[str, Any]] = []
        for i, call in enumerate(plan.calls):
            spec = self._registry.get(call.tool)
            try:
                outcome = spec.func(self._ctx, call.args)
            except Exception as exc:
                error = "%s: %s" % (type(exc).__name__, exc)
                self._audit.append("call_failed", run_id, {"index": i, "tool": call.tool, "error": error})
                self._audit.append("run_finished", run_id, {"status": "failed", "applied": i})
                return RunResult(run_id, "failed", results, error)
            results.append(outcome.result)
            self._audit.append(
                "call_applied",
                run_id,
                {"index": i, "tool": call.tool, "args": call.args, "result": outcome.result, "undo": outcome.undo},
            )
        self._audit.append("run_finished", run_id, {"status": "ok", "applied": len(plan.calls)})
        return RunResult(run_id, "ok", results)

    def undo(self, run_id: str) -> UndoReport:
        """Reverse a run, newest call first, using only data from the verified audit log.

        Safe to repeat: calls already undone are skipped, so an interrupted undo can resume.
        """
        check = self._audit.verify()
        if not check.ok:
            raise AuditIntegrityError("refusing to undo from a log that fails verification: %s" % check.problem)
        events = [e for e in self._audit.entries() if e["run_id"] == run_id]
        if not events:
            raise UnknownRun(run_id)
        applied = [e["data"] for e in events if e["event"] == "call_applied"]
        done = {e["data"]["index"] for e in events if e["event"] == "call_undone"}

        undone: List[int] = []
        irreversible: List[int] = []
        for data in reversed(applied):
            index = data["index"]
            if index in done:
                continue
            spec = self._registry.get(data["tool"])
            if spec.undo_fn is None or data["undo"] is None:
                irreversible.append(index)
                continue
            try:
                spec.undo_fn(self._ctx, data["undo"])
            except Exception as exc:
                self._audit.append("undo_failed", run_id, {"index": index, "error": "%s: %s" % (type(exc).__name__, exc)})
                raise
            self._audit.append("call_undone", run_id, {"index": index})
            undone.append(index)
        self._audit.append("run_undone", run_id, {"undone": undone, "irreversible": sorted(irreversible)})
        return UndoReport(run_id, undone, sorted(irreversible))
