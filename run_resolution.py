"""
Run the Customer Resolution Agent against the stateful sandbox from the terminal.

    python run_resolution.py --list
    python run_resolution.py R03                       # scenario ID or prefix
    python run_resolution.py R04 --version v1_baseline
    python run_resolution.py --all --trials 3 --save

Each run prints as Goal -> Decision -> Action -> Result -> Adaptation ->
Outcome, followed by the verifier's verdict. --save appends every finished
trial to data/traces/<timestamp>_<version>.jsonl as it goes, then writes the
full report JSON (traces, final database state, findings) at the end, so a
run stopped by a rate limit still keeps what it finished.
"""

import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

import llm_providers  # noqa: E402
from src import resolution_agent, resolution_eval  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
SCENARIO_PATH = os.path.join(ROOT, "data", "resolution_scenarios.json")
TRACE_DIR = os.path.join(ROOT, "data", "traces")


def _short(value, limit=140):
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def print_trial(scenario, run):
    print(f"\n=== {scenario['id']} | trial {run['trial']}")
    if "error" in run:
        print(f"ERROR     {run['error']} (not counted as an agent failure)")
        return

    trace, verification = run["trace"], run["verification"]
    print(f"VERSION   {trace['agent_version']} ({trace['model']})")
    print(f"GOAL      {_short(trace['goal'], 300)}")
    shown_iteration = None
    for step in trace["steps"]:
        if step["decision"] and step["iteration"] != shown_iteration:
            label = "DECISION" if step.get("decision_source") == "stated" else "REASONING"
            print(f"{label:<9} {_short(step['decision'], 220)}")
        shown_iteration = step["iteration"]
        if step["adaptation"]:
            print(f"ADAPT     reacting to step {step['adaptation']['after_step']}: "
                  f"{_short(step['adaptation']['problem'], 110)}")
        if step["retry_of"]:
            print(f"RETRY     same call as step {step['retry_of']}")
        print(f"ACTION    [{step['index']}] {step['tool']} {_short(step['input'], 120)}")
        print(f"RESULT    {step['status'].upper()} {_short(step['result'])}")
        for event in step["env_events"]:
            if not event["visible_to_agent"]:
                print(f"WORLD     {event['note'] or event['effect']} (the agent is not told)")
    if trace["final_text"]:
        print(f"REPLY     {_short(trace['final_text'], 400)}")
    o = verification["outcome"]
    print(f"OUTCOME   {o['resolution']} | refunded INR {o['total_refunded']:g} in {o['refund_count']} refund(s) | "
          f"replacements {o['replacements']} | escalated {o['escalated']} | adaptations {o['adaptations']} | "
          f"stop={trace['stop_reason']} | {trace['llm_calls']} LLM calls, {trace['duration_s']}s")
    print(f"VERDICT   {verification['verdict'].upper()}")
    for finding in verification["findings"]:
        print(f"  - {finding['mode']}: {finding['detail']}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        # Replace characters the console lacks, and flush per line so progress shows when output is redirected.
        sys.stdout.reconfigure(errors="replace", line_buffering=True)

    parser = argparse.ArgumentParser(description="Run resolution scenarios against the agent.")
    parser.add_argument("scenario", nargs="*", help="scenario IDs or prefixes, e.g. R03")
    parser.add_argument("--all", action="store_true", help="run every scenario")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--version", default="v2_verified", choices=sorted(resolution_agent.SYSTEM_PROMPTS))
    parser.add_argument("--trials", type=int, default=1, help="runs per scenario, for pass^k")
    parser.add_argument("--save", action="store_true", help="save trials and the report under data/traces/")
    args = parser.parse_args()

    with open(SCENARIO_PATH) as f:
        scenarios = json.load(f)

    if args.list:
        for s in scenarios:
            print(f"{s['id']:<40} {s['title']}")
        return

    chosen = scenarios if args.all else [s for s in scenarios if any(s["id"].startswith(q) for q in args.scenario)]
    if not chosen:
        parser.error("name at least one scenario, or use --all / --list")

    stem = os.path.join(TRACE_DIR, f"{time.strftime('%Y%m%d-%H%M%S')}_{args.version}")
    if args.save:
        os.makedirs(TRACE_DIR, exist_ok=True)

    def on_trial(scenario, run):
        print_trial(scenario, run)
        if args.save:
            with open(stem + ".jsonl", "a") as f:
                f.write(json.dumps({"scenario_id": scenario["id"], **run}, default=str) + "\n")

    try:
        client = llm_providers.get_client()
    except RuntimeError as exc:
        sys.exit(f"error: {exc}")
    report = resolution_eval.run_suite(client, chosen, args.version, trials=max(1, args.trials), on_trial=on_trial)

    s = report["summary"]
    print(f"\n=== SUMMARY {args.version}: {s['passed']}/{s['trials']} completed trials passed ({s['pass_rate']}%), "
          f"pass^{s['k']} {s['pass_hat_k']}% of {s['pass_hat_k_scenarios']} fully-run scenarios, "
          f"{s['errors']} provider error(s), {s['scenarios']}/{report['scenarios_requested']} scenarios reached")
    if s["failure_mode_breakdown"]:
        print("failure modes: " + ", ".join(f"{m} x{n}" for m, n in s["failure_mode_breakdown"].items()))
    if report["aborted"]:
        print(f"ABORTED   {report['aborted']}")

    if args.save:
        with open(stem + ".json", "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"saved {stem}.json")


if __name__ == "__main__":
    main()
