import unittest
from dataclasses import replace

from helpers import FakeClock, KV_TOOLS
from agent_tool_safety import Plan, Policy, RiskLevel, ToolCall
from agent_tool_safety.policy import (
    ApprovalAlreadyUsed,
    ApprovalExpired,
    ApprovalInvalid,
    ApprovalMismatch,
    ApprovalRequired,
    DryRunRequired,
)

SET_A = Plan((ToolCall("kv.set", {"key": "a", "value": 1}),))
SET_B = Plan((ToolCall("kv.set", {"key": "a", "value": 2}),))
READ = Plan((ToolCall("kv.get", {"key": "a"}),))
DROP = Plan((ToolCall("kv.drop", {"key": "a"}),))


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.policy = Policy(clock=self.clock)

    def authorize(self, plan, dry=(), token=None, policy=None):
        (policy or self.policy).authorize(plan, KV_TOOLS, set(dry), token)

    def test_read_only_plan_needs_no_dry_run_or_approval(self):
        self.authorize(READ)

    def test_write_without_dry_run_is_refused(self):
        with self.assertRaises(DryRunRequired):
            self.authorize(SET_A)

    def test_write_with_dry_run_but_no_token_is_refused(self):
        with self.assertRaises(ApprovalRequired):
            self.authorize(SET_A, dry=[SET_A.hash])

    def test_valid_token_authorizes(self):
        token = self.policy.issue_token(SET_A, "reviewer")
        self.authorize(SET_A, dry=[SET_A.hash], token=token)

    def test_token_for_a_different_plan_is_a_mismatch(self):
        token = self.policy.issue_token(SET_A, "reviewer")
        with self.assertRaises(ApprovalMismatch):
            self.authorize(SET_B, dry=[SET_B.hash], token=token)

    def test_editing_a_token_to_match_another_plan_breaks_the_signature(self):
        token = replace(self.policy.issue_token(SET_A, "reviewer"), plan_hash=SET_B.hash)
        with self.assertRaises(ApprovalInvalid):
            self.authorize(SET_B, dry=[SET_B.hash], token=token)

    def test_extending_a_token_expiry_breaks_the_signature(self):
        token = self.policy.issue_token(SET_A, "reviewer", ttl_seconds=1)
        with self.assertRaises(ApprovalInvalid):
            self.authorize(SET_A, dry=[SET_A.hash], token=replace(token, expires_at=token.expires_at + 9999))

    def test_token_from_another_policy_is_invalid(self):
        token = Policy(clock=self.clock).issue_token(SET_A, "reviewer")
        with self.assertRaises(ApprovalInvalid):
            self.authorize(SET_A, dry=[SET_A.hash], token=token)

    def test_expired_token_is_refused(self):
        token = self.policy.issue_token(SET_A, "reviewer", ttl_seconds=5)
        self.clock.now += 60
        with self.assertRaises(ApprovalExpired):
            self.authorize(SET_A, dry=[SET_A.hash], token=token)

    def test_token_is_single_use(self):
        token = self.policy.issue_token(SET_A, "reviewer")
        self.authorize(SET_A, dry=[SET_A.hash], token=token)
        with self.assertRaises(ApprovalAlreadyUsed):
            self.authorize(SET_A, dry=[SET_A.hash], token=token)

    def test_failed_authorization_does_not_spend_the_token(self):
        token = self.policy.issue_token(SET_A, "reviewer")
        with self.assertRaises(DryRunRequired):
            self.authorize(SET_A, token=token)
        self.authorize(SET_A, dry=[SET_A.hash], token=token)

    def test_irreversible_and_notify_calls_need_approval_by_default(self):
        for name, args in (("kv.drop", {"key": "a"}), ("msg.send", {"text": "hi"})):
            plan = Plan((ToolCall(name, args),))
            with self.assertRaises(ApprovalRequired):
                self.authorize(plan, dry=[plan.hash])

    def test_one_risky_call_makes_the_whole_plan_need_approval(self):
        mixed = Plan((ToolCall("kv.get", {"key": "a"}), ToolCall("kv.drop", {"key": "a"})))
        with self.assertRaises(ApprovalRequired):
            self.authorize(mixed, dry=[mixed.hash])

    def test_relaxed_policy_skips_approval_but_never_the_dry_run(self):
        relaxed = Policy(approval_required={RiskLevel.WRITE_IRREVERSIBLE}, clock=self.clock)
        with self.assertRaises(DryRunRequired):
            self.authorize(SET_A, policy=relaxed)
        self.authorize(SET_A, dry=[SET_A.hash], policy=relaxed)
        with self.assertRaises(ApprovalRequired):
            self.authorize(DROP, dry=[DROP.hash], policy=relaxed)


if __name__ == "__main__":
    unittest.main()
