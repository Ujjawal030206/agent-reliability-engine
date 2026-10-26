"""
Provider adapter: run the reliability engine on a free LLM API.

Everything in src/ is written against the Anthropic client surface - it calls
`client.messages.create(...)` and reads `response.content` blocks and
`response.stop_reason`. Rather than rewrite that tested logic for a second
provider, this module supplies a drop-in object with the *same* surface,
backed by any OpenAI-compatible chat-completions endpoint (Groq, Google
Gemini's compat endpoint, OpenRouter, Cerebras, Mistral, a local Ollama, ...).

The result is that src/ needs no changes at all: `server.py` hands the engine
either a real `Anthropic` client or this shim, and the harness cannot tell the
difference.

Configure with LLM_PROVIDER in .env:

    LLM_PROVIDER=anthropic   # default; uses ANTHROPIC_API_KEY (paid)
    LLM_PROVIDER=groq        # free tier, needs GROQ_API_KEY
    LLM_PROVIDER=gemini      # free tier, needs GEMINI_API_KEY
    LLM_PROVIDER=openrouter  # free models, needs OPENROUTER_API_KEY
    LLM_PROVIDER=ollama      # fully local, no key, no network
    LLM_PROVIDER=custom      # any OpenAI-compatible endpoint; set LLM_BASE_URL

Tool calling is required - the target agent under test is a tool-use agent.
Pick a model that supports it (the defaults below all do).

Optional backups, tried in order when the main provider is rate-limited or
down (each needs its own key; pin a model with name:model):

    LLM_FALLBACKS=gemini,groq:openai/gpt-oss-20b
"""

import os
import json
import time
import uuid

# Preset endpoints + a tool-calling-capable default model for each provider.
PRESETS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "agent_model": "openai/gpt-oss-120b",
        "judge_model": "openai/gpt-oss-120b",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": "GEMINI_API_KEY",
        "agent_model": "gemini-3.5-flash",
        "judge_model": "gemini-3.1-flash-lite",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "agent_model": "meta-llama/llama-3.3-70b-instruct:free",
        "judge_model": "meta-llama/llama-3.3-70b-instruct:free",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "key_env": "CEREBRAS_API_KEY",
        "agent_model": "llama-3.3-70b",
        "judge_model": "llama3.1-8b",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "key_env": "MISTRAL_API_KEY",
        "agent_model": "mistral-large-latest",
        "judge_model": "mistral-small-latest",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "key_env": None,
        "agent_model": "llama3.1",
        "judge_model": "llama3.1",
    },
    "custom": {
        "base_url": None,
        "key_env": "LLM_API_KEY",
        "agent_model": None,
        "judge_model": None,
    },
}


def provider_name() -> str:
    return (os.environ.get("LLM_PROVIDER") or "anthropic").strip().lower()


def _preset() -> dict:
    return PRESETS.get(provider_name(), PRESETS["custom"])


def agent_model() -> str:
    """Model for the target agent under test and the red-team attacker."""
    return os.environ.get("AGENT_MODEL") or _preset().get("agent_model") or "claude-sonnet-5"


def judge_model() -> str:
    """Model for the LLM-as-judge layer and the scenario generator."""
    return os.environ.get("JUDGE_MODEL") or _preset().get("judge_model") or "claude-haiku-4-5-20251001"


def api_key() -> str:
    if provider_name() == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY", "")
    preset = _preset()
    if preset["key_env"] is None:          # e.g. local Ollama needs no key
        return "not-needed"
    return os.environ.get(preset["key_env"]) or os.environ.get("LLM_API_KEY", "")


def is_configured() -> bool:
    return bool(api_key())


def describe() -> dict:
    """Small summary for /api/health, so the frontend can show what's driving the engine."""
    return {
        "provider": provider_name(),
        "agent_model": agent_model(),
        "judge_model": judge_model(),
        "configured": is_configured(),
        "fallbacks": [label for label, _, _ in _fallback_specs() if _key_for(label.split(":")[0])],
    }


# --------------------------------------------------------------------------
# Anthropic-shaped response objects
# --------------------------------------------------------------------------

class TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class ToolUseBlock:
    type = "tool_use"

    def __init__(self, id, name, input):
        self.id = id
        self.name = name
        self.input = input


class Response:
    """Mimics anthropic.types.Message closely enough for everything in src/."""

    def __init__(self, content, stop_reason, reasoning=None):
        self.content = content
        self.stop_reason = stop_reason
        self.reasoning = reasoning


# --------------------------------------------------------------------------
# Translation: Anthropic request shape <-> OpenAI chat-completions shape
# --------------------------------------------------------------------------

def _tools_to_openai(tools):
    """Anthropic {name, description, input_schema} -> OpenAI function tools."""
    out = []
    for t in tools or []:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return out


# Models that emit internal reasoning tokens, which count against max_tokens.
_REASONING_HINTS = ("gpt-oss", "qwen3", "deepseek-r1", "o1-", "o3-", "-thinking")


def _budget_for(model, max_tokens):
    """Scale up max_tokens for reasoning models.

    src/ budgets tokens for non-reasoning models (the judge asks for a verdict
    in 300). A reasoning model spends most of that thinking before it writes a
    character, so the JSON gets truncated and scored as a parse error. Giving
    it headroom fixes the cause; the model still stops when it's done, so this
    costs nothing on shorter replies.
    """
    name = (model or "").lower()
    if any(hint in name for hint in _REASONING_HINTS):
        return max(max_tokens * 4, 2048)
    return max_tokens


# Gemini 3 signs each tool call it makes and rejects the next turn unless the
# signature comes back with it. src/ rebuilds tool_use blocks from id, name and
# input only, so the adapter remembers the signatures by tool-call id.
_TOOL_CALL_EXTRAS = {}
_MAX_REMEMBERED_CALLS = 5000

# Google's documented placeholder for tool calls Gemini did not make itself,
# e.g. a case that failed over to Gemini partway through.
_GEMINI_UNSIGNED = {"google": {"thought_signature": "skip_thought_signature_validator"}}


def _remember_extra(call_id, extra):
    if len(_TOOL_CALL_EXTRAS) >= _MAX_REMEMBERED_CALLS:
        _TOOL_CALL_EXTRAS.pop(next(iter(_TOOL_CALL_EXTRAS)))
    _TOOL_CALL_EXTRAS[call_id] = extra


def _messages_to_openai(system, messages, provider=None):
    """Flatten Anthropic content blocks into the OpenAI message sequence.

    Anthropic carries tool results as blocks inside a *user* message; OpenAI
    wants them as separate `role: "tool"` messages keyed by tool_call_id.
    """
    out = []
    if system:
        out.append({"role": "system", "content": system})

    for m in messages:
        role, content = m["role"], m["content"]

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if role == "assistant":
            text_parts, tool_calls = [], []
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    call = {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    }
                    if block["id"] in _TOOL_CALL_EXTRAS:
                        call["extra_content"] = _TOOL_CALL_EXTRAS[block["id"]]
                    tool_calls.append(call)
            msg = {"role": "assistant", "content": " ".join(text_parts).strip() or None}
            if tool_calls:
                if provider == "gemini" and not any("extra_content" in c for c in tool_calls):
                    tool_calls[0]["extra_content"] = _GEMINI_UNSIGNED
                msg["tool_calls"] = tool_calls
            out.append(msg)
            continue

        # user message: may hold tool_result blocks, plain text, or both
        pending_text = []
        for block in content:
            btype = block.get("type")
            if btype == "tool_result":
                result = block.get("content")
                out.append({
                    "role": "tool",
                    "tool_call_id": block["tool_use_id"],
                    "content": result if isinstance(result, str) else json.dumps(result),
                })
            elif btype == "text":
                pending_text.append(block.get("text", ""))
        if pending_text:
            out.append({"role": "user", "content": " ".join(pending_text)})

    return out


def _response_from_openai(completion):
    """OpenAI completion -> Anthropic-shaped Response."""
    choice = completion.choices[0]
    message = choice.message
    blocks = []

    if getattr(message, "content", None):
        blocks.append(TextBlock(message.content))

    tool_calls = getattr(message, "tool_calls", None) or []
    for call in tool_calls:
        raw = call.function.arguments or "{}"
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # Smaller free models occasionally emit malformed argument JSON.
            # Surface it as an empty call rather than crashing the harness -
            # the trace still records that the tool was attempted.
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        call_id = getattr(call, "id", None) or f"call_{uuid.uuid4().hex[:12]}"
        extra = (getattr(call, "model_extra", None) or {}).get("extra_content")
        if extra:
            _remember_extra(call_id, extra)
        blocks.append(ToolUseBlock(id=call_id, name=call.function.name, input=parsed))

    stop_reason = "tool_use" if tool_calls else "end_turn"
    if not blocks:
        blocks.append(TextBlock(""))
    # Reasoning models (gpt-oss on Groq, DeepSeek-R1, ...) often return tool calls
    # with no text and put their reasoning in a separate field. Keep it on the side
    # rather than as a text block, so it can never become an agent's reply.
    reasoning = getattr(message, "reasoning", None) or getattr(message, "reasoning_content", None)
    return Response(blocks, stop_reason, reasoning if isinstance(reasoning, str) else None)


# --------------------------------------------------------------------------
# The shim client
# --------------------------------------------------------------------------

class ProviderError(Exception):
    """A provider-side failure (bad key, unknown model, rate limit, outage).

    Raised from inside the shim so it propagates cleanly up through the
    untouched src/ harness; server.py turns it into a JSON error response
    instead of a bare 500, so the dashboard can show what actually went wrong.
    """

    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class _Messages:
    def __init__(self, client, fallback_model, force_model=None, provider=None):
        self._client = client
        self._fallback_model = fallback_model
        self._force_model = force_model
        self._provider = provider

    def create(self, model=None, max_tokens=1024, system=None, messages=None, tools=None, **kwargs):
        # src/ hardcodes some Claude model names as defaults. On a non-Anthropic
        # provider those don't exist, so fall back to the configured model.
        if self._force_model:
            # A backup provider serves its own model, whatever the caller asked for.
            model = self._force_model
        elif not model or model.startswith("claude"):
            model = self._fallback_model

        request = {
            "model": model,
            "max_tokens": _budget_for(model, max_tokens),
            "messages": _messages_to_openai(system, messages or [], self._provider),
        }
        if tools:
            request["tools"] = _tools_to_openai(tools)
            request["tool_choice"] = "auto"

        # NOTE: response_format={"type": "json_object"} looks like the obvious fix
        # for judges that wrap their JSON in prose, but it is a trap here. When a
        # reasoning model spends its whole budget thinking and returns empty
        # content, JSON mode fails the *request* with a 400 - aborting the entire
        # evaluation. Without it, the same event costs one scenario a
        # judge_output_parse_error and the run completes. Soft failure wins.

        try:
            completion = self._client.chat.completions.create(**request)
        except Exception as exc:
            raise _as_provider_error(exc, model, self._provider) from exc
        return _response_from_openai(completion)


def _as_provider_error(exc, model, provider=None):
    """Turn an SDK exception into a ProviderError with an actionable message."""
    status = getattr(exc, "status_code", None)
    detail = ""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            detail = err.get("message") or ""
        elif isinstance(err, str):
            detail = err
    detail = detail or str(exc)
    provider = provider or provider_name()

    if status == 401:
        return ProviderError(
            f"{provider} rejected the API key (401). Check the key in .env and restart the server.",
            status_code=400,
        )
    if status == 404 and "model" in detail.lower():
        return ProviderError(
            f"{provider} has no model '{model}' ({detail}). Update AGENT_MODEL / JUDGE_MODEL "
            f"in .env to a model this provider currently serves.",
            status_code=400,
        )
    if status == 429:
        return ProviderError(
            f"{provider} rate limit hit ({detail}). Free tiers cap requests per minute - "
            f"wait a moment, or run fewer scenarios per evaluation.",
            status_code=429,
        )
    return ProviderError(f"{provider} request failed: {detail}", status_code=502)


class OpenAICompatClient:
    """Drop-in stand-in for anthropic.Anthropic, backed by an OpenAI-compatible API."""

    def __init__(self, base_url, api_key, fallback_model, force_model=None, provider=None, max_retries=None):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "The 'openai' package is required for non-Anthropic providers. "
                "Run: pip install -r requirements.txt"
            ) from exc
        # Free tiers throttle aggressively - Groq caps tokens-per-minute, not
        # just requests - and a 15-scenario run sends growing conversation
        # history. The SDK honours Retry-After on 429, so give it enough
        # attempts to ride out a TPM window instead of failing the whole run.
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=max_retries if max_retries is not None else int(os.environ.get("LLM_MAX_RETRIES", "8")),
            timeout=float(os.environ.get("LLM_TIMEOUT", "120")),
        )
        self.messages = _Messages(self._client, fallback_model, force_model, provider)


# --------------------------------------------------------------------------
# Backup providers
# --------------------------------------------------------------------------

def _key_for(name):
    if name == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY", "")
    preset = PRESETS.get(name)
    if preset is None or preset["base_url"] is None:
        return ""
    return "not-needed" if preset["key_env"] is None else os.environ.get(preset["key_env"], "")


def _fallback_specs():
    """Parse LLM_FALLBACKS ("gemini,groq:openai/gpt-oss-20b") into (label, name, model)."""
    specs = []
    for item in (os.environ.get("LLM_FALLBACKS") or "").split(","):
        item = item.strip()
        if not item:
            continue
        name, _, model = item.partition(":")
        name = name.strip().lower()
        if name != "anthropic" and name not in PRESETS:
            continue
        model = model.strip() or (PRESETS.get(name, {}).get("agent_model") or "claude-sonnet-5")
        specs.append((f"{name}:{model}", name, model))
    return specs


class _AnthropicForcedModel:
    """An Anthropic client that always uses one model, for use as a backup."""

    def __init__(self, client, model):
        self._client = client
        self._model = model
        self.messages = self

    def create(self, **kwargs):
        kwargs["model"] = self._model
        return self._client.messages.create(**kwargs)


class FallbackClient:
    """Tries each provider in order, moving on when one is rate-limited or down.

    A provider that answers with a rate limit is skipped for a cool-down
    period, so a daily quota that has run out costs one failed call rather
    than one per request. `last_provider` names whoever answered last.
    """

    def __init__(self, clients, cooldown_s=None):
        self._clients = clients
        self._cooldown_s = float(os.environ.get("LLM_FALLBACK_COOLDOWN", "300")) if cooldown_s is None else cooldown_s
        self._resting_until = {}
        self.last_provider = None
        self.messages = self

    @property
    def providers(self):
        return [label for label, _ in self._clients]

    def create(self, **kwargs):
        failures = []
        now = time.time()
        ready = [c for c in self._clients if self._resting_until.get(c[0], 0) <= now]
        # If every provider is resting, try them anyway rather than fail outright.
        for label, client in ready or self._clients:
            try:
                response = client.messages.create(**kwargs)
            except Exception as exc:  # any provider failure moves on to the next one
                status = getattr(exc, "status_code", None)
                if status == 429:
                    self._resting_until[label] = time.time() + self._cooldown_s
                failures.append((label, status, getattr(exc, "message", None) or str(exc)))
                continue
            self.last_provider = label
            return response
        summary = "; ".join(f"{label}: {message}" for label, _, message in failures)
        status = 429 if all(s == 429 for _, s, _ in failures) else 502
        raise ProviderError(f"All configured providers failed. {summary}", status_code=status)


def _backup_client(name, model):
    if name == "anthropic":
        from anthropic import Anthropic
        return _AnthropicForcedModel(Anthropic(api_key=_key_for(name), max_retries=1), model)
    preset = PRESETS[name]
    return OpenAICompatClient(preset["base_url"], _key_for(name), model,
                              force_model=model, provider=name, max_retries=1)


def get_client(fallback_model=None):
    """Return the configured client, wrapped with backups when LLM_FALLBACKS is set.

    Raises RuntimeError with a user-facing message if nothing is configured;
    server.py turns that into an HTTP 400.
    """
    backups = [(label, name, model) for label, name, model in _fallback_specs() if _key_for(name)]
    primary = _primary_client(fallback_model, fast_failover=bool(backups))
    if not backups:
        return primary
    label = f"{provider_name()}:{agent_model()}"
    chain = [(label, primary)] + [(lbl, _backup_client(name, model)) for lbl, name, model in backups]
    return FallbackClient(chain)


def _primary_client(fallback_model=None, fast_failover=False):
    provider = provider_name()

    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set on the server.")
        from anthropic import Anthropic
        return Anthropic(api_key=key, max_retries=1) if fast_failover else Anthropic(api_key=key)

    preset = _preset()
    base_url = os.environ.get("LLM_BASE_URL") or preset["base_url"]
    if not base_url:
        raise RuntimeError(
            f"LLM_PROVIDER={provider} needs LLM_BASE_URL set to an OpenAI-compatible endpoint."
        )

    key = api_key()
    if not key:
        env_name = preset["key_env"] or "LLM_API_KEY"
        raise RuntimeError(f"{env_name} not set on the server (LLM_PROVIDER={provider}).")

    # With backups configured, give up quickly here and let the next provider answer.
    return OpenAICompatClient(base_url, key, fallback_model or agent_model(), provider=provider,
                              max_retries=1 if fast_failover else None)
