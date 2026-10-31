import unittest

from helpers import KV_TOOLS
from agent_tool_safety import Outcome, RiskLevel, ToolRegistry, tool
from agent_tool_safety.risk import ToolDefinitionError, ToolSpec, UnknownTool


class RegistryTests(unittest.TestCase):
    def test_lookup_returns_registered_spec(self):
        spec = KV_TOOLS.get("kv.set")
        self.assertEqual(spec.risk, RiskLevel.WRITE_REVERSIBLE)

    def test_unknown_tool_raises(self):
        with self.assertRaises(UnknownTool):
            KV_TOOLS.get("kv.nope")

    def test_duplicate_name_rejected(self):
        registry = ToolRegistry()
        tool(registry, "a", RiskLevel.READ)(lambda ctx, args: Outcome({}))
        with self.assertRaises(ToolDefinitionError):
            tool(registry, "a", RiskLevel.READ)(lambda ctx, args: Outcome({}))

    def test_describe_lists_name_risk_and_description(self):
        listing = {d["name"]: d for d in KV_TOOLS.describe()}
        self.assertEqual(listing["kv.drop"]["risk"], "write_irreversible")
        self.assertEqual(listing["msg.send"]["risk"], "external_notify")
        self.assertTrue(listing["kv.get"]["description"])

    def test_description_falls_back_to_docstring(self):
        registry = ToolRegistry()

        @tool(registry, "t", RiskLevel.READ)
        def fn(ctx, args):
            """First line.

            More detail."""
            return Outcome({})

        self.assertEqual(registry.get("t").description, "First line.")

    def test_decorated_tool_stays_callable(self):
        self.assertEqual(KV_TOOLS.get("kv.get")({"k": 1}, {"key": "k"}).result, {"value": 1})


class ValidationTests(unittest.TestCase):
    def test_write_tool_without_preview_is_unusable(self):
        spec = ToolSpec("w", RiskLevel.WRITE_IRREVERSIBLE, "", lambda c, a: Outcome({}))
        with self.assertRaises(ToolDefinitionError):
            spec.validate()

    def test_reversible_tool_without_undo_is_unusable(self):
        spec = ToolSpec("w", RiskLevel.WRITE_REVERSIBLE, "", lambda c, a: Outcome({}))
        spec.preview(lambda c, a: None)
        with self.assertRaises(ToolDefinitionError):
            spec.validate()

    def test_irreversible_tool_needs_no_undo(self):
        KV_TOOLS.get("kv.drop").validate()

    def test_read_tool_needs_neither(self):
        KV_TOOLS.get("kv.get").validate()

    def test_only_reads_are_not_writes(self):
        self.assertFalse(RiskLevel.READ.is_write)
        self.assertTrue(all(r.is_write for r in RiskLevel if r is not RiskLevel.READ))


if __name__ == "__main__":
    unittest.main()
