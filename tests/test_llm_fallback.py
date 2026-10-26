"""
Offline tests for the provider fallback chain.

    python -m unittest discover -s tests
"""

import json
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import llm_providers  # noqa: E402
from src import resolution_agent  # noqa: E402

with open(os.path.join(ROOT, "data", "resolution_scenarios.json")) as f:
    R01 = next(s for s in json.load(f) if s["id"].startswith("R01"))


class FakeProvider:
    """Fails with the given status codes in order, then answers."""

    def __init__(self, failures=(), text="ok"):
        self.failures = list(failures)
        self.text = text
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        if self.failures:
            raise llm_providers.ProviderError("boom", status_code=self.failures.pop(0))
        return llm_providers.Response([llm_providers.TextBlock(self.text)], "end_turn")


class FallbackChainTests(unittest.TestCase):
    def test_moves_to_next_provider_on_rate_limit(self):
        primary, backup = FakeProvider([429]), FakeProvider(text="from backup")
        client = llm_providers.FallbackClient([("groq:a", primary), ("gemini:b", backup)])
        response = client.messages.create(model="x", messages=[])
        self.assertEqual(response.content[0].text, "from backup")
        self.assertEqual(client.last_provider, "gemini:b")

    def test_rate_limited_provider_rests_during_cooldown(self):
        primary, backup = FakeProvider([429, 429]), FakeProvider()
        client = llm_providers.FallbackClient([("groq:a", primary), ("gemini:b", backup)], cooldown_s=60)
        client.messages.create(model="x", messages=[])
        client.messages.create(model="x", messages=[])
        self.assertEqual(primary.calls, 1, "a rate-limited provider should be skipped while resting")
        self.assertEqual(backup.calls, 2)

    def test_outage_does_not_trigger_cooldown(self):
        primary, backup = FakeProvider([502]), FakeProvider()
        client = llm_providers.FallbackClient([("groq:a", primary), ("gemini:b", backup)], cooldown_s=60)
        client.messages.create(model="x", messages=[])
        client.messages.create(model="x", messages=[])
        self.assertEqual(primary.calls, 2)
        self.assertEqual(client.last_provider, "groq:a")

    def test_all_failing_raises_one_combined_error(self):
        client = llm_providers.FallbackClient([("groq:a", FakeProvider([429])), ("gemini:b", FakeProvider([429]))])
        with self.assertRaises(llm_providers.ProviderError) as ctx:
            client.messages.create(model="x", messages=[])
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("groq:a", ctx.exception.message)
        self.assertIn("gemini:b", ctx.exception.message)

    def test_backup_always_uses_its_own_model(self):
        sent = {}

        def complete(**request):
            sent.update(request)
            return NS(choices=[NS(message=NS(content="hi", tool_calls=None))])

        fake_openai = NS(chat=NS(completions=NS(create=complete)))
        messages = llm_providers._Messages(fake_openai, "default-model", force_model="gemini-3.5-flash")
        messages.create(model="openai/gpt-oss-120b", messages=[{"role": "user", "content": "x"}])
        self.assertEqual(sent["model"], "gemini-3.5-flash")


class ThoughtSignatureTests(unittest.TestCase):
    def history(self, call_id):
        return [{"role": "user", "content": "Where is ORD-2001?"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": call_id, "name": "get_order",
                                                   "input": {"order_id": "ORD-2001"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "{}"}]}]

    def test_signature_is_sent_back_with_the_tool_call(self):
        signed = {"google": {"thought_signature": "sig-123"}}
        call = NS(id="call-signed", function=NS(name="get_order", arguments='{"order_id": "ORD-2001"}'),
                  model_extra={"extra_content": signed})
        llm_providers._response_from_openai(NS(choices=[NS(message=NS(content=None, tool_calls=[call]))]))
        sent = llm_providers._messages_to_openai(None, self.history("call-signed"), "gemini")
        self.assertEqual(sent[1]["tool_calls"][0]["extra_content"], signed)

    def test_calls_from_another_provider_get_the_placeholder_only_on_gemini(self):
        to_gemini = llm_providers._messages_to_openai(None, self.history("call-from-groq"), "gemini")
        to_groq = llm_providers._messages_to_openai(None, self.history("call-from-groq"), "groq")
        self.assertEqual(to_gemini[1]["tool_calls"][0]["extra_content"], llm_providers._GEMINI_UNSIGNED)
        self.assertNotIn("extra_content", to_groq[1]["tool_calls"][0])


PROVIDER_VARS = ("LLM_PROVIDER", "LLM_FALLBACKS", "LLM_BASE_URL", "LLM_API_KEY", "AGENT_MODEL", "JUDGE_MODEL",
                 "GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")


def provider_env(**values):
    """Patch only the provider settings; Windows needs the rest of the environment for SSL."""
    patcher = mock.patch.dict(os.environ, values)
    patcher.start()
    for name in PROVIDER_VARS:
        if name not in values:
            os.environ.pop(name, None)
    return patcher


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.patcher = None

    def tearDown(self):
        if self.patcher:
            self.patcher.stop()

    def test_get_client_builds_chain_and_skips_backups_without_keys(self):
        self.patcher = provider_env(LLM_PROVIDER="groq", GROQ_API_KEY="k1", GEMINI_API_KEY="k2",
                                    LLM_FALLBACKS="gemini, openrouter, groq:openai/gpt-oss-20b, nonsense")
        client = llm_providers.get_client()
        self.assertIsInstance(client, llm_providers.FallbackClient)
        self.assertEqual(client.providers,
                         ["groq:openai/gpt-oss-120b", "gemini:gemini-3.5-flash", "groq:openai/gpt-oss-20b"])
        self.assertEqual(llm_providers.describe()["fallbacks"],
                         ["gemini:gemini-3.5-flash", "groq:openai/gpt-oss-20b"])

    def test_without_fallbacks_the_client_is_unchanged(self):
        self.patcher = provider_env(LLM_PROVIDER="groq", GROQ_API_KEY="k1")
        self.assertIsInstance(llm_providers.get_client(), llm_providers.OpenAICompatClient)
        self.assertEqual(llm_providers.describe()["fallbacks"], [])


class TraceRecordsProviderTests(unittest.TestCase):
    def test_trace_names_the_provider_that_answered(self):
        primary, backup = FakeProvider([429]), FakeProvider(text="Your replacement is being looked into.")
        client = llm_providers.FallbackClient([("groq:a", primary), ("gemini:b", backup)])
        trace = resolution_agent.run_case(client, R01, "v2_verified", model="x")
        self.assertEqual(trace["providers"], ["gemini:b"])


if __name__ == "__main__":
    unittest.main()
