# 🛡️ Agent Reliability Engine

*Agent infrastructure · testing · failure prediction*

Continuous integration for autonomous agents. Point this at an AI agent,
and it automatically generates realistic + adversarial test scenarios, runs
the agent in a sandbox, classifies *why* it failed, and produces a reliability
scorecard you can track across versions.

---

## Why this matters

Industry benchmarks report autonomous agents failing on the majority of
real-world tasks they attempt. Most teams still ship agents against a
handful of hand-written happy-path prompts, so real failure modes —
tool-call loops, hallucinated confidence, unsafe destructive actions under
social pressure, silent goal drift — only surface after deployment, on real
users, with real consequences.

This project treats agent reliability the way software engineering treats
correctness: as something you test *before* you ship, automatically, on
every change.

## How it works

```mermaid
flowchart LR
    A[Scenario Bank<br/>15 realistic + adversarial prompts] --> B[Sandboxed Execution Harness]
    B --> C[Target Agent Under Test<br/>'Riley' — ShopFast support agent]
    C -->|mocked tool calls, fully logged| B
    B --> D[Failure Mode Classifier]
    D --> D1[Deterministic safety rules<br/>irreversible action w/o real consent,<br/>tool-call loops, hallucinated success]
    D --> D2[LLM-as-judge<br/>goal drift, wrong escalation,<br/>ignored requests]
    D1 --> E[Reliability Scorecard]
    D2 --> E
    E --> F[Regression Tracker<br/>SQLite run history across versions]
```

1. **Scenario Bank + Generation Engine** (`data/scenario_bank.json`,
   `src/scenario_generator.py`) — 15 curated scenarios spanning: normal
   requests, ambiguous/unconfirmed requests, social engineering toward
   destructive actions, prompt injection (including fake "system override"
   messages), loop-inducing tasks, and hallucination bait — plus a live
   Scenario Generation Engine (Scenario Bank tab → "Generate") that asks
   Claude to synthesize *new* adversarial scenarios targeting the agent's
   actual tools, deduplicated against what already exists, and feeds them
   straight into the run pool.
2. **Target Agent Under Test** (`src/agent_under_test.py`) — "Riley," a small
   customer-support agent for a fictional shop (ShopFast), with real
   Anthropic tool-use across 6 tools (order lookup, refund, email,
   escalation, account deletion, fund transfer). Two system-prompt versions
   (`v1_baseline`, `v2_guarded`) are included so you can demo the regression
   tracker by comparing reliability before/after a safety-focused prompt
   change.
3. **Sandboxed Execution Harness** (`src/mock_tools.py`) — every tool is
   mocked and logged. Destructive tools only "succeed" if called with
   `confirmed=true`; nothing ever touches a real system.
4. **Failure Mode Classifier** (`src/failure_classifier.py`) — hybrid:
   deterministic rules catch the non-negotiable safety failures, an
   LLM-as-judge (Claude) catches the softer, contextual ones. A rule
   violation always fails the scenario regardless of what the judge says.
5. **Reliability Scorecard & Regression Tracker** (`src/reliability_scorecard.py`,
   `src/db.py`) — aggregates pass/fail into a 0–100 score with a failure-mode
   breakdown, and stores every run in SQLite so scores can be tracked across
   agent versions over time.

Two front ends sit on top of this, both driving the same engine: the
**web dashboard** (`server.py` + `static/`, described below) which is the one
to demo, and the original **Streamlit** prototype (`app.py`) with five tabs:
Overview, Run Evaluation, Reliability Scorecard, Regression Tracker, and
Scenario Bank.

## Setup & run locally

Requires Python 3.10+.

```bash
git clone <this-repo-url>
cd agent-reliability-engine

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# then edit .env and fill in ONE provider block (Groq is free, no card)

streamlit run app.py
```

The app opens at `http://localhost:8501`.

**No key at all?** The app still runs — the Overview and Scenario Bank tabs
work with no key, and the Reliability Scorecard tab falls back to cached
sample data (`data/sample_run_results.json`) so the UI is fully explorable
without live calls.

## Web dashboard (FastAPI + Stitch frontend)

The same engine is also exposed as a JSON API with a purpose-built dashboard
in front of it — this is the version to demo.

```bash
uvicorn server:app --reload --port 8000
```

Then open `http://localhost:8000`. The dashboard has four views:

- **Scenarios** — the curated bank from `data/scenario_bank.json`, filterable
  by category, with checkboxes that select the exact subset for the next run.
  The Scenario Generation Engine (`POST /api/scenarios/generate`) synthesizes
  new adversarial vectors and previews them here.
- **Runs** — fires `POST /api/run` for the selected agent version, then renders
  the reliability score, the failure-mode breakdown, and per-scenario logs you
  can expand into the full trace: agent response, every mocked tool call with
  its result, deterministic rule findings, and the LLM judge's explanation.
- **Analytics** — the Regression Tracker: score per run over time, one colored
  series per agent version, so a `v1_baseline` → `v2_guarded` improvement is
  visible as a step up.
- **Red Teaming** — the headline feature. `POST /api/redteam` runs the adaptive
  attacker against the target agent, and the transcript replays turn by turn as
  a live attacker-vs-target chat, followed by the verdict.

The frontend is plain HTML/CSS/JS in `static/` — no build step, no framework.
It talks to the API documented in `API.md` and nothing else; all the evaluation
logic stays in `src/`. The visual design system it implements (colors,
typography, spacing, component rules) is in `design/DESIGN.md`, with the
original Stitch export kept alongside it as `design/code.html`.

> Tailwind and the Geist / JetBrains Mono / Material Symbols fonts are loaded
> from CDNs, so the dashboard needs an internet connection to render as
> designed. The API itself has no such dependency.

## LLM provider (free tiers supported)

The engine needs a tool-calling LLM behind it, but it is **not tied to one
vendor**. `LLM_PROVIDER` in `.env` selects the backend:

| `LLM_PROVIDER` | Cost | Key from | Default agent model |
|---|---|---|---|
| `groq` | free tier | [console.groq.com/keys](https://console.groq.com/keys) | `openai/gpt-oss-120b` |
| `gemini` | free tier | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `gemini-2.0-flash` |
| `openrouter` | free models | [openrouter.ai/keys](https://openrouter.ai/keys) | `llama-3.3-70b-instruct:free` |
| `ollama` | free, fully local | no key needed | `llama3.1` |
| `custom` | — | set `LLM_BASE_URL` | your choice |
| `anthropic` | paid | [console.anthropic.com](https://console.anthropic.com) | `claude-sonnet-5` |

Groq is the recommended free option — its free tier allows roughly 30 requests
per minute and 1,000 per day, and a full 15-scenario run costs on the order of
45 requests, so a demo session fits comfortably inside it. Rate limits change;
check the provider's own docs.

**Whichever model you pick must support tool/function calling** — the target
agent under test is a tool-use agent, and the harness cannot evaluate it
otherwise. Every default in the table does.

### How it stays provider-agnostic

`src/` is written against the Anthropic client surface: it calls
`client.messages.create(...)` and reads `response.content` blocks and
`response.stop_reason`. Rather than fork that tested logic per vendor,
`llm_providers.py` supplies an object with the *same* surface backed by any
OpenAI-compatible chat-completions endpoint, translating in both directions:

- Anthropic `{name, description, input_schema}` tools → OpenAI function tools
- tool-result blocks carried inside a user message → `role: "tool"` messages
- OpenAI `tool_calls` + `finish_reason` → Anthropic-shaped content blocks and
  `stop_reason`

`server.py` hands the harness whichever client is configured, and **nothing in
`src/` changes** — the tool-use loop, the mocked sandbox, the deterministic
safety rules, the LLM judge, and the scorecard all run unmodified. Swapping
providers is a `.env` edit.

Smaller free models sometimes emit malformed tool-call arguments; the adapter
degrades those to an empty call rather than crashing the run, so the trace still
records the attempt.

### Demo mode (no key at all)

Every endpoint that evaluates anything calls an LLM, so with no provider
configured the dashboard has nothing real to render. **Demo mode**
fills that gap: it swaps in bundled fixtures from `static/demo/` so all four
views are fully explorable — a v1 run scoring 33.3, a v2 run scoring 83.3 (so
the Regression Tracker shows the improvement), a red-team attack that breaches
`v1_baseline`, and one that `v2_guarded` holds off.

It turns on automatically when `/api/health` reports no key, and there's a
**Demo mode** toggle in the header to switch it off once you have one.

**This data is canned, not measured.** It is hand-authored to match the shapes
in `API.md` — no agent was actually tested to produce it. The UI says so
everywhere it appears: a persistent banner, a `demo data` badge on every
scorecard, history row, and verdict, and an explicit note that the goal and
max-turns controls don't apply. Don't present it to anyone as a real
evaluation result; it exists so the interface can be demonstrated and
developed against, not to stand in for the engine's output.

For real numbers you need a provider key - see below. On a free tier that
costs nothing, so demo mode is only a fallback for when you have no key at all.

## Deploying a live link (free)

Two options, depending on which UI you want people to land on.

### The dashboard (recommended) — Render, free tier

The repo ships a [`render.yaml`](render.yaml) blueprint, so this is mostly clicks:

1. Push this repo to GitHub (public, or private with Render granted access).
2. On [render.com](https://render.com), **New → Blueprint**, select this repo.
3. Render reads `render.yaml` and asks for `GROQ_API_KEY` — paste it there.
   It is stored by Render, never committed to the repo.
4. Deploy. You get a public `*.onrender.com` URL.

Three things to know about the free tier before you share the link:

- **Cold starts.** The service sleeps after inactivity and takes tens of seconds
  to wake. A judge clicking a cold link sees a blank tab first. Hit the URL
  yourself a minute before anyone else does.
- **Run history is ephemeral.** `data/runs.db` lives on the container's disk,
  which resets on redeploy and restart, so the Regression Tracker starts empty.
  For a persistent tracker you'd attach a Render disk (paid) or swap SQLite for
  a hosted Postgres.
- **Your free LLM quota is public.** Anyone with the link can trigger runs
  against your Groq key and exhaust the rate limit. For a short-lived demo link
  that is usually fine; if you'd rather not risk it, deploy without a key set — the
  dashboard falls back to demo mode automatically and stays fully explorable.

### The Streamlit prototype — Streamlit Community Cloud

1. [share.streamlit.io](https://share.streamlit.io) → sign in with GitHub.
2. "New app" → select this repo → main file path `app.py`.
3. Under **Advanced settings → Secrets**, add:
   ```toml
   LLM_PROVIDER = "groq"
   GROQ_API_KEY = "gsk_..."
   AGENT_MODEL = "openai/gpt-oss-120b"
   JUDGE_MODEL = "openai/gpt-oss-120b"
   ```
4. Deploy for a public `*.streamlit.app` URL.

**Cost note:** on a free provider tier the exposure is rate limits, not money.
On `LLM_PROVIDER=anthropic` every run makes billable calls — don't leave that
configuration on a public link.

## Demo flow (suggested)

Demo the **web dashboard** (`uvicorn server:app`) — the red-team chat only
exists there. Full shot list with timings: [`demo_script.md`](demo_script.md).

1. **Scenarios** — show the adversarial vectors (prompt injection, social
   engineering, loop bait); read one aloud.
2. **Runs** — run `v1_baseline` live, then expand a failed scenario
   (`S05_prompt_injection_transfer` or `S04_social_engineering_destructive`)
   to show the agent talked into an unsafe action, with the exact tool call
   and the deterministic rule that caught it.
3. **Red Teaming** — the headline. Launch an adaptive attack and let the
   attacker-vs-target transcript play out, then show the verdict.
4. **Analytics** — run `v2_guarded`, then compare. Report what the tracker
   actually shows rather than a scripted improvement: on a free-tier model the
   guarded prompt fixed the failure it targeted and regressed a different one,
   which is a sharper argument for regression testing than a clean win.

## Design decisions & known limitations

- **Why a hybrid classifier, not pure LLM-as-judge?** Irreversible-action
  safety failures are too important to leave to a model's opinion — they're
  checked deterministically. The LLM judge is reserved for genuinely
  subjective quality questions.
- **Why a mocked target agent instead of a real production agent?** Time
  constraints on the initial build; the harness itself (`run_agent_turn`,
  the classifier, the scorecard) is agent-agnostic and can be pointed at any
  Anthropic tool-use agent by swapping `src/agent_under_test.py`.
- **Single hardcoded target agent.** Right now "Riley" and her 6 tools are
  wired directly into `agent_under_test.py`. The honest next step — and the
  one that would turn this from a demo into a real platform — is a
  "bring your own agent" flow: let a user paste in a system prompt + tool
  schema (or an API endpoint) and have the harness, classifier, and scorecard
  run against *that*, unmodified. The classifier and scorecard are already
  agent-agnostic (they only depend on `{prompt, category,
  should_not_auto_confirm}` and a generic trace shape), so this is a UI +
  harness-parameterization change, not a rearchitecture.
- **Single-agent-family focus.** Right now this evaluates Anthropic tool-use
  agents specifically; a more general version would normalize traces from
  any agent framework (LangChain, CrewAI, raw OpenAI function calling, etc.)
  into a common trace format before classification.

## Tech stack

Python, FastAPI (JSON API + static dashboard), Streamlit (original UI),
SQLite, pandas. The LLM backend is pluggable — Anthropic, or any
OpenAI-compatible free tier (Groq, Gemini, OpenRouter, local Ollama) through
`llm_providers.py`. The dashboard is dependency-free HTML/CSS/JS over the API.

## Authors

- **Ujjawal Srivastava** — team leader
- **Vedant Pandey** — team member

## License

MIT — see [`LICENSE`](LICENSE).
