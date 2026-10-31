import unittest

from agent_tool_safety import Plan, ToolCall


def plan(*calls, goal=""):
    return Plan(tuple(calls), goal=goal)


class PlanHashTests(unittest.TestCase):
    def test_hash_ignores_dict_key_order(self):
        a = plan(ToolCall("t", {"x": 1, "y": {"b": 2, "a": 3}}))
        b = plan(ToolCall("t", {"y": {"a": 3, "b": 2}, "x": 1}))
        self.assertEqual(a.hash, b.hash)

    def test_hash_changes_with_args(self):
        self.assertNotEqual(plan(ToolCall("t", {"x": 1})).hash, plan(ToolCall("t", {"x": 2})).hash)

    def test_hash_changes_with_call_order(self):
        one, two = ToolCall("a", {}), ToolCall("b", {})
        self.assertNotEqual(plan(one, two).hash, plan(two, one).hash)

    def test_hash_changes_with_tool_name_and_goal(self):
        self.assertNotEqual(plan(ToolCall("a")).hash, plan(ToolCall("b")).hash)
        self.assertNotEqual(plan(ToolCall("a"), goal="g1").hash, plan(ToolCall("a"), goal="g2").hash)

    def test_hash_is_sha256_hex(self):
        h = plan(ToolCall("a")).hash
        self.assertEqual(len(h), 64)
        int(h, 16)

    def test_mutating_args_after_creation_changes_the_hash(self):
        p = plan(ToolCall("t", {"x": 1}))
        before = p.hash
        p.calls[0].args["x"] = 2
        self.assertNotEqual(before, p.hash)


if __name__ == "__main__":
    unittest.main()
