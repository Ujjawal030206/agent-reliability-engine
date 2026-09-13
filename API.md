# API Contract

Base URL when running locally: `http://localhost:8000`
Interactive docs (auto-generated, always accurate): `http://localhost:8000/docs`

All endpoints return JSON. All `POST` bodies are JSON.

---

### `GET /api/health`
Returns `{ "ok": true, "has_api_key": bool, "llm": {provider, agent_model, judge_model, configured} }`.
Use this on frontend load to show a banner if the server has no LLM provider
configured yet, and to display which provider/model is driving the engine.
`has_api_key` reflects the *active* provider (set by `LLM_PROVIDER`), not
Anthropic specifically.

### `GET /api/agent-versions`
Returns `{ "versions": ["v1_baseline", "v2_guarded"] }` — populate a version
picker with these.

### `GET /api/scenarios`
Returns `{ "scenarios": [ {id, category, prompt, should_not_auto_confirm, notes}, ... ] }`
— the 15-scenario curated bank. Use for the Scenario Bank view.

### `POST /api/scenarios/generate`
Body: `{ "n": 5 }`
Returns: `{ "scenarios": [ {id, category, prompt, ...}, ... ] }` — new,
LLM-generated scenarios, deduplicated against the curated bank. Append these
to whatever list the frontend is showing/using for a run.

### `POST /api/run`
Body: `{ "agent_version": "v1_baseline", "n": 6 }`
(or `{ "agent_version": "...", "scenario_ids": ["S01_normal_lookup", ...] }`
to run a specific subset)

Returns:
```json
{
  "run_id": "a1b2c3d4",
  "scorecard": {
    "score": 60.0, "total": 15, "passed": 9, "failed": 6,
    "failure_mode_breakdown": {"destructive_action_without_real_confirmation": 3, "...": 1}
  },
  "results": [
    {
      "scenario": {...},
      "trace": {"scenario_input": "...", "turns": [...], "tool_calls": [...], "final_text": "...", "hit_iteration_cap": false},
      "classification": {"verdict": "pass"|"fail", "failure_modes": [...], "rule_findings": [...], "judge_explanation": "..."}
    },
    ...
  ]
}
```
This call runs synchronously and can take a while for a full 15-scenario
run (each scenario = 1+ target-agent calls + 1 judge call). Show a loading
state. The loop in `server.py`'s `run_evaluation` is scenario-by-scenario, so
it can be converted to a Server-Sent Events (SSE) stream if live per-scenario
progress is needed.

### `POST /api/redteam`
Body: `{ "agent_version": "v1_baseline", "goal": "get an unconfirmed refund processed", "max_turns": 5 }`

Returns:
```json
{
  "transcript": {
    "goal": "...", "target_version": "v1_baseline",
    "turns": [{"speaker": "attacker", "text": "..."}, {"speaker": "target", "text": "..."}, ...],
    "tool_calls": [...], "goal_achieved": false, "hit_iteration_cap": false
  },
  "classification": {"verdict": "pass"|"fail", "failure_modes": [...], "judge_explanation": "..."}
}
```
Render `transcript.turns` as a back-and-forth conversation (attacker vs.
target), then show the verdict.

### `GET /api/runs`
Returns `{ "runs": [ {run_id, agent_version, timestamp, score, total_scenarios, passed, failed}, ... ] }`
— feed this straight into a line chart for the Regression Tracker view.

### `GET /api/runs/{run_id}`
Returns the full per-scenario results for one historical run.

---

## Local dev

```bash
pip install -r requirements.txt
cp .env.example .env   # add your ANTHROPIC_API_KEY
uvicorn server:app --reload --port 8000
```

The dashboard in `./static/` is served at `/`, alongside `/api/*`.

---

## Customer Resolution Agent

The resolution agent runs against a stateful sandbox (`src/shop_env.py`) and is
graded by a deterministic verifier (`src/outcome_verifier.py`). No LLM judge is
involved in these verdicts.

### `GET /api/resolution/scenarios`
Returns `{ "scenarios": [...], "agent_versions": ["v1_baseline", "v2_verified"] }`.
Each scenario has `id`, `title`, `category`, `customer_message`, `events`
(disruptions such as a stock sell-out or gateway timeout), `expect`, and
`what_it_tests`.

### `POST /api/resolution/run`
Body: `{ "scenario_id": "R03_stock_sells_out_mid_case", "agent_version": "v2_verified" }`

Returns `{ scenario, trace, verification }`:
- `trace.steps[]`: one entry per tool call, with `decision` (what the agent said it
  decided, or an excerpt of the model's own reasoning when it said nothing; see
  `decision_source`), `tool`, `kind` (read / write / ask / verify / escalate), `input`,
  `result`, `status` (ok / blocked / error), `adaptation`
  (`{after_step, problem}` when the agent changed course after a failure or a
  negative observation such as no stock),
  `retry_of`, and `env_events` (world changes, with `visible_to_agent`).
- `trace.final_text`, `trace.stop_reason` (`completed` / `loop_detected` /
  `max_iterations`), `trace.case_file` (the task state the agent saw),
  `trace.final_state` (the sandbox database at the end).
- `verification`: `{ verdict, failure_modes[], findings[{mode, detail}], outcome }`
  where `outcome` holds `resolution`, `total_refunded`, `refund_count`,
  `replacements`, `escalated`, `adaptations`, `retries`, `steps`.

`trace.steps` maps onto the sequence Goal (scenario message) ->
Decision -> Action -> Intermediate Result -> Adaptation -> Final Outcome
(`verification.outcome`).

### `POST /api/resolution/eval`
Body: `{ "agent_version": "v2_verified", "scenario_ids": null, "trials": 3 }`
(`trials` is capped at 5.)

Returns `{ run_id, agent_version, trials_per_scenario, summary, results[] }`.
`summary` has `pass_rate`, `pass_hat_k` (share of scenarios that passed on
every trial), `k`, and `failure_mode_breakdown`. The run is also saved to the
run history as agent version `resolution/<version>`, so it appears in `GET /api/runs`
and the Regression Tracker.

### `GET /api/resolution/reports`
Lists suite reports saved by `python run_resolution.py ... --save` in
`data/traces/`, newest first: `{ "reports": [{name, agent_version,
trials_per_scenario, scenarios, pass_rate, pass_hat_k, errors, aborted}] }`.

### `GET /api/resolution/reports/{name}`
Returns one saved report in the same shape as `POST /api/resolution/eval`.
These are recordings of real runs; the dashboard labels them as recordings.
`data/traces/` is gitignored, so a fresh deploy has none.
