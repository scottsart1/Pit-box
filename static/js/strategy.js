/* Strategy workspace: the ranked plans as a real comparison, the race shape
   they imply, deterministic what-ifs, the strategy conversation, and the log
   of how the call moved.

   Live state arrives via the `pitwall:state` event the inline dashboard
   dispatches on every websocket frame. Rendering only happens while this tab
   is active — at 4 Hz there is no reason to lay out a hidden page. */

const HAS_DOM = typeof window !== "undefined" && typeof document !== "undefined";

const byId = (id) => document.getElementById(id);

const COMPOUND_COLORS = {
  SOFT: "#ff5b5b",
  MEDIUM: "#ffd21f",
  HARD: "#e8eef4",
  INTER: "#49d17d",
  WET: "#3f86ff",
  UNKNOWN: "#6b7c8c",
};

const view = {
  active: false,
  lastState: null,
  planSignature: "",
  rivals: null,
  rivalsFetchedAt: 0,
  logSignature: "",
  radioLength: -1,
};

function esc(value) {
  return String(value ?? "");
}

async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `Request failed (${response.status})`);
  }
  return payload;
}

function post(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
}

/* ---- Current call --------------------------------------------------------- */

function renderCall(s) {
  const st = s.strategy || {};
  const rec = st.recommended || {};
  const chip = byId("stratConfidence");
  if (st.available) {
    const confidence = String(st.confidence || "low");
    chip.textContent = `Confidence ${confidence} · pit loss ${st.pit_loss_s ?? "—"}s`;
    chip.dataset.state =
      confidence === "high" ? "healthy" : confidence === "medium" ? "neutral" : "warning";
  } else {
    chip.textContent = s.connected || s.game_presence === "standing_by"
      ? "Plans building"
      : "Waiting for telemetry";
    chip.dataset.state = "neutral";
  }
  byId("stratInstruction").textContent =
    rec.instruction || st.reason || "Waiting for a session.";
  byId("stratWhy").textContent = rec.rationale || rec.tyre_reason || "";
  byId("stratChange").textContent = rec.change_condition
    ? `Changes if: ${rec.change_condition}`
    : "";
  const mc = rec.monte_carlo || {};
  byId("stratMeta").textContent = st.available
    ? `Projected finish P${rec.projected_finish_position ?? "—"} · ${rec.projected_points ?? 0} pts · rejoin P${rec.projected_rejoin_position ?? "—"} · P75 ${mc.p75_s ?? rec.risk_adjusted_time_s ?? "—"}s · uncertainty ${mc.uncertainty_s ?? "—"}s`
    : "";
  const rule = st.compound_rule || {};
  const ruleNode = byId("stratRule");
  ruleNode.textContent = rule.applies
    ? `Compound rule: ${rule.dry_count || 0}/2 dry compounds used${rule.change_outstanding ? " — a change is still required" : ""}`
    : rule.wet_waiver
      ? "Compound requirement waived by wet/inter running"
      : "";
  ruleNode.className = "small " + (rule.change_outstanding ? "warn" : "good");
  const hold = s.strategy_hold || {};
  byId("stratHold").hidden = !hold.active;
}

/* ---- Plan board ----------------------------------------------------------- */

function planKey(plan) {
  return [
    plan.stops_remaining,
    (plan.compounds || []).join(">"),
    (plan.box_laps || []).join(","),
    plan.feasible,
    plan.projected_finish_position,
  ].join("|");
}

function adoptPlan(plan, statusNode) {
  const compounds = (plan.compounds || []).map((c) => String(c).toUpperCase());
  const boxLaps = (plan.box_laps || []).map(Number);
  statusNode.textContent = "Committing plan…";
  statusNode.dataset.tone = "";
  post("/api/strategy/plan", {
    compounds,
    box_laps: boxLaps,
    lap_tolerance: 2,
    note: "adopted from the Strategy board",
    source: "strategy-board",
  })
    .then((result) => {
      statusNode.textContent = result.spoken || "Plan locked.";
      statusNode.dataset.tone = "success";
    })
    .catch((error) => {
      statusNode.textContent = String(error.message || error);
      statusNode.dataset.tone = "error";
    });
}

function renderPlans(s) {
  const st = s.strategy || {};
  const plans = st.plans || [];
  const signature = plans.map(planKey).join(";");
  byId("stratPlanCount").textContent = `${plans.length} plan${plans.length === 1 ? "" : "s"}`;
  if (signature === view.planSignature) return;
  view.planSignature = signature;

  const body = byId("stratPlanRows");
  body.replaceChildren();
  if (!plans.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 9;
    cell.className = "empty";
    cell.textContent = "Plans build once telemetry arrives.";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  const rec = st.recommended || {};
  const recKey = `${rec.box_lap}|${String(rec.fit_compound || "").toUpperCase()}`;
  plans.forEach((plan, index) => {
    const row = document.createElement("tr");
    const firstBox = (plan.box_laps || [])[0];
    const firstCompound = String((plan.compounds || [])[1] || (plan.compounds || [])[0] || "").toUpperCase();
    const isRecommended = index === 0 || `${firstBox}|${firstCompound}` === recKey;
    if (index === 0) row.className = "plan-row-recommended";
    const cells = [
      `${index + 1}${index === 0 ? " ★" : ""} · ${plan.stops_remaining}-stop`,
      (plan.compounds || []).map((c) => esc(c)).join(" → "),
      (plan.box_laps || []).join(", ") || "none",
      plan.projected_finish_position != null ? `P${plan.projected_finish_position}` : "—",
      plan.projected_points ?? "—",
      `${plan.monte_carlo?.p75_s ?? plan.risk_adjusted_time_s ?? "—"}s`,
      `${plan.projected_max_wear_pct ?? "—"}%`,
    ];
    for (const text of cells) {
      const cell = document.createElement("td");
      cell.textContent = String(text);
      row.appendChild(cell);
    }
    const verdict = document.createElement("td");
    verdict.textContent = plan.feasible ? "feasible" : plan.verdict || "rejected";
    verdict.className = plan.feasible ? "good" : "warn";
    row.appendChild(verdict);
    const action = document.createElement("td");
    const adopt = document.createElement("button");
    adopt.type = "button";
    adopt.className = "button ghost";
    adopt.textContent = "Adopt";
    adopt.disabled = !plan.feasible;
    adopt.addEventListener("click", () => adoptPlan(plan, byId("stratPlanStatus")));
    action.appendChild(adopt);
    row.appendChild(action);
    body.appendChild(row);
  });
}

/* ---- Stint timeline ------------------------------------------------------- */

function drawTimeline(s) {
  const canvas = byId("stratTimeline");
  const context = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);

  const st = s.strategy || {};
  const plans = (st.plans || []).slice(0, 4);
  const totalLaps = Number(s.total_laps || 0);
  const currentLap = Number(s.current_lap || 0);
  if (!plans.length || totalLaps < 2) {
    context.fillStyle = "#91a6b8";
    context.font = "14px Segoe UI, system-ui, sans-serif";
    context.fillText("The timeline draws once ranked plans and a race distance exist.", 20, 34);
    return;
  }

  const gutter = 96;
  const axisTop = 18;
  const rowHeight = 34;
  const barHeight = 18;
  const plotWidth = width - gutter - 16;
  const lapX = (lap) => gutter + (Math.min(Math.max(lap, 1), totalLaps) - 1) / (totalLaps - 1) * plotWidth;

  context.font = "11px Segoe UI, system-ui, sans-serif";
  context.textBaseline = "middle";

  // Lap grid every 5 laps.
  context.strokeStyle = "#22303c";
  context.fillStyle = "#7d93a6";
  for (let lap = 5; lap <= totalLaps; lap += 5) {
    const x = lapX(lap);
    context.beginPath();
    context.moveTo(x, axisTop);
    context.lineTo(x, height - 26);
    context.stroke();
    context.fillText(String(lap), x - 6, height - 14);
  }

  // One row per plan: stints from the current lap (or lights) to the flag.
  plans.forEach((plan, index) => {
    const y = axisTop + 10 + index * rowHeight;
    const compounds = (plan.compounds || []).map((c) => String(c).toUpperCase());
    const boxLaps = (plan.box_laps || []).map(Number);
    const startLap = Math.max(1, currentLap || 1);
    const bounds = [startLap, ...boxLaps, totalLaps];
    context.fillStyle = "#8fa4b7";
    context.fillText(`${index === 0 ? "★ " : ""}${plan.stops_remaining}-stop`, 8, y + barHeight / 2);
    for (let stint = 0; stint < bounds.length - 1; stint += 1) {
      const from = lapX(bounds[stint]);
      const to = lapX(bounds[stint + 1]);
      if (to <= from) continue;
      context.fillStyle = COMPOUND_COLORS[compounds[stint]] || COMPOUND_COLORS.UNKNOWN;
      context.fillRect(from, y, to - from, barHeight);
    }
    context.fillStyle = "#eef4f8";
    for (const box of boxLaps) {
      const x = lapX(box);
      context.beginPath();
      context.moveTo(x - 5, y - 3);
      context.lineTo(x + 5, y - 3);
      context.lineTo(x, y + 4);
      context.closePath();
      context.fill();
    }
  });

  // Current lap marker.
  if (currentLap >= 1) {
    const x = lapX(currentLap);
    context.strokeStyle = "#3f86ff";
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(x, axisTop - 4);
    context.lineTo(x, height - 26);
    context.stroke();
    context.lineWidth = 1;
  }

  // Projected cliff from the live degradation model.
  const cliff = Number(s.analysis?.deg_model?.projected_cliff_lap || 0);
  if (cliff >= 1 && cliff <= totalLaps) {
    const x = lapX(cliff);
    context.strokeStyle = "#ffc15c";
    context.setLineDash([5, 4]);
    context.beginPath();
    context.moveTo(x, axisTop - 4);
    context.lineTo(x, height - 26);
    context.stroke();
    context.setLineDash([]);
    context.fillStyle = "#ffc15c";
    context.fillText(`cliff ~L${cliff}`, Math.min(x + 4, width - 60), axisTop + 2);
  }

  // Projected rival stops along a dedicated bottom row.
  const rivals = (view.rivals?.rivals || []).filter((r) => r.projected_stop_lap);
  if (rivals.length) {
    const y = axisTop + 10 + plans.length * rowHeight + 6;
    context.fillStyle = "#7d93a6";
    context.fillText("rivals", 8, y + 5);
    for (const rival of rivals) {
      const x = lapX(Number(rival.projected_stop_lap));
      context.fillStyle = rival.undercut_threat ? "#ff5b5b" : "#7d93a6";
      context.beginPath();
      context.arc(x, y + 5, 4, 0, Math.PI * 2);
      context.fill();
      context.fillText(String(rival.driver || "").slice(0, 3).toUpperCase(), x - 10, y + 18);
    }
  }
}

async function refreshRivals(s) {
  const now = Date.now();
  if (now - view.rivalsFetchedAt < 10_000) return;
  view.rivalsFetchedAt = now;
  if (!s.connected || s.mode_profile !== "race") {
    view.rivals = null;
    return;
  }
  try {
    view.rivals = await api("/api/strategy/rivals");
    const threats = (view.rivals?.rivals || []).filter((r) => r.undercut_threat);
    byId("stratRivalNote").textContent = threats.length
      ? `Undercut threat: ${threats.map((r) => r.driver).join(", ")} — projected to stop within 2 laps close behind.`
      : "Projected rival stops are marked on the timeline.";
  } catch {
    view.rivals = null;
  }
}

/* ---- Strategy radio ------------------------------------------------------- */

function renderRadio(s) {
  const log = s.radio_log || [];
  if (log.length === view.radioLength) return;
  view.radioLength = log.length;
  const node = byId("stratRadio");
  node.textContent = log.length
    ? log.map((x) => `${x.role === "driver" ? "DRIVER" : "ENGINEER"}: ${x.text}`).join("\n\n")
    : "Engineer standing by.";
  node.scrollTop = node.scrollHeight;
}

async function sendAsk() {
  const input = byId("stratAsk");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  try {
    await post("/api/ask", { text });
  } catch (error) {
    byId("stratPlanStatus").textContent = String(error.message || error);
    byId("stratPlanStatus").dataset.tone = "error";
  }
}

/* ---- What if -------------------------------------------------------------- */

async function runWhatIf(event) {
  event.preventDefault();
  const free = byId("stratWhatIfText").value.trim();
  const lap = byId("stratWhatIfLap").value;
  const compound = byId("stratWhatIfCompound").value;
  let scenario = free;
  if (!scenario) {
    if (!lap && !compound) {
      byId("stratWhatIfResult").textContent = "Give a lap, a tyre, or describe the scenario.";
      byId("stratWhatIfResult").dataset.tone = "error";
      return;
    }
    scenario = `box${lap ? ` lap ${lap}` : ""}${compound ? ` for ${compound.toLowerCase()}s` : ""}`;
  }
  const result = byId("stratWhatIfResult");
  result.dataset.tone = "";
  result.textContent = "Simulating…";
  try {
    const outcome = await post("/api/strategy/what-if", { scenario });
    if (!outcome.available) {
      result.textContent = outcome.reason || "That scenario cannot be simulated.";
      result.dataset.tone = "error";
      return;
    }
    const delta = Number(outcome.delta_to_best_s ?? 0);
    const deltaText = delta > 0 ? `+${delta.toFixed(1)}s slower than` : delta < 0 ? `${Math.abs(delta).toFixed(1)}s quicker than` : "level with";
    const legality = outcome.compound_rule?.compliant === false ? " Breaks the two-compound rule." : "";
    result.textContent = `${outcome.scenario}: ${deltaText} the recommended plan (risk-adjusted). Finishes at ${outcome.projected_finish_wear_pct}% wear · ${outcome.feasible ? "feasible" : "not feasible"}.${legality}`;
    result.dataset.tone = outcome.feasible ? "success" : "error";
  } catch (error) {
    result.textContent = String(error.message || error);
    result.dataset.tone = "error";
  }
}

/* ---- Decision log --------------------------------------------------------- */

async function loadDecisionLog() {
  const host = byId("stratLog");
  try {
    const history = await api("/api/history?scope=current_session&limit=80");
    const snapshots = (history.strategies || []).slice().reverse();
    const signature = snapshots
      .map((x) => `${x.lap_num}:${x.recommended?.instruction || ""}`)
      .join(";");
    if (signature === view.logSignature) return;
    view.logSignature = signature;
    host.replaceChildren();
    if (!snapshots.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "Strategy snapshots appear as the session runs.";
      host.appendChild(empty);
      return;
    }
    let previous = "";
    for (const snap of snapshots) {
      const instruction = snap.recommended?.instruction || "snapshot";
      const entry = document.createElement("div");
      entry.className = "decision-entry" + (previous && instruction !== previous ? " changed" : "");
      previous = instruction;
      const lap = document.createElement("span");
      lap.className = "lap";
      lap.textContent = `Lap ${snap.lap_num ?? "—"}`;
      const text = document.createElement("span");
      text.textContent = instruction;
      const context = document.createElement("span");
      context.className = "context";
      context.textContent = `${snap.race_control_phase || "green"} · ${snap.model?.confidence || "—"}`;
      entry.append(lap, text, context);
      host.appendChild(entry);
    }
  } catch (error) {
    host.replaceChildren();
    const failed = document.createElement("div");
    failed.className = "empty";
    failed.textContent = `Decision log unavailable: ${error.message || error}`;
    host.appendChild(failed);
  }
}

/* ---- Wiring --------------------------------------------------------------- */

function renderAll(s) {
  renderCall(s);
  renderPlans(s);
  drawTimeline(s);
  renderRadio(s);
  refreshRivals(s);
}

if (HAS_DOM) {
  window.addEventListener("pitwall:state", (event) => {
    view.lastState = event.detail;
    if (view.active) renderAll(event.detail);
  });
  window.addEventListener("pitwall:pagechange", (event) => {
    view.active = event.detail?.page === "strategy";
    if (view.active) {
      if (view.lastState) renderAll(view.lastState);
      loadDecisionLog();
    }
  });
  document.addEventListener("DOMContentLoaded", () => {
    view.active = !byId("strategy").hidden;
  });
  byId("stratRecompute")?.addEventListener("click", () => {
    post("/api/strategy/recompute").catch(() => {});
  });
  byId("stratWhatIfForm")?.addEventListener("submit", runWhatIf);
  byId("stratAskSend")?.addEventListener("click", sendAsk);
  byId("stratAsk")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendAsk();
  });
  byId("stratLogRefresh")?.addEventListener("click", () => {
    view.logSignature = "";
    loadDecisionLog();
  });
}

export { planKey, COMPOUND_COLORS };
