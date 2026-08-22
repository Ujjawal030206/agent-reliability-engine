/* Agent Reliability Engine — frontend.
 *
 * Talks only to the JSON API documented in API.md; nothing in src/ is touched.
 * Endpoints used:
 *   GET  /api/health           GET  /api/agent-versions
 *   GET  /api/scenarios        POST /api/scenarios/generate
 *   POST /api/run              GET  /api/runs        GET /api/runs/{run_id}
 *   POST /api/redteam
 */

const state = {
  scenarios: [],
  generated: [],
  selected: new Set(),
  lastRun: null,
  logFilter: "all",
  runs: [],
  busy: false,
};

/* ------------------------------------------------------------------ utils */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function api(path, options) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, options));
  let body = null;
  try { body = await res.json(); } catch (_) { /* non-JSON error page */ }
  if (!res.ok) {
    const detail = (body && (body.detail || body.message)) || `HTTP ${res.status} ${res.statusText}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

let toastTimer = null;
function toast(title, message) {
  $("#toast-title").textContent = title;
  $("#toast-body").textContent = message;
  $("#toast").classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.add("hidden"), 9000);
}
$("#toast-close").addEventListener("click", () => $("#toast").classList.add("hidden"));

/* Category → accent, so adversarial vectors read at a glance. */
const CATEGORY_STYLE = {
  normal: "text-[#7ee08a] border-[#7ee08a]/40 bg-[#7ee08a]/10",
  ambiguous_confirmation: "text-tertiary border-tertiary/40 bg-tertiary/10",
  social_engineering_destructive: "text-error border-error/40 bg-error/10",
  destructive_pressure: "text-error border-error/40 bg-error/10",
  prompt_injection: "text-error border-error/40 bg-error/10",
  loop_inducing: "text-primary border-primary/40 bg-primary/10",
  hallucination_bait: "text-tertiary border-tertiary/40 bg-tertiary/10",
  out_of_scope: "text-on-surface-variant border-outline-variant bg-surface-container-lowest",
  escalation_needed: "text-secondary border-secondary/40 bg-secondary/10",
  adaptive_red_team: "text-error border-error/40 bg-error/10",
};
const catClass = (c) => CATEGORY_STYLE[c] || "text-on-surface-variant border-outline-variant bg-surface-container-lowest";
const chip = (text, cls) =>
  `<span class="font-label-mono text-[10px] px-2 py-0.5 rounded border ${cls}">${esc(text)}</span>`;
const modeLabel = (m) => esc(String(m).replace(/_/g, " "));

/* ------------------------------------------------------------------- nav */

const VIEW_META = {
  scenarios: { tag: "SCENARIO_BANK", sub: "/ curated + generated attack vectors", title: "Scenario Bank" },
  runs: { tag: "RUN_EVALUATION", sub: "/ sandboxed execution harness", title: "Run Evaluation" },
  analytics: { tag: "REGRESSION_TRACKER", sub: "/ reliability across versions", title: "Analytics" },
  redteam: { tag: "ADAPTIVE_RED_TEAM", sub: "/ closed-loop adversarial probe", title: "Red Teaming" },
};

function showView(name) {
  if (!VIEW_META[name]) name = "scenarios";
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $("#view-" + name).classList.remove("hidden");

  const meta = VIEW_META[name];
  $("#view-tag").textContent = meta.tag;
  $("#view-sub").textContent = meta.sub;
  $("#view-title").textContent = meta.title;

  $$(".nav-link").forEach((a) => {
    const active = a.dataset.nav === name;
    a.classList.toggle("text-primary", active);
    a.classList.toggle("border-r-2", active);
    a.classList.toggle("border-primary", active);
    a.classList.toggle("bg-primary/5", active);
    a.classList.toggle("text-on-surface-variant", !active);
  });

  // The header "Run Evaluation" button is the wrong verb on the red-team view.
  $("#btn-header-run").classList.toggle("hidden", name === "redteam");

  if (name === "analytics") loadRuns();
  if (window.location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
}

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-nav]");
  if (!el) return;
  e.preventDefault();
  showView(el.dataset.nav);
});

/* --------------------------------------------------------------- bootstrap */

async function loadHealth() {
  try {
    const h = await api("/api/health");
    $("#health-dot").className =
      "inline-block w-1.5 h-1.5 rounded-full align-middle mr-1 " + (h.has_api_key ? "bg-[#7ee08a]" : "bg-error");
    $("#health-label").textContent = h.has_api_key ? "API key loaded" : "no API key";
    $("#key-banner").classList.toggle("hidden", !!h.has_api_key);
    $("#key-banner").classList.toggle("flex", !h.has_api_key);
  } catch (err) {
    $("#health-dot").className = "inline-block w-1.5 h-1.5 rounded-full align-middle mr-1 bg-error";
    $("#health-label").textContent = "server unreachable";
  }
}

async function loadVersions() {
  try {
    const { versions } = await api("/api/agent-versions");
    $("#agent-version").innerHTML = versions
      .map((v) => `<option value="${esc(v)}">${esc(v)}</option>`)
      .join("");
  } catch (err) {
    toast("VERSIONS_UNAVAILABLE", err.message);
  }
}

async function loadScenarios() {
  try {
    const { scenarios } = await api("/api/scenarios");
    state.scenarios = scenarios;
    const cats = Array.from(new Set(scenarios.map((s) => s.category))).sort();
    $("#category-filter").innerHTML =
      `<option value="">All categories (${scenarios.length})</option>` +
      cats.map((c) => `<option value="${esc(c)}">${modeLabel(c)}</option>`).join("");
    renderScenarios();
  } catch (err) {
    toast("SCENARIOS_UNAVAILABLE", err.message);
  }
}

/* --------------------------------------------------------------- scenarios */

function renderScenarios() {
  const filter = $("#category-filter").value;
  const rows = [];

  const render = (s, generated) => {
    if (filter && s.category !== filter) return;
    const checked = state.selected.has(s.id) ? "checked" : "";
    rows.push(`
      <div class="log-row px-stack-md py-3 flex gap-3 items-start">
        <input type="checkbox" data-scenario="${esc(s.id)}" ${checked} ${generated ? "disabled" : ""}
          class="mt-1 rounded bg-surface-container-lowest border-outline-variant text-primary focus:ring-primary ${generated ? "opacity-30 cursor-not-allowed" : ""}"/>
        <div class="flex-1 min-w-0">
          <div class="flex flex-wrap items-center gap-2 mb-1">
            <span class="font-label-mono text-label-mono text-primary">${esc(s.id)}</span>
            ${chip(s.category, catClass(s.category))}
            ${s.should_not_auto_confirm ? chip("guardrail", "text-error border-error/40 bg-error/10") : ""}
            ${generated ? chip("generated · preview only", "text-tertiary border-tertiary/40 bg-tertiary/10") : ""}
          </div>
          <p class="font-body-md text-on-surface">${esc(s.prompt)}</p>
          ${s.notes ? `<p class="font-label-mono text-[10px] leading-4 text-on-surface-variant mt-1">${esc(s.notes)}</p>` : ""}
        </div>
      </div>`);
  };

  state.scenarios.forEach((s) => render(s, false));
  state.generated.forEach((s) => render(s, true));

  $("#scenario-list").innerHTML =
    rows.join("") ||
    `<div class="p-stack-lg text-center text-on-surface-variant font-body-md">No scenarios match this filter.</div>`;
  $("#selected-count").textContent = state.selected.size;
}

$("#category-filter").addEventListener("change", renderScenarios);

$("#scenario-list").addEventListener("change", (e) => {
  const cb = e.target.closest("[data-scenario]");
  if (!cb) return;
  if (cb.checked) state.selected.add(cb.dataset.scenario);
  else state.selected.delete(cb.dataset.scenario);
  $("#selected-count").textContent = state.selected.size;
});

$("#btn-select-all").addEventListener("click", () => {
  const filter = $("#category-filter").value;
  state.scenarios.forEach((s) => {
    if (!filter || s.category === filter) state.selected.add(s.id);
  });
  renderScenarios();
});

$("#btn-select-none").addEventListener("click", () => {
  state.selected.clear();
  renderScenarios();
});

$("#gen-n").addEventListener("input", (e) => ($("#gen-n-label").textContent = e.target.value));

$("#btn-generate").addEventListener("click", async () => {
  const btn = $("#btn-generate");
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="material-symbols-outlined text-sm animate-spin">progress_activity</span> Generating…`;
  try {
    const { scenarios } = await api("/api/scenarios/generate", {
      method: "POST",
      body: JSON.stringify({ n: Number($("#gen-n").value) }),
    });
    state.generated = state.generated.concat(scenarios || []);
    renderScenarios();
  } catch (err) {
    toast("GENERATION_FAILED", err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
  }
});

/* --------------------------------------------------------------- run: POST */

function scoreRing(score) {
  const r = 52, c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score)) / 100;
  const color = score >= 80 ? "#7ee08a" : score >= 50 ? "#c0c1ff" : "#ffb4ab";
  return `
    <svg viewBox="0 0 130 130" width="130" height="130">
      <circle cx="65" cy="65" r="${r}" fill="none" stroke="#2D333B" stroke-width="9"/>
      <circle cx="65" cy="65" r="${r}" fill="none" stroke="${color}" stroke-width="9" stroke-linecap="round"
        stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - pct)}" transform="rotate(-90 65 65)"/>
      <text x="65" y="62" text-anchor="middle" fill="${color}"
        style="font-family:Geist,sans-serif;font-size:30px;font-weight:600">${score}</text>
      <text x="65" y="82" text-anchor="middle" fill="#908fa0"
        style="font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:0.05em">/ 100</text>
    </svg>`;
}

function renderBreakdown(scorecard) {
  const modes = Object.entries(scorecard.failure_mode_breakdown || {});
  if (!modes.length) {
    $("#failure-breakdown").innerHTML =
      `<div class="font-body-md text-[#7ee08a] flex items-center gap-2">
         <span class="material-symbols-outlined text-sm">verified</span>
         No failure modes recorded in this run.
       </div>`;
    return;
  }
  const max = Math.max(...modes.map(([, n]) => n));
  $("#failure-breakdown").innerHTML = modes
    .map(([mode, n]) => `
      <div>
        <div class="flex justify-between items-center mb-1">
          <span class="font-label-mono text-label-mono text-on-surface">${modeLabel(mode)}</span>
          <span class="font-label-mono text-label-mono text-error">${n}</span>
        </div>
        <div class="h-1.5 w-full bg-surface-container-lowest rounded overflow-hidden">
          <div class="h-full bg-error" style="width:${(n / max) * 100}%"></div>
        </div>
      </div>`)
    .join("");
}

function toolCallBlock(calls) {
  if (!calls || !calls.length) {
    return `<p class="font-label-mono text-[10px] text-on-surface-variant">No tool calls.</p>`;
  }
  return calls
    .map((c) => {
      const status = (c.result && c.result.status) || "";
      const bad = ["refunded", "deleted", "transferred"].includes(status);
      return `
        <div class="mono-block mb-2">
          <div class="flex items-center gap-2 mb-1">
            <span class="${bad ? "text-error" : "text-secondary"}">${esc(c.name)}</span>
            ${status ? chip(status, bad ? "text-error border-error/40 bg-error/10" : "text-on-surface-variant border-outline-variant") : ""}
          </div>
          <span class="text-on-surface-variant">input </span>${esc(JSON.stringify(c.input))}
          <br/><span class="text-on-surface-variant">result </span>${esc(JSON.stringify(c.result))}
        </div>`;
    })
    .join("");
}

function renderRunLog() {
  const results = (state.lastRun && state.lastRun.results) || [];
  const shown = state.logFilter === "fail"
    ? results.filter((r) => r.classification.verdict === "fail")
    : results;

  if (!shown.length) {
    $("#run-log").innerHTML =
      `<div class="p-stack-lg text-center text-on-surface-variant font-body-md">Nothing to show for this filter.</div>`;
    return;
  }

  $("#run-log").innerHTML = shown
    .map((r) => {
      const fail = r.classification.verdict === "fail";
      const trace = r.trace || {};
      return `
      <details class="log-row">
        <summary class="log-head px-stack-md py-3 flex items-start gap-3">
          <span class="chev material-symbols-outlined text-on-surface-variant text-sm mt-0.5">chevron_right</span>
          <span class="mt-0.5">${chip(fail ? "FAIL" : "PASS", fail
              ? "text-error border-error/40 bg-error/10"
              : "text-[#7ee08a] border-[#7ee08a]/40 bg-[#7ee08a]/10")}</span>
          <span class="flex-1 min-w-0">
            <span class="flex flex-wrap items-center gap-2">
              <span class="font-label-mono text-label-mono text-primary">${esc(r.scenario.id)}</span>
              ${chip(r.scenario.category, catClass(r.scenario.category))}
              ${trace.hit_iteration_cap ? chip("iteration cap", "text-error border-error/40 bg-error/10") : ""}
            </span>
            <span class="block font-body-md text-on-surface mt-1">${esc(r.scenario.prompt)}</span>
            ${r.classification.failure_modes.length
              ? `<span class="block mt-1 flex flex-wrap gap-1">${r.classification.failure_modes
                  .map((m) => chip(modeLabel(m), "text-error border-error/40 bg-error/10")).join("")}</span>`
              : ""}
          </span>
        </summary>

        <div class="px-stack-md pb-stack-md pl-12 flex flex-col gap-3">
          <div>
            <h5 class="font-label-mono text-label-mono text-on-surface-variant uppercase mb-1">Agent response</h5>
            <div class="mono-block">${esc(trace.final_text || "(no final text)")}</div>
          </div>

          <div>
            <h5 class="font-label-mono text-label-mono text-on-surface-variant uppercase mb-1">
              Tool calls (${(trace.tool_calls || []).length})</h5>
            ${toolCallBlock(trace.tool_calls)}
          </div>

          ${(r.classification.rule_findings || []).length ? `
          <div>
            <h5 class="font-label-mono text-label-mono text-error uppercase mb-1">Deterministic rule findings</h5>
            ${r.classification.rule_findings.map((f) => `
              <div class="mono-block mb-2 border-error/40">
                <span class="text-error">${esc(f.mode)}</span><br/>${esc(f.detail)}
              </div>`).join("")}
          </div>` : ""}

          <div>
            <h5 class="font-label-mono text-label-mono text-on-surface-variant uppercase mb-1">LLM judge</h5>
            <p class="font-body-md text-on-surface-variant">${esc(r.classification.judge_explanation || "(no explanation)")}</p>
          </div>

          ${(trace.turns || []).length > 1 ? `
          <details>
            <summary class="font-label-mono text-label-mono text-secondary">Full trace (${trace.turns.length} turns)</summary>
            <div class="mt-2">${trace.turns.map((t, i) => `
              <div class="mono-block mb-2">
                <span class="text-secondary">turn ${i + 1} · ${esc(t.role)}</span><br/>${esc(t.text || "(tool use only)")}
                ${(t.tool_uses || []).length
                  ? `<br/><span class="text-on-surface-variant">calls </span>${esc(t.tool_uses.map((u) => u.name).join(", "))}`
                  : ""}
              </div>`).join("")}</div>
          </details>` : ""}
        </div>
      </details>`;
    })
    .join("");
}

$$(".log-filter").forEach((b) =>
  b.addEventListener("click", () => {
    state.logFilter = b.dataset.filter;
    $$(".log-filter").forEach((x) => {
      const on = x.dataset.filter === state.logFilter;
      x.className = "log-filter px-2 py-1 rounded font-label-mono text-[10px] border " +
        (on ? "border-primary text-primary" : "border-outline-variant text-on-surface-variant hover:border-error hover:text-error");
    });
    renderRunLog();
  })
);

function renderRun(run) {
  state.lastRun = run;
  $("#run-empty").classList.add("hidden");
  $("#run-results").classList.remove("hidden");
  $("#run-results").classList.add("flex");

  const sc = run.scorecard;
  $("#score-ring").innerHTML = scoreRing(sc.score);
  $("#run-id-label").textContent = "run_id " + run.run_id;
  $("#stat-passed").textContent = sc.passed;
  $("#stat-failed").textContent = sc.failed;
  $("#stat-total").textContent = sc.total;
  renderBreakdown(sc);
  renderRunLog();
}

async function runEvaluation() {
  if (state.busy) return;
  showView("runs");

  const body = { agent_version: $("#agent-version").value };
  if (state.selected.size) body.scenario_ids = Array.from(state.selected);
  else body.n = Number($("#run-n").value) || 6;

  state.busy = true;
  $("#btn-header-run").disabled = true;
  $("#run-empty").classList.add("hidden");
  $("#run-results").classList.add("hidden");
  $("#run-loading").classList.remove("hidden");
  $("#status-dot").className = "w-2 h-2 rounded-full bg-primary animate-pulse";

  const t0 = Date.now();
  const timer = setInterval(() => {
    $("#run-elapsed").textContent = Math.round((Date.now() - t0) / 1000) + "s";
  }, 1000);

  try {
    const run = await api("/api/run", { method: "POST", body: JSON.stringify(body) });
    renderRun(run);
    loadRuns();
  } catch (err) {
    toast("RUN_FAILED", err.message);
    if (!state.lastRun) $("#run-empty").classList.remove("hidden");
    else $("#run-results").classList.remove("hidden");
  } finally {
    clearInterval(timer);
    state.busy = false;
    $("#btn-header-run").disabled = false;
    $("#run-loading").classList.add("hidden");
    $("#status-dot").className = "w-2 h-2 rounded-full bg-secondary";
  }
}

$("#btn-header-run").addEventListener("click", runEvaluation);

/* ------------------------------------------------------------ run history */

const fmtTime = (ts) => new Date(ts * 1000).toLocaleString();

async function loadRuns() {
  try {
    const { runs } = await api("/api/runs");
    state.runs = runs;
    renderHistory();
    renderAnalytics();
  } catch (err) {
    $("#run-history").innerHTML =
      `<div class="p-stack-md text-on-surface-variant font-body-md">Could not load run history: ${esc(err.message)}</div>`;
  }
}

function renderHistory() {
  if (!state.runs.length) {
    $("#run-history").innerHTML =
      `<div class="p-stack-md text-on-surface-variant font-body-md">No runs stored yet. Run an evaluation to populate the regression tracker.</div>`;
    return;
  }
  $("#run-history").innerHTML = state.runs
    .slice()
    .reverse()
    .map((r) => `
      <details class="log-row" data-run="${esc(r.run_id)}">
        <summary class="log-head px-stack-md py-3 flex items-center gap-3 flex-wrap">
          <span class="chev material-symbols-outlined text-on-surface-variant text-sm">chevron_right</span>
          <span class="font-label-mono text-label-mono text-primary">${esc(r.run_id)}</span>
          ${chip(r.agent_version, "text-secondary border-secondary/40 bg-secondary/10")}
          <span class="font-label-mono text-label-mono text-on-surface-variant">${esc(fmtTime(r.timestamp))}</span>
          <span class="ml-auto flex gap-3 font-label-mono text-label-mono">
            <span class="text-[#7ee08a]">${r.passed}P</span>
            <span class="text-error">${r.failed}F</span>
            <span class="text-on-surface">${r.score}</span>
          </span>
        </summary>
        <div class="px-stack-md pb-stack-md pl-12 run-detail font-body-md text-on-surface-variant">Loading…</div>
      </details>`)
    .join("");
}

// Historical runs are fetched lazily, when a row is first expanded.
$("#run-history").addEventListener("toggle", async (e) => {
  const d = e.target.closest("details[data-run]");
  if (!d || !d.open || d.dataset.loaded) return;
  d.dataset.loaded = "1";
  const box = d.querySelector(".run-detail");
  try {
    const { results } = await api("/api/runs/" + encodeURIComponent(d.dataset.run));
    box.innerHTML = results
      .map((r) => {
        const fail = r.classification.verdict === "fail";
        return `
          <div class="flex items-start gap-2 py-1">
            ${chip(fail ? "FAIL" : "PASS", fail
              ? "text-error border-error/40 bg-error/10"
              : "text-[#7ee08a] border-[#7ee08a]/40 bg-[#7ee08a]/10")}
            <span class="font-label-mono text-label-mono text-primary">${esc(r.scenario_id)}</span>
            <span class="flex-1">${(r.classification.failure_modes || []).map(modeLabel).join(", ")
              || `<span class="text-on-surface-variant">no failure modes</span>`}</span>
          </div>`;
      })
      .join("") || "No stored results for this run.";
  } catch (err) {
    box.textContent = "Could not load run: " + err.message;
  }
}, true);

/* -------------------------------------------------------------- analytics */

const SERIES_COLORS = ["#c0c1ff", "#ffaaf7", "#7ee08a", "#ffb4ab", "#8083ff"];

function renderAnalytics() {
  const runs = state.runs;
  $("#an-runs").textContent = runs.length;

  if (!runs.length) {
    $("#chart").innerHTML =
      `<div class="py-stack-lg text-center text-on-surface-variant font-body-md">No runs yet — the tracker fills in as you run evaluations.</div>`;
    $("#chart-legend").innerHTML = "";
    $("#an-table").innerHTML =
      `<div class="p-stack-md text-on-surface-variant font-body-md">No runs stored.</div>`;
    return;
  }

  $("#an-best").textContent = Math.max(...runs.map((r) => r.score));
  if (runs.length >= 2) {
    const d = runs[runs.length - 1].score - runs[runs.length - 2].score;
    const el = $("#an-delta");
    el.textContent = (d > 0 ? "+" : "") + d.toFixed(1);
    el.className = "font-headline-lg text-headline-lg mt-1 " +
      (d > 0 ? "text-[#7ee08a]" : d < 0 ? "text-error" : "text-on-surface");
  }

  // Group by agent version so a prompt change shows up as a separate line.
  const byVersion = {};
  runs.forEach((r) => (byVersion[r.agent_version] = byVersion[r.agent_version] || []).push(r));
  const versions = Object.keys(byVersion);

  const W = Math.max(640, runs.length * 90), H = 280;
  const pad = { l: 44, r: 20, t: 20, b: 42 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const n = Math.max(runs.length - 1, 1);
  const x = (i) => pad.l + (i / n) * iw;
  const y = (s) => pad.t + ih - (s / 100) * ih;
  const index = new Map(runs.map((r, i) => [r.run_id, i]));

  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Reliability score by run">`;
  for (let g = 0; g <= 100; g += 25) {
    svg += `<line x1="${pad.l}" y1="${y(g)}" x2="${W - pad.r}" y2="${y(g)}" stroke="#2D333B" stroke-width="1"/>
            <text x="${pad.l - 8}" y="${y(g) + 4}" text-anchor="end" fill="#908fa0"
              style="font-family:'JetBrains Mono',monospace;font-size:10px">${g}</text>`;
  }
  // Faint chronological trend under the per-version series, so the run-to-run
  // trajectory still reads when each version only has a single run (the
  // v1_baseline -> v2_guarded comparison the tracker exists to show).
  if (runs.length > 1) {
    svg += `<polyline fill="none" stroke="#464554" stroke-width="1.5" stroke-dasharray="4 4"
              points="${runs.map((r, i) => `${x(i)},${y(r.score)}`).join(" ")}"/>`;
  }

  versions.forEach((v, vi) => {
    const color = SERIES_COLORS[vi % SERIES_COLORS.length];
    const pts = byVersion[v].map((r) => [x(index.get(r.run_id)), y(r.score)]);
    if (pts.length > 1) {
      svg += `<polyline fill="none" stroke="${color}" stroke-width="2"
                points="${pts.map((p) => p.join(",")).join(" ")}"/>`;
    }
    pts.forEach((p, i) => {
      const r = byVersion[v][i];
      svg += `<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="${color}" stroke="#13131b" stroke-width="2">
                <title>${esc(v)} · ${esc(r.run_id)} · ${r.score} (${r.passed}/${r.total_scenarios})</title></circle>`;
    });
  });
  runs.forEach((r, i) => {
    svg += `<text x="${x(i)}" y="${H - 16}" text-anchor="middle" fill="#908fa0"
              style="font-family:'JetBrains Mono',monospace;font-size:10px">${esc(r.run_id)}</text>`;
  });
  svg += `</svg>`;

  $("#chart").innerHTML = svg;
  $("#chart-legend").innerHTML = versions
    .map((v, i) => `<span class="flex items-center gap-1">
        <span class="w-2 h-2 rounded-full" style="background:${SERIES_COLORS[i % SERIES_COLORS.length]}"></span>${esc(v)}
      </span>`)
    .join("");

  $("#an-table").innerHTML = versions
    .map((v) => {
      const rs = byVersion[v];
      const avg = rs.reduce((a, r) => a + r.score, 0) / rs.length;
      return `
        <div class="log-row px-stack-md py-3 flex items-center gap-3 flex-wrap">
          ${chip(v, "text-secondary border-secondary/40 bg-secondary/10")}
          <span class="font-body-md text-on-surface-variant">${rs.length} run${rs.length === 1 ? "" : "s"}</span>
          <span class="ml-auto flex gap-4 font-label-mono text-label-mono">
            <span class="text-on-surface-variant">avg <span class="text-on-surface">${avg.toFixed(1)}</span></span>
            <span class="text-on-surface-variant">best <span class="text-[#7ee08a]">${Math.max(...rs.map((r) => r.score))}</span></span>
            <span class="text-on-surface-variant">latest <span class="text-primary">${rs[rs.length - 1].score}</span></span>
          </span>
        </div>`;
    })
    .join("");
}

/* ---------------------------------------------------------------- redteam */

const SPEED_MS = { 1: 1400, 2: 800, 3: 250 };
const SPEED_LABEL = { 1: "Slow", 2: "Normal", 3: "Instant" };

$("#rt-turns").addEventListener("input", (e) => ($("#rt-turns-label").textContent = e.target.value));
$("#rt-speed").addEventListener("input", (e) => ($("#rt-speed-label").textContent = SPEED_LABEL[e.target.value]));

$("#rt-presets").addEventListener("change", (e) => {
  if (e.target.name !== "rt-preset") return;
  $("#rt-goal").value = e.target.value;
  $$("#rt-presets .rt-preset").forEach((l) => {
    const on = l.querySelector("input").checked;
    l.className = "rt-preset flex items-center gap-2 p-2 rounded cursor-pointer transition-colors border " +
      (on ? "border-primary/30 bg-primary/5 hover:border-primary/50"
          : "border-[#2D333B] bg-surface-container-lowest hover:border-[#4B5563]");
  });
});

function rtStatus(text, cls) {
  $("#rt-status-text").textContent = text;
  $("#rt-status-dot").className = "w-1.5 h-1.5 rounded-full " + cls;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chatScroll = () => ($("#rt-chat").scrollTop = $("#rt-chat").scrollHeight);

function appendTurn(turn, i) {
  const attacker = turn.speaker === "attacker";
  const el = document.createElement("div");
  el.className = "turn-in flex gap-3 " + (attacker ? "" : "flex-row-reverse");
  el.innerHTML = `
    <div class="w-8 h-8 rounded-full shrink-0 flex items-center justify-center border
      ${attacker ? "border-error/40 bg-error/10 text-error" : "border-primary/40 bg-primary/10 text-primary"}">
      <span class="material-symbols-outlined text-sm">${attacker ? "swords" : "support_agent"}</span>
    </div>
    <div class="max-w-[80%] ${attacker ? "" : "text-right"}">
      <div class="font-label-mono text-[10px] mb-1 ${attacker ? "text-error" : "text-primary"}">
        ${attacker ? "ATTACKER" : "TARGET · RILEY"} · turn ${Math.floor(i / 2) + 1}
      </div>
      <div class="rounded-lg p-3 font-body-md text-left border
        ${attacker ? "bg-error/5 border-error/30 text-on-surface" : "bg-primary/5 border-primary/30 text-on-surface"}">
        ${esc(turn.text || "(no reply)")}
      </div>
    </div>`;
  $("#rt-chat").appendChild(el);
  chatScroll();
}

function appendTyping(attacker) {
  const el = document.createElement("div");
  el.id = "rt-typing";
  el.className = "flex gap-3 " + (attacker ? "" : "flex-row-reverse");
  el.innerHTML = `
    <div class="w-8 h-8 rounded-full shrink-0 flex items-center justify-center border
      ${attacker ? "border-error/40 bg-error/10 text-error" : "border-primary/40 bg-primary/10 text-primary"}">
      <span class="material-symbols-outlined text-sm">${attacker ? "swords" : "support_agent"}</span>
    </div>
    <div class="typing rounded-lg px-3 py-3 border self-center
      ${attacker ? "border-error/30 text-error" : "border-primary/30 text-primary"}">
      <span></span><span></span><span></span>
    </div>`;
  $("#rt-chat").appendChild(el);
  chatScroll();
}
const removeTyping = () => { const t = $("#rt-typing"); if (t) t.remove(); };

function toolCallsPanel(transcript) {
  if (!transcript.tool_calls || !transcript.tool_calls.length) return "";
  return `
    <details class="mt-3">
      <summary class="font-label-mono text-label-mono text-secondary">
        Tool calls during attack (${transcript.tool_calls.length})</summary>
      <div class="mt-2">${toolCallBlock(transcript.tool_calls)}</div>
    </details>`;
}

function renderVerdict(data) {
  const { transcript, classification } = data;
  const breached = transcript.goal_achieved || classification.verdict === "fail";
  const box = $("#rt-verdict");
  box.className = "border-t border-[#2D333B] p-stack-md shrink-0 " +
    (breached ? "bg-error/5" : "bg-[#7ee08a]/5");
  box.innerHTML = `
    <div class="flex flex-wrap items-center gap-3 mb-2">
      <span class="material-symbols-outlined ${breached ? "text-error" : "text-[#7ee08a]"}">
        ${breached ? "gpp_bad" : "verified_user"}</span>
      <span class="font-headline-sm text-headline-sm ${breached ? "text-error" : "text-[#7ee08a]"}">
        ${breached ? "Guardrail breached" : "Guardrail held"}</span>
      ${chip("verdict: " + classification.verdict, breached
        ? "text-error border-error/40 bg-error/10"
        : "text-[#7ee08a] border-[#7ee08a]/40 bg-[#7ee08a]/10")}
      ${transcript.goal_achieved ? chip("goal achieved", "text-error border-error/40 bg-error/10") : ""}
      ${transcript.hit_iteration_cap ? chip("iteration cap", "text-tertiary border-tertiary/40 bg-tertiary/10") : ""}
      ${chip(transcript.target_version, "text-secondary border-secondary/40 bg-secondary/10")}
    </div>
    ${(classification.failure_modes || []).length
      ? `<div class="flex flex-wrap gap-1 mb-2">${classification.failure_modes
          .map((m) => chip(modeLabel(m), "text-error border-error/40 bg-error/10")).join("")}</div>`
      : ""}
    <p class="font-body-md text-on-surface-variant">${esc(classification.judge_explanation || "")}</p>
    ${(classification.rule_findings || []).map((f) => `
      <div class="mono-block mt-2 border-error/40"><span class="text-error">${esc(f.mode)}</span><br/>${esc(f.detail)}</div>`).join("")}
    ${toolCallsPanel(transcript)}`;
  box.classList.remove("hidden");
  chatScroll();
}

async function launchRedTeam() {
  if (state.busy) return;
  const goal = $("#rt-goal").value.trim();
  if (!goal) { toast("GOAL_REQUIRED", "Enter an attacker goal first."); return; }

  const btn = $("#btn-redteam");
  state.busy = true;
  btn.disabled = true;
  btn.classList.add("opacity-60");
  $("#rt-chat").innerHTML = "";
  $("#rt-verdict").classList.add("hidden");
  rtStatus("ATTACK_RUNNING", "bg-error animate-pulse");
  $("#status-dot").className = "w-2 h-2 rounded-full bg-error animate-pulse";

  // The attack loop is server-side and blocking, so show the attacker "thinking"
  // until the transcript comes back, then replay the turns in sequence.
  appendTyping(true);

  try {
    const data = await api("/api/redteam", {
      method: "POST",
      body: JSON.stringify({
        agent_version: $("#agent-version").value,
        goal,
        max_turns: Number($("#rt-turns").value),
      }),
    });
    removeTyping();

    const delay = SPEED_MS[$("#rt-speed").value] || 800;
    const turns = data.transcript.turns || [];
    rtStatus("REPLAYING_TRANSCRIPT", "bg-primary animate-pulse");
    for (let i = 0; i < turns.length; i++) {
      if (delay > 300) {
        appendTyping(turns[i].speaker === "attacker");
        await sleep(delay);
        removeTyping();
      }
      appendTurn(turns[i], i);
      await sleep(delay / 2);
    }
    renderVerdict(data);
    rtStatus(data.transcript.goal_achieved ? "GUARDRAIL_BREACHED" : "ATTACK_COMPLETE",
             data.transcript.goal_achieved ? "bg-error" : "bg-[#7ee08a]");
  } catch (err) {
    removeTyping();
    toast("REDTEAM_FAILED", err.message);
    rtStatus("ATTACK_FAILED", "bg-error");
    $("#rt-chat").innerHTML =
      `<div class="text-center text-on-surface-variant font-body-md py-stack-lg">
         The attack could not run: ${esc(err.message)}
       </div>`;
  } finally {
    state.busy = false;
    btn.disabled = false;
    btn.classList.remove("opacity-60");
    $("#status-dot").className = "w-2 h-2 rounded-full bg-secondary";
  }
}

$("#btn-redteam").addEventListener("click", launchRedTeam);

/* ------------------------------------------------------------------- init */

$("#rt-chat").innerHTML = `
  <div class="m-auto text-center text-on-surface-variant font-body-md max-w-md">
    <span class="material-symbols-outlined text-error text-4xl">swords</span>
    <p class="mt-2">A Claude attacker converses directly with the target agent, adapting each turn.
    Set a goal and launch — the transcript replays here, attacker vs. target.</p>
  </div>`;

showView((window.location.hash || "#scenarios").slice(1));
loadHealth();
loadVersions();
loadScenarios();
loadRuns();
