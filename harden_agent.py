"""
Run the self-hardening loop from the terminal.

    python harden_agent.py --rounds 3                   # all scenarios, from v1_baseline
    python harden_agent.py R02 R10 --rounds 2 --save    # a subset, saved to data/traces/hardening/

Each round: collect the verifier's findings, have the patcher propose one
rule, re-run the suite with it, and keep it only if it fixed a scenario and
broke none. Every round re-runs the whole chosen suite, so a round costs as
much as a suite run.
"""

import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

import llm_providers  # noqa: E402
from src import hardening_loop, resolution_agent  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
SCENARIO_PATH = os.path.join(ROOT, "data", "resolution_scenarios.json")
OUT_DIR = os.path.join(ROOT, "data", "traces", "hardening")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace", line_buffering=True)

    parser = argparse.ArgumentParser(description="Self-harden the resolution agent against the scenario suite.")
    parser.add_argument("scenario", nargs="*", help="scenario IDs or prefixes (default: all)")
    parser.add_argument("--from", dest="base", default="v1_baseline", choices=sorted(resolution_agent.SYSTEM_PROMPTS))
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--model", help="agent model to harden (default: AGENT_MODEL); the patcher uses JUDGE_MODEL")
    parser.add_argument("--save", action="store_true", help="write the result to data/traces/hardening/")
    args = parser.parse_args()

    with open(SCENARIO_PATH) as f:
        scenarios = json.load(f)
    if args.scenario:
        scenarios = [s for s in scenarios if any(s["id"].startswith(q) for q in args.scenario)]
    if not scenarios:
        parser.error("no scenarios matched")

    def on_trial(scenario, run):
        verdict = "ERROR" if "error" in run else run["verification"]["verdict"].upper()
        modes = run["error"][:200] if "error" in run else ", ".join(run["verification"]["failure_modes"])
        print(f"    {scenario['id'][:3]} T{run['trial']} {verdict} {modes}")

    def on_round(entry):
        if entry["round"] == 0:
            s = entry["summary"]
            print(f"\nBASELINE  {args.base}: {s['passed']}/{s['trials']} passed ({s['pass_rate']}%)")
            return
        print(f"\nROUND {entry['round']}  targeting {entry['targeted_failure_modes']}")
        print(f"  RULE      {entry['rule'] or '(none proposed)'}")
        if "summary" in entry:
            print(f"  RESULT    {entry['summary']['passed']}/{entry['summary']['trials']} passed "
                  f"({entry['summary']['pass_rate']}%)")
        print(f"  {'ACCEPTED' if entry['accepted'] else 'REJECTED'}  {entry['reason']}")

    try:
        client = llm_providers.get_client()
        print(f"Hardening {args.base} on {len(scenarios)} scenario(s), up to {args.rounds} round(s)")
        result = hardening_loop.harden(client, scenarios, base_version=args.base, rounds=args.rounds,
                                       trials=max(1, args.trials), model=args.model,
                                       on_round=on_round, on_trial=on_trial)
    except (RuntimeError, llm_providers.ProviderError) as exc:
        sys.exit(f"error: {exc}")

    first, last = result["initial_summary"], result["final_summary"]
    print(f"\n=== DONE ({result['stopped']}): {first['pass_rate']}% -> {last['pass_rate']}% "
          f"with {len(result['rules'])} accepted rule(s)")
    for i, rule in enumerate(result["rules"], 1):
        print(f"  {i}. {rule}")

    if args.save:
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, f"{time.strftime('%Y%m%d-%H%M%S')}_{args.base}.json")
        with open(path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
