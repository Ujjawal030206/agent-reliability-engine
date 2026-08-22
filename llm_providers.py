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
"""

import os
import json
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
        "agent_model": "gemini-2.0-flash",
        "judge_model": "gemini-2.0-flash-lite",
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

    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


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


def _messages_to_openai(system, messages):
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
                    tool_calls.append({
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    })
            msg = {"role": "assistant", "content": " ".join(text_parts).strip() or None}
            if tool_calls:
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
        blocks.append(ToolUseBlock(
            id=getattr(call, "id", None) or f"call_{uuid.uuid4().hex[:12]}",
            name=call.function.name,
            input=parsed,
        ))

    stop_reason = "tool_use" if tool_calls else "end_turn"
    if not blocks:
        blocks.append(TextBlock(""))
    return Response(blocks, stop_reason)


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
    def __init__(self, client, fallback_model):
        self._client = client
        self._fallback_model = fallback_model

    def create(self, model=None, max_tokens=1024, system=None, messages=None, tools=None, **kwargs):
        # src/ hardcodes some Claude model names as defaults. On a non-Anthropic
        # provider those don't exist, so fall back to the configured model.
        if not model or model.startswith("claude"):
            model = self._fallback_model

        request = {
            "model": model,
            "max_tokens": _budget_for(model, max_tokens),
            "messages": _messages_to_openai(system, messages or []),
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
            raise _as_provider_error(exc, model) from exc
        return _response_from_openai(completion)


def _as_provider_error(exc, model):
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
    provider = provider_name()

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

    def __init__(self, base_url, api_key, fallback_model):
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
            max_retries=int(os.environ.get("LLM_MAX_RETRIES", "8")),
            timeout=float(os.environ.get("LLM_TIMEOUT", "120")),
        )
        self.messages = _Messages(self._client, fallback_model)


def get_client(fallback_model=None):
    """Return an Anthropic client, or an Anthropic-shaped shim for a free provider.

    Raises RuntimeError with a user-facing message if nothing is configured;
    server.py turns that into an HTTP 400.
    """
    provider = provider_name()

    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set on the server.")
        from anthropic import Anthropic
        return Anthropic(api_key=key)

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

    return OpenAICompatClient(base_url, key, fallback_model or agent_model())
