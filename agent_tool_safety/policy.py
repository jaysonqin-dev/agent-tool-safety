"""Policy: when a plan may run.

Rules enforced here, independent of what the agent asks for:
  1. Any plan containing a write must have been dry-run first (unchanged).
  2. Plans containing a risk level listed in `approval_required` need an
     ApprovalToken bound to the plan's hash.
  3. Tokens are signed, expire, and are single use.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass, replace
from typing import AbstractSet, Callable, Optional, Set

from .plan import Plan
from .risk import RiskLevel, ToolRegistry


class PolicyError(Exception):
    pass


class DryRunRequired(PolicyError):
    pass


class ApprovalRequired(PolicyError):
    pass


class ApprovalInvalid(PolicyError):
    """The token was not issued by this policy (bad signature)."""


class ApprovalMismatch(PolicyError):
    """The token approves a different plan than the one being executed."""


class ApprovalExpired(PolicyError):
    pass


class ApprovalAlreadyUsed(PolicyError):
    pass


@dataclass(frozen=True)
class ApprovalToken:
    plan_hash: str
    approver: str
    expires_at: float
    signature: str


DEFAULT_APPROVAL_REQUIRED = frozenset(
    {RiskLevel.WRITE_REVERSIBLE, RiskLevel.WRITE_IRREVERSIBLE, RiskLevel.EXTERNAL_NOTIFY}
)


class Policy:
    def __init__(
        self,
        approval_required: AbstractSet[RiskLevel] = DEFAULT_APPROVAL_REQUIRED,
        secret: Optional[bytes] = None,
        clock: Callable[[], float] = time.time,
    ):
        self.approval_required = frozenset(approval_required)
        self._secret = secret or os.urandom(32)
        self._clock = clock
        self._spent: Set[str] = set()

    def requires_dry_run(self, plan: Plan, registry: ToolRegistry) -> bool:
        return any(registry.get(c.tool).risk.is_write for c in plan.calls)

    def requires_approval(self, plan: Plan, registry: ToolRegistry) -> bool:
        return any(registry.get(c.tool).risk in self.approval_required for c in plan.calls)

    def issue_token(self, plan: Plan, approver: str, ttl_seconds: float = 300.0) -> ApprovalToken:
        """The human-side action. The agent is never handed this method."""
        unsigned = ApprovalToken(plan.hash, approver, self._clock() + ttl_seconds, "")
        return replace(unsigned, signature=self._sign(unsigned))

    def authorize(
        self,
        plan: Plan,
        registry: ToolRegistry,
        dry_run_hashes: AbstractSet[str],
        token: Optional[ApprovalToken],
    ) -> None:
        """Raise a PolicyError unless the plan may run now. Consumes the token on success."""
        current = plan.hash
        if self.requires_dry_run(plan, registry) and current not in dry_run_hashes:
            raise DryRunRequired("plan %s has no clean dry run" % current[:12])
        if not self.requires_approval(plan, registry):
            return
        if token is None:
            raise ApprovalRequired("plan %s requires approval" % current[:12])
        if not hmac.compare_digest(token.signature, self._sign(token)):
            raise ApprovalInvalid("approval token signature is not valid")
        if token.plan_hash != current:
            raise ApprovalMismatch(
                "token approves %s but plan is %s" % (token.plan_hash[:12], current[:12])
            )
        if self._clock() >= token.expires_at:
            raise ApprovalExpired("approval token has expired")
        if token.signature in self._spent:
            raise ApprovalAlreadyUsed("approval token was already used")
        self._spent.add(token.signature)

    def _sign(self, token: ApprovalToken) -> str:
        message = "%s|%s|%r" % (token.plan_hash, token.approver, token.expires_at)
        return hmac.new(self._secret, message.encode("utf-8"), hashlib.sha256).hexdigest()
