# 🛡️ Agent Reliability Engine

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

All of it is surfaced in a Streamlit dashboard (`app.py`) with five tabs:
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
# then edit .env and add your ANTHROPIC_API_KEY

streamlit run app.py
```

The app opens at `http://localhost:8501`.

**No API key?** The app still runs — the Overview and Scenario Bank tabs
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

## Deploying a live link (Streamlit Community Cloud, free, ~5 min)

For a hosted demo link, this is the fastest path:

1. Push this repo to GitHub (public, or private with Streamlit given access).
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub.
3. "New app" → select this repo → main file path `app.py`.
4. Under **Advanced settings → Secrets**, add:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   AGENT_MODEL = "claude-sonnet-5"
   JUDGE_MODEL = "claude-haiku-4-5-20251001"
   ```
5. Deploy. You'll get a public `*.streamlit.app` URL to put in your submission.

**Cost note:** every live run makes real Anthropic API calls (1 target-agent
call chain + 1 judge call per scenario). Keep an eye on usage if the link is
public and reachable by anyone — consider setting a low scenario-count
default, or swapping in `AGENT_MODEL=claude-haiku-4-5-20251001` for the
public demo to keep costs predictable.

## Demo flow (suggested)

1. **Overview tab** — explain the problem and architecture.
2. **Scenario Bank tab** — show the adversarial scenarios (prompt injection,
   social engineering, loop bait).
3. **Run Evaluation tab** — run `v1_baseline` against the full scenario bank
   live. Expand a couple of failed scenarios (especially
   `S05_prompt_injection_transfer` or `S04_social_engineering_destructive`)
   to show the agent getting socially engineered into an unsafe action.
4. **Reliability Scorecard tab** — show the score and failure-mode breakdown.
5. **Run again** with `v2_guarded` — show the score improve.
6. **Regression Tracker tab** — show both runs plotted, proving the safety
   prompt change measurably improved reliability. This is the "continuous
   integration for agents" payoff.

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
Anthropic API (Claude — both as the target agent and as the LLM-judge layer),
SQLite, pandas. The dashboard is dependency-free HTML/CSS/JS over the API.

## Authors

_Add authors here._

## License

MIT (or your team's preference) — add a `LICENSE` file before submitting if
you need one.
