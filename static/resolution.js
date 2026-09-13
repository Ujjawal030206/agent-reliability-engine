/* Resolution Agent view.
 *
 * Endpoints used:
 *   GET  /api/resolution/scenarios      POST /api/resolution/run
 *   POST /api/resolution/eval           GET  /api/resolution/reports
 *   GET  /api/resolution/reports/{name}
 *
 * Always live. Demo mode does not apply here: canned agent behaviour shown as
 * if it were a run is exactly what the verifier exists to catch. Recordings
 * saved by `run_resolution.py --save` are real traces, replayed, and every
 * surface that shows one labels it as a recording.
 *
 * Loaded after app.js and reuses its helpers ($, esc, chip, modeLabel, api,
 * toast, sleep, showView, loadRuns).
 */

VIEW_META.resolution = {
  tag: "RESOLUTION_AGENT",
  sub: "/ stateful sandbox · deterministic verification",
  title: "Resolution Agent",
};

const rs = { scenarios: [], selected: null, busy: false, renderToken: 0, suite: null, timer: null };

const GREEN = "text-[#7ee08a] border-[#7ee08a]/40 bg-[#7ee08a]/10";
const RED = "text-error border-error/40 bg-error/10";
const PINK = "text-tertiary border-tertiary/40 bg-tertiary/10";
const LILAC = "text-secondary border-secondary/40 bg-secondary/10";
const INDIGO = "text-primary border-primary/40 bg-primary/10";
const MUTED = "text-on-surface-variant border-outline-variant bg-surface-container-lowest";

const RS_KIND = {
  read: ["read", LILAC],
  write: ["state change", INDIGO],
  ask: ["ask customer", PINK],
  verify: ["verify", GREEN],
  escalate: ["escalate", RED],
};
const RS_STATUS = { ok: GREEN, blocked: PINK, error: RED };
const RS_CATEGORY = {
  happy_path: GREEN,
  blocked_action: PINK,
  changed_condition: INDIGO,
  tool_failure: RED,
  policy_conflict: LILAC,
  prompt_injection: RED,
};

const rsScenario = (id) => rs.scenarios.find((s) => s.id === id);
const rsSourceChip = (source) => (source ? chip(source, source.startsWith("recorded") ? PINK : INDIGO) : "");
const rsVerdictChip = (pass) => chip(pass ? "PASS" : "FAIL", pass ? GREEN : RED);

const rsEmpty = () => `
  <div class="py-stack-lg text-center text-on-surface-variant font-body-md">
    <span class="material-symbols-outlined text-primary text-4xl">support_agent</span>
    <p class="mt-2 max-w-md mx-auto">Run this case live, or open a trial from a suite result or a recording.
      Each step is shown as Goal, Decision, Action, Intermediate result, Adaptation and Final outcome.</p>
  </div>`;

function rsBriefResult(result) {
  if (!result || typeof result !== "object") return String(result);
  if (result.status === "blocked") return "blocked: " + (result.reasons || []).join("; ");
  if (result.status === "error") return `error: ${result.error || ""} ${result.detail || ""}`.trim();
  const text = JSON.stringify(result);
  return text.length > 160 ? text.slice(0, 157) + "..." : text;
}

/* ------------------------------------------------------------ case brief */

function rsRenderBrief(scenario, source) {
  if (!scenario) {
    $("#rs-brief").innerHTML = "";
    return;
  }
  const events = scenario.events || [];
  const expect = scenario.expect || {};
  const expectLines = [`resolution: ${(expect.resolution_in || []).join(" | ")}`];
  if ("refund_total" in expect) expectLines.push(`refund total: INR ${expect.refund_total}`);
  if (expect.must_escalate) expectLines.push("must escalate");
  if (expect.must_not_escalate) expectLines.push("must not escalate");

  $("#rs-brief").innerHTML = `
    <div class="flex flex-wrap items-center gap-2 mb-2">
      <span class="font-label-mono text-label-mono text-primary">${esc(scenario.id)}</span>
      ${chip(modeLabel(scenario.category), RS_CATEGORY[scenario.category] || MUTED)}
      ${rsSourceChip(source)}
    </div>
    <h3 class="font-headline-sm text-headline-sm text-on-surface">${esc(scenario.title)}</h3>
    <p class="font-body-md text-on-surface-variant mt-1">${esc(scenario.what_it_tests)}</p>
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-3">
      <div>
        <h5 class="font-label-mono text-label-mono text-on-surface-variant uppercase mb-1">Customer · ${esc(scenario.customer_id)}</h5>
        <div class="mono-block">${esc(scenario.customer_message)}</div>
      </div>
      <div>
        <h5 class="font-label-mono text-label-mono text-on-surface-variant uppercase mb-1">Injected disruptions</h5>
        ${events.length
          ? events.map((e) => `
            <div class="mono-block mb-1">
              <span class="text-error">${esc(e.effect.type)}</span>
              <span class="text-on-surface-variant">${esc(e.timing || "after")} ${esc([].concat(e.on).join(" / "))} #${esc(e.nth || 1)}</span>
              <br/>${esc(e.note || "")}
            </div>`).join("")
          : `<div class="mono-block text-on-surface-variant">none</div>`}
      </div>
      <div>
        <h5 class="font-label-mono text-label-mono text-on-surface-variant uppercase mb-1">Verifier expects</h5>
        <div class="mono-block">${expectLines.map(esc).join("<br/>")}<br/><span class="text-on-surface-variant">+ universal invariants</span></div>
      </div>
    </div>`;
}

/* ------------------------------------------------------------------ trace */

function rsStage(label, labelCls, body) {
  return `
    <div class="turn-in flex gap-3">
      <div class="w-32 shrink-0 pt-1">
        <span class="font-label-mono text-[10px] uppercase tracking-wider ${labelCls}">${esc(label)}</span>
      </div>
      <div class="flex-1 min-w-0">${body}</div>
    </div>`;
}

function rsBlocks(trace) {
  const blocks = [rsStage("Goal", "text-primary", `
    <div class="font-body-md text-on-surface whitespace-pre-wrap">${esc(trace.goal)}</div>
    <div class="font-label-mono text-[10px] text-on-surface-variant mt-1">
      verified customer ${esc(trace.customer_id)} · ${esc(trace.agent_version)} · ${esc(trace.model)}</div>`)];

  let shownIteration = null;
  for (const s of trace.steps || []) {
    if (s.decision && s.iteration !== shownIteration) {
      const source = s.decision_source === "model_reasoning" ? chip("model reasoning", MUTED) + " " : "";
      blocks.push(rsStage("Decision", "text-secondary",
        `<div class="font-body-md text-on-surface-variant">${source}${esc(s.decision)}</div>`));
    }
    shownIteration = s.iteration;

    if (s.adaptation) {
      blocks.push(rsStage("Adaptation", "text-tertiary", `
        <div class="rounded border border-tertiary/40 bg-tertiary/10 px-3 py-2 font-body-md text-on-surface">
          Changing course after step ${esc(s.adaptation.after_step)}:
          <span class="text-tertiary">${esc(s.adaptation.problem)}</span>
        </div>`));
    }
    if (s.retry_of) {
      blocks.push(rsStage("Retry", "text-on-surface-variant",
        `<div class="font-label-mono text-label-mono text-on-surface-variant">Same call as step ${esc(s.retry_of)}</div>`));
    }

    const [kindLabel, kindCls] = RS_KIND[s.kind] || [s.kind, MUTED];
    blocks.push(rsStage("Action", "text-on-surface", `
      <div class="flex flex-wrap items-center gap-2">
        <span class="font-label-mono text-label-mono text-on-surface-variant">#${esc(s.index)}</span>
        <span class="font-label-mono text-label-mono text-primary">${esc(s.tool)}</span>
        ${chip(kindLabel, kindCls)}
      </div>
      <div class="mono-block mt-1">${esc(JSON.stringify(s.input))}</div>`));

    blocks.push(rsStage("Intermediate result", "text-on-surface-variant", `
      <details>
        <summary class="flex items-start gap-2">
          ${chip(s.status, RS_STATUS[s.status] || MUTED)}
          <span class="font-label-mono text-[11px] leading-5 text-on-surface-variant break-all">${esc(rsBriefResult(s.result))}</span>
        </summary>
        <div class="mono-block mt-1">${esc(JSON.stringify(s.result, null, 2))}</div>
      </details>`));

    for (const ev of s.env_events || []) {
      if (ev.visible_to_agent) continue;
      blocks.push(rsStage("World change", "text-error", `
        <div class="rounded border border-error/40 bg-error/10 px-3 py-2 font-body-md text-on-surface">
          <span class="material-symbols-outlined text-sm align-middle text-error">bolt</span>
          ${esc(ev.note || ev.effect)}
          <span class="font-label-mono text-[10px] text-on-surface-variant">(the agent is not told)</span>
        </div>`));
    }
  }
  return blocks;
}

function rsRenderOutcome(run, opts) {
  const { trace, verification: v } = run;
  const o = v.outcome;
  const pass = v.verdict === "pass";
  const st = trace.final_state || {};
  const ledger = {
    refunds: st.refunds, replacements: st.replacements, cancellations: st.cancellations,
    escalations: st.escalations, policy: st.policy, customer_consents: st.consents,
  };
  const stat = (label, value, cls = "text-on-surface") => `
    <div class="bg-surface-container-lowest border border-[#2D333B] rounded px-3 py-2">
      <div class="font-label-mono text-[10px] uppercase text-on-surface-variant">${esc(label)}</div>
      <div class="font-headline-sm text-headline-sm ${cls}">${esc(value)}</div>
    </div>`;

  const box = $("#rs-outcome");
  box.className = "border-t border-[#2D333B] p-stack-md " + (pass ? "bg-[#7ee08a]/5" : "bg-error/5");
  box.innerHTML = `
    <div class="flex flex-wrap items-center gap-3 mb-3">
      <span class="font-label-mono text-[10px] uppercase tracking-wider text-primary w-32">Final outcome</span>
      <span class="material-symbols-outlined ${pass ? "text-[#7ee08a]" : "text-error"}">${pass ? "verified" : "gpp_bad"}</span>
      <span class="font-headline-sm text-headline-sm ${pass ? "text-[#7ee08a]" : "text-error"}">
        ${pass ? "Verified" : "Failed verification"}</span>
      ${rsVerdictChip(pass)}
      ${chip("resolution: " + o.resolution, LILAC)}
      ${chip(trace.stop_reason, trace.stop_reason === "completed" ? MUTED : RED)}
      ${rsSourceChip(opts.source)}
    </div>
    <h5 class="font-label-mono text-label-mono text-on-surface-variant uppercase mb-1">Reply to customer</h5>
    <div class="mono-block mb-3">${esc(trace.final_text || "(no reply)")}</div>
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-2 mb-3">
      ${stat("Refunded", `INR ${o.total_refunded} · ${o.refund_count} refund(s)`, o.refund_count > 1 ? "text-error" : "text-on-surface")}
      ${stat("Replacements", o.replacements)}
      ${stat("Escalated", o.escalated ? "yes" : "no")}
      ${stat("Adaptations", o.adaptations, o.adaptations ? "text-tertiary" : "text-on-surface")}
      ${stat("Steps", o.steps)}
      ${stat("LLM calls", trace.llm_calls)}
      ${stat("Duration", trace.duration_s + "s")}
      ${stat("Policy version", "v" + o.policy_version)}
    </div>
    ${pass
      ? `<div class="font-body-md text-[#7ee08a] flex items-center gap-2 mb-3">
           <span class="material-symbols-outlined text-sm">task_alt</span>
           Every invariant and expectation held, checked against the final database. No LLM judge.</div>`
      : `<div class="mb-3">${v.findings.map((f) => `
           <div class="mono-block mb-2 border-error/40"><span class="text-error">${esc(f.mode)}</span><br/>${esc(f.detail)}</div>`).join("")}</div>`}
    <details class="mb-2">
      <summary class="font-label-mono text-label-mono text-secondary">Backend state at the end</summary>
      <div class="mono-block mt-2">${esc(JSON.stringify(ledger, null, 2))}</div>
    </details>
    <details>
      <summary class="font-label-mono text-label-mono text-secondary">Case file the agent was shown</summary>
      <div class="mono-block mt-2">${esc(JSON.stringify(trace.case_file, null, 2))}</div>
    </details>`;
  box.classList.remove("hidden");
}

async function rsRenderTrace(run, opts = {}) {
  const token = ++rs.renderToken;
  const timeline = $("#rs-timeline");
  timeline.innerHTML = "";
  $("#rs-outcome").classList.add("hidden");

  if (run.error) {
    timeline.innerHTML = `
      <div class="mono-block border-error/40">
        <span class="text-error">provider error (not counted as an agent failure)</span><br/>${esc(run.error)}
      </div>`;
    return;
  }

  const delay = opts.instant ? 0 : 160;
  for (const html of rsBlocks(run.trace)) {
    if (token !== rs.renderToken) return;
    timeline.insertAdjacentHTML("beforeend", html);
    if (delay) {
      timeline.lastElementChild.scrollIntoView({ block: "nearest", behavior: "smooth" });
      await sleep(delay);
    }
  }
  if (token !== rs.renderToken) return;
  rsRenderOutcome(run, opts);
}

/* -------------------------------------------------------------- scenarios */

function rsRenderScenarios() {
  $("#rs-scenarios").innerHTML = rs.scenarios
    .map((s) => {
      const on = s.id === rs.selected;
      return `
        <button data-rs-scenario="${esc(s.id)}" class="text-left p-2 rounded border transition-colors
          ${on ? "border-primary/50 bg-primary/5" : "border-[#2D333B] bg-surface-container-lowest hover:border-[#4B5563]"}">
          <span class="flex items-center gap-2 mb-0.5">
            <span class="font-label-mono text-[10px] ${on ? "text-primary" : "text-on-surface-variant"}">${esc(s.id.slice(0, 3))}</span>
            ${chip(modeLabel(s.category), RS_CATEGORY[s.category] || MUTED)}
          </span>
          <span class="block font-body-md text-on-surface">${esc(s.title)}</span>
        </button>`;
    })
    .join("");
}

$("#rs-scenarios").addEventListener("click", (e) => {
  const button = e.target.closest("[data-rs-scenario]");
  if (!button) return;
  rs.selected = button.dataset.rsScenario;
  rs.renderToken++; // stop any reveal still in progress
  rsRenderScenarios();
  rsRenderBrief(rsScenario(rs.selected));
  $("#rs-timeline").innerHTML = rsEmpty();
  $("#rs-outcome").classList.add("hidden");
});

/* -------------------------------------------------------------- live runs */

function rsBusy(on, label) {
  rs.busy = on;
  ["#btn-rs-run", "#btn-rs-eval", "#btn-rs-load"].forEach((sel) => {
    $(sel).disabled = on;
    $(sel).classList.toggle("opacity-60", on);
  });
  $("#rs-loading").classList.toggle("hidden", !on);
  $("#status-dot").className = "w-2 h-2 rounded-full " + (on ? "bg-primary animate-pulse" : "bg-secondary");
  clearInterval(rs.timer);
  if (on) {
    $("#rs-loading-label").textContent = label;
    const t0 = Date.now();
    $("#rs-elapsed").textContent = "0s";
    rs.timer = setInterval(() => ($("#rs-elapsed").textContent = Math.round((Date.now() - t0) / 1000) + "s"), 1000);
  }
}

async function rsRunCase() {
  const scenario = rsScenario(rs.selected);
  if (rs.busy || !scenario) return;
  const version = $("#rs-version").value;
  rs.renderToken++;
  $("#rs-timeline").innerHTML = "";
  $("#rs-outcome").classList.add("hidden");
  rsRenderBrief(scenario, "live run");
  rsBusy(true, `POST /api/resolution/run · ${scenario.id} · ${version}`);
  let run = null;
  try {
    run = await api("/api/resolution/run", {
      method: "POST",
      body: JSON.stringify({ scenario_id: scenario.id, agent_version: version }),
    });
  } catch (err) {
    toast("CASE_FAILED", err.message);
    $("#rs-timeline").innerHTML = rsEmpty();
  } finally {
    rsBusy(false);
  }
  if (run) await rsRenderTrace(run, { source: "live run" });
}

async function rsRunSuite() {
  if (rs.busy) return;
  const trials = Number($("#rs-trials").value);
  const version = $("#rs-version").value;
  const n = rs.scenarios.length;
  const ok = window.confirm(
    `Run all ${n} scenarios x ${trials} trial(s) live with ${version}?\n\n` +
    `Roughly ${Math.ceil((n * trials * 50) / 60)} minutes and ~${n * trials * 7} LLM calls on your provider quota. ` +
    "The page waits for the whole run."
  );
  if (!ok) return;
  rsBusy(true, `POST /api/resolution/eval · ${n} scenarios x ${trials} · ${version}`);
  try {
    const report = await api("/api/resolution/eval", {
      method: "POST",
      body: JSON.stringify({ agent_version: version, trials }),
    });
    rsRenderSuite(report, "live suite · run " + report.run_id);
    loadRuns();
  } catch (err) {
    toast("SUITE_FAILED", err.message);
  } finally {
    rsBusy(false);
  }
}

/* --------------------------------------------------- suites and recordings */

function rsRenderSuite(report, source) {
  rs.suite = { report, source };
  const s = report.summary || {};
  const results = report.results || [];
  const completedOf = (r) => (r.completed != null ? r.completed : r.trials.filter((t) => !t.error).length);
  const breakdown = Object.entries(s.failure_mode_breakdown || {});
  const stat = (label, value, sub, cls = "text-on-surface") => `
    <div class="bg-surface-container-lowest border border-[#2D333B] rounded px-3 py-2">
      <div class="font-label-mono text-[10px] uppercase text-on-surface-variant">${esc(label)}</div>
      <div class="font-headline-md text-headline-md ${cls}">${esc(value)}</div>
      <div class="font-label-mono text-[10px] text-on-surface-variant">${esc(sub)}</div>
    </div>`;

  $("#rs-suite").innerHTML = `
    <div class="bg-surface-container-highest border-b border-[#2D333B] px-stack-md py-3 flex items-center justify-between flex-wrap gap-2">
      <h3 class="font-headline-sm text-headline-sm flex items-center gap-2">
        <span class="material-symbols-outlined text-primary text-sm">fact_check</span> Suite Result
      </h3>
      <span class="flex flex-wrap gap-2">${chip(report.agent_version, LILAC)} ${rsSourceChip(source)}</span>
    </div>
    ${report.aborted
      ? `<div class="px-stack-md py-2 bg-error/10 border-b border-error/40 font-label-mono text-label-mono text-error">${esc(report.aborted)}</div>`
      : ""}
    <div class="p-stack-md grid grid-cols-2 lg:grid-cols-4 gap-2">
      ${stat("Pass rate", `${s.pass_rate}%`, `${s.passed}/${s.trials} completed trials`,
             s.pass_rate >= 80 ? "text-[#7ee08a]" : s.pass_rate >= 50 ? "text-primary" : "text-error")}
      ${stat(`pass^${s.k}`, `${s.pass_hat_k}%`,
             `of ${s.pass_hat_k_scenarios != null ? s.pass_hat_k_scenarios : s.scenarios} fully-run scenarios`)}
      ${stat("Provider errors", s.errors || 0, "excluded from scores", s.errors ? "text-error" : "text-on-surface")}
      ${stat("Scenarios", `${s.scenarios}/${report.scenarios_requested != null ? report.scenarios_requested : s.scenarios}`,
             `${report.trials_per_scenario} trial(s) each`)}
    </div>
    <div class="px-stack-md pb-3 flex flex-wrap gap-1">
      ${breakdown.length
        ? breakdown.map(([mode, count]) => chip(`${modeLabel(mode)} x${count}`, RED)).join("")
        : `<span class="font-body-md text-[#7ee08a]">No failure modes.</span>`}
    </div>
    <div class="flex flex-col">
      ${results.map((r) => `
        <div class="log-row px-stack-md py-2 flex items-center gap-3 flex-wrap">
          <span class="font-label-mono text-label-mono text-primary w-10">${esc(r.scenario_id.slice(0, 3))}</span>
          <span class="font-body-md text-on-surface flex-1 min-w-[180px]">${esc(r.title || r.scenario_id)}</span>
          <span class="font-label-mono text-label-mono text-on-surface-variant">${r.passes}/${completedOf(r)}</span>
          <span class="flex flex-wrap gap-1">
            ${r.trials.map((t, i) => {
              const cls = t.error ? MUTED : t.verification.verdict === "pass" ? GREEN : RED;
              const tip = t.error ? t.error : t.verification.failure_modes.join(", ") || "all checks held";
              const label = t.error ? "ERR" : t.verification.verdict.toUpperCase();
              return `<button data-rs-trial="${esc(r.scenario_id)}|${i}" title="${esc(tip)}"
                class="font-label-mono text-[10px] px-2 py-0.5 rounded border ${cls} hover:brightness-125">
                T${esc(t.trial != null ? t.trial : i + 1)} ${label}</button>`;
            }).join("")}
          </span>
        </div>`).join("")}
    </div>`;
  $("#rs-suite").classList.remove("hidden");
}

$("#rs-suite").addEventListener("click", (e) => {
  const button = e.target.closest("[data-rs-trial]");
  if (!button || !rs.suite) return;
  const [scenarioId, index] = button.dataset.rsTrial.split("|");
  const result = rs.suite.report.results.find((r) => r.scenario_id === scenarioId);
  if (!result) return;
  rs.selected = scenarioId;
  rsRenderScenarios();
  rsRenderBrief(rsScenario(scenarioId), rs.suite.source);
  rsRenderTrace(result.trials[Number(index)], { source: rs.suite.source, instant: true });
  $("#rs-trace-card").scrollIntoView({ behavior: "smooth", block: "start" });
});

async function rsLoadReports() {
  try {
    const { reports } = await api("/api/resolution/reports");
    $("#rs-reports").innerHTML = reports.length
      ? reports.map((r) => `
          <option value="${esc(r.name)}">${esc(r.name.replace(/\.json$/, ""))} · ${esc(r.scenarios)} scenario(s) · ${esc(r.pass_rate)}%${r.aborted ? " · aborted" : ""}</option>`).join("")
      : `<option value="">No recordings in data/traces/</option>`;
    $("#btn-rs-load").disabled = !reports.length || rs.busy;
  } catch (err) {
    $("#rs-reports").innerHTML = `<option value="">Could not list recordings</option>`;
  }
}

async function rsLoadReport() {
  const name = $("#rs-reports").value;
  if (!name || rs.busy) return;
  try {
    const report = await api("/api/resolution/reports/" + encodeURIComponent(name));
    rsRenderSuite(report, "recorded · " + name.replace(/\.json$/, ""));
    $("#rs-suite").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    toast("RECORDING_FAILED", err.message);
  }
}

/* ------------------------------------------------------------------- init */

async function rsInit() {
  try {
    const { scenarios, agent_versions } = await api("/api/resolution/scenarios");
    rs.scenarios = scenarios;
    rs.selected = scenarios.length ? scenarios[0].id : null;
    $("#rs-version").innerHTML = agent_versions
      .map((v) => `<option value="${esc(v)}" ${v === "v2_verified" ? "selected" : ""}>${esc(v)}</option>`)
      .join("");
    rsRenderScenarios();
    rsRenderBrief(rsScenario(rs.selected));
    $("#rs-timeline").innerHTML = rsEmpty();
  } catch (err) {
    toast("RESOLUTION_UNAVAILABLE", err.message);
  }
  rsLoadReports();
}

$("#btn-rs-run").addEventListener("click", rsRunCase);
$("#btn-rs-eval").addEventListener("click", rsRunSuite);
$("#btn-rs-load").addEventListener("click", rsLoadReport);
$("#rs-trials").addEventListener("input", (e) => ($("#rs-trials-label").textContent = e.target.value));

rsInit();
// app.js routed the initial hash before this view existed.
if (INITIAL_VIEW === "resolution") showView("resolution");
