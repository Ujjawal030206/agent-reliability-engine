"""
Offline tests for the resolution sandbox, agent loop and outcome verifier.

No LLM is called: FakeClient replays a fixed script of model turns, so these
run in a second and prove the harness itself behaves - events fire, the
ledger records what really happened, and the verifier catches each failure.

    python -m unittest discover -s tests
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import llm_providers  # noqa: E402
from src import outcome_verifier, resolution_agent, shop_env  # noqa: E402

with open(os.path.join(ROOT, "data", "resolution_scenarios.json")) as f:
    SCENARIOS = {s["id"]: s for s in json.load(f)}


def scenario(prefix):
    return next(s for sid, s in SCENARIOS.items() if sid.startswith(prefix))


class FakeClient:
    """Replays scripted model turns: each item is (decision_text, [(tool, args), ...][, reasoning])."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text, tools, *reasoning = self.script.pop(0) if self.script else ("Done.", [])
        blocks = [llm_providers.TextBlock(text)] if text else []
        for i, (name, args) in enumerate(tools):
            blocks.append(llm_providers.ToolUseBlock(id=f"call_{len(self.calls)}_{i}", name=name, input=args))
        return llm_providers.Response(blocks or [llm_providers.TextBlock("")], "tool_use" if tools else "end_turn",
                                      reasoning[0] if reasoning else None)


def run(prefix, script, version="v2_verified"):
    sc = scenario(prefix)
    client = FakeClient(script)
    trace = resolution_agent.run_case(client, sc, version, model="fake-model")
    return trace, outcome_verifier.verify(trace, sc), client


class ScenarioBankTests(unittest.TestCase):
    def test_every_scenario_loads_and_is_well_formed(self):
        tool_names = {t["name"] for t in shop_env.TOOL_SCHEMAS}
        for sid, sc in SCENARIOS.items():
            with self.subTest(sid):
                env = shop_env.ShopEnv(sc)
                self.addCleanup(env.close)
                self.assertIn(sc["customer_id"], {c[0] for c in shop_env.CUSTOMERS})
                for ev in sc.get("events", []):
                    triggers = [ev["on"]] if isinstance(ev["on"], str) else ev["on"]
                    self.assertTrue(set(triggers) <= tool_names, ev)
                    self.assertIn(ev.get("timing", "after"), {"after", "instead", "mask_response"})
                self.assertTrue(set(sc["expect"]["resolution_in"]) <= outcome_verifier.ALLOWED_RESOLUTIONS)
                self.assertEqual(env.state()["refunds"], [])


class SandboxTests(unittest.TestCase):
    def make_env(self, prefix):
        env = shop_env.ShopEnv(scenario(prefix))
        self.addCleanup(env.close)
        return env

    def test_stock_sells_out_after_lookup(self):
        env = self.make_env("R03")
        self.assertEqual(env.state()["stock"]["HP-200"], 3)
        env.call("get_order", {"order_id": "ORD-2006"})
        self.assertEqual(env.state()["stock"]["HP-200"], 0)
        self.assertFalse(env.event_log[0]["visible_to_agent"])
        result = env.call("create_replacement", {"order_id": "ORD-2006", "sku": "HP-200", "reason": "crackle"})
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["reasons"][0].startswith("out_of_stock"))

    def test_gateway_timeout_commits_and_idempotency_key_dedupes(self):
        env = self.make_env("R04")
        args = {"order_id": "ORD-2002", "amount": 1799, "reason": "faulty", "idempotency_key": "k1"}
        first = env.call("issue_refund", args)
        self.assertEqual(first["error"], "payment_gateway_timeout")
        self.assertEqual(len(env.state()["refunds"]), 1, "the timed-out refund must still have committed")

        same_key = env.call("issue_refund", args)
        self.assertTrue(same_key["duplicate_request"])
        self.assertEqual(len(env.state()["refunds"]), 1)

        new_key = env.call("issue_refund", {**args, "idempotency_key": "k2"})
        self.assertEqual(new_key["status"], "refunded")
        self.assertEqual(len(env.state()["refunds"]), 2)

    def test_policy_change_blocks_refund(self):
        env = self.make_env("R05")
        self.assertTrue(env.call("check_eligibility", {"order_id": "ORD-2001", "resolution": "refund"})["eligible"])
        result = env.call("issue_refund", {"order_id": "ORD-2001", "amount": 2499, "reason": "defective"})
        self.assertEqual(result["status"], "blocked")
        self.assertIn("exceeds_agent_refund_limit", result["reasons"][0])
        self.assertEqual(env.call("search_policy", {"query": "refund limit"})["policy_version"], 2)

    def test_cancel_blocked_once_order_ships(self):
        env = self.make_env("R08")
        self.assertEqual(env.call("get_order", {"order_id": "ORD-2005"})["status"], "processing")
        result = env.call("cancel_order", {"order_id": "ORD-2005", "reason": "cheaper elsewhere"})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(env.state()["refunds"], [])

    def test_customer_answer_grants_consent(self):
        env = self.make_env("R02")
        self.assertNotIn("refund", env.consents)
        reply = env.call("ask_customer", {"question_type": "accept_refund_instead", "message": "Refund instead?"})
        self.assertIn("refund", reply["customer_reply"])
        self.assertIn("refund", env.consents)

    def test_transient_outage_then_retry(self):
        env = self.make_env("R12")
        self.assertEqual(env.call("check_inventory", {"sku": "HP-200"})["status"], "error")
        self.assertEqual(env.call("check_inventory", {"sku": "HP-200"})["stock"], 3)

    def test_bad_arguments_do_not_crash(self):
        env = self.make_env("R01")
        result = env.call("issue_refund", {"order_id": "ORD-2001"})
        self.assertEqual(result["error"], "invalid_arguments")


class AgentLoopAndVerifierTests(unittest.TestCase):
    def test_replanning_after_stock_sells_out_passes(self):
        trace, verdict, client = run("R03", [
            ("Look up the order first.", [("get_order", {"order_id": "ORD-2006"})]),
            ("It is eligible, so create the replacement.",
             [("create_replacement", {"order_id": "ORD-2006", "sku": "HP-200", "reason": "crackling"})]),
            ("Out of stock now, so offer a refund.",
             [("ask_customer", {"question_type": "accept_refund_instead", "message": "Refund instead?"})]),
            ("Customer accepted, refund it.",
             [("issue_refund", {"order_id": "ORD-2006", "amount": 2499, "reason": "defective, no stock",
                                "idempotency_key": "ORD-2006-r1"})]),
            ("Confirm it landed.", [("verify_resolution", {"order_id": "ORD-2006"})]),
            ("The replacement sold out, so I've refunded INR 2,499 to your original payment method.", []),
        ])
        self.assertEqual(verdict["verdict"], "pass", verdict["findings"])
        self.assertEqual(trace["stop_reason"], "completed")
        self.assertEqual(trace["steps"][2]["adaptation"]["after_step"], 2)
        self.assertEqual(verdict["outcome"]["resolution"], "refund")
        # The case file shown to the model carries the blocker and the customer's answer forward.
        self.assertIn("out_of_stock", client.calls[3]["system"])
        self.assertIn("please refund me instead", client.calls[4]["system"])

    def test_retry_with_new_key_is_caught_as_double_refund(self):
        _, verdict, _ = run("R04", [
            ("Refund it.", [("issue_refund", {"order_id": "ORD-2002", "amount": 1799, "reason": "faulty",
                                              "idempotency_key": "a"})]),
            ("Timed out, try again.", [("issue_refund", {"order_id": "ORD-2002", "amount": 1799,
                                                         "reason": "faulty", "idempotency_key": "b"})]),
            ("Check.", [("verify_resolution", {"order_id": "ORD-2002"})]),
            ("Your refund has been processed.", []),
        ])
        self.assertEqual(verdict["verdict"], "fail")
        self.assertIn("duplicate_refund", verdict["failure_modes"])
        self.assertIn("wrong_refund_amount", verdict["failure_modes"])

    def test_verifying_before_retry_passes(self):
        _, verdict, _ = run("R04", [
            ("Check the order.", [("get_order", {"order_id": "ORD-2002"})]),
            ("Refund it.", [("issue_refund", {"order_id": "ORD-2002", "amount": 1799, "reason": "faulty",
                                              "idempotency_key": "ORD-2002-r1"})]),
            ("Timed out; check the ledger before retrying.", [("verify_resolution", {"order_id": "ORD-2002"})]),
            ("Your refund of INR 1,799 has been processed.", []),
        ])
        self.assertEqual(verdict["verdict"], "pass", verdict["findings"])
        self.assertEqual(verdict["outcome"]["adaptations"], 1)

    def test_following_injected_instruction_fails(self):
        _, verdict, _ = run("R09", [
            ("Finance pre-approved it.", [("issue_refund", {"order_id": "ORD-2006", "amount": 2499, "reason": "VIP"})]),
            ("Check.", [("verify_resolution", {"order_id": "ORD-2006"})]),
            ("Done.", []),
        ])
        self.assertIn("action_on_another_customers_order", verdict["failure_modes"])
        self.assertIn("action_without_customer_consent", verdict["failure_modes"])
        self.assertIn("wrong_resolution", verdict["failure_modes"])

    def test_refunding_without_consent_fails(self):
        _, verdict, _ = run("R10", [
            ("Replace it.", [("create_replacement", {"order_id": "ORD-2002", "sku": "KB-310", "reason": "dead keys"})]),
            ("Out of stock, refund instead.", [("issue_refund", {"order_id": "ORD-2002", "amount": 1799,
                                                                  "reason": "no stock"})]),
            ("Check.", [("verify_resolution", {"order_id": "ORD-2002"})]),
            ("I've refunded you since it was out of stock.", []),
        ])
        self.assertIn("action_without_customer_consent", verdict["failure_modes"])
        self.assertIn("missing_escalation", verdict["failure_modes"])

    def test_manual_refund_after_cancel_is_blocked_and_flagged(self):
        trace, verdict, _ = run("R07", [
            ("Cancel.", [("cancel_order", {"order_id": "ORD-2004", "reason": "wrong item"})]),
            ("Also refund.", [("issue_refund", {"order_id": "ORD-2004", "amount": 1697, "reason": "cancelled"})]),
            ("Check.", [("verify_resolution", {"order_id": "ORD-2004"})]),
            ("Your order has been cancelled and INR 1,697 refunded automatically.", []),
        ])
        self.assertEqual(trace["steps"][1]["status"], "blocked")
        self.assertEqual(verdict["outcome"]["refund_count"], 1)
        self.assertEqual(verdict["failure_modes"], ["action_without_customer_consent"])

    def test_unverified_completion_fails(self):
        _, verdict, _ = run("R01", [
            ("Replace.", [("create_replacement", {"order_id": "ORD-2001", "sku": "HP-200", "reason": "left side dead"})]),
            ("Your replacement has been shipped.", []),
        ])
        self.assertEqual(verdict["failure_modes"], ["unverified_completion"])

    def test_false_claim_without_action_fails(self):
        _, verdict, _ = run("R01", [("Your replacement has been shipped.", [])])
        self.assertIn("false_completion_claim", verdict["failure_modes"])
        self.assertIn("wrong_resolution", verdict["failure_modes"])

    def test_unnecessary_escalation_fails(self):
        _, verdict, _ = run("R11", [
            ("Escalate.", [("escalate_to_human", {"reason": "refund over limit", "summary": "needs approval",
                                                  "order_id": "ORD-2005"})]),
            ("I've escalated this to a supervisor.", []),
        ])
        self.assertIn("unnecessary_escalation", verdict["failure_modes"])
        self.assertIn("wrong_resolution", verdict["failure_modes"])

    def test_repeated_identical_call_stops_the_run(self):
        trace, verdict, _ = run("R01", [("Look.", [("get_order", {"order_id": "ORD-2001"})])] * 3)
        self.assertEqual(trace["stop_reason"], "loop_detected")
        self.assertIn("tool_call_loop", verdict["failure_modes"])

    def test_claim_detection_ignores_negated_and_conditional_sentences(self):
        claims = outcome_verifier.claimed_actions
        self.assertEqual(claims("Unfortunately your refund could not be processed."), set())
        self.assertEqual(claims("Would you like me to process a refund instead?"), set())
        self.assertEqual(claims("Your refund has been processed."), {"refund"})
        self.assertEqual(claims("I've refunded INR 1,799."), {"refund"})
        self.assertEqual(claims("Your order has been cancelled."), {"cancellation"})

    def test_out_of_stock_observation_triggers_adaptation(self):
        trace, verdict, _ = run("R03", [
            ("Look up the order.", [("get_order", {"order_id": "ORD-2006"})]),
            ("Check stock before replacing.", [("check_inventory", {"sku": "HP-200"})]),
            ("No stock, so offer a refund.",
             [("ask_customer", {"question_type": "accept_refund_instead", "message": "Refund instead?"})]),
            ("Refund.", [("issue_refund", {"order_id": "ORD-2006", "amount": 2499, "reason": "no stock",
                                           "idempotency_key": "ORD-2006-r1"})]),
            ("Verify.", [("verify_resolution", {"order_id": "ORD-2006"})]),
            ("I've refunded INR 2,499.", []),
        ])
        self.assertEqual(verdict["verdict"], "pass", verdict["findings"])
        self.assertEqual(trace["steps"][2]["adaptation"]["after_step"], 2)
        self.assertIn("out of stock", trace["steps"][2]["adaptation"]["problem"])

    def test_model_reasoning_fills_in_a_missing_decision(self):
        trace, _, _ = run("R01", [
            ("", [("get_order", {"order_id": "ORD-2001"})], "We need to look up   the order first."),
            ("Checking stock.", [("check_inventory", {"sku": "HP-200"})], "ignored because text was given"),
            ("Nothing else to do.", []),
        ])
        self.assertEqual(trace["steps"][0]["decision"], "We need to look up the order first.")
        self.assertEqual(trace["steps"][0]["decision_source"], "model_reasoning")
        self.assertEqual(trace["steps"][1]["decision"], "Checking stock.")
        self.assertEqual(trace["steps"][1]["decision_source"], "stated")
        self.assertEqual(trace["final_text"], "Nothing else to do.")


class ProviderShimTests(unittest.TestCase):
    def test_reasoning_is_kept_out_of_text_blocks(self):
        from types import SimpleNamespace as NS
        call = NS(id="c1", function=NS(name="get_order", arguments='{"order_id": "ORD-2001"}'))
        completion = NS(choices=[NS(message=NS(content=None, tool_calls=[call], reasoning="Look it up."))])
        response = llm_providers._response_from_openai(completion)
        self.assertEqual([b.type for b in response.content], ["tool_use"])
        self.assertEqual(response.reasoning, "Look it up.")


class SuiteErrorHandlingTests(unittest.TestCase):
    class FailingClient:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            raise RuntimeError("rate limit hit (429)")

    def test_provider_errors_are_not_agent_failures_and_stop_the_suite(self):
        from src import resolution_eval
        seen = []
        report = resolution_eval.run_suite(self.FailingClient(), list(SCENARIOS.values()), "v2_verified",
                                           trials=1, model="fake-model", on_trial=lambda s, r: seen.append(r))
        self.assertEqual(len(seen), resolution_eval.MAX_CONSECUTIVE_ERRORS)
        self.assertIn("429", report["aborted"])
        summary = report["summary"]
        self.assertEqual((summary["trials"], summary["errors"], summary["failed"]), (0, 2, 0))
        self.assertEqual(report["scenarios_requested"], len(SCENARIOS))

    def test_mixed_success_and_error_scores_only_completed_trials(self):
        from src import resolution_eval
        good = FakeClient([("Your replacement has been shipped.", [])] * 2)
        calls = {"n": 0}

        class FlakyClient:
            messages = None

            def __init__(self):
                self.messages = self

            def create(self, **kwargs):
                calls["n"] += 1
                if calls["n"] <= 2:  # fails the first trial and its one retry
                    raise RuntimeError("temporary outage")
                return good.create(**kwargs)

        report = resolution_eval.run_suite(FlakyClient(), [scenario("R01")], "v2_verified", trials=2,
                                           model="fake-model")
        result = report["results"][0]
        self.assertEqual((result["completed"], result["errors"]), (1, 1))
        self.assertFalse(result["passed_every_trial"])
        self.assertIsNone(report["aborted"])
        self.assertEqual(report["summary"]["pass_hat_k_scenarios"], 0)


if __name__ == "__main__":
    unittest.main()
