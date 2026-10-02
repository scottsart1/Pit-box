/* Test Engineer uses canonical saved sessions; DOM text never interprets notes. */
const $ = (id) => document.getElementById(id);
let report = null;
let request = 0;
let comparisonRequest = 0;
let active = false;
let notesDirty = false;
let rivalsPending = false;
let rivalRequest = 0;
let lastRivalsAt = 0;
const node = (tag, text = "", cls = "") => {
  const result = document.createElement(tag);
  result.textContent = String(text);
  if (cls) result.className = cls;
  return result;
};
const value = (number, unit = "") => number == null ? "Unavailable" : `${Number(number).toFixed(3)}${unit}`;
const status = (text) => { $("engineeringStatus").textContent = text; };
async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, { ...options, headers: { "Content-Type": "application/json" } });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
  return body;
}
function options(id, runs, includeSession = false) {
  const select = $(id), previous = select.value;
  select.replaceChildren();
  if (includeSession) select.add(new Option("Whole session", ""));
  for (const run of runs) select.add(new Option(`Run ${run.number} · ${run.compound} · laps ${run.lap_range.join("–")}`, run.id));
  if ([...select.options].some((item) => item.value === previous)) select.value = previous;
}
function currentNotes() {
  if (!report) return {};
  return $("engineeringNotesScope").value ? report.runs.find((run) => run.id === $("engineeringNotesScope").value)?.notes || {} : report.notes;
}
function showNotes() {
  const notes = currentNotes();
  $("engineeringObjective").value = notes.objective || "";
  $("engineeringConclusion").value = notes.conclusion || "";
  notesDirty = false;
}
function resetComparison() {
  comparisonRequest++;
  $("engineeringComparison").replaceChildren(node("p", "Choose two runs to compare. The test uses the same compound and weather, tyre age within one lap, fuel within 3 kg and track/air temperatures within 3°C. It excludes invalid, pit, flagged, traffic and incomplete laps.", "muted"));
}
function render() {
  const runs = report?.runs || [];
  $("engineeringTitle").textContent = report ? `${report.track_name} · ${report.session_type}` : "Practice / Test Engineer";
  $("engineeringRuns").replaceChildren();
  for (const run of runs) {
    const s = run.summary, card = node("article", "", "panel-surface engineering-run");
    card.append(node("h2", `Run ${run.number} · ${run.compound}`), node("p", `Laps ${run.lap_range.join("–")} · ${s.clean_lap_count} / ${s.lap_count} clean`));
    const metrics = node("div", "", "summary-grid");
    for (const [label, metric] of [["Median pace", value(s.median_pace_s, " s")], ["Consistency (SD)", value(s.consistency_stdev_s, " s")], ["Observed pace trend", value(s.observed_pace_trend_s_per_lap, " s/lap")], ["Tyre inner temperatures", s.tyre_inner_temp_range_c ? `${s.tyre_inner_temp_range_c.join("–")}°C` : "Unavailable"]]) {
      const tile = node("div", "", "summary-metric"); tile.append(node("span", label), node("strong", metric)); metrics.append(tile);
    }
    card.append(metrics, node("p", s.takeaway), node("p", `Next test: ${s.next_test}`), node("p", s.degradation_caveat, "small muted"));
    if (s.balance.available) card.append(node("p", `Handling indicators: ${s.balance.wheelspin_corner_flags} corner wheelspin flags; ${s.balance.locking_corner_flags} locking flags. ${s.balance.caveat}`, "small"));
    else card.append(node("p", "Balance: insufficient corner evidence.", "small muted"));
    const details = node("details"), summary = node("summary", "Setup changes and lap evidence");
    details.append(summary);
    const changes = Object.entries(run.setup_changes).map(([key, change]) => `${key}: ${change.from ?? "unknown"} → ${change.to ?? "unknown"}`);
    details.append(node("p", changes.join("; ") || "No recorded setup change from the previous run."));
    details.append(node("p", `Setup: ${Object.entries(run.setup).map(([key, val]) => `${key} ${val}`).join(", ") || "Unavailable"}`));
    for (const excluded of s.excluded_laps) details.append(node("p", `Lap ${excluded.lap} excluded: ${excluded.reasons.join(", ").replaceAll("_", " ")}`, "small"));
    card.append(details); $("engineeringRuns").append(card);
  }
  if (!runs.length) $("engineeringRuns").append(node("p", "Complete timed laps to build a run. Returning to the pits produces a debrief; leaving starts the next run.", "empty"));
  options("engineeringRunA", runs); options("engineeringRunB", runs);
  if (runs.length > 1 && $("engineeringRunA").value === $("engineeringRunB").value) $("engineeringRunB").selectedIndex = 1;
  options("engineeringNotesScope", runs, true);
  if (!notesDirty) showNotes();
  $("engineeringCompare").disabled = runs.length < 2;
  $("engineeringSaveNotes").disabled = !report;
  for (const [id, format] of [["engineeringExportText", "text"], ["engineeringExportJson", "json"]]) {
    $(id).hidden = !report;
    if (report) $(id).href = `/api/v1/sessions/${encodeURIComponent(report.session_id)}/engineering/export?format=${format}`;
    else $(id).removeAttribute("href");
  }
}
async function loadSessions() {
  try {
    const result = await api("/sessions?limit=200");
    const select = $("engineeringSession"), previous = select.value;
    select.replaceChildren(new Option("Current live session", ""));
    for (const session of result.items || []) select.add(new Option(session.display_name || `${session.track_name || "Circuit"} · ${session.session_type} · ${(session.started_at || "").slice(0, 16)}`, session.id));
    if ([...select.options].some((option) => option.value === previous)) select.value = previous;
  } catch (error) { status(error.message); }
}
async function refresh() {
  const ticket = ++request, selected = $("engineeringSession").value;
  try {
    const result = await api(selected ? `/sessions/${encodeURIComponent(selected)}/engineering` : "/engineering/live");
    if (ticket !== request) return;
    const next = result.available === false ? null : result;
    if (next?.session_id !== report?.session_id) { notesDirty = false; resetComparison(); }
    report = next; render();
    status(result.reason || `${report.runs.length} runs recorded. Runs split at pit exits, tyre and setup changes. Pace trends include fuel effects.`);
  } catch (error) {
    if (ticket !== request) return;
    report = null; render(); resetComparison(); status(error.message);
  }
}
async function compare() {
  if (!report) return;
  const ticket = ++comparisonRequest, key = report.session_id;
  const a = $("engineeringRunA").value, b = $("engineeringRunB").value;
  if (!a || a === b) { $("engineeringComparison").textContent = "Choose two different runs."; return; }
  $("engineeringComparison").textContent = "Matching lap conditions…";
  try {
    const result = await api(`/sessions/${encodeURIComponent(key)}/engineering/compare`, { method: "POST", body: JSON.stringify({ a, b }) });
    if (ticket !== comparisonRequest || report?.session_id !== key) return;
    const output = $("engineeringComparison"); output.replaceChildren(node("h3", result.conclusion), node("p", result.delta_definition));
    output.append(node("p", `Sector gains/losses: ${result.sector_deltas_s.map((v, i) => `S${i + 1} ${value(v, " s")}`).join(" · ")}`));
    for (const pair of result.pairs) output.append(node("p", `A lap ${pair.a_lap} / B lap ${pair.b_lap}: ${value(pair.delta_s, " s")} · tyre ages ${pair.tyre_ages.join("/")} · fuel ${pair.fuel_kg.join("/")} kg · track ${pair.track_temp_c.join("/")}°C`, "small"));
    for (const side of ["a", "b"]) for (const lap of result.excluded[side]) output.append(node("p", `${side.toUpperCase()} lap ${lap.lap}: ${lap.reasons.join(", ").replaceAll("_", " ")}`, "small muted"));
    for (const caveat of result.caveats) output.append(node("p", caveat, "small muted"));
  } catch (error) { if (ticket === comparisonRequest) $("engineeringComparison").textContent = error.message; }
}
async function saveNotes() {
  if (!report) return;
  const key = report.session_id, run_id = $("engineeringNotesScope").value || null;
  const objective = $("engineeringObjective").value, conclusion = $("engineeringConclusion").value;
  $("engineeringSaveNotes").disabled = true;
  try {
    const saved = await api(`/sessions/${encodeURIComponent(key)}/engineering/notes`, { method: "PATCH", body: JSON.stringify({ run_id, objective, conclusion }) });
    if (report?.session_id !== key) return;
    if (run_id) report.runs.find((run) => run.id === run_id).notes = saved;
    else report.notes = { ...report.notes, ...saved };
    notesDirty = $("engineeringObjective").value !== objective || $("engineeringConclusion").value !== conclusion;
    status("Test notes saved. They are included in the session report.");
  } catch (error) { status(error.message); }
  finally { $("engineeringSaveNotes").disabled = !report; }
}
async function refreshRivals() {
  if (rivalsPending || document.hidden) return;
  rivalsPending = true;
  const ticket = ++rivalRequest;
  try {
    const result = await api("/engineering/rivals");
    if (ticket !== rivalRequest) return;
    const output = $("strategicRivals"); output.replaceChildren();
    if (!result.available) output.append(node("p", result.reason || "Finish projections are still building.", "muted"));
    else {
      for (const rival of result.rivals) output.append(node("p", `${rival.driver} · now P${rival.position} → projected P${rival.projected_position} · ${value(rival.projected_gap_s, " s")} at the finish · ${rival.compound}, age ${rival.tyre_age} · ${rival.estimated_stops_remaining} estimated stops · ${rival.confidence} confidence${rival.gap_assumed ? " (gap assumed)" : ""}`));
      output.append(node("p", "Projected gap is relative to your selected plan; negative means ahead of you. " + result.caveat, "small muted"));
    }
  } catch (error) { $("strategicRivals").textContent = error.message; }
  finally { rivalsPending = false; }
}
$("engineeringSession").addEventListener("change", () => { notesDirty = false; report = null; resetComparison(); render(); refresh(); });
$("engineeringRefresh").addEventListener("click", async () => { await loadSessions(); await refresh(); });
$("engineeringCompare").addEventListener("click", compare);
$("engineeringRunA").addEventListener("change", resetComparison);
$("engineeringRunB").addEventListener("change", resetComparison);
$("engineeringNotesScope").addEventListener("change", showNotes);
$("engineeringObjective").addEventListener("input", () => { notesDirty = true; });
$("engineeringConclusion").addEventListener("input", () => { notesDirty = true; });
$("engineeringSaveNotes").addEventListener("click", saveNotes);
window.addEventListener("pitwall:pagechange", async (event) => {
  active = event.detail?.page === "test-engineer";
  if (active) { await loadSessions(); await refresh(); }
  if (event.detail?.page === "strategy") refreshRivals();
});
window.addEventListener("pitwall:engineering-session", async (event) => {
  const select = $("engineeringSession"), id = event.detail.sessionId;
  if (![...select.options].some((option) => option.value === id)) select.add(new Option("Selected session", id));
  select.value = id; report = null; notesDirty = false; resetComparison();
});
let liveKey = "";
window.addEventListener("pitwall:state", (event) => {
  const s = event.detail;
  const debrief = s.briefings?.post_stint?.text;
  $("stintBriefCard").hidden = !debrief;
  $("stintBriefText").textContent = debrief || "";
  const key = `${s.session_uid}:${s.restart_epoch}:${s.timeline_epoch}:${s.session_generation}`;
  if (key !== liveKey) {
    liveKey = key; rivalRequest++; $("strategicRivals").textContent = "Finish projections are still building.";
    if (!$("engineeringSession").value) { request++; report = null; notesDirty = false; render(); resetComparison(); if (active) refresh(); }
  }
  if (!$("strategy").hidden && Date.now() - lastRivalsAt > 5000) { lastRivalsAt = Date.now(); refreshRivals(); }
});
setInterval(() => { if (active && !document.hidden && !$("engineeringSession").value) refresh(); }, 10000);
resetComparison(); render();
if (location.hash === "#test-engineer" || (!$("test-engineer").hidden && !$("analysis").hidden)) { active = true; loadSessions().then(refresh); }
