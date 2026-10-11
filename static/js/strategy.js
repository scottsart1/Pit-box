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

/* "1-stop", "2-stop": English reads the same for every count; other
   languages choose their plural form. */
function stopsLabel(count) {
  return i18n.tp(count, "{count}-stop", "{count}-stop", { count: String(count) });
}

async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || i18n.t("Request failed ({status})", { status: response.status }));
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

function redFlagRestart(s) {
  if (globalThis.confirmedSessionFinish?.(s)) return null;
  if (!(s.red_flag_active || s.race_control_phase === "red_flag" || s.fia_flag === "red")) return null;
  const stale = typeof globalThis.redFlagTelemetryStale === "function"
    ? globalThis.redFlagTelemetryStale(s) : true;
  const plan = s.strategy?.red_flag_restart || {};
  const sameSession = typeof plan.session_identity === "string" && plan.session_identity.length > 0 && plan.session_identity === s.session_identity;
  return { ...(plan.active === true && (sameSession || (!stale && plan.session_identity == null)) ? plan : {}), stale };
}

function evidenceSource(source) {
  const text = String(source || "");
  if (!text) return i18n.t("source unavailable");
  const label = text.startsWith("blended_live") ? i18n.t("live laps blended with prior evidence")
    : text.startsWith("live_") ? i18n.t("live laps")
    : text.includes("condition_adjusted") ? i18n.t("history adjusted for conditions")
    : text.includes("personal") ? i18n.t("your past runs")
    : text.includes("inferred") ? i18n.t("estimated from other compounds")
    : i18n.t("track estimate");
  return text.includes("driver_feedback") ? i18n.t("{source}, including your tyre report", { source: label }) : label;
}

function planEvidence(s) {
  const st = s.strategy || {}, rec = st.recommended || {}, model = st.model_summary || {};
  const count = value => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  const describe = (label, evidence) => {
    const observed = count(evidence.laps_observed);
    const saved = count(evidence.historical_laps_recorded);
    const wear = count(evidence.wear_sample_size), pace = count(evidence.pace_sample_size ?? evidence.deg_sample_size);
    const reasons = {
      warmup_lap: i18n.t("warm-up laps"), missing_or_invalid_fuel: i18n.t("missing or invalid fuel readings"),
      insufficient_stint_span: i18n.t("too few consecutive comparable laps"), inconsistent_stint_pace: i18n.t("inconsistent pace"),
      implausible_pace_slope: i18n.t("pace trend outside the valid range"), unresolved_wet_conditions: i18n.t("unresolved wet conditions"),
      invalid_lap: i18n.t("invalid laps"), pit_lap: i18n.t("pit laps"), neutralised_lap: i18n.t("neutralised laps"),
      missing_tyre_age: i18n.t("missing tyre age"), tyre_reset_or_missing_laps: i18n.t("tyre reset or missing laps"),
    };
    const exclusions = Object.entries(evidence.pace_excluded_laps || {}).filter(([, value]) => count(value) > 0)
      .map(([reason, value]) => `${reasons[reason] || reason.replaceAll("_", " ")} (${value})`);
    const parts = [];
    if (observed !== null) parts.push(i18n.t("{count} eligible laps", { count: observed }));
    if (saved !== null) parts.push(i18n.t("saved history {count} laps", { count: saved }));
    const wearSource = evidenceSource(evidence.wear_source), paceSource = evidenceSource(evidence.pace_source ?? evidence.deg_source);
    parts.push(wear === null ? i18n.t("wear from {source}", { source: wearSource })
      : i18n.t("wear from {source} ({count} samples)", { source: wearSource, count: wear }));
    parts.push(pace === null ? i18n.t("pace from {source}", { source: paceSource })
      : i18n.t("pace from {source} ({count} laps)", { source: paceSource, count: pace }));
    return `${label}: ${parts.join("; ")}.`
      + (exclusions.length ? ` ${i18n.t("Pace exclusions: {reasons}.", { reasons: exclusions.join(", ") })}` : "");
  };
  const compounds = model.compounds && typeof model.compounds === "object" ? Object.entries(model.compounds) : [];
  const evidence = compounds.filter(([, value]) => value && typeof value === "object")
    .map(([compound, value]) => describe(String(compound).toUpperCase(), value));
  if (!evidence.length) {
    for (const [index, stint] of (rec.stint_models || []).entries()) {
      if (!stint || typeof stint !== "object") continue;
      const compound = stint.compound || rec.compounds?.[index] || i18n.t("unknown tyre");
      evidence.push(describe(i18n.t("Stint {number} · {compound}", { number: index + 1, compound }), stint));
    }
  }
  return evidence.length
    ? [i18n.t("Plan confidence covers every planned stint. An untested tyre or unconfirmed spare can keep it low."), ...evidence].join("\n")
    : i18n.t("Tyre evidence details are not available for this plan.");
}

function canAdopt(plan) {
  return plan.feasible === true && plan.legal === true;
}

function finishDisplay(plan) {
  if (plan.legal === false) {
    return { supported: false, position: i18n.t("Not eligible"), points: "0", verdict: i18n.t("illegal"),
      message: i18n.t("Finish projection unavailable: this plan is illegal · 0 eligible points.") };
  }
  if (plan.feasible !== true || plan.finish_projection_valid === false) {
    return { supported: false, position: i18n.t("Unsupported"), points: "—",
      verdict: plan.feasible === false ? (plan.verdict || i18n.t("not feasible")) : i18n.t("finish unsupported"),
      message: i18n.t("Finish projection unavailable: this plan has no supported finish estimate.") };
  }
  if (plan.legal !== true) {
    return { supported: false, position: i18n.t("Unconfirmed"), points: "—", verdict: i18n.t("legality unconfirmed"),
      message: i18n.t("Finish projection unavailable until this plan's legality is confirmed.") };
  }
  const conditional = plan.stops_remaining > 0
    && (plan.inventory_status === "unknown" || plan.inventory_feasible === null);
  const position = plan.projected_finish_position != null ? `P${plan.projected_finish_position}` : "—";
  const points = String(plan.projected_points ?? "—");
  return { supported: true, conditional,
    position: conditional ? i18n.t("{position} (conditional)", { position }) : position,
    points: conditional ? i18n.t("{points} (conditional)", { points }) : points,
    verdict: conditional ? i18n.t("conditional — confirm spare tyres") : i18n.t("feasible"),
    message: conditional
      ? i18n.t("Estimated finish {position} · {points} pts · conditional on the spare tyres being available", { position, points })
      : i18n.t("Estimated finish {position} · {points} pts", { position, points }) };
}

function renderCall(s) {
  const finished = globalThis.confirmedSessionFinish?.(s);
  if (finished) {
    byId("stratConfidence").textContent = i18n.t("Confirmed final result");
    byId("stratConfidence").dataset.state = "healthy";
    byId("stratInstruction").textContent = i18n.t("Session complete · P{position}", { position: finished.position });
    byId("stratNotice").textContent = i18n.t("Review recorded laps and session history in Analysis.");
    for (const id of ["stratWhy", "stratChange", "stratMeta", "stratReasoning", "stratLearning", "stratRule"]) byId(id).textContent = "";
    byId("stratHold").hidden = true;
    return;
  }
  const st = s.strategy || {};
  const rec = st.recommended || {};
  const chip = byId("stratConfidence");
  if (st.available) {
    const confidence = String(st.confidence || "low");
    chip.textContent = confidence === "high" ? i18n.t("Plan confidence: high")
      : confidence === "medium" ? i18n.t("Plan confidence: medium")
      : confidence === "low" ? i18n.t("Plan confidence: low")
      : i18n.t("Plan confidence: {confidence}", { confidence });
    chip.dataset.state =
      confidence === "high" ? "healthy" : confidence === "medium" ? "neutral" : "warning";
  } else {
    chip.textContent = s.connected || s.game_presence === "standing_by"
      ? i18n.t("Plans building")
      : i18n.t("Waiting for telemetry");
    chip.dataset.state = "neutral";
  }
  const intent = s.strategy_intent || {};
  byId("stratInstruction").textContent = intent.active && intent.direction === "stay_out"
    ? intent.intent
      ? i18n.t("Running the {intent} — staying out", { intent: String(intent.intent).replaceAll("_", " ") })
      : i18n.t("Running the plan — staying out")
    : rec.instruction || st.reason || i18n.t("Waiting for a session.");
  // The classic Drive script supplies the same current-tyre summary to both views.
  const tyres = typeof globalThis.tyreStintInsight === "function"
    ? globalThis.tyreStintInsight(s)
    : { headline: i18n.t("Tyre data unavailable"), stop: "", warning: "", tone: "muted" };
  byId("stratWhy").textContent = [tyres.headline, tyres.stop].filter(Boolean).join(" · ");
  byId("stratChange").textContent = tyres.warning;
  byId("stratChange").className = "small " + tyres.tone;
  const mc = rec.monte_carlo || {};
  const finish = finishDisplay(rec);
  const meta = { finish: finish.message, rejoin: rec.projected_rejoin_position ?? "—",
    p75: mc.p75_s ?? rec.risk_adjusted_time_s ?? "—", uncertainty: mc.uncertainty_s ?? "—" };
  byId("stratMeta").textContent = st.available
    ? finish.supported
      ? mc.calibrated !== true
        ? i18n.t("{finish} · rejoin P{rejoin} · P75 {p75}s · uncertainty {uncertainty}s · forecast range is not yet calibrated", meta)
        : i18n.t("{finish} · rejoin P{rejoin} · P75 {p75}s · uncertainty {uncertainty}s", meta)
      : finish.message
    : "";
  const notices = [];
  if (s.connected === false || s.telemetry_stale === true) notices.push(i18n.t("Live telemetry unavailable; this is the last plan."));
  if (st.available && !finish.supported) notices.push(finish.message);
  else if (st.available && finish.conditional) notices.push(i18n.t("Confirm spare tyres before adopting this plan."));
  const neutral = st.neutralisation || {};
  if (neutral.phase && neutral.phase !== "green") {
    const phase = String(neutral.phase).replaceAll("_", " "), entry = neutral.pit_entry_status || "unknown";
    const pitEntry = entry === "available" ? i18n.t("{phase} · pit entry available", { phase })
      : entry === "passed" ? i18n.t("{phase} · pit entry passed", { phase })
      : entry === "unknown" ? i18n.t("{phase} · pit entry unknown", { phase })
      : i18n.t("{phase} · pit entry {status}", { phase, status: entry });
    notices.push(`${pitEntry}${neutral.track_position_warning ? ` · ${neutral.track_position_warning}` : ""}`);
  }
  byId("stratNotice").textContent = notices.join(" ");
  byId("stratReasoning").textContent = [rec.rationale, rec.tyre_reason,
    rec.change_condition ? i18n.t("Changes if: {condition}", { condition: rec.change_condition }) : "",
    st.available ? i18n.t("Pit loss estimate: {seconds}s.", { seconds: st.pit_loss_s ?? "—" }) : ""].filter(Boolean).join(" ");
  byId("stratLearning").textContent = st.available ? planEvidence(s) : "";
  const rule = st.observed_compound_rule || st.compound_rule || {};
  const ruleNode = byId("stratRule");
  ruleNode.textContent = rule.applies
    ? rule.change_outstanding
      ? i18n.t("Compound rule: {count}/2 dry compounds used — a change is still required", { count: rule.dry_count || 0 })
      : i18n.t("Compound rule: {count}/2 dry compounds used", { count: rule.dry_count || 0 })
    : rule.wet_waiver
      ? i18n.t("Compound requirement waived by wet/inter running")
      : "";
  ruleNode.className = "small " + (rule.change_outstanding ? "warn" : "good");
  const hold = s.strategy_hold || {};
  byId("stratHold").hidden = !hold.active;
  const restart = redFlagRestart(s);
  if (restart) {
    const instruction = restart.primary?.instruction || restart.instruction || (restart.stale
      ? i18n.t("No recorded restart plan. Confirm restart tyres in game.")
      : i18n.t("Session suspended. Prepare fresh restart tyres while stopped; checking available sets."));
    byId("stratInstruction").textContent = restart.stale
      ? i18n.t("Last confirmed · provisional: {instruction}", { instruction: String(instruction) })
      : instruction;
    byId("stratWhy").textContent = restart.primary ? restart.stale ? i18n.t("Provisional primary restart strategy") : i18n.t("Primary restart strategy") : "";
    byId("stratChange").textContent = restart.alternative ? i18n.t("Alternative: {instruction}", { instruction: String(restart.alternative.instruction) }) : "";
    byId("stratChange").className = "small warn";
    byId("stratNotice").textContent = restart.stale
      ? i18n.t("Live telemetry unavailable. Confirm the suspension, tyre sets and track conditions in game before using this plan.")
      : i18n.t("Follow the game’s suspension instructions. Select restart tyres while stopped.");
    byId("stratMeta").textContent = "";
    byId("stratReasoning").textContent = restart.alternative_reason || "";
    byId("stratHold").hidden = true;
  }
}

/* ---- Plan board ----------------------------------------------------------- */

function planKey(plan) {
  return [
    plan.stops_remaining,
    (plan.compounds || []).join(">"),
    (plan.box_laps || []).join(","),
    plan.feasible,
    plan.legal,
    plan.finish_projection_valid,
    plan.inventory_status,
    plan.inventory_feasible,
    plan.projected_finish_position,
    plan.projected_points,
    plan.monte_carlo?.p75_s,
    plan.risk_adjusted_time_s,
    plan.projected_max_wear_pct,
    plan.verdict,
  ].join("|");
}

function adoptPlan(plan, statusNode) {
  if (globalThis.confirmedSessionFinish?.(view.lastState)) {
    statusNode.textContent = i18n.t("Session complete. No plan can be adopted.");
    return;
  }
  if (!canAdopt(plan)) {
    statusNode.textContent = i18n.t("Only feasible, legal plans can be adopted.");
    statusNode.dataset.tone = "error";
    return;
  }
  const compounds = (plan.compounds || []).map((c) => String(c).toUpperCase());
  const boxLaps = (plan.box_laps || []).map(Number);
  statusNode.textContent = i18n.t("Committing plan…");
  statusNode.dataset.tone = "";
  post("/api/strategy/plan", {
    compounds,
    box_laps: boxLaps,
    lap_tolerance: 2,
    note: "adopted from the Strategy board",
    source: "strategy-board",
  })
    .then((result) => {
      statusNode.textContent = result.spoken || i18n.t("Plan locked.");
      statusNode.dataset.tone = "success";
    })
    .catch((error) => {
      statusNode.textContent = String(error.message || error);
      statusNode.dataset.tone = "error";
    });
}

function renderPlans(s) {
  const st = s.strategy || {};
  const restart = redFlagRestart(s);
  const finished = globalThis.confirmedSessionFinish?.(s);
  byId("stratPlansHeading").textContent = finished ? i18n.t("Session complete") : restart ? i18n.t("Compare restart tyres") : i18n.t("Compare other plans");
  const planHead = byId("stratPlanRows").closest?.("table")?.tHead;
  if (planHead) planHead.hidden = !!(restart || finished);
  if (finished) {
    view.planSignature = "session-complete";
    byId("stratPlanCount").textContent = i18n.t("0 active plans");
    byId("stratPlanRows").replaceChildren();
    byId("stratPlanStatus").textContent = i18n.t("Confirmed final result. No further stop is scheduled.");
    return;
  }
  if (restart) {
    view.planSignature = "red-flag";
    const choices = [[i18n.t("Primary"), restart.primary], [i18n.t("Alternative"), restart.alternative]].filter(([, plan]) => plan);
    byId("stratPlanCount").textContent = restart.stale
      ? i18n.tp(choices.length, "{count} provisional restart option", "{count} provisional restart options", { count: choices.length })
      : i18n.tp(choices.length, "{count} restart option", "{count} restart options", { count: choices.length });
    const body = byId("stratPlanRows");
    body.replaceChildren();
    for (const [label, plan] of choices.length ? choices : [["Restart tyres", null]]) {
      const row = document.createElement("tr"), cell = document.createElement("td");
      cell.colSpan = 9;
      cell.textContent = plan ? `${label} · ${plan.compound}: ${plan.instruction}` : restart.stale
        ? i18n.t("Current race status unavailable. Confirm restart tyres in game.")
        : i18n.t("Checking available restart tyres. Select tyres through the game's suspension controls.");
      row.appendChild(cell); body.appendChild(row);
    }
    byId("stratPlanStatus").textContent = i18n.t("Restart tyre selections are made in game while stopped.");
    return;
  }
  const plans = st.plans || [];
  if (["red-flag", "session-complete"].includes(view.planSignature)) byId("stratPlanStatus").textContent = "";
  const signature = plans.map(planKey).join(";");
  byId("stratPlanCount").textContent = i18n.tp(plans.length, "{count} plan", "{count} plans", { count: plans.length });
  if (signature === view.planSignature) return;
  view.planSignature = signature;

  const body = byId("stratPlanRows");
  body.replaceChildren();
  if (!plans.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 9;
    cell.className = "empty";
    cell.textContent = i18n.t("Plans build once telemetry arrives.");
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
    const finish = finishDisplay(plan);
    const cells = [
      `${index + 1}${index === 0 ? " ★" : ""} · ${stopsLabel(plan.stops_remaining)}`,
      (plan.compounds || []).map((c) => esc(c)).join(" → "),
      (plan.box_laps || []).join(", ") || i18n.t("none"),
      finish.position,
      finish.points,
      finish.supported ? `${plan.monte_carlo?.p75_s ?? plan.risk_adjusted_time_s ?? "—"}s` : "—",
      `${plan.projected_max_wear_pct ?? "—"}%`,
    ];
    for (const text of cells) {
      const cell = document.createElement("td");
      cell.textContent = String(text);
      row.appendChild(cell);
    }
    const verdict = document.createElement("td");
    verdict.textContent = finish.verdict;
    verdict.className = finish.supported && !finish.conditional ? "good" : "warn";
    row.appendChild(verdict);
    const action = document.createElement("td");
    const adopt = document.createElement("button");
    adopt.type = "button";
    adopt.className = "button ghost";
    adopt.textContent = i18n.t("Adopt");
    adopt.disabled = !canAdopt(plan);
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
  view.timelineGeometry = null;
  if (globalThis.confirmedSessionFinish?.(s)) {
    context.fillStyle = "#91a6b8";
    context.font = "14px Segoe UI, system-ui, sans-serif";
    context.fillText(i18n.t("Session complete. Review recorded laps in Analysis."), 20, 34);
    return;
  }
  if (redFlagRestart(s)) {
    context.fillStyle = "#91a6b8";
    context.font = "14px Segoe UI, system-ui, sans-serif";
    context.fillText(i18n.t("Session suspended. Restart tyre choices are listed above."), 20, 34);
    return;
  }

  const st = s.strategy || {};
  const plans = (st.plans || []).slice(0, 4);
  const totalLaps = Number(s.total_laps || 0);
  const currentLap = Number(s.current_lap || 0);
  if (!plans.length || totalLaps < 2) {
    context.fillStyle = "#91a6b8";
    context.font = "14px Segoe UI, system-ui, sans-serif";
    context.fillText(i18n.t("The timeline draws once ranked plans and a race distance exist."), 20, 34);
    return;
  }

  const gutter = 96;
  const axisTop = 18;
  const rowHeight = 34;
  const barHeight = 18;
  // Geometry is remembered so clicks can be mapped back to (plan, stint, lap).
  view.timelineGeometry = null;
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
    context.fillText(`${index === 0 ? "★ " : ""}${stopsLabel(plan.stops_remaining)}`, 8, y + barHeight / 2);
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

  // The TOP row is editable by direct clicks; remember where things are.
  {
    const topPlan = plans[0];
    const startLap = Math.max(1, currentLap || 1);
    view.timelineGeometry = {
      gutter,
      plotWidth,
      totalLaps,
      currentLap,
      axisY: height - 26,
      topRow: {
        y: axisTop + 10,
        height: barHeight,
        boxLaps: (topPlan.box_laps || []).map(Number),
        compounds: (topPlan.compounds || []).map((c) => String(c).toUpperCase()),
        startLap,
      },
    };
  }

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
    context.fillText(i18n.t("cliff ~L{lap}", { lap: cliff }), Math.min(x + 4, width - 60), axisTop + 2);
  }

  // Projected rival stops along a dedicated bottom row.
  const rivals = (view.rivals?.rivals || []).filter((r) => r.projected_stop_lap);
  if (rivals.length) {
    const y = axisTop + 10 + plans.length * rowHeight + 6;
    context.fillStyle = "#7d93a6";
    context.fillText(i18n.t("rivals"), 8, y + 5);
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
      ? i18n.t("Undercut threat: {drivers} — projected to stop within 2 laps close behind.", { drivers: threats.map((r) => r.driver).join(", ") })
      : i18n.t("Projected rival stops are marked on the timeline.");
  } catch {
    view.rivals = null;
  }
}

/* ---- Direct-manipulation editing on the timeline -------------------------- */

const DRY_CYCLE = ["SOFT", "MEDIUM", "HARD"];

function commitTimelinePlan(compounds, boxLaps, note) {
  const statusNode = byId("stratPlanStatus");
  statusNode.textContent = i18n.t("Committing plan…");
  statusNode.dataset.tone = "";
  post("/api/strategy/plan", {
    compounds,
    box_laps: boxLaps,
    lap_tolerance: 2,
    note,
    source: "timeline-click",
  })
    .then((result) => {
      statusNode.textContent = result.spoken || i18n.t("Plan updated.");
      statusNode.dataset.tone = "success";
    })
    .catch((error) => {
      statusNode.textContent = String(error.message || error);
      statusNode.dataset.tone = "error";
    });
}

function handleTimelineClick(event) {
  const geometry = view.timelineGeometry;
  if (!geometry) return;
  const canvas = byId("stratTimeline");
  const bounds = canvas.getBoundingClientRect();
  // The canvas is CSS-scaled; convert to drawing coordinates first.
  const x = (event.clientX - bounds.left) * (canvas.width / bounds.width);
  const y = (event.clientY - bounds.top) * (canvas.height / bounds.height);
  const lapAt = (px) =>
    Math.round(
      1 + ((px - geometry.gutter) / geometry.plotWidth) * (geometry.totalLaps - 1)
    );
  const clickedLap = Math.min(Math.max(lapAt(x), 1), geometry.totalLaps);
  const row = geometry.topRow;
  // Edits anchor on the driver's own committed plan when one exists. The
  // drawn top row is the engine's live ranking, which may sit a lap or two
  // off the committed plan — clicking a compound must never silently move
  // the stop the driver placed.
  const committed = (view.lastState || {}).strategy_override || {};
  const base = committed.enabled && committed.locked && committed.plan?.compounds?.length
    ? committed.plan
    : { compounds: row.compounds, box_laps: row.boxLaps };
  const compounds = (base.compounds || []).map((c) => String(c).toUpperCase());
  const boxLaps = (base.box_laps || []).map(Number);

  // Click on the lap axis: move the nearest stop of the top plan to that lap
  // (or create the first stop when the plan has none).
  if (y >= geometry.axisY - 4) {
    if (clickedLap <= geometry.currentLap) {
      byId("stratPlanStatus").textContent = i18n.t("That lap has already been raced.");
      byId("stratPlanStatus").dataset.tone = "error";
      return;
    }
    if (!boxLaps.length) {
      const displaced = compounds[0];
      const second = DRY_CYCLE.find((c) => c !== displaced) || "MEDIUM";
      commitTimelinePlan(
        [compounds[0] || "MEDIUM", second],
        [clickedLap],
        `stop added at lap ${clickedLap} from the timeline`
      );
      return;
    }
    let nearest = 0;
    for (let i = 1; i < boxLaps.length; i += 1) {
      if (Math.abs(boxLaps[i] - clickedLap) < Math.abs(boxLaps[nearest] - clickedLap)) nearest = i;
    }
    boxLaps[nearest] = clickedLap;
    const ordered = boxLaps.every((lap, i) => i === 0 || lap > boxLaps[i - 1]);
    if (!ordered) {
      byId("stratPlanStatus").textContent = i18n.t("Stops must stay in lap order.");
      byId("stratPlanStatus").dataset.tone = "error";
      return;
    }
    commitTimelinePlan(compounds, boxLaps, `stop moved to lap ${clickedLap} from the timeline`);
    return;
  }

  // Adjacent stints may use distinct sets of the same compound. The engine
  // checks physical inventory and race legality for the resulting plan.
  if (y >= row.y && y <= row.y + row.height) {
    const bounds_ = [row.startLap, ...boxLaps, geometry.totalLaps];
    let stint = -1;
    for (let i = 0; i < bounds_.length - 1; i += 1) {
      if (clickedLap >= bounds_[i] && clickedLap <= bounds_[i + 1]) { stint = i; break; }
    }
    if (stint < 0 || stint >= compounds.length) return;
    const current = compounds[stint];
    const candidate = DRY_CYCLE[(DRY_CYCLE.indexOf(current) + 1 + DRY_CYCLE.length) % DRY_CYCLE.length];
    if (candidate === current) return;
    compounds[stint] = candidate;
    commitTimelinePlan(
      compounds,
      boxLaps,
      `stint ${stint + 1} set to ${candidate.toLowerCase()}s from the timeline`
    );
  }
}

/* ---- Strategy radio ------------------------------------------------------- */

function renderRadio(s) {
  const log = s.radio_log || [];
  if (log.length === view.radioLength) return;
  view.radioLength = log.length;
  const node = byId("stratRadio");
  node.textContent = log.length
    ? log.map((x) => `${x.role === "driver" ? i18n.t("DRIVER") : i18n.t("ENGINEER")}: ${x.text}`).join("\n\n")
    : i18n.t("Engineer standing by.");
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
  const chosen = {};
  if (!scenario) {
    if (!lap && !compound) {
      byId("stratWhatIfResult").textContent = i18n.t("Give a lap, a tyre, or describe the scenario.");
      byId("stratWhatIfResult").dataset.tone = "error";
      return;
    }
    scenario = `box${lap ? ` lap ${lap}` : ""}${compound ? ` for ${compound.toLowerCase()}s` : ""}`;
    // The selectors also travel as values, so the server does not have to
    // read them back out of the English sentence. A lap goes as a number only
    // in the plain one- or two-digit form the sentence parser reads the same.
    if (/^\d{1,2}$/.test(lap)) chosen.lap = Number(lap);
    if (compound) chosen.compound = compound;
  }
  const result = byId("stratWhatIfResult");
  result.dataset.tone = "";
  result.textContent = i18n.t("Simulating…");
  try {
    const outcome = await post("/api/strategy/what-if", { scenario, ...chosen });
    if (!outcome.available) {
      result.textContent = outcome.reason ? i18n.t(outcome.reason) : i18n.t("That scenario cannot be simulated.");
      result.dataset.tone = "error";
      return;
    }
    const delta = Number(outcome.delta_to_best_s ?? 0);
    const stop = Number.isInteger(outcome.box_lap) && outcome.compound
      ? i18n.t("Box lap {lap} for {compound}", { lap: outcome.box_lap, compound: outcome.compound })
      : String(outcome.scenario);
    const comparison = delta > 0
      ? i18n.t("{scenario}: +{delta}s slower than the recommended plan (estimate).", { scenario: stop, delta: delta.toFixed(1) })
      : delta < 0
        ? i18n.t("{scenario}: {delta}s quicker than the recommended plan (estimate).", { scenario: stop, delta: Math.abs(delta).toFixed(1) })
        : i18n.t("{scenario}: level with the recommended plan (estimate).", { scenario: stop });
    const cover = outcome.feasible ? i18n.t("Tyres are expected to cover the stint.") : i18n.t("Tyres may not cover this stint.");
    const legality = outcome.compound_rule?.compliant === false ? ` ${i18n.t("Breaks the two-compound rule.")}` : "";
    result.textContent = `${comparison} ${cover}${legality}`;
    result.dataset.tone = outcome.feasible ? "success" : "error";
  } catch (error) {
    result.textContent = String(error.message || error);
    result.dataset.tone = "error";
  }
}

/* ---- Race planner ---------------------------------------------------------
   The ranked plans on the live path all start from where the car is now, so a
   race could not be settled before the lights — the exact conversation a
   driver wants to have in the garage. This runs the same stint simulation
   over a hypothetical grid start. */

async function loadPlannerTracks() {
  const select = byId("stratPlannerTrack");
  if (!select || select.dataset.loaded === "1") return;
  try {
    const payload = await api("/api/tracks");
    for (const track of payload.tracks || []) {
      const option = document.createElement("option");
      option.value = String(track.id);
      option.textContent = track.name;
      select.appendChild(option);
    }
    select.dataset.loaded = "1";
    if (view.lastState?.track_id >= 0) select.value = String(view.lastState.track_id);
  } catch {
    /* The current circuit still works; only the picker is unavailable. */
  }
}

function describeEvidence(evidence) {
  const parts = [];
  for (const [compound, model] of Object.entries(evidence || {})) {
    const measured = (model.pace_laps_observed ?? model.laps_observed ?? 0) > 0;
    const deg = model.deg_s_per_lap == null ? null : Number(model.deg_s_per_lap).toFixed(3);
    const pace = deg === null ? i18n.t("pace still learning")
      : measured
        ? i18n.t("{deg}s/lap from {laps} clean laps in {stints} stints", { deg, laps: model.pace_laps_observed ?? model.laps_observed, stints: model.stint_count ?? "—" })
        : i18n.t("{deg}s/lap inferred from {compounds}", { deg, compounds: (model.inferred_from || []).join(" + ") || i18n.t("observed compounds") });
    parts.push(
      `${compound}: ${pace}${model.wear_per_lap_pct == null ? "" : ` · ${i18n.t("{wear}% wear/lap", { wear: Number(model.wear_per_lap_pct).toFixed(2) })}`}`,
    );
  }
  return parts.join(" · ");
}

async function buildRacePlans(event) {
  event.preventDefault();
  const status = byId("stratPlannerStatus");
  const laps = byId("stratPlannerLaps").value;
  const trackValue = byId("stratPlannerTrack").value;
  const start = byId("stratPlannerStart").value;
  status.dataset.tone = "";
  status.textContent = i18n.t("Simulating the race from a standing start…");
  try {
    const payload = await post("/api/strategy/plan-race", {
      track_id: trackValue ? Number(trackValue) : null,
      total_laps: laps ? Number(laps) : null,
      start_compound: start || null,
    });
    const body = byId("stratPlannerRows");
    body.replaceChildren();
    byId("stratPlannerEvidence").textContent = describeEvidence(payload.tyre_evidence);
    if (!payload.available) {
      status.textContent = payload.reason ? i18n.t(payload.reason) : i18n.t("No plans available for that distance.");
      status.dataset.tone = "error";
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 7;
      cell.className = "empty";
      cell.textContent = payload.reason ? i18n.t(payload.reason) : i18n.t("No plans available.");
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    (payload.plans || []).forEach((plan, index) => {
      const row = document.createElement("tr");
      if (index === 0) row.className = "plan-row-recommended";
      const evidence = payload.tyre_evidence?.[String((plan.compounds || [])[0] || "").toUpperCase()];
      const cells = [
        `${index + 1}${index === 0 ? " ★" : ""} · ${stopsLabel(plan.stops_remaining)}`,
        (plan.compounds || []).join(" → "),
        (plan.box_laps || []).join(", ") || i18n.t("none"),
        `${plan.monte_carlo?.p75_s ?? plan.risk_adjusted_time_s ?? "—"}s`,
        `${plan.projected_max_wear_pct ?? "—"}%`,
        evidence && (evidence.laps_observed || 0) > 0 ? i18n.t("measured") : i18n.t("inferred"),
      ];
      for (const text of cells) {
        const cell = document.createElement("td");
        cell.textContent = String(text);
        row.appendChild(cell);
      }
      const action = document.createElement("td");
      const load = document.createElement("button");
      load.type = "button";
      load.className = "button ghost";
      load.textContent = i18n.t("Load");
      load.disabled = !canAdopt(plan);
      load.addEventListener("click", () => adoptPlan(plan, status));
      action.appendChild(load);
      row.appendChild(action);
      body.appendChild(row);
    });
    const count = payload.plans.length;
    const summary = { count, laps: String(payload.total_laps), track: payload.track_name, pit: payload.pit_loss_s ?? "—" };
    const planned = payload.track_name
      ? i18n.tp(count, "{count} plan for {laps} laps at {track} · pit loss {pit}s.", "{count} plans for {laps} laps at {track} · pit loss {pit}s.", summary)
      : i18n.tp(count, "{count} plan for {laps} laps at this circuit · pit loss {pit}s.", "{count} plans for {laps} laps at this circuit · pit loss {pit}s.", summary);
    status.textContent = `${planned} ${i18n.t(payload.basis)}`;
    status.dataset.tone = "success";
  } catch (error) {
    status.textContent = String(error.message || error);
    status.dataset.tone = "error";
  }
}

/* ---- Decision log --------------------------------------------------------- */

async function loadDecisionLog() {
  const host = byId("stratLog");
  try {
    const history = await api("/api/history?scope=current_session&limit=80");
    const snapshots = (history.strategies || []).slice().reverse();
    const signature = JSON.stringify(snapshots.map((snap) => [
      snap.id, snap.lap_num, snap.race_control_phase, snap.model?.confidence,
      snap.recommended?.instruction, snap.recommended?.box_laps, snap.recommended?.compounds,
      snap.recommended?.projected_finish_position, snap.recommended?.driver_override,
    ]));
    if (signature === view.logSignature) return;
    view.logSignature = signature;
    host.replaceChildren();
    if (!snapshots.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = i18n.t("Strategy snapshots appear as the session runs.");
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
      lap.textContent = i18n.t("Lap {lap}", { lap: snap.lap_num ?? "—" });
      const text = document.createElement("div");
      text.textContent = snap.recommended?.instruction || i18n.t("snapshot");
      const recommendation = snap.recommended || {};
      const details = [];
      if ((recommendation.box_laps || []).length > 1) {
        const stops = recommendation.box_laps.map((lap, index) =>
          i18n.t("lap {lap} {compound}", { lap: String(lap), compound: recommendation.compounds?.[index + 1] || i18n.t("compound unavailable") }));
        details.push(i18n.t("Full plan: {stops}", { stops: stops.reduce((plan, stop) => i18n.t("{plan}, then {stop}", { plan, stop })) }));
      }
      const projected = recommendation.projected_finish_position;
      if (Number.isInteger(projected) && projected > 0) {
        details.push(i18n.t("Estimated finish P{position}", { position: projected }));
      }
      if (details.length) {
        const detail = document.createElement("div");
        detail.className = "small";
        detail.textContent = details.join(" · ");
        text.appendChild(detail);
      }
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
    failed.textContent = i18n.t("Decision log unavailable: {error}", { error: error.message || error });
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
      loadPlannerTracks();
    }
  });
  document.addEventListener("DOMContentLoaded", () => {
    view.active = !byId("strategy").hidden;
  });
  byId("stratRecompute")?.addEventListener("click", () => {
    post("/api/strategy/recompute").catch(() => {});
  });
  byId("stratWhatIfForm")?.addEventListener("submit", runWhatIf);
  byId("stratTimeline")?.addEventListener("click", handleTimelineClick);
  byId("stratPlannerForm")?.addEventListener("submit", buildRacePlans);
  byId("stratAskSend")?.addEventListener("click", sendAsk);
  byId("stratAsk")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendAsk();
  });
  byId("stratLogRefresh")?.addEventListener("click", () => {
    view.logSignature = "";
    loadDecisionLog();
  });
}

export { planKey, describeEvidence, renderCall, renderPlans, loadDecisionLog, COMPOUND_COLORS };
