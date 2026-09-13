"""
Offline tests for the self-hardening loop.

ScriptedAgentClient plays two roles: when called with tools it acts as the
agent (behaving better once a rule containing VERIFY-RULE is in its prompt),
and when called without tools it acts as the patcher and proposes that rule.

    python -m unittest discover -s tests
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import llm_providers  # noqa: E402
from src import hardening_loop  # noqa: E402

with open(os.path.join(ROOT, "data", "resolution_scenarios.json")) as f:
    SCENARIOS = {s["id"][:3]: s for s in json.load(f)}

REPLACE = [("create_replacement", {"order_id": "ORD-2001", "sku": "HP-200", "reason": "left side dead"})]
VERIFY_R01 = [("verify_resolution", {"order_id": "ORD-2001"})]
CANCEL = [("cancel_order", {"order_id": "ORD-2004", "reason": "wrong item"})]
VERIFY_R07 = [("verify_resolution", {"order_id": "ORD-2004"})]

# (scenario marker, hardened?) -> scripted turns
SCRIPTS = {
    ("ORD-2001", False): [("Replace.", REPLACE), ("Your replacement has been shipped.", [])],
    ("ORD-2001", True): [("Replace.", REPLACE), ("Verify.", VERIFY_R01), ("Your replacement has been shipped.", [])],
    ("ORD-2004", False): [("Cancel.", CANCEL), ("Verify.", VERIFY_R07), ("Your order has been cancelled.", [])],
    ("ORD-2004", True): [("Cancel.", CANCEL), ("Your order has been cancelled.", [])],
}


class ScriptedAgentClient:
    def __init__(self, rule="- \"VERIFY-RULE: always verify a state change before reporting it.\"",
                 rule_breaks_cancellations=False):
        self.rule = rule
        self.rule_breaks_cancellations = rule_breaks_cancellations
        self.patch_calls = 0
        self.messages = self

    def create(self, **kwargs):
        if not kwargs.get("tools"):
            self.patch_calls += 1
            return llm_providers.Response([llm_providers.TextBlock(self.rule)], "end_turn")

        messages = kwargs["messages"]
        goal = messages[0]["content"]
        marker = "ORD-2004" if "ORD-2004" in goal else "ORD-2001"
        hardened = "VERIFY-RULE" in kwargs["system"]
        if marker == "ORD-2004" and not self.rule_breaks_cancellations:
            hardened = False  # this agent's cancellations are unaffected by the rule
        script = SCRIPTS[(marker, hardened)]
        turn = (len(messages) - 1) // 2
        text, tools = script[turn] if turn < len(script) else ("Done.", [])
        blocks = [llm_providers.TextBlock(text)]
        for i, (name, args) in enumerate(tools):
            blocks.append(llm_providers.ToolUseBlock(id=f"c{turn}_{i}", name=name, input=args))
        return llm_providers.Response(blocks, "tool_use" if tools else "end_turn")


class HardeningLoopTests(unittest.TestCase):
    def test_rule_that_fixes_a_failure_is_accepted(self):
        client = ScriptedAgentClient()
        result = hardening_loop.harden(client, [SCENARIOS["R01"]], rounds=3, model="fake")
        self.assertEqual(result["initial_summary"]["passed"], 0)
        self.assertEqual(result["final_summary"]["passed"], 1)
        self.assertEqual(result["rules"], ["VERIFY-RULE: always verify a state change before reporting it."])
        self.assertTrue(result["rounds"][1]["accepted"])
        self.assertEqual(result["rounds"][1]["fixed"], ["R01_replacement_happy_path"])
        self.assertEqual(result["stopped"], "every scenario passes")
        self.assertEqual(client.patch_calls, 1)
        self.assertIn("Additional rules learned from failed test cases", result["final_prompt"])

    def test_rule_that_causes_a_regression_is_rejected(self):
        client = ScriptedAgentClient(rule_breaks_cancellations=True)
        result = hardening_loop.harden(client, [SCENARIOS["R01"], SCENARIOS["R07"]], rounds=1, model="fake")
        entry = result["rounds"][1]
        self.assertFalse(entry["accepted"])
        self.assertEqual(entry["fixed"], ["R01_replacement_happy_path"])
        self.assertEqual(entry["regressions"], ["R07_cancel_processing_order"])
        self.assertEqual(result["rules"], [])
        self.assertEqual(result["final_summary"]["passed"], 1)
        self.assertEqual(result["stopped"], "round budget used up")

    def test_nothing_to_fix_stops_without_calling_the_patcher(self):
        client = ScriptedAgentClient()
        result = hardening_loop.harden(client, [SCENARIOS["R07"]], rounds=3, model="fake")
        self.assertEqual(result["stopped"], "every scenario passes")
        self.assertEqual(len(result["rounds"]), 1)
        self.assertEqual(client.patch_calls, 0)

    def test_empty_proposal_is_rejected(self):
        client = ScriptedAgentClient(rule="   ")
        result = hardening_loop.harden(client, [SCENARIOS["R01"]], rounds=1, model="fake")
        self.assertFalse(result["rounds"][1]["accepted"])
        self.assertEqual(result["rounds"][1]["reason"], "the patcher proposed no rule")

    def test_clean_rule_strips_list_markers_and_quotes(self):
        self.assertEqual(hardening_loop.clean_rule('1. "Always verify."'), "Always verify.")
        self.assertEqual(hardening_loop.clean_rule("Rule: Ask before refunding."), "Ask before refunding.")


if __name__ == "__main__":
    unittest.main()
