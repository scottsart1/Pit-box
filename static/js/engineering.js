/* Canonical lap IDs keep user groups and qualitative reports tied to their session. */
import { comparisonVisuals } from './engineering-visuals.mjs';
const $ = (id) => document.getElementById(id);
let report = null, request = 0, comparisonRequest = 0, active = false;
let notesDirty = false, notesScope = "", selectedSession = "", pendingSession = null, planBusy = false;
let groupDraft = [], groupDirty = false, groupRevision = 0, groupBusy = false;
let lapNoteDirty = false, lapNoteId = null, lapNoteTargets = null, noteBusy = false, noteRevision = 0;
let rivalsPending = false, rivalRequest = 0, lastRivalsAt = 0;
let comparisonReport = null, comparisonReportRequest = 0, comparisonLoading = false;
let comparisonSessions = [], comparisonSessionsRequest = 0, comparisonCursor = null, comparisonTrack = null;
const node = (tag, text = "", cls = "") => {
  const result = document.createElement(tag); result.textContent = String(text);
  if (cls) result.className = cls;
  return result;
};
const value = (number, unit = "") => number == null ? i18n.t("Unavailable") : `${Number(number).toFixed(3)}${unit}`;
const range = (numbers, unit = "") => Array.isArray(numbers) && numbers.length ? `${numbers.map((n) => Number(n).toFixed(1)).join("–")}${unit}` : i18n.t("Unavailable");
const status = (text) => { $("engineeringStatus").textContent = text; };
const allLaps = () => report?.laps || (report?.runs || []).flatMap((run) => run.laps);
const lapLabel = (lap) => Number(lap.timeline_epoch)
  ? i18n.t("Lap {lap} · timeline {timeline}", { lap: String(lap.lap_num), timeline: Number(lap.timeline_epoch) + 1 })
  : i18n.t("Lap {lap}", { lap: String(lap.lap_num) });
const groupLabel = (group) => group.name || i18n.t("Run {number} · {compound}", { number: String(group.number), compound: String(group.compound) });
const compareItems = () => report?.[$("engineeringCompareSource").value] || [];
const compareSourceB = () => $("engineeringCompareSourceB").value || $("engineeringCompareSource").value;
const reportB = () => $("engineeringSessionB").value ? comparisonReport : report;
const sessionLabel = (session) => `${session.track_name || i18n.t("Circuit")} · ${session.session_type || i18n.t("Session")} · ${String(session.started_at || "").slice(0, 19).replace("T", " ") || session.session_id || session.id}`;
const hasDraft = () => notesDirty || groupDirty || lapNoteDirty;
const mutationBusy = () => groupBusy || noteBusy || planBusy;
async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, { ...options, headers: { "Content-Type": "application/json" } });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : i18n.t("Request failed ({status})", { status: response.status }));
  return body;
}
function fillOptions(id, items, label, includeSession = false) {
  const select = $(id), previous = select.value;
  select.replaceChildren();
  if (includeSession) select.add(new Option(i18n.t("Whole session"), ""));
  for (const item of items) select.add(new Option(label(item), item.id));
  if ([...select.options].some((item) => item.value === previous)) select.value = previous;
}
function currentNotes() {
  if (!report) return {};
  return notesScope ? [...report.runs, ...(report.groups || [])].find((run) => run.id === notesScope)?.notes || {} : report.notes;
}
function showNotes() {
  const notes = currentNotes();
  $("engineeringObjective").value = notes.objective || "";
  $("engineeringConclusion").value = notes.conclusion || ""; notesDirty = false;
}
function resetComparison() {
  comparisonRequest++;
  const stint = $("engineeringCompareMode").value === "stint";
  $("engineeringCompare").textContent = stint ? i18n.t("Compare stints") : i18n.t("Compare matched laps");
  $("engineeringComparison").replaceChildren(node("p", stint
    ? i18n.t("Compare the clean laps you selected, including different compounds or setups. Differences in fuel, tyre age, weather and reported context remain visible; this does not isolate what caused a pace change.")
    : i18n.t("The setup test uses the same compound and weather, tyre age within one lap, fuel within 3 kg and track/air temperatures within 3°C. It excludes invalid, pit, flagged, traffic, reported exclusions and incomplete laps."), "muted"));
}
function clearComparisonSession() {
  comparisonReportRequest++; comparisonSessionsRequest++; comparisonReport = null; comparisonLoading = false;
  comparisonSessions = []; comparisonCursor = null; comparisonTrack = null;
  $("engineeringSessionB").replaceChildren(new Option(i18n.t("Same session as A"), ""));
  $("engineeringCompareSourceB").value = "";
  $("engineeringSessionBStatus").textContent = ""; $("engineeringMoreSessions").hidden = true;
}
function renderComparisonChoices() {
  const aItems = compareItems(), bItems = reportB()?.[compareSourceB()] || [];
  fillOptions("engineeringRunA", aItems, groupLabel); fillOptions("engineeringRunB", bItems, groupLabel);
  if (reportB()?.session_id === report?.session_id && compareSourceB() === $("engineeringCompareSource").value && bItems.length > 1 && $("engineeringRunA").value === $("engineeringRunB").value) $("engineeringRunB").selectedIndex = 1;
  $("engineeringCompare").disabled = comparisonLoading || !aItems.length || !bItems.length || (reportB()?.session_id === report?.session_id && $("engineeringRunA").value === $("engineeringRunB").value);
  $("engineeringSessionB").disabled = !report;
  $("engineeringRunB").disabled = comparisonLoading || !bItems.length;
  $("engineeringEditSessionB").hidden = !$("engineeringSessionB").value || !comparisonReport;
  $("engineeringSessionALabel").textContent = report ? sessionLabel(report) : i18n.t("Choose a session above.");
  $("engineeringEditorSession").textContent = report ? i18n.t("Groups, notes and exports below belong to A: {session}.", { session: sessionLabel(report) }) : i18n.t("Choose a session to edit its groups and notes.");
  const preview = $("engineeringSelectionB"); preview.replaceChildren();
  if (comparisonLoading) { preview.append(node("p", i18n.t("Loading B’s runs and notes…"), "muted")); return; }
  if (!bItems.length && reportB()) { preview.append(node("p", compareSourceB() === "groups" ? i18n.t("No saved lap groups in B. Open that session to define groups, or choose its automatic runs.") : i18n.t("No recorded runs in B."), "muted")); return; }
  const selected = bItems.find((item) => item.id === $("engineeringRunB").value);
  if (!selected || !$("engineeringSessionB").value) return;
  const details = node("details", "", "engineering-evidence");
  details.append(node("summary", i18n.t("B evidence · {count} clean laps", { count: selected.summary?.clean_lap_count ?? 0 })), node("p", sessionLabel(reportB()), "small muted"), node("p", i18n.t("Included: {laps}", { laps: (selected.laps || []).map(lapLabel).join(", ") })));
  const notes = new Map((selected.laps || []).flatMap((lap) => lap.context_notes || []).map((note) => [note.id, note]));
  for (const note of notes.values()) details.append(node("p", `${noteLabel(note)}: ${note.text}`, "small engineering-reported"));
  for (const [label, text] of [[i18n.t("Objective"), selected.notes?.objective], [i18n.t("Conclusion"), selected.notes?.conclusion]]) if (text) details.append(node("p", `${label}: ${text}`));
  preview.append(details);
}
async function loadComparisonSessions(more = false) {
  const track = report?.track_id;
  if (!Number.isInteger(track) || track < 0) { $("engineeringSessionBStatus").textContent = i18n.t("A needs a recorded track identity to find other sessions."); return; }
  const key = report.session_id, ticket = ++comparisonSessionsRequest, cursor = more ? comparisonCursor : null;
  $("engineeringMoreSessions").disabled = true;
  try {
    const result = await api(`/sessions?limit=200&track_id=${track}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
    if (ticket !== comparisonSessionsRequest || report?.session_id !== key) return;
    comparisonTrack = track; comparisonCursor = result.next_cursor || null;
    comparisonSessions = [...(more ? comparisonSessions : []), ...(result.items || [])];
    const select = $("engineeringSessionB"), previous = select.value;
    select.replaceChildren(new Option(i18n.t("Same session as A"), ""));
    const added = new Set();
    for (const session of comparisonSessions) if (session.id !== key && session.track_id === track && !added.has(session.id)) { select.add(new Option(session.display_name ? `${session.display_name} · ${sessionLabel(session)}` : sessionLabel(session), session.id)); added.add(session.id); }
    if (previous && added.has(previous)) select.value = previous;
    else if (previous && comparisonReport?.session_id === previous && comparisonReport.track_id === track) { select.add(new Option(sessionLabel(comparisonReport), previous)); select.value = previous; }
    $("engineeringSessionBStatus").textContent = added.size ? i18n.t("Only saved sessions at A’s track are listed. Each session keeps its own notes and lap groups.") : i18n.t("No other saved sessions at this track yet.");
    $("engineeringMoreSessions").hidden = !comparisonCursor;
  } catch (error) { if (ticket === comparisonSessionsRequest) $("engineeringSessionBStatus").textContent = error.message; }
  finally { if (ticket === comparisonSessionsRequest) $("engineeringMoreSessions").disabled = false; }
}
async function loadComparisonReport() {
  const id = $("engineeringSessionB").value, key = report?.session_id, ticket = ++comparisonReportRequest;
  const previousRun = comparisonReport?.session_id === id ? $("engineeringRunB").value : "";
  comparisonReport = null; comparisonLoading = Boolean(id); resetComparison(); renderComparisonChoices();
  if (!id || !report) return;
  try {
    const result = await api(`/sessions/${encodeURIComponent(id)}/engineering`);
    if (ticket !== comparisonReportRequest || report?.session_id !== key || $("engineeringSessionB").value !== id) return;
    if (result.track_id !== report.track_id || !Number.isInteger(result.track_id) || result.track_id < 0) throw new Error(i18n.t("Choose a saved session at the same recorded track as A."));
    comparisonReport = result;
  } catch (error) { if (ticket === comparisonReportRequest) $("engineeringComparison").textContent = error.message; }
  finally { if (ticket === comparisonReportRequest) { comparisonLoading = false; renderComparisonChoices(); if (previousRun && [...$("engineeringRunB").options].some((option) => option.value === previousRun)) { $("engineeringRunB").value = previousRun; renderComparisonChoices(); } } }
}
function conditionLines(conditions) {
  if (!conditions) return [];
  const traffic = conditions.traffic || {};
  const missing = Object.entries(conditions.field_coverage || {}).filter(([, coverage]) => coverage.missing_laps > 0).map(([field, coverage]) => i18n.t("{field}: {observed}/{total} laps", { field: field.replaceAll("_", " "), observed: String(coverage.observed_laps), total: String(conditions.lap_count) }));
  // A tyre-age range reads "1.0–3.0 laps"; without one it is just "Unavailable".
  const fuel = range(conditions.fuel_kg, " kg"), age = range(conditions.tyre_age_laps);
  return [
    i18n.t("Measured air {air} · track {track}", { air: range(conditions.air_temp_c, "°C"), track: range(conditions.track_temp_c, "°C") }),
    Array.isArray(conditions.tyre_age_laps) && conditions.tyre_age_laps.length
      ? i18n.t("Starting fuel {fuel} · tyre age {age} laps", { fuel, age })
      : i18n.t("Starting fuel {fuel} · tyre age {age}", { fuel, age }),
    i18n.t("Compounds: {compounds} · weather: {weather}", { compounds: (conditions.compounds || []).join(", ") || i18n.t("Unavailable"), weather: (conditions.weather || []).join(", ") || i18n.t("Unavailable") }),
    i18n.t("Traffic telemetry: {affected} affected, {observed} observed, {unknown} unknown laps. Reported traffic: {reported} laps.", { affected: traffic.measured_affected_laps ?? 0, observed: traffic.observed_laps ?? 0, unknown: traffic.unknown_laps ?? 0, reported: traffic.reported_laps ?? 0 }),
    ...(missing.length ? [i18n.t("Partial measured coverage — {fields}. Missing values are not treated as matching conditions.", { fields: missing.join("; ") })] : []),
    ...(traffic.caveat ? [traffic.caveat] : []),
  ];
}
function noteCategory(category) {
  switch (category) {
    case "traffic": return i18n.t("traffic");
    case "mistake": return i18n.t("mistake");
    case "balance": return i18n.t("balance");
    case "conditions": return i18n.t("conditions");
    case "mechanical": return i18n.t("mechanical");
    case "other": return i18n.t("other");
    default: return category.replaceAll("_", " ");
  }
}
function noteLabel(note) { return `${note.source === "engineer" ? i18n.t("Engineer note") : i18n.t("Driver report")} · ${noteCategory(note.category || "other")} · ${note.exclude_from_pace ? i18n.t("excluded from pace") : i18n.t("context only")}`; }
function savedNoteText(side, scope, field, text) {
  const params = { side: side.toUpperCase(), text };
  if (scope === "session") return field === "objective" ? i18n.t("{side} · session objective: {text}", params) : i18n.t("{side} · session conclusion: {text}", params);
  return field === "objective" ? i18n.t("{side} · selected run/group objective: {text}", params) : i18n.t("{side} · selected run/group conclusion: {text}", params);
}
function renderCards() {
  const items = compareItems(), output = $("engineeringRuns");
  const opened = new Set([...output.querySelectorAll("details[open][data-group]")].map((item) => item.dataset.group));
  output.replaceChildren();
  for (const run of items) {
    const s = run.summary, card = node("details", "", "panel-surface engineering-run");
    card.dataset.group = run.id; card.open = opened.has(run.id);
    const heading = node("summary");
    heading.append(node("strong", groupLabel(run)), node("span", i18n.t("{clean} / {total} clean · median {pace}", { clean: String(s.clean_lap_count), total: String(s.lap_count), pace: value(s.median_pace_s, " s") }), "small muted"));
    card.append(heading);
    const metrics = node("div", "", "summary-grid");
    for (const [label, metric] of [[i18n.t("Median pace"), value(s.median_pace_s, " s")], [i18n.t("Consistency (SD)"), value(s.consistency_stdev_s, " s")], [i18n.t("Observed pace trend"), value(s.observed_pace_trend_s_per_lap, " s/lap")], [i18n.t("Tyre inner temperatures"), range(s.tyre_inner_temp_range_c, "°C")]]) {
      const tile = node("div", "", "summary-metric"); tile.append(node("span", label), node("strong", metric)); metrics.append(tile);
    }
    card.append(node("p", i18n.t("Included: {laps}", { laps: (run.laps || []).map(lapLabel).join(", ") })), metrics, node("p", s.takeaway), node("p", i18n.t("Next test: {test}", { test: String(s.next_test) })), node("p", s.degradation_caveat || i18n.t("Pace trends include fuel and condition effects."), "small muted"));
    for (const line of conditionLines(run.conditions)) card.append(node("p", line, "small"));
    if (s.balance?.available) card.append(node("p", i18n.t("Handling indicators: {wheelspin} corner wheelspin flags; {locking} locking flags. {caveat}", { wheelspin: String(s.balance.wheelspin_corner_flags), locking: String(s.balance.locking_corner_flags), caveat: String(s.balance.caveat) }), "small"));
    else card.append(node("p", i18n.t("Balance: insufficient corner evidence."), "small muted"));
    const changes = Object.entries(run.setup_changes || {}).map(([key, change]) => `${key}: ${change.from ?? i18n.t("unknown")} → ${change.to ?? i18n.t("unknown")}`);
    card.append(node("p", changes.join("; ") || i18n.t("No recorded setup change from the previous run."), "small"));
    card.append(node("p", i18n.t("Setup: {setup}", { setup: run.heterogeneous ? i18n.t("Multiple setups or conditions; review individual lap evidence.") : Object.entries(run.setup || {}).map(([key, val]) => `${key} ${val}`).join(", ") || i18n.t("Unavailable") }), "small"));
    const notes = new Map();
    for (const lap of run.laps || []) for (const note of lap.context_notes || []) notes.set(note.id, note);
    for (const note of notes.values()) card.append(node("p", `${noteLabel(note)}: ${note.text}`, "small engineering-reported"));
    for (const excluded of s.excluded_laps || []) card.append(node("p", i18n.t("Lap {lap} excluded: {reasons}", { lap: String(excluded.lap), reasons: excluded.reasons.join(", ").replaceAll("_", " ") }), "small muted"));
    output.append(card);
  }
  if (!items.length) output.append(node("p", $("engineeringCompareSource").value === "groups" ? i18n.t("No saved lap groups yet. Open ‘Define my lap groups’ to select your laps, then save.") : i18n.t("Complete timed laps to build a run. Returning to the pits produces a debrief; leaving starts the next run."), "empty"));
  renderComparisonChoices();
}
function groupControls() {
  $("engineeringSaveGroups").disabled = !report || !groupDirty || mutationBusy();
  $("engineeringDiscardGroups").disabled = !groupDirty || groupBusy;
  $("engineeringSuggestGroups").disabled = allLaps().length < 2 || mutationBusy();
  $("engineeringAddGroup").disabled = !allLaps().length || groupBusy || groupDraft.length >= 12;
  const assigned = new Set(groupDraft.flatMap((group) => group.lap_ids));
  const counts = { groups: groupDraft.length, laps: allLaps().filter((lap) => !assigned.has(lap.id)).length };
  $("engineeringGroupSummary").textContent = ` ${groupDirty ? i18n.t("{groups} groups · unsaved edits · {laps} laps unassigned", counts) : i18n.t("{groups} groups saved · {laps} laps unassigned", counts)}`;
}
function mutationControls() {
  groupControls();
  $("engineeringSaveNotes").disabled = !report || mutationBusy();
  $("engineeringNotesScope").disabled = mutationBusy();
  $("engineeringSaveLapNote").disabled = !allLaps().length || mutationBusy();
  $("engineeringCancelLapNote").disabled = noteBusy;
}
function markGroupsDirty() { groupDirty = true; groupRevision++; resetComparison(); groupControls(); }
function activeGroup() { return groupDraft.find((group) => group.id === $("engineeringGroupSelect").value); }
function renderGroupEditor() {
  fillOptions("engineeringGroupSelect", groupDraft, (group) => i18n.t("{name} · {count} laps", { name: String(group.name), count: group.lap_ids.length }));
  const group = activeGroup(), laps = allLaps();
  $("engineeringGroupFields").hidden = !group;
  $("engineeringGroupName").value = group?.name || "";
  for (const id of ["engineeringGroupFrom", "engineeringGroupTo"]) fillOptions(id, laps, lapLabel);
  if (group?.lap_ids.length) {
    $("engineeringGroupFrom").value = group.lap_ids[0]; $("engineeringGroupTo").value = group.lap_ids.at(-1);
  }
  const choices = $("engineeringLapChoices"); choices.replaceChildren();
  if (!group) { groupControls(); return; }
  for (const lap of laps) {
    const owner = groupDraft.find((item) => item.id !== group?.id && item.lap_ids.includes(lap.id));
    const label = node("label", "", "engineering-lap-choice");
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.value = lap.id;
    checkbox.checked = !!group?.lap_ids.includes(lap.id); checkbox.disabled = !!owner;
    checkbox.setAttribute("aria-label", owner ? i18n.t("{lap}, assigned to {group}", { lap: lapLabel(lap), group: String(owner.name) }) : lapLabel(lap));
    checkbox.addEventListener("change", () => {
      const selected = new Set(group.lap_ids); if (checkbox.checked) selected.add(lap.id); else selected.delete(lap.id);
      group.lap_ids = laps.filter((item) => selected.has(item.id)).map((item) => item.id); markGroupsDirty();
      const option = [...$("engineeringGroupSelect").options].find((item) => item.value === group.id);
      option.textContent = i18n.t("{name} · {count} laps", { name: String(group.name), count: group.lap_ids.length });
    });
    const info = node("span"); info.append(node("strong", `${lapLabel(lap)} · ${lap.compound || i18n.t("Unknown compound")}`));
    const measured = { time: value(Number(lap.lap_time_ms) / 1000, " s"), air: lap.air_temp_c ?? "?", track: lap.track_temp_c ?? "?" };
    info.append(node("span", owner ? i18n.t("{time} · air {air}°C · track {track}°C · in {group}", { ...measured, group: String(owner.name) }) : i18n.t("{time} · air {air}°C · track {track}°C", measured), "small muted"));
    for (const note of lap.context_notes || []) info.append(node("span", `${noteLabel(note)}: ${note.text}`, "small engineering-reported"));
    label.append(checkbox, info); choices.append(label);
  }
  groupControls();
}
function draftFromReport() { groupDraft = (report?.groups || []).map((group) => ({ id: group.id, name: group.name, lap_ids: [...group.lap_ids] })); groupDirty = false; groupRevision++; renderGroupEditor(); }
function selectedRange(fromId, toId) {
  const laps = allLaps(), from = laps.findIndex((lap) => lap.id === $(fromId).value), to = laps.findIndex((lap) => lap.id === $(toId).value);
  if (from < 0 || to < from) throw new Error(i18n.t("Choose a first lap followed by a last lap in session order."));
  return laps.slice(from, to + 1).map((lap) => lap.id);
}
function applyRange() {
  try {
    const group = activeGroup(), ids = selectedRange("engineeringGroupFrom", "engineeringGroupTo");
    if (!group) return;
    const owner = groupDraft.find((item) => item.id !== group.id && item.lap_ids.some((id) => ids.includes(id)));
    if (owner) throw new Error(i18n.t("This range includes laps in {group}. Remove those laps from that group first.", { group: String(owner.name) }));
    group.lap_ids = ids; markGroupsDirty(); renderGroupEditor(); $("engineeringGroupStatus").textContent = i18n.t("Range applied to the draft. Save groups to use it in comparisons.");
  } catch (error) { $("engineeringGroupStatus").textContent = error.message; }
}
async function suggestGroups(confirmed = false) {
  if (!report || mutationBusy()) return;
  if (groupDirty && !confirmed) { $("engineeringReplaceDraft").hidden = false; return; }
  const count = Number($("engineeringGroupCount").value);
  if (!Number.isInteger(count) || count < 2 || count > Math.min(12, allLaps().length)) { $("engineeringGroupStatus").textContent = i18n.t("Choose between 2 and {max} groups, with at least one lap each.", { max: Math.min(12, allLaps().length) }); return; }
  const key = report.session_id, revision = groupRevision; groupBusy = true; request++; mutationControls();
  $("engineeringReplaceDraft").hidden = true;
  try {
    const result = await api(`/sessions/${encodeURIComponent(key)}/engineering/groups/suggest`, { method: "POST", body: JSON.stringify({ count }) });
    if (report?.session_id !== key) return;
    if (revision !== groupRevision) { $("engineeringGroupStatus").textContent = i18n.t("Your edits changed while the suggestion was loading. They have been kept; suggest again when ready."); return; }
    groupDraft = result.groups.map((group) => ({ id: group.id, name: group.name, lap_ids: [...group.lap_ids] }));
    markGroupsDirty(); renderGroupEditor(); $("engineeringGroupStatus").textContent = `${result.explanation ? i18n.t(result.explanation) : i18n.t("Groups follow session order.")} ${i18n.t("Review the lap selection, then save.")}`;
  } catch (error) { if (report?.session_id === key) $("engineeringGroupStatus").textContent = error.message; }
  finally { groupBusy = false; mutationControls(); }
}
async function saveGroups() {
  if (!report || mutationBusy()) return;
  if (notesDirty && notesScope && report.groups?.some((group) => group.id === notesScope) && !groupDraft.some((group) => group.id === notesScope)) { $("engineeringGroupStatus").textContent = i18n.t("Save the unsaved test notes for the group you are removing before saving this grouping."); return; }
  if (groupDraft.some((group) => !group.name.trim() || !group.lap_ids.length)) { $("engineeringGroupStatus").textContent = i18n.t("Give every group a name and at least one lap, or remove empty groups."); return; }
  const key = report.session_id, revision = groupRevision; groupBusy = true; request++; mutationControls();
  try {
    const result = await api(`/sessions/${encodeURIComponent(key)}/engineering/groups`, { method: "PUT", body: JSON.stringify({ groups: groupDraft }) });
    if (report?.session_id !== key) return;
    request++; report = result;
    if (revision === groupRevision) draftFromReport();
    $("engineeringCompareSource").value = "groups"; $("engineeringCompareMode").value = "stint";
    resetComparison(); render(); $("engineeringGroupStatus").textContent = groupDirty ? i18n.t("Groups saved. Your newer edits are still unsaved.") : i18n.t("Groups saved. They are ready to compare and included in exports.");
    status(i18n.t("{runs} automatic runs · {groups} saved lap groups. Choose your groups or compare the automatic runs.", { runs: report.runs.length, groups: report.groups.length }));
  } catch (error) { if (report?.session_id === key) $("engineeringGroupStatus").textContent = error.message; }
  finally { groupBusy = false; mutationControls(); }
}
function renderLapNotes() {
  const notes = report?.lap_notes || [], output = $("engineeringContextNotes"); output.replaceChildren();
  $("engineeringLapNoteSummary").textContent = ` ${i18n.t("{count} saved notes", { count: notes.length })}`;
  for (const note of notes) {
    const card = node("article", "", "engineering-context-note");
    const laps = allLaps().filter((lap) => note.lap_ids.includes(lap.id));
    card.append(node("strong", noteLabel(note)), node("p", laps.map(lapLabel).join(", "), "small muted"), node("p", note.text));
    const pending = (note.pending_laps || []).filter((target) => !laps.some((lap) => lap.lap_num === target.lap_num && Number(lap.timeline_epoch || 0) === Number(target.timeline_epoch || 0)));
    if (pending.length) card.append(node("p", i18n.t("Awaiting recording: {laps}. The note will attach when those laps are saved; you can remove it now or edit after they arrive.", { laps: pending.map(lapLabel).join(", ") }), "small muted"));
    const actions = node("div", "", "linked-actions");
    const edit = node("button", i18n.t("Edit note"), "button ghost"), remove = node("button", i18n.t("Remove note"), "button ghost"); edit.type = remove.type = "button";
    edit.disabled = mutationBusy() || pending.length > 0; remove.disabled = mutationBusy();
    edit.addEventListener("click", () => {
      if (lapNoteDirty) { $("engineeringLapNoteStatus").textContent = i18n.t("Save or cancel your current note before editing another."); return; }
      lapNoteId = note.id; lapNoteTargets = [...note.lap_ids]; noteRevision++;
      $("engineeringLapNoteHeading").textContent = i18n.t("Edit note · {laps}", { laps: laps.map(lapLabel).join(", ") });
      $("engineeringNoteFrom").value = laps[0]?.id || ""; $("engineeringNoteTo").value = laps.at(-1)?.id || "";
      $("engineeringNoteCategory").value = note.category; $("engineeringLapNoteText").value = note.text; $("engineeringNoteExclude").checked = note.exclude_from_pace;
      $("engineeringCancelLapNote").hidden = false; $("engineeringLapNoteText").focus();
    });
    remove.addEventListener("click", () => deleteLapNote(note.id)); actions.append(edit, remove); card.append(actions); output.append(card);
  }
  if (!notes.length) output.append(node("p", i18n.t("No reported lap context yet."), "muted"));
  for (const id of ["engineeringNoteFrom", "engineeringNoteTo"]) fillOptions(id, allLaps(), lapLabel);
  mutationControls();
}
function clearLapNote() {
  lapNoteDirty = false; lapNoteId = null; lapNoteTargets = null; noteRevision++;
  $("engineeringLapNoteText").value = ""; $("engineeringNoteExclude").checked = false;
  $("engineeringLapNoteHeading").textContent = i18n.t("Add a lap note"); $("engineeringCancelLapNote").hidden = true;
}
async function saveLapNote() {
  if (!report || mutationBusy()) return;
  const key = report.session_id, revision = noteRevision;
  try {
    const text = $("engineeringLapNoteText").value.trim(); if (!text) throw new Error(i18n.t("Describe what happened on the selected laps."));
    const payload = { lap_ids: lapNoteTargets || selectedRange("engineeringNoteFrom", "engineeringNoteTo"), text, category: $("engineeringNoteCategory").value, exclude_from_pace: $("engineeringNoteExclude").checked };
    if (lapNoteId) payload.note_id = lapNoteId;
    noteBusy = true; request++; mutationControls();
    const result = await api(`/sessions/${encodeURIComponent(key)}/engineering/lap-notes`, { method: "PATCH", body: JSON.stringify(payload) });
    if (report?.session_id !== key) return;
    request++; report = result;
    if (revision === noteRevision) clearLapNote();
    else if (!lapNoteId) { const saved = result.lap_notes?.find((note) => note.text === payload.text && note.lap_ids.length === payload.lap_ids.length && note.lap_ids.every((id) => payload.lap_ids.includes(id))); if (saved) lapNoteId = saved.id; }
    resetComparison(); render();
    $("engineeringLapNoteStatus").textContent = lapNoteDirty ? i18n.t("Lap note saved. Your newer edits are still unsaved.") : i18n.t("Lap note saved. Comparisons and the engineer's review now include this context.");
  } catch (error) { if (report?.session_id === key) $("engineeringLapNoteStatus").textContent = error.message; }
  finally { noteBusy = false; renderLapNotes(); }
}
async function deleteLapNote(id) {
  if (!report || mutationBusy()) return;
  const key = report.session_id; noteBusy = true; request++; renderLapNotes();
  try {
    const result = await api(`/sessions/${encodeURIComponent(key)}/engineering/lap-notes/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (report?.session_id !== key) return;
    request++; report = result; if (lapNoteId === id) clearLapNote(); resetComparison(); render();
    $("engineeringLapNoteStatus").textContent = i18n.t("Note removed. Any exclusion from this note has also been removed.");
  } catch (error) { if (report?.session_id === key) $("engineeringLapNoteStatus").textContent = error.message; }
  finally { noteBusy = false; renderLapNotes(); }
}
function render() {
  $("engineeringTitle").textContent = report ? `${report.track_name} · ${report.session_type}` : i18n.t("Practice / Test Engineer");
  renderCards(); renderLapNotes();
  fillOptions("engineeringNotesScope", [...(report?.runs || []), ...(report?.groups || [])], groupLabel, true);
  if (notesDirty && notesScope && ![...$("engineeringNotesScope").options].some((option) => option.value === notesScope)) { $("engineeringNotesScope").add(new Option(i18n.t("Previous group · unsaved notes"), notesScope)); $("engineeringNotesScope").value = notesScope; }
  if (!notesDirty) { notesScope = $("engineeringNotesScope").value; showNotes(); }
  mutationControls();
  if (!groupDirty) draftFromReport(); else groupControls();
  for (const [id, format] of [["engineeringExportText", "text"], ["engineeringExportJson", "json"]]) {
    $(id).hidden = !report;
    if (report) $(id).href = `/api/v1/sessions/${encodeURIComponent(report.session_id)}/engineering/export?format=${format}`;
    else $(id).removeAttribute("href");
  }
}
async function loadSessions() {
  try {
    const result = await api("/sessions?limit=200"), select = $("engineeringSession");
    const previous = select.value, previousLabel = select.selectedOptions[0]?.textContent;
    select.replaceChildren(new Option(i18n.t("Current live session"), ""));
    for (const session of result.items || []) select.add(new Option(session.display_name || `${session.track_name || i18n.t("Circuit")} · ${session.session_type} · ${(session.started_at || "").slice(0, 16)}`, session.id));
    if (previous && ![...select.options].some((option) => option.value === previous)) select.add(new Option(previousLabel || i18n.t("Selected session"), previous));
    select.value = previous;
  } catch (error) { status(error.message); }
}
async function refresh() {
  if (mutationBusy()) return;
  const ticket = ++request, selected = $("engineeringSession").value;
  try {
    const result = await api(selected ? `/sessions/${encodeURIComponent(selected)}/engineering` : "/engineering/live");
    if (ticket !== request) return;
    const next = result.available === false ? null : result;
    if (next?.session_id !== report?.session_id) {
      if (hasDraft() && report) {
        // Pin the old session rather than applying draft laps or notes to a new track.
        selectedSession = report.session_id;
        const select = $("engineeringSession"); if (![...select.options].some((item) => item.value === selectedSession)) select.add(new Option(i18n.t("{track} · unsaved edits", { track: String(report.track_name) }), selectedSession));
        select.value = selectedSession; status(i18n.t("The live session changed. Your edits are kept with the previous session; save them before opening the new session.")); return;
      }
      notesDirty = false; groupDirty = false; clearLapNote(); clearComparisonSession(); resetComparison();
    }
    if (report?.session_id === next?.session_id && JSON.stringify([report?.laps, report?.groups, report?.lap_notes]) !== JSON.stringify([next?.laps, next?.groups, next?.lap_notes])) resetComparison();
    report = next; render();
    if (report && comparisonTrack !== report.track_id) loadComparisonSessions();
    status(result.reason ? i18n.t(result.reason) : report ? i18n.t("{runs} automatic runs · {groups} saved lap groups. Choose your groups or compare the automatic runs.", { runs: report.runs.length, groups: report.groups?.length || 0 }) : i18n.t("No saved laps in this session yet."));
  } catch (error) { if (ticket === request) status(error.message); }
}
async function compare() {
  if (!report) return;
  const ticket = ++comparisonRequest, key = report.session_id, bKey = reportB()?.session_id;
  const a = $("engineeringRunA").value, b = $("engineeringRunB").value, source = $("engineeringCompareSource").value, mode = $("engineeringCompareMode").value;
  const selected = { a: compareItems().find(item => item.id === a), b: reportB()?.[compareSourceB()]?.find(item => item.id === b) };
  if (!a || !b || !bKey || comparisonLoading || (a === b && key === bKey)) { $("engineeringComparison").textContent = i18n.t("Choose two different runs or groups."); return; }
  if ((source === "groups" || (bKey === key && compareSourceB() === "groups")) && groupDirty) { $("engineeringComparison").textContent = i18n.t("Save or discard your group edits before comparing, so the result uses the lap selection you can see."); return; }
  $("engineeringComparison").textContent = mode === "stint" ? i18n.t("Reviewing clean laps and recorded context…") : i18n.t("Matching lap conditions and reviewing context…");
  try {
    const result = await api(`/sessions/${encodeURIComponent(key)}/engineering/compare`, { method: "POST", body: JSON.stringify({ a, b, source, mode, b_session_id: bKey, b_source: compareSourceB() }) });
    if (ticket !== comparisonRequest || report?.session_id !== key || reportB()?.session_id !== bKey) return;
    const output = $("engineeringComparison"); output.replaceChildren(node("h3", result.conclusion), node("p", result.delta_definition));
    for (const side of ["a", "b"]) if (result.selections?.[side]) { const selection = result.selections[side]; output.append(node("p", `${side.toUpperCase()}: ${selection.name} · ${sessionLabel(selection)}`, "small engineering-comparison-origin")); }
    output.append(comparisonVisuals(result, selected));
    const testNotes = node("details", "", "engineering-evidence"); testNotes.append(node("summary", i18n.t("Saved test objectives and conclusions")));
    for (const side of ["a", "b"]) for (const [scope, notes] of [["selected run/group", result.selections?.[side]?.notes], ["session", result.selections?.[side]?.session_notes]]) for (const field of ["objective", "conclusion"]) if (notes?.[field]) testNotes.append(node("p", savedNoteText(side, scope, field, notes[field])));
    if (testNotes.children.length > 1) output.append(testNotes);
    if (result.clean_lap_counts) output.append(node("p", i18n.t("Clean laps: A {a}, B {b}.", { a: String(result.clean_lap_counts.a), b: String(result.clean_lap_counts.b) })));
    output.append(node("p", i18n.t("Sector gains/losses: {sectors}", { sectors: (result.sector_deltas_s || []).map((v, i) => `S${i + 1} ${value(v, " s")}`).join(" · ") })));
    if (result.sector_lap_counts) output.append(node("p", i18n.t("Complete sector evidence: A {a} laps, B {b} laps.", { a: String(result.sector_lap_counts.a), b: String(result.sector_lap_counts.b) }), "small muted"));
    for (const side of ["a", "b"]) {
      if (result.conditions?.[side]) { output.append(node("h4", i18n.t("{side} · all selected laps", { side: side.toUpperCase() }))); for (const line of conditionLines(result.conditions[side])) output.append(node("p", line, "small")); }
      if (result.clean_conditions?.[side]) {
        const cleanEvidence = node("details", "", "engineering-evidence"); cleanEvidence.append(node("summary", i18n.t("{side} · conditions of clean laps used for pace", { side: side.toUpperCase() })));
        for (const line of conditionLines(result.clean_conditions[side])) cleanEvidence.append(node("p", line, "small")); output.append(cleanEvidence);
      }
      for (const note of result.notes?.[side] || []) output.append(node("p", `${side.toUpperCase()} · ${noteLabel(note)}: ${note.text}`, "small engineering-reported"));
    }
    if (result.pairs?.length) {
      const evidence = node("details", "", "engineering-evidence"); evidence.append(node("summary", i18n.t("{count} matched pairs · view lap evidence", { count: result.pairs.length })));
      for (const pair of result.pairs) evidence.append(node("p", i18n.t("A lap {a} / B lap {b}: {delta} · tyre ages {ages} · fuel {fuel} kg · track {track}°C · air {air}°C", { a: String(pair.a_lap), b: String(pair.b_lap), delta: value(pair.delta_s, " s"), ages: pair.tyre_ages.join("/"), fuel: pair.fuel_kg.join("/"), track: pair.track_temp_c.join("/"), air: pair.air_temp_c?.join("/") || i18n.t("Unavailable") }), "small"));
      output.append(evidence);
    }
    for (const side of ["a", "b"]) for (const lap of result.excluded?.[side] || []) output.append(node("p", i18n.t("{side} lap {lap}: {reasons}", { side: side.toUpperCase(), lap: String(lap.lap), reasons: lap.reasons.join(", ").replaceAll("_", " ") }), "small muted"));
    if (result.caveats?.length || result.comparison_compatibility) {
      const limits = node("details", "", "engineering-evidence engineering-comparison-limits");
      limits.append(node("summary", result.cross_session ? i18n.t("Comparison limits · game and car unverified") : i18n.t("Comparison limits")));
      const compatibility = result.comparison_compatibility;
      if (compatibility) {
        limits.append(node("p", compatibility.track_length_status === "recorded_within_tolerance" ? i18n.t("Track length: recorded within {tolerance} m.", { tolerance: String(compatibility.track_length_tolerance_m) }) : i18n.t("Track length: unavailable for one or both selections."), "small"));
        for (const side of ["a", "b"]) { const selection = result.selections?.[side]; if (selection) limits.append(node("p", i18n.t("{side} · selected track length {length} · recording format {format}. Recording format does not identify the game version.", { side: side.toUpperCase(), length: range(selection.track_length_range_m, " m"), format: selection.packet_format || i18n.t("Unavailable") }), "small")); }
      }
      for (const caveat of result.caveats || []) limits.append(node("p", caveat, "small muted"));
      output.append(limits);
    }
  } catch (error) { if (ticket === comparisonRequest) $("engineeringComparison").textContent = error.message; }
}
async function saveNotes() {
  if (!report || mutationBusy()) return;
  const key = report.session_id, run_id = notesScope || null;
  const objective = $("engineeringObjective").value, conclusion = $("engineeringConclusion").value;
  planBusy = true; request++; mutationControls();
  try {
    const saved = await api(`/sessions/${encodeURIComponent(key)}/engineering/notes`, { method: "PATCH", body: JSON.stringify({ run_id, objective, conclusion }) });
    if (report?.session_id !== key) return;
    request++;
    if (run_id) { const group = [...report.runs, ...(report.groups || [])].find((run) => run.id === run_id); if (group) group.notes = saved; }
    else report.notes = { ...report.notes, ...saved };
    notesDirty = notesScope !== (run_id || "") || $("engineeringObjective").value !== objective || $("engineeringConclusion").value !== conclusion;
    status(i18n.t("Test notes saved. They are included in the session report."));
  } catch (error) { status(error.message); }
  finally { planBusy = false; mutationControls(); }
}
function openSession(id) {
  if (hasDraft() || mutationBusy()) {
    pendingSession = id; $("engineeringSession").value = selectedSession; $("engineeringDiscardPrompt").hidden = false; return;
  }
  selectedSession = id; $("engineeringSession").value = id; request++; report = null; notesScope = ""; notesDirty = false; groupDirty = false; clearLapNote(); clearComparisonSession();
  $("engineeringGroupStatus").textContent = ""; $("engineeringLapNoteStatus").textContent = "";
  resetComparison(); render(); refresh();
}
async function refreshRivals() {
  if (rivalsPending || document.hidden) return;
  rivalsPending = true; const ticket = ++rivalRequest;
  try {
    const result = await api("/engineering/rivals"); if (ticket !== rivalRequest) return;
    const output = $("strategicRivals"); output.replaceChildren();
    if (!result.available) output.append(node("p", result.reason ? i18n.t(result.reason) : i18n.t("Finish projections are still building."), "muted"));
    else {
      for (const rival of result.rivals) {
        const projection = { driver: String(rival.driver), position: String(rival.position), projected: String(rival.projected_position),
          gap: value(rival.projected_gap_s, " s"), compound: String(rival.compound), age: String(rival.tyre_age),
          stops: String(rival.estimated_stops_remaining), confidence: String(rival.confidence) };
        output.append(node("p", rival.gap_assumed
          ? i18n.t("{driver} · now P{position} → projected P{projected} · {gap} at the finish · {compound}, age {age} · {stops} estimated stops · {confidence} confidence (gap assumed)", projection)
          : i18n.t("{driver} · now P{position} → projected P{projected} · {gap} at the finish · {compound}, age {age} · {stops} estimated stops · {confidence} confidence", projection)));
      }
      output.append(node("p", i18n.t("Projected gap is relative to your selected plan; negative means ahead of you.") + " " + i18n.t(result.caveat), "small muted"));
    }
  } catch (error) { $("strategicRivals").textContent = error.message; }
  finally { rivalsPending = false; }
}
$("engineeringSession").addEventListener("change", () => openSession($("engineeringSession").value));
$("engineeringKeepEditing").addEventListener("click", () => { pendingSession = null; $("engineeringDiscardPrompt").hidden = true; });
$("engineeringDiscardAndOpen").addEventListener("click", () => {
  if (mutationBusy()) { status(i18n.t("Wait for the current save to finish before changing sessions.")); return; }
  const next = pendingSession; pendingSession = null; $("engineeringDiscardPrompt").hidden = true;
  notesDirty = false; groupDirty = false; clearLapNote(); if (next !== null) openSession(next);
});
$("engineeringRefresh").addEventListener("click", async () => { await loadSessions(); await refresh(); if (report) { await loadComparisonSessions(); if ($("engineeringSessionB").value) await loadComparisonReport(); } });
$("engineeringCompare").addEventListener("click", compare);
for (const id of ["engineeringRunA", "engineeringRunB", "engineeringCompareMode"]) $(id).addEventListener("change", () => { resetComparison(); renderComparisonChoices(); });
$("engineeringCompareSource").addEventListener("change", () => { renderCards(); resetComparison(); });
$("engineeringCompareSourceB").addEventListener("change", () => { resetComparison(); renderComparisonChoices(); });
$("engineeringSessionB").addEventListener("change", loadComparisonReport);
$("engineeringMoreSessions").addEventListener("click", () => loadComparisonSessions(true));
$("engineeringEditSessionB").addEventListener("click", () => { const id = $("engineeringSessionB").value; if (id) { const select = $("engineeringSession"); if (![...select.options].some((option) => option.value === id)) select.add(new Option(sessionLabel(comparisonReport), id)); openSession(id); } });
$("engineeringNotesScope").addEventListener("change", () => {
  if (notesDirty) { $("engineeringNotesScope").value = notesScope; status(i18n.t("Save your test notes before changing their scope.")); return; }
  notesScope = $("engineeringNotesScope").value; showNotes();
});
for (const id of ["engineeringObjective", "engineeringConclusion"]) $(id).addEventListener("input", () => { notesDirty = true; });
$("engineeringSaveNotes").addEventListener("click", saveNotes);
$("engineeringSuggestGroups").addEventListener("click", () => suggestGroups());
$("engineeringConfirmSuggestion").addEventListener("click", () => suggestGroups(true));
$("engineeringCancelSuggestion").addEventListener("click", () => { $("engineeringReplaceDraft").hidden = true; });
$("engineeringAddGroup").addEventListener("click", () => {
  const group = { id: `group-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`, name: `Stint ${String.fromCharCode(65 + groupDraft.length)}`, lap_ids: [] };
  groupDraft.push(group); markGroupsDirty(); renderGroupEditor(); $("engineeringGroupSelect").value = group.id; renderGroupEditor(); $("engineeringGroupName").focus();
});
$("engineeringGroupSelect").addEventListener("change", renderGroupEditor);
$("engineeringGroupName").addEventListener("input", () => { const group = activeGroup(); if (group) { group.name = $("engineeringGroupName").value; markGroupsDirty(); const option = $("engineeringGroupSelect").selectedOptions[0]; option.textContent = i18n.t("{name} · {count} laps", { name: String(group.name), count: group.lap_ids.length }); } });
$("engineeringRemoveGroup").addEventListener("click", () => { groupDraft = groupDraft.filter((group) => group.id !== $("engineeringGroupSelect").value); markGroupsDirty(); renderGroupEditor(); });
$("engineeringApplyRange").addEventListener("click", applyRange);
$("engineeringSaveGroups").addEventListener("click", saveGroups);
$("engineeringDiscardGroups").addEventListener("click", () => { draftFromReport(); $("engineeringReplaceDraft").hidden = true; $("engineeringGroupStatus").textContent = i18n.t("Saved groups restored."); resetComparison(); });
for (const id of ["engineeringNoteFrom", "engineeringNoteTo"]) $(id).addEventListener("change", () => { lapNoteTargets = null; lapNoteDirty = true; noteRevision++; $("engineeringCancelLapNote").hidden = false; });
$("engineeringNoteFrom").addEventListener("change", () => { $("engineeringNoteTo").value = $("engineeringNoteFrom").value; });
for (const id of ["engineeringLapNoteText", "engineeringNoteCategory", "engineeringNoteExclude"]) $(id).addEventListener("input", () => { lapNoteDirty = true; noteRevision++; $("engineeringCancelLapNote").hidden = false; });
$("engineeringSaveLapNote").addEventListener("click", saveLapNote);
$("engineeringCancelLapNote").addEventListener("click", clearLapNote);
window.addEventListener("beforeunload", (event) => { if (hasDraft()) { event.preventDefault(); event.returnValue = ""; } });
window.addEventListener("pitwall:pagechange", async (event) => {
  active = event.detail?.page === "test-engineer";
  if (active) { await loadSessions(); await refresh(); if ($("engineeringSessionB").value) await loadComparisonReport(); }
  if (event.detail?.page === "strategy") refreshRivals();
});
window.addEventListener("pitwall:engineering-session", (event) => {
  const select = $("engineeringSession"), id = event.detail.sessionId;
  if (![...select.options].some((option) => option.value === id)) select.add(new Option(i18n.t("Selected session"), id));
  if (id !== report?.session_id) openSession(id); else { selectedSession = id; select.value = id; }
});
let liveKey = "";
window.addEventListener("pitwall:state", (event) => {
  const s = event.detail, debrief = s.briefings?.post_stint?.text;
  $("stintBriefCard").hidden = !debrief; $("stintBriefText").textContent = debrief || "";
  const key = `${s.session_uid}:${s.restart_epoch}:${s.timeline_epoch}:${s.session_generation}`;
  if (key !== liveKey) {
    liveKey = key; rivalRequest++; $("strategicRivals").textContent = i18n.t("Finish projections are still building.");
    if (!$("engineeringSession").value) { request++; clearComparisonSession(); resetComparison(); if (!hasDraft() && !mutationBusy()) { report = null; render(); } else renderComparisonChoices(); if (active) refresh(); }
  }
  if (!$("strategy").hidden && Date.now() - lastRivalsAt > 5000) { lastRivalsAt = Date.now(); refreshRivals(); }
});
setInterval(() => { if (active && !document.hidden && !$("engineeringSession").value) refresh(); }, 10000);
resetComparison(); render();
if (location.hash === "#test-engineer" || (!$("test-engineer").hidden && !$("analysis").hidden)) { active = true; loadSessions().then(refresh); }
