"""
Runs the resolution scenario suite and scores it.

Agents are not deterministic, so one pass proves little. Each scenario can be
run k times; the summary reports both the plain pass rate and pass^k - the
share of scenarios that passed on every one of the k trials, which is the
number that says whether the agent can be trusted with that case.

A trial that still fails on a provider problem (rate limit, outage, bad key)
after one retry is recorded as an error, not as an agent failure, and is left out of the pass
rate. Two errors in a row stop the suite, keeping what already finished.
"""

from collections import Counter

from src import outcome_verifier, resolution_agent

MAX_CONSECUTIVE_ERRORS = 2


def run_trial(client, scenario: dict, agent_version: str, model: str = None, system_prompt: str = None) -> dict:
    trace = resolution_agent.run_case(client, scenario, agent_version, model=model, system_prompt=system_prompt)
    return {"trace": trace, "verification": outcome_verifier.verify(trace, scenario)}


def _attempt(client, scenario: dict, agent_version: str, model: str, system_prompt: str, attempts: int = 2) -> dict:
    """One trial, retried if the provider fails. Each attempt gets a fresh sandbox."""
    for _ in range(attempts):
        try:
            return run_trial(client, scenario, agent_version, model=model, system_prompt=system_prompt)
        except Exception as exc:  # provider-side failures surface here; keep the finished trials
            error = f"{type(exc).__name__}: {exc}"
    return {"error": error, "attempts": attempts}


def run_suite(client, scenarios: list, agent_version: str, trials: int = 1, model: str = None,
              on_trial=None, system_prompt: str = None) -> dict:
    results = []
    consecutive_errors = 0
    aborted = None

    for scenario in scenarios:
        runs = []
        for k in range(1, trials + 1):
            run = _attempt(client, scenario, agent_version, model, system_prompt)
            consecutive_errors = consecutive_errors + 1 if "error" in run else 0
            run["trial"] = k
            runs.append(run)
            if on_trial:
                on_trial(scenario, run)
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                aborted = f"Stopped after {consecutive_errors} consecutive errors. Last: {run['error']}"
                break
        results.append(_scenario_result(scenario, runs, trials))
        if aborted:
            break

    return {
        "agent_version": agent_version,
        "trials_per_scenario": trials,
        "scenarios_requested": len(scenarios),
        "aborted": aborted,
        "summary": summarize(results, trials),
        "results": results,
    }


def _scenario_result(scenario: dict, runs: list, trials: int) -> dict:
    completed = [r for r in runs if "error" not in r]
    passes = sum(1 for r in completed if r["verification"]["verdict"] == "pass")
    return {
        "scenario_id": scenario["id"],
        "title": scenario.get("title", ""),
        "trials": runs,
        "completed": len(completed),
        "errors": len(runs) - len(completed),
        "passes": passes,
        "pass_rate": round(100 * passes / len(completed), 1) if completed else None,
        "passed_every_trial": len(completed) == trials and passes == trials,
    }


def summarize(results: list, trials: int) -> dict:
    completed = sum(r["completed"] for r in results)
    passed = sum(r["passes"] for r in results)
    fully_run = [r for r in results if r["completed"] == trials]
    modes = Counter(mode for r in results for t in r["trials"] if "error" not in t
                    for mode in t["verification"]["failure_modes"])
    return {
        "scenarios": len(results),
        "trials": completed,
        "errors": sum(r["errors"] for r in results),
        "passed": passed,
        "failed": completed - passed,
        "pass_rate": round(100 * passed / completed, 1) if completed else 0.0,
        "k": trials,
        "pass_hat_k": round(100 * sum(r["passed_every_trial"] for r in fully_run) / len(fully_run), 1)
        if fully_run else 0.0,
        "pass_hat_k_scenarios": len(fully_run),
        "failure_mode_breakdown": dict(modes.most_common()),
    }
