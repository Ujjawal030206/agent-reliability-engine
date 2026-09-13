"""
Self-hardening loop for the Customer Resolution Agent.

The reliability engine stops only grading the agent and starts fixing it:

    1. run the scenario suite and collect the verifier's findings
    2. ask a patcher model for ONE general rule that would prevent the most
       common failure
    3. re-run the whole suite with that rule added to the agent's prompt
    4. keep the rule only if it fixed at least one scenario and broke none;
       otherwise discard it
    5. repeat until nothing fails or the round budget runs out

The accept/reject gate is deterministic: it compares which scenarios passed
every trial before and after. An LLM proposes a change, but never decides
whether the change worked.
"""

import json
import os
import re
from collections import Counter

from src import resolution_agent, resolution_eval

PATCHER_SYSTEM_PROMPT = (
    "You improve the operating rules of an AI customer-resolution agent. You are given the extra rules it "
    "already follows and the verifier's findings from test cases it failed. Propose exactly ONE new rule, "
    "one or two sentences, that would prevent the most frequent failure without making the agent refuse or "
    "escalate legitimate requests. The rule must generalise: do not mention scenario names, order IDs, "
    "amounts or customer names. Respond with only the rule text."
)


def compose_prompt(base_prompt: str, rules: list) -> str:
    if not rules:
        return base_prompt
    numbered = "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules, 1))
    return f"{base_prompt}\n\nAdditional rules learned from failed test cases:\n{numbered}"


def passing_set(report: dict) -> set:
    return {r["scenario_id"] for r in report["results"] if r["passed_every_trial"]}


def collect_failures(report: dict, scenarios_by_id: dict) -> list:
    failures = []
    for result in report["results"]:
        scenario = scenarios_by_id.get(result["scenario_id"], {})
        for trial in result["trials"]:
            if "error" in trial or trial["verification"]["verdict"] == "pass":
                continue
            failures.append({
                "scenario": scenario.get("title", result["scenario_id"]),
                "what_it_tests": scenario.get("what_it_tests", ""),
                "failure_modes": trial["verification"]["failure_modes"],
                "findings": [f["detail"] for f in trial["verification"]["findings"]][:3],
            })
    return failures


def clean_rule(text: str) -> str:
    rule = " ".join((text or "").split())
    rule = re.sub(r"^(rule\s*\d*\s*[:.\-]\s*|[-*\d.)\s]+)", "", rule, flags=re.I)
    return rule.strip().strip("\"'").strip()[:400]


def propose_rule(client, rules: list, failures: list, model: str = None) -> str:
    payload = {"current_extra_rules": rules, "failed_cases": failures[:8]}
    response = client.messages.create(
        model=model or os.environ.get("JUDGE_MODEL") or "claude-sonnet-5",
        max_tokens=300,
        system=PATCHER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload)}],
    )
    return clean_rule("".join(b.text for b in response.content if b.type == "text"))


def gate(before: dict, after: dict):
    """Accept a candidate only if it fixes something and breaks nothing."""
    if after["summary"].get("errors") or after.get("aborted"):
        return False, "provider errors in the candidate run, so it cannot be compared fairly", [], []
    was, now = passing_set(before), passing_set(after)
    fixed, regressions = sorted(now - was), sorted(was - now)
    if regressions:
        return False, f"broke {', '.join(regressions)}", fixed, regressions
    if not fixed:
        return False, "fixed nothing", fixed, regressions
    return True, f"fixed {', '.join(fixed)} with no regressions", fixed, regressions


def harden(client, scenarios: list, base_version: str = "v1_baseline", rounds: int = 3, trials: int = 1,
           model: str = None, patch_model: str = None, on_round=None, on_trial=None) -> dict:
    base_prompt = resolution_agent.SYSTEM_PROMPTS[base_version]
    by_id = {s["id"]: s for s in scenarios}

    def evaluate(rule_list):
        label = base_version if not rule_list else f"{base_version}+{len(rule_list)}rule"
        return resolution_eval.run_suite(client, scenarios, label, trials=trials, model=model,
                                         on_trial=on_trial, system_prompt=compose_prompt(base_prompt, rule_list))

    rules = []
    current = evaluate(rules)
    history = [{"round": 0, "rule": None, "accepted": None, "summary": current["summary"],
                "passing": sorted(passing_set(current))}]
    if on_round:
        on_round(history[0])

    stopped = "round budget used up"
    for round_no in range(1, rounds + 1):
        if current.get("aborted") or current["summary"].get("errors"):
            stopped = "provider errors, so results cannot be compared"
            break
        failures = collect_failures(current, by_id)
        if not failures:
            stopped = "every scenario passes"
            break

        rule = propose_rule(client, rules, failures, patch_model)
        entry = {"round": round_no, "rule": rule,
                 "targeted_failure_modes": dict(Counter(m for f in failures for m in f["failure_modes"]))}
        if not rule:
            entry.update(accepted=False, reason="the patcher proposed no rule", fixed=[], regressions=[])
        else:
            candidate = evaluate(rules + [rule])
            accepted, reason, fixed, regressions = gate(current, candidate)
            entry.update(accepted=accepted, reason=reason, fixed=fixed, regressions=regressions,
                         summary=candidate["summary"], passing=sorted(passing_set(candidate)))
            if accepted:
                rules = rules + [rule]
                current = candidate
        history.append(entry)
        if on_round:
            on_round(entry)
    else:
        if not collect_failures(current, by_id) and not current["summary"].get("errors"):
            stopped = "every scenario passes"

    return {
        "base_version": base_version,
        "trials_per_scenario": trials,
        "scenarios": [s["id"] for s in scenarios],
        "rules": rules,
        "final_prompt": compose_prompt(base_prompt, rules),
        "initial_summary": history[0]["summary"],
        "final_summary": current["summary"],
        "rounds": history,
        "stopped": stopped,
    }
