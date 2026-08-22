import os
import json
import uuid

import streamlit as st
import pandas as pd
from dotenv import load_dotenv
import llm_providers

from src import agent_under_test, failure_classifier, reliability_scorecard, db, scenario_generator, mock_tools

load_dotenv()

st.set_page_config(page_title="Agent Reliability Engine", page_icon="🛡️", layout="wide")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

with open(os.path.join(DATA_DIR, "scenario_bank.json")) as f:
    SCENARIO_BANK = json.load(f)

# Provider-agnostic: whichever backend LLM_PROVIDER names (Anthropic, or a free
# OpenAI-compatible tier via llm_providers.py). See .env.example.
API_KEY = llm_providers.is_configured()
JUDGE_MODEL = llm_providers.judge_model()
PROVIDER = llm_providers.provider_name()

st.title("🛡️ AI Agent Evaluation & Reliability Engine")
st.caption("Continuous integration for autonomous agents")

tab_overview, tab_run, tab_scorecard, tab_regression, tab_bank = st.tabs(
    ["Overview", "Run Evaluation", "Reliability Scorecard", "Regression Tracker", "Scenario Bank"]
)

# ---------------------------------------------------------------- Overview
with tab_overview:
    st.markdown("""
### What this is
A sandboxed testing platform for autonomous AI agents. Point it at a target
agent — here, a small customer-support agent called **Riley**, built for this
demo — and it will:

1. **Run realistic + adversarial test scenarios** — normal requests, prompt
   injection attempts, social-engineering pressure toward destructive actions,
   loop-inducing tasks, and hallucination bait (see the *Scenario Bank* tab).
2. **Execute the agent in a sandbox** — every tool call is mocked and logged.
   Nothing the agent does can touch a real system.
3. **Classify failures with a hybrid approach**: hard deterministic safety
   rules (an irreversible action executed without real user confirmation is
   *always* a fail — no LLM opinion needed) layered under an LLM-as-judge for
   softer failure modes like goal drift or ignoring the user's actual request.
4. **Produce a Reliability Scorecard** and track it across agent versions —
   continuous integration for autonomous agents.

Use the tabs above: pick an agent version, run the evaluation, then check the
scorecard and regression tracker.
""")
    if not API_KEY:
        st.warning(
            f"No API key found for provider `{PROVIDER}`. You can still explore the UI — the "
            "**Reliability Scorecard** tab will show cached sample data. Copy `.env.example` to "
            "`.env` and fill in one provider block (Groq and Gemini have free tiers) to run "
            "live evaluations."
        )
    else:
        st.caption(f"Provider: `{PROVIDER}` · agent `{llm_providers.agent_model()}` · judge `{JUDGE_MODEL}`")

# ---------------------------------------------------------------- Run
with tab_run:
    st.subheader("Run an evaluation")
    version = st.selectbox("Agent version to test", list(agent_under_test.SYSTEM_PROMPTS.keys()))
    st.code(agent_under_test.SYSTEM_PROMPTS[version], language="text")

    full_pool = SCENARIO_BANK + st.session_state.get("dynamic_scenarios", [])
    if st.session_state.get("dynamic_scenarios"):
        st.caption(f"Pool includes {len(st.session_state['dynamic_scenarios'])} scenario(s) "
                   "generated live in the Scenario Bank tab.")

    n_scenarios = st.slider("Number of scenarios to run", 1, len(full_pool),
                             min(6, len(full_pool)))
    run_button = st.button("▶ Run evaluation", type="primary", disabled=not API_KEY)

    if not API_KEY:
        st.info(f"Configure a key for provider `{PROVIDER}` in `.env` to enable live runs.")

    if run_button and API_KEY:
        client = llm_providers.get_client()
        scenarios = full_pool[:n_scenarios]
        results = []
        progress = st.progress(0.0, text="Starting...")

        for i, scenario in enumerate(scenarios):
            progress.progress(i / len(scenarios), text=f"Running: {scenario['id']}")
            trace = agent_under_test.run_agent_turn(client, scenario["prompt"], system_prompt_key=version)
            classification = failure_classifier.classify(client, trace, scenario, JUDGE_MODEL)
            results.append({"scenario": scenario, "trace": trace, "classification": classification})

            icon = "✅" if classification["verdict"] == "pass" else "❌"
            with st.expander(f"{icon} {scenario['id']} — {scenario['category']}"):
                st.markdown(f"**Prompt:** {scenario['prompt']}")
                st.markdown(f"**Final agent response:** {trace['final_text']}")
                if trace["tool_calls"]:
                    st.markdown("**Tool calls:**")
                    st.json(trace["tool_calls"])
                if classification["failure_modes"]:
                    st.markdown(f"**Failure modes:** `{', '.join(classification['failure_modes'])}`")
                if classification["judge_explanation"]:
                    st.markdown(f"**Judge notes:** {classification['judge_explanation']}")

        progress.progress(1.0, text="Done.")

        scorecard = reliability_scorecard.build_scorecard(results)
        run_id = str(uuid.uuid4())[:8]
        db.save_run(
            run_id, version, scorecard["score"], scorecard["total"],
            scorecard["passed"], scorecard["failed"],
            [{"scenario_id": r["scenario"]["id"], "classification": r["classification"]} for r in results],
        )

        st.session_state["last_scorecard"] = scorecard
        st.success(f"Run complete — score {scorecard['score']}/100. See the Reliability Scorecard tab.")

# ---------------------------------------------------------------- Scorecard
with tab_scorecard:
    st.subheader("Reliability Scorecard")
    scorecard = st.session_state.get("last_scorecard")

    if not scorecard:
        sample_path = os.path.join(DATA_DIR, "sample_run_results.json")
        if os.path.exists(sample_path):
            with open(sample_path) as f:
                scorecard = json.load(f)["scorecard"]
            st.caption("Showing cached sample data — run a live evaluation to replace this.")
        else:
            st.info("Run an evaluation first.")

    if scorecard:
        c1, c2, c3 = st.columns(3)
        c1.metric("Reliability Score", f"{scorecard['score']}/100")
        c2.metric("Passed", scorecard["passed"])
        c3.metric("Failed", scorecard["failed"])

        if scorecard["failure_mode_breakdown"]:
            st.markdown("**Failure mode breakdown**")
            df = pd.DataFrame(
                list(scorecard["failure_mode_breakdown"].items()), columns=["Failure mode", "Count"]
            ).set_index("Failure mode")
            st.bar_chart(df)
        else:
            st.success("No failure modes detected in this run.")

# ---------------------------------------------------------------- Regression
with tab_regression:
    st.subheader("Regression Tracker")
    runs = db.get_all_runs()
    if not runs:
        st.info("No runs recorded yet. Run evaluations against different agent versions to start "
                 "tracking reliability over time.")
    else:
        df = pd.DataFrame(runs)
        df["time"] = pd.to_datetime(df["timestamp"], unit="s")
        st.line_chart(df.set_index("time")[["score"]])
        st.dataframe(
            df[["run_id", "agent_version", "time", "score", "passed", "failed"]],
            use_container_width=True,
        )

# ---------------------------------------------------------------- Scenario Bank
with tab_bank:
    st.subheader("Scenario Bank")
    st.caption("Realistic + adversarial test scenarios covering the failure modes called out in the "
               "target failure modes: tool-call loops, hallucinated confidence, unsafe destructive "
               "actions, and silent goal drift.")
    df = pd.DataFrame(SCENARIO_BANK)
    st.dataframe(df[["id", "category", "prompt"]], use_container_width=True)

    st.divider()
    st.markdown("### 🎲 Scenario Generation Engine")
    st.caption("Ask Claude to synthesize new adversarial scenarios targeting this agent's actual "
               "tools, on top of the curated bank above — this is what makes the bank a generator, "
               "not just a fixture file.")

    if "dynamic_scenarios" not in st.session_state:
        st.session_state["dynamic_scenarios"] = []

    gen_col1, gen_col2 = st.columns([3, 1])
    with gen_col1:
        n_gen = st.number_input("How many new scenarios to generate", min_value=1, max_value=10, value=5)
    with gen_col2:
        st.write("")
        st.write("")
        gen_button = st.button("Generate", disabled=not API_KEY)

    if not API_KEY:
        st.info(f"Configure a key for provider `{PROVIDER}` in `.env` to enable live scenario generation.")

    if gen_button and API_KEY:
        client = llm_providers.get_client()
        with st.spinner("Generating adversarial scenarios..."):
            new_scenarios = scenario_generator.generate_scenarios(
                client,
                agent_description="Riley, a customer support agent for ShopFast (an online store) "
                                   "that can look up orders, issue refunds, send emails, escalate to "
                                   "a human, delete customer accounts, and transfer funds.",
                tool_names=[t["name"] for t in mock_tools.TOOL_SCHEMAS],
                existing_scenarios=SCENARIO_BANK + st.session_state["dynamic_scenarios"],
                n=int(n_gen),
                model=JUDGE_MODEL,
            )
        if new_scenarios:
            st.session_state["dynamic_scenarios"].extend(new_scenarios)
            st.success(f"Generated {len(new_scenarios)} new scenario(s) — added to the run pool.")
        else:
            st.error("Generation failed or returned no valid scenarios. Try again.")

    if st.session_state["dynamic_scenarios"]:
        st.markdown("**Generated scenarios (included in Run Evaluation pool):**")
        gen_df = pd.DataFrame(st.session_state["dynamic_scenarios"])
        st.dataframe(gen_df[["id", "category", "prompt"]], use_container_width=True)
        if st.button("Clear generated scenarios"):
            st.session_state["dynamic_scenarios"] = []
            st.rerun()
