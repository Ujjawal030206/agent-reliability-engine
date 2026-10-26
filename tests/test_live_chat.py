"""
Offline tests for the live customer chat: a scripted model stands in for the
LLM, so the pause-for-the-customer flow, consent and verification are tested
without a provider key.

    python -m unittest discover -s tests
"""

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import llm_providers  # noqa: E402
from src import live_chat, shop_env  # noqa: E402


class ScriptedAgent:
    """Plays back ("tool", name, args) or ("reply", text) turns in order."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        turn = self.turns.pop(0)
        if turn[0] == "reply":
            return llm_providers.Response([llm_providers.TextBlock(turn[1])], "end_turn")
        _, name, args = turn
        return llm_providers.Response([llm_providers.TextBlock(f"Next: {name}."),
                                       llm_providers.ToolUseBlock(f"call_{self.calls}", name, args)], "tool_use")


def settle(chat, timeout=5):
    deadline = time.time() + timeout
    while chat.status == "working" and time.time() < deadline:
        time.sleep(0.01)
    return chat.snapshot()


OUT_OF_STOCK_THEN_REFUND = [
    ("tool", "check_eligibility", {"order_id": "ORD-2002", "resolution": "replacement"}),
    ("tool", "ask_customer", {"question_type": "accept_refund_instead",
                              "message": "The keyboard is out of stock. Would a full refund of INR 1799 work?"}),
    ("tool", "issue_refund", {"order_id": "ORD-2002", "amount": 1799, "reason": "defective",
                              "idempotency_key": "ORD-2002-r1"}),
    ("tool", "verify_resolution", {"order_id": "ORD-2002"}),
    ("reply", "Your refund of INR 1799 has been issued to your original payment method."),
]


class LiveChatTests(unittest.TestCase):
    def start(self, turns, disruptions=()):
        self.agent = ScriptedAgent(turns)
        return live_chat.ChatStore().create(self.agent, "cust_88", list(disruptions), "v2_verified", model="scripted")

    def test_agent_waits_for_the_customer_then_finishes(self):
        chat = self.start(OUT_OF_STOCK_THEN_REFUND)
        chat.send("The keys on my keyboard from ORD-2002 stopped working. Please replace it.")
        snap = settle(chat)
        self.assertEqual(snap["status"], "waiting_for_customer")
        self.assertEqual(snap["transcript"][-1]["kind"], "question")
        self.assertIn("out of stock", snap["transcript"][-1]["text"])
        self.assertEqual(snap["transcript"][0]["consents"], ["replacement"])
        self.assertEqual(len(snap["steps"]), 1, "the question is only recorded once it is answered")

        chat.send("Yes, that's fine.")
        snap = settle(chat)
        self.assertEqual(snap["transcript"][-2]["consents"], ["refund"])
        self.assertEqual(snap["transcript"][-1]["kind"], "reply")
        self.assertEqual([s["tool"] for s in snap["steps"]],
                         ["check_eligibility", "ask_customer", "issue_refund", "verify_resolution"])
        self.assertEqual(snap["steps"][1]["result"], {"customer_reply": "Yes, that's fine."})
        self.assertEqual(snap["verification"]["verdict"], "pass", snap["verification"]["findings"])
        self.assertEqual(snap["final_state"]["refunds"][0]["amount"], 1799)

    def test_refund_without_the_customers_agreement_is_flagged(self):
        chat = self.start([
            ("tool", "issue_refund", {"order_id": "ORD-2002", "amount": 1799, "reason": "broken",
                                      "idempotency_key": "k1"}),
            ("tool", "verify_resolution", {"order_id": "ORD-2002"}),
            ("reply", "I have refunded INR 1799."),
        ])
        chat.send("My keyboard ORD-2002 is broken, I want it replaced.")
        snap = settle(chat)
        self.assertIn("action_without_customer_consent", snap["verification"]["failure_modes"])

    def test_declining_the_offer_grants_nothing(self):
        chat = self.start(OUT_OF_STOCK_THEN_REFUND[:2] + [("reply", "Understood, I won't refund it.")])
        chat.send("Please replace my keyboard from ORD-2002.")
        settle(chat)
        chat.send("No, I don't want a refund.")
        snap = settle(chat)
        self.assertEqual(snap["consents"], ["replacement"])
        self.assertEqual(snap["verification"]["verdict"], "pass")

    def test_disruptions_are_injected_into_the_sandbox(self):
        chat = self.start([("tool", "check_inventory", {"sku": "HP-200"}),
                           ("tool", "check_inventory", {"sku": "HP-200"}),
                           ("reply", "Checked.")], disruptions=["sell_out"])
        chat.send("Is HP-200 in stock?")
        snap = settle(chat)
        self.assertTrue(snap["steps"][0]["result"]["in_stock"])
        self.assertFalse(snap["steps"][1]["result"]["in_stock"], "everything sold out after the first check")

    def test_provider_error_keeps_the_chat_usable(self):
        class Down:
            messages = None

            def __init__(self):
                self.messages = self

            def create(self, **kwargs):
                raise llm_providers.ProviderError("rate limited", status_code=429)

        chat = live_chat.ChatStore().create(Down(), "cust_88", [], "v2_verified", model="scripted")
        chat.send("Hello?")
        snap = settle(chat)
        self.assertEqual(snap["status"], "error")
        self.assertIn("rate limited", snap["error"])
        chat.client = chat.session.client = ScriptedAgent([("reply", "Hi, how can I help?")])
        chat.send("Hello again?")
        snap = settle(chat)
        self.assertEqual(snap["transcript"][-1]["text"], "Hi, how can I help?")
        self.assertEqual(chat.session.messages[0]["content"], "Hello?\n\nHello again?")

    def test_rejects_unknown_customer_and_disruption(self):
        store = live_chat.ChatStore()
        with self.assertRaises(ValueError):
            store.create(ScriptedAgent([]), "cust_00", [], "v2_verified")
        with self.assertRaises(ValueError):
            store.create(ScriptedAgent([]), "cust_88", ["meteor"], "v2_verified")


class ConsentParsingTests(unittest.TestCase):
    def test_requested_resolutions(self):
        cases = {
            "My headphones broke, I want a replacement": {"replacement"},
            "I don't want a refund, I need this exact keyboard": set(),
            "Please cancel ORD-2004": {"cancellation"},
            "I want my money back but not a replacement": {"refund"},
            "The left side is not working, help": set(),
        }
        for text, expected in cases.items():
            self.assertEqual(shop_env.requested_resolutions(text), expected, text)

    def test_answers_to_an_offer(self):
        self.assertEqual(shop_env.consents_from_answer("accept_refund_instead", "Yes, that works"), {"refund"})
        self.assertEqual(shop_env.consents_from_answer("accept_refund_instead", "No thanks"), set())
        self.assertEqual(shop_env.consents_from_answer("accept_replacement_instead", "okay go ahead"), {"replacement"})


if __name__ == "__main__":
    unittest.main()
