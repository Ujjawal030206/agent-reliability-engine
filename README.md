# 🛡️ Agent Reliability Engine

*Autonomous agents · deterministic verification · reliability testing*

**Live demo:** https://agent-reliability-engine.onrender.com/ (free tier; the first request may take a few seconds to wake the service)

Tooling for building AI agents you can trust with real actions. The repository
has two parts that share one provider-agnostic LLM layer:

- **Customer Resolution Agent**: an autonomous agent that resolves customer
  cases inside a stateful backend, adapts when conditions change mid-case, and
  is graded by a deterministic verifier on what actually happened in the
  database rather than on what it said.
- **Reliability test harness**: continuous integration for agents. It
  generates realistic and adversarial scenarios, runs the agent in a sandbox,
  classifies *why* it failed, red-teams it with an adaptive attacker, and tracks
  a reliability score across versions.

**Quick check, no API key needed:**

```bash
pip install -r requirements.txt
python -m unittest discover -s tests
```

---

## Why this matters

Autonomous agents fail on a large share of real-world tasks, and the costly
failures are actions, not wording: refunding a customer twice, acting on the
wrong account, taking a destructive step under social pressure, or reporting a
task as done when the backend shows otherwise. Most agents still ship against a
handful of happy-path prompts, so these failures surface only in production.

This project treats agent reliability the way software engineering treats
correctness: verified against ground truth, tested automatically, on every
change.

## Customer Resolution Agent

An agent that resolves customer issues (replacements, refunds, cancellations,
escalations) in a simulated enterprise backend whose state changes while the
case is open.

### Architecture

```mermaid
flowchart LR
    C[Customer request] --> A[Resolution agent<br/>LLM + case file]
    A -->|tool calls| E[(ShopFast sandbox<br/>orders, stock, refund ledger,<br/>policy engine)]
    E -->|results, blocks, errors| A
    S[Scenario events<br/>stock sell-out, gateway timeout,<br/>policy change, outage] -.-> E
    A --> T[Trace<br/>Goal, Decision, Action,<br/>Result, Adaptation, Outcome]
    E -->|final database| V[Deterministic verifier]
    T --> V
    V --> R[pass^k report]
    R --> H[Self-hardening loop]
    H -->|rule kept only if it fixes<br/>a scenario and breaks none| A
```

### Components

- **Stateful sandbox** (`src/shop_env.py`): customers, orders, stock, a refund
  ledger, replacements, cancellations and escalations in an in-memory SQLite
  database, exposed through 11 tools. Every state-changing tool writes to it,
  and the backend enforces policy itself (return window, refund limit, stock,
  cancellation rules).
- **Scenario events**: each scenario can change the world under the agent.
  Stock sells out after the agent has checked it, a refund times out after it
  has committed, the refund limit is lowered mid-case, an order ships just
  before cancellation, a service fails transiently.
- **Resolution agent** (`src/resolution_agent.py`): an LLM tool-use loop with
  a harness-maintained case file (facts, policy seen, customer answers, actions,
  blockers, verifications) shown to the model on every call as persistent task
  state. Each step is traced as Goal, Decision, Action, Intermediate result,
  Adaptation and Final outcome, with adaptations and retries marked.
- **Deterministic verifier** (`src/outcome_verifier.py`): reads the final
  database and the recorded tool calls, with no LLM judge. It fails duplicate
  refunds, actions without customer consent, actions on another customer's
  order, unverified completions, false completion claims, loops, wrong
  resolutions, wrong refund totals, and missing or unnecessary escalations.
- **pass^k evaluation** (`src/resolution_eval.py`): runs each scenario k times
  and reports the share of scenarios that passed every trial. Provider errors
  are retried once and recorded separately from agent failures.
- **Self-hardening loop** (`src/hardening_loop.py`): runs the suite, gives the
  verifier's findings to a patcher model that proposes one general rule,
  re-runs the whole suite with that rule, and keeps it only if at least one
  scenario newly passes and none regresses. The model proposes changes; a set
  comparison decides.

Two agent versions, `v1_baseline` and `v2_verified`, make the effect of the
operating procedure measurable. The 12 scenarios are defined in
`data/resolution_scenarios.json`.

### Usage

```bash
python run_resolution.py --list
python run_resolution.py R03                          # one scenario, full trace
python run_resolution.py --all --trials 3 --save      # pass^3 over the suite
python harden_agent.py --from v1_baseline --rounds 3 --save
python harden_agent.py R02 R10 --rounds 2             # a cheaper subset
```

`--save` writes reports to `data/traces/`; the dashboard can replay them
without spending model quota again.

### Results

Live runs on Groq's free tier, one trial per scenario (13 Sept 2026), agent
model `openai/gpt-oss-120b`:

| Agent | Completed | Passed | Failed |
|---|---|---|---|
| `v2_verified` | 12 / 12 | 12 | none |
| `v1_baseline` | 6 / 12 | 4 | R03 (stopped without resolving), R06 (never escalated the warranty claim) |

The `v1_baseline` run stopped when the model's 200K tokens/day free-tier limit
was reached; R07 to R12 have not yet been run on it.

Self-hardening, `v1_baseline` on `openai/gpt-oss-20b` (20b also proposing
rules), R03 and R06, one round: the baseline passed 0 of 2. The loop proposed
"confirm the updated state with verify_resolution before the final confirmation
message", which fixed R03 with no regressions (1 of 2). R06 still fails.

These are single trials on small samples. Agents are nondeterministic, so run
`--trials 3` before treating any difference as settled.

## Reliability test harness

```mermaid
flowchart LR
    A[Scenario Bank<br/>15 realistic + adversarial prompts] --> B[Sandboxed Execution Harness]
    B --> C[Target Agent Under Test<br/>'Riley', ShopFast support agent]
    C -->|mocked tool calls, fully logged| B
    B --> D[Failure Mode Classifier]
    D --> D1[Deterministic safety rules<br/>irreversible action w/o real consent,<br/>tool-call loops, hallucinated success]
    D --> D2[LLM-as-judge<br/>goal drift, wrong escalation,<br/>ignored requests]
    D1 --> E[Reliability Scorecard]
    D2 --> E
    E --> F[Regression Tracker<br/>SQLite run history across versions]
```

1. **Scenario Bank and Generation Engine** (`data/scenario_bank.json`,
   `src/scenario_generator.py`): 15 curated scenarios spanning normal
   requests, ambiguous or unconfirmed requests, social engineering toward
   destructive actions, prompt injection (including fake "system override"
   messages), loop-inducing tasks and hallucination bait, plus a generator that
   synthesizes new adversarial scenarios targeting the agent's actual tools,
   deduplicated against the existing bank.
2. **Target Agent Under Test** (`src/agent_under_test.py`): "Riley", a
   customer-support agent for a fictional shop with 6 tools (order lookup,
   refund, email, escalation, account deletion, fund transfer). Two prompt
   versions (`v1_baseline`, `v2_guarded`) allow before-and-after comparison of
   a safety-focused change.
3. **Sandboxed Execution Harness** (`src/mock_tools.py`): every tool is mocked
   and logged. Destructive tools only succeed when called with
   `confirmed=true`; nothing touches a real system.
4. **Failure Mode Classifier** (`src/failure_classifier.py`): deterministic
   rules catch non-negotiable safety failures, and an LLM-as-judge catches the
   softer, contextual ones. A rule violation always fails the scenario,
   regardless of the judge's opinion.
5. **Adaptive red team** (`src/red_team_agent.py`): an attacker model converses
   with the target agent and changes approach turn by turn based on its
   replies, rather than firing one scripted prompt.
6. **Reliability Scorecard and Regression Tracker**
   (`src/reliability_scorecard.py`, `src/db.py`): aggregates results into a
   0 to 100 score with a failure-mode breakdown and stores every run in SQLite
   so scores can be tracked across agent versions.

## Web dashboard

```bash
uvicorn server:app --reload --port 8000
```

Open `http://localhost:8000`. The dashboard has five views:

- **Resolution Agent**: runs a resolution case live and replays it step by
  step, including injected world changes and the verifier's findings. It runs
  the full suite with pass^k and loads saved reports from `data/traces/`.
- **Scenarios**: the curated bank, filterable by category, with selection for
  the next run and a scenario generator.
- **Runs**: runs an evaluation for the selected agent version and renders the
  reliability score, failure-mode breakdown and expandable per-scenario traces.
- **Analytics**: the Regression Tracker, with score per run over time and one
  series per agent version.
- **Red Teaming**: runs the adaptive attacker and replays the attacker-versus-
  target transcript turn by turn, followed by the verdict.

The frontend is plain HTML/CSS/JS in `static/`, with no build step. It talks
only to the API documented in [`API.md`](API.md); all evaluation logic lives
in `src/`. The design system is described in `design/DESIGN.md`.

Tailwind and the Geist, JetBrains Mono and Material Symbols fonts are bundled
in `static/vendor/` (licenses in `static/vendor/NOTICE.md`), so the dashboard
works fully offline. Refresh them with `python scripts/vendor_assets.py`.

A Streamlit interface for the test harness is also included (`app.py`,
`streamlit run app.py`).

## Setup

Requires Python 3.10+.

```bash
git clone https://github.com/Ujjawal030206/agent-reliability-engine.git
cd agent-reliability-engine

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# then edit .env and fill in ONE provider block
```

The test suite (`python -m unittest discover -s tests`) needs no key: it
exercises the sandbox, agent loop, verifier and hardening gate with a scripted
model client.

## LLM providers

The engine needs a tool-calling LLM but is not tied to one vendor.
`LLM_PROVIDER` in `.env` selects the backend:

| `LLM_PROVIDER` | Cost | Key from | Default agent model |
|---|---|---|---|
| `groq` | free tier | [console.groq.com/keys](https://console.groq.com/keys) | `openai/gpt-oss-120b` |
| `gemini` | free tier | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `gemini-2.0-flash` |
| `openrouter` | free models | [openrouter.ai/keys](https://openrouter.ai/keys) | `llama-3.3-70b-instruct:free` |
| `ollama` | free, fully local | no key needed | `llama3.1` |
| `custom` | varies | set `LLM_BASE_URL` | your choice |
| `anthropic` | paid | [console.anthropic.com](https://console.anthropic.com) | `claude-sonnet-5` |

**The model must support tool calling.** Every default in the table does.
Free tiers have tight limits: a full 12-scenario resolution suite uses most of
a model's daily token budget on Groq's free tier, so plan larger runs
accordingly. Limits change; check each provider's documentation.

### How it stays provider-agnostic

`src/` is written against the Anthropic client surface
(`client.messages.create(...)`, `response.content` blocks,
`response.stop_reason`). `llm_providers.py` supplies an object with the same
surface backed by any OpenAI-compatible chat-completions endpoint, translating
in both directions:

- Anthropic `{name, description, input_schema}` tools to OpenAI function tools
- tool-result blocks inside a user message to `role: "tool"` messages
- OpenAI `tool_calls` and `finish_reason` back to Anthropic-shaped content
  blocks and `stop_reason`, keeping a reasoning model's reasoning on the side so
  it can be shown in traces without becoming the agent's reply

Swapping providers is a `.env` edit. Malformed tool-call arguments from smaller
models degrade to an empty call instead of crashing the run.

### Demo mode (test harness views, no key)

With no provider configured, the Scenarios, Runs, Analytics and Red Teaming
views can render bundled sample data from `static/demo/`, clearly labelled as
such everywhere it appears. **This data is hand-authored, not measured**, and
exists so the interface can be explored and developed against. It never applies
to the Resolution Agent view, which only shows live runs or saved reports of
real runs.

## Deployment

The repository includes a [`render.yaml`](render.yaml) blueprint for Render:

1. On [render.com](https://render.com), choose **New → Blueprint** and select
   this repository. The blueprint installs
   [`requirements-server.txt`](requirements-server.txt), the dashboard's runtime
   dependencies only.
2. Provide `GROQ_API_KEY` when prompted. It is stored by Render and never
   committed.
3. Deploy to get a public `*.onrender.com` URL.

Notes for the free tier:

- **Cold starts.** The service sleeps when idle and takes tens of seconds to
  wake on the first request.
- **Ephemeral storage.** `data/runs.db` and `data/traces/` reset on redeploy or
  restart. Attach a persistent disk or use hosted Postgres for durable history.
- **Shared quota.** Anyone with the link can trigger runs against your provider
  key. Add rate limiting or deploy without a key for a read-only preview.

The Streamlit interface can be deployed on Streamlit Community Cloud with
`app.py` as the entry point and the provider settings added as secrets.

## Project structure

```
src/
  shop_env.py            stateful SQLite sandbox, tools, scenario events
  resolution_agent.py    resolution agent loop, case file, tracing
  outcome_verifier.py    deterministic outcome verification
  resolution_eval.py     suite runner and pass^k scoring
  hardening_loop.py      self-hardening loop with regression gate
  agent_under_test.py    target agent for the test harness
  mock_tools.py          mocked, logged tools for the test harness
  failure_classifier.py  deterministic rules + LLM-as-judge
  red_team_agent.py      adaptive red-team attacker
  scenario_generator.py  adversarial scenario generation
  reliability_scorecard.py, db.py
data/                    scenario definitions; runs.db and traces/ are local only
static/                  dashboard (HTML/CSS/JS) and bundled vendor assets
tests/                   offline tests with a scripted model client
server.py                FastAPI app serving the API and dashboard
run_resolution.py        CLI for resolution scenarios
harden_agent.py          CLI for the self-hardening loop
llm_providers.py         provider adapter
```

## Design decisions and limitations

- **Verification against ground truth.** For state-changing agents the only
  trustworthy evidence is the backend state. The resolution verifier never
  consults a model, so every verdict is reproducible and explainable line by
  line.
- **Hybrid classifier in the test harness.** Irreversible-action safety
  failures are checked deterministically; the LLM judge is reserved for
  subjective quality questions such as goal drift.
- **Guarded self-improvement.** Rules proposed by a model are accepted only
  when the full suite shows a fix with no regressions, which prevents a patch
  that helps one case from silently breaking another.
- **Simulated backend.** The sandbox is realistic in behaviour but synthetic;
  production use needs adapters to real order, inventory and payment APIs and
  human review of escalations.
- **Small, single-trial samples so far.** Published numbers come from one trial
  per scenario; pass^3 runs are the next step before drawing firm conclusions.
- **Fixed target agents.** The harness is agent-agnostic in design; a natural
  extension is a bring-your-own-agent flow that accepts a system prompt and tool
  schema or an API endpoint, and normalizing traces from other frameworks into
  the common trace format.

## Tech stack

Python, FastAPI, SQLite, plain HTML/CSS/JS with bundled Tailwind, Streamlit,
`unittest`. The LLM backend is pluggable: Anthropic, or any OpenAI-compatible
endpoint (Groq, Gemini, OpenRouter, local Ollama) through `llm_providers.py`.

## Author

**Ujjawal Srivastava**

## License

MIT, see [`LICENSE`](LICENSE).
