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
state; don't expect it to return instantly. **If your frontend needs live
per-scenario progress instead of one big blocking response, ask Claude Code
to convert this into a Server-Sent Events (SSE) stream — the loop in
`server.py`'s `run_evaluation` is already scenario-by-scenario, so it's a
small change, not a rewrite.**

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
This is the headline feature — render `transcript.turns` as an actual
back-and-forth chat (attacker vs. target), ideally revealed turn by turn for
dramatic effect on camera, then show the verdict.

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

Drop your Stitch export (`index.html` + its CSS/JS assets) into `./static/`
— it's served automatically at `/`, and `/api/*` stays untouched alongside it.
