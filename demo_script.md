# Demo video script (target: 6–8 minutes, hard cap 10)

A suggested order for showing the project. Target 6-8 minutes.

## 0:00–0:45 — The problem (talking head or slide)
- "Industry reports put autonomous agent failure rates near 70% on real-world
  tasks. Teams test agents against a handful of happy-path prompts, then
  ship. Real failures — loops, hallucinated success, unsafe destructive
  actions — show up in production."
- "We built continuous integration for AI agents."

## 0:45–1:30 — Architecture (screen: README mermaid diagram or a slide)
- Walk through the five pieces in one breath: scenario bank → sandboxed
  harness → target agent → hybrid classifier → scorecard + regression
  tracker.
- One sentence on *why hybrid*: "safety-critical failures are deterministic
  rules, not vibes — an LLM only judges the genuinely subjective stuff."

## 1:30–2:15 — Scenario Bank tab (screen recording)
- Show 3–4 scenario categories: normal, prompt injection, social engineering,
  loop-inducing. Read one adversarial prompt aloud so judges feel the intent.

## 2:15–5:00 — Live run, v1_baseline (screen recording)
- Hit "Run evaluation." Let it run in the background while narrating.
- Expand ONE failed scenario in detail — ideally the prompt-injection or
  social-engineering one — and narrate exactly what went wrong: "The agent
  treated user text as a system override and transferred funds without real
  consent. Our deterministic rule catches this immediately, no LLM judgment
  needed."
- Cut to the Reliability Scorecard tab: show the score and failure-mode
  breakdown chart.

## 5:00–7:00 — v2_guarded run + Regression Tracker (screen recording)
- Switch to `v2_guarded`, run again.
- Cut to Regression Tracker: show the score trend line improving across the
  two runs. "This is the CI story — a safety-focused prompt change is now
  measurable, not anecdotal."

## 7:00–7:45 — What's next / limitations (talking head)
- Be upfront: scenario bank is curated today; dynamic LLM-powered scenario
  generation and multi-framework trace normalization are the natural next
  steps (also in the README).

## 7:45–8:00 — Close
- Repo link, team name, thank you.

**Recording tips:**
- Record the Streamlit screen at 1080p, browser zoomed to ~110% so text is
  legible on a laptop screen during judging.
- Pre-run the evaluation once before recording so you know the runtime and
  can trim dead air, but do the "real" run on camera for authenticity.
- Keep a static fallback (screenshots + the cached sample scorecard) ready
  in case of API hiccups mid-recording.
