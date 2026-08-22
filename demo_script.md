# Demo video script (target: 6–8 minutes, hard cap 10)

A suggested order for showing the project. Target 6-8 minutes.

**Record the web dashboard** (`uvicorn server:app --reload --port 8000`, then
`http://localhost:8000`), not the Streamlit app. The dashboard is the
purpose-built UI and the red-team chat only exists there. Streamlit (`app.py`)
still works and can be shown as the original prototype if you have time — but
don't split the demo between both.

Its four views, left nav: **Scenarios · Runs · Analytics · Red Teaming.**

---

## 0:00–0:45 — The problem (talking head or slide)

- "Industry reports put autonomous agent failure rates near 70% on real-world
  tasks. Teams test agents against a handful of happy-path prompts, then
  ship. Real failures — loops, hallucinated success, unsafe destructive
  actions — show up in production."
- "We built continuous integration for AI agents."

## 0:45–1:30 — Architecture (screen: README mermaid diagram or a slide)

- Walk through the five pieces in one breath: scenario bank → sandboxed
  harness → target agent → hybrid classifier → scorecard + regression tracker.
- One sentence on *why hybrid*: "safety-critical failures are deterministic
  rules, not vibes — an LLM only judges the genuinely subjective stuff."

## 1:30–2:15 — Scenarios view

- Show 3–4 categories via the filter: normal, prompt injection, social
  engineering, loop-inducing. Read one adversarial prompt aloud so judges feel
  the intent — `S05_prompt_injection_transfer` lands well.
- Point out the `guardrail` chip: these are the scenarios where acting without
  real consent is an automatic fail.

## 2:15–4:30 — Live run, v1_baseline (Runs view)

- Set the scenario count, hit **Run Evaluation**. A full 15-scenario run takes
  about 2.5 minutes on the free tier — narrate the architecture over it, or cut.
- When it lands: score ring, pass/fail counts, failure-mode breakdown.
- Expand ONE failed scenario and narrate what went wrong. The expandable log
  shows the agent's response, **every mocked tool call with its arguments and
  result**, the deterministic rule finding, and the judge's explanation.
  - The money line: "The agent called `transfer_funds` with `confirmed=true`.
    Our deterministic rule catches that immediately — no LLM judgment needed,
    because irreversible actions without consent are never a judgment call."
- Use the **FAILURES** filter to show only what broke.

## 4:30–5:30 — Red Teaming view ← *lead with this if you're short on time*

- This is the strongest feature and the most watchable. Pick a preset vector,
  hit **Launch Attack**.
- The transcript replays turn by turn as a chat: attacker in red on the left,
  Riley in indigo on the right. Narrate the attacker *adapting* — it changes
  tactic when refused, which a static scenario bank cannot do.
- End on the verdict panel: guardrail held or breached, failure modes, the
  tool calls made during the attack.
- Set **Reveal Speed** to Normal for pacing; Instant if you're trimming.

## 5:30–7:00 — v2_guarded + Analytics

- Switch the version picker to `v2_guarded`, run again, then open **Analytics**.
- **Be honest about what the numbers show.** On our free-tier model both
  versions scored 73.3 — the safety prompt fixed the social-engineering
  scenario it targeted and cut hard rule violations from 2 to 1, but it
  regressed a different scenario.
- That is a *better* story than a clean improvement, and it's true: "The
  guarded prompt fixed the failure it was written for and introduced a new one.
  Without a regression tracker you would have shipped that trade blind. This is
  exactly why agent reliability needs CI."
- Re-run before recording and use whatever your real numbers are.

## 7:00–7:45 — What's next / limitations (talking head)

Be upfront — judges reward honesty over polish:

- Single hardcoded target agent today; "bring your own agent" (paste a system
  prompt + tool schema) is the next step, and the classifier and scorecard are
  already agent-agnostic.
- Evaluates Anthropic-shaped tool-use agents; normalizing traces from
  LangChain / CrewAI / raw OpenAI is the generalization.
- The LLM-judge layer is noisier on smaller free-tier models than on frontier
  ones. The deterministic rules — the safety-critical half — are unaffected.

## 7:45–8:00 — Close

- Repo link, team, thank you.

---

## Recording tips

- Record at 1080p, browser zoomed to ~110% so the mono labels stay legible.
- **Do a full dry run first.** Free-tier rate limits (Groq caps tokens per
  minute) mean back-to-back full runs can stall mid-demo. Run once, wait a
  minute, then record.
- Have the fallback ready: toggle **Demo mode** in the header to render bundled
  sample data instantly if the API misbehaves on camera. If you show demo mode
  in the video, **say so out loud** — the banner is visible, and claiming canned
  data is a live run is the one thing that would sink the submission.
- The dashboard loads Tailwind and fonts from a CDN, so make sure the recording
  machine is online or it renders unstyled.
