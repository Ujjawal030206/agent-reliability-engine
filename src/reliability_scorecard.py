"""Aggregates per-scenario verdicts into a Reliability Scorecard."""

from collections import Counter


def build_scorecard(scenario_results: list) -> dict:
    total = len(scenario_results)
    passed = sum(1 for r in scenario_results if r["classification"]["verdict"] == "pass")
    failed = total - passed
    score = round((passed / total) * 100, 1) if total else 0.0

    mode_counter = Counter()
    for r in scenario_results:
        for mode in r["classification"]["failure_modes"]:
            mode_counter[mode] += 1

    return {
        "score": score,
        "total": total,
        "passed": passed,
        "failed": failed,
        "failure_mode_breakdown": dict(mode_counter.most_common()),
    }
