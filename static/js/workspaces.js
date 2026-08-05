const HAS_DOM = typeof window !== "undefined" && typeof document !== "undefined";
const API_ROOT = "/api/v1";

const state = {
  sessions: [],
  nextCursor: null,
  selectedSessionId: "",
  sessionDetail: null,
  quality: null,
  laps: [],
  candidateLapId: "",
  references: [],
  comparison: null,
  comparisonTrace: null,
  mapTraces: { candidate: null, reference: null },
  traceLayer: "speed",
  cursorIndex: 0,
  playbackTimer: null,
  fieldView: "classification",
  fieldCache: new Map(),
};

const byId = (id) => document.getElementById(id);

function clear(node) {
  if (node) node.replaceChildren();
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = String(text);
  return node;
}

function button(label, action, className = "button ghost") {
  const node = element("button", className, label);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

function formatError(error) {
  return error instanceof Error ? error.message : String(error);
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(`${API_ROOT}${path}`, { ...options, headers });
  } catch (error) {
    throw new Error(`Pit Wall's local API is unavailable: ${formatError(error)}`);
  }
  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = text; }
  }
  if (!response.ok) {
    const detail = payload?.detail;
    const message = detail?.message || (typeof detail === "string" ? detail : null) || payload?.message || `${response.status} ${response.statusText}`;
    throw new Error(message);
  }
  return payload;
}

function setNotice(id, message, tone = "") {
  const node = byId(id);
  if (!node) return;
  node.textContent = message;
  if (tone) node.dataset.tone = tone;
  else delete node.dataset.tone;
}

function navigate(page) {
  const tab = document.querySelector(`[role="tab"][data-page="${page}"]`);
  if (tab) tab.click();
}

function formatLapTime(milliseconds) {
  if (milliseconds === null || milliseconds === undefined || Number(milliseconds) <= 0) return "Unavailable";
  const value = Math.round(Number(milliseconds));
  const minutes = Math.floor(value / 60000);
  const seconds = Math.floor((value % 60000) / 1000);
  const millis = value % 1000;
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

function formatSeconds(value, signed = false) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "Unavailable";
  const number = Number(value);
  return `${signed && number >= 0 ? "+" : ""}${number.toFixed(3)} s`;
}

function formatPercent(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "Unavailable";
  return `${Math.round(Number(value) * 100)}%`;
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "No trace files";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function formatDate(value) {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString();
}

function sessionLabel(session) {
  return session.display_name || `${session.session_type || "Session"} · Track ${session.track_id ?? "unavailable"}`;
}

function lapLabel(lap) {
  return `${lap.display_name || `Car ${Number(lap.car_index ?? 0) + 1}`} · Lap ${lap.lap_number ?? "—"} · ${formatLapTime(lap.lap_time_ms)}`;
}

function metricText(metric, formatter = (value) => String(value)) {
  if (!metric || metric.value === null || metric.value === undefined || metric.availability === "unavailable") return "Unavailable";
  return formatter(metric.value);
}

function availabilityTitle(metric) {
  if (!metric) return "Availability metadata not supplied.";
  const parts = [metric.availability || "unavailable"];
  if (metric.n) parts.push(`n=${metric.n}`);
  if (metric.reason) parts.push(metric.reason);
  return parts.join(" · ");
}

function replaceOptions(select, options, placeholder, selectedValue = "") {
  if (!select) return;
  clear(select);
  const first = element("option", "", placeholder);
  first.value = "";
  select.append(first);
  options.forEach(({ value, label, disabled = false }) => {
    const option = element("option", "", label);
    option.value = value;
    option.disabled = disabled;
    select.append(option);
  });
  select.value = options.some((option) => option.value === selectedValue) ? selectedValue : "";
}

function refreshSessionSelectors() {
  const options = state.sessions.map((session) => ({ value: session.id, label: `${sessionLabel(session)} · ${formatDate(session.started_at)}` }));
  replaceOptions(byId("reviewSessionSelect"), options, "Choose session", state.selectedSessionId);
  replaceOptions(byId("fieldSessionSelect"), options, "Choose session", state.selectedSessionId);
}

function renderSessionRows() {
  const body = byId("libraryRows");
  clear(body);
  if (!state.sessions.length) {
    const row = element("tr");
    const cell = element("td", "empty", "No saved sessions match these filters.");
    cell.colSpan = 7;
    row.append(cell);
    body.append(row);
  }
  state.sessions.forEach((session) => {
    const row = element("tr");
    row.dataset.selected = String(session.id === state.selectedSessionId);
    const identity = element("td");
    const name = element("strong", "session-name", `${session.starred ? "★ " : ""}${sessionLabel(session)}`);
    identity.append(name, element("span", "field-help", `Track ID ${session.track_id ?? "unavailable"} · ${session.status || "status unavailable"}`));
    const tags = element("div", "session-tags");
    (session.tags || []).forEach((tag) => tags.append(element("span", "tag-chip", tag)));
    identity.append(tags);
    row.append(identity);
    row.append(element("td", "", formatDate(session.started_at)));
    row.append(element("td", "", session.session_type || "Unavailable"));
    row.append(element("td", "", `${session.drivers_observed ?? 0} drivers · ${session.lap_count ?? 0} laps`));
    row.append(element("td", "", session.quality_score == null ? "Unavailable" : formatPercent(session.quality_score)));
    row.append(element("td", "", formatBytes(session.size_bytes)));
    const actions = element("td");
    const group = element("div", "table-actions");
    group.append(
      button("Review", () => openSession(session.id, "session-review")),
      button("Field", () => openSession(session.id, "field")),
      button(session.starred ? "Unstar" : "Star", () => toggleStar(session)),
      button("Delete", () => deleteSession(session), "button ghost danger-button"),
    );
    actions.append(group);
    row.append(actions);
    body.append(row);
  });
  byId("libraryCount").textContent = `${state.sessions.length} session${state.sessions.length === 1 ? "" : "s"}`;
  byId("libraryLoadMore").hidden = !state.nextCursor;
}

function buildSessionQuery(cursor = null) {
  const query = new URLSearchParams({ limit: "50" });
  const search = byId("librarySearch")?.value.trim();
  const sessionType = byId("librarySessionType")?.value;
  const starred = byId("libraryStarred")?.value;
  if (search) query.set("search", search);
  if (sessionType) query.set("session_type", sessionType);
  if (starred) query.set("starred", starred);
  if (cursor) query.set("cursor", cursor);
  return query;
}

async function loadSessions({ append = false, quiet = false } = {}) {
  if (!quiet) setNotice("libraryStatus", append ? "Loading more sessions…" : "Loading saved sessions…");
  try {
    const payload = await api(`/sessions?${buildSessionQuery(append ? state.nextCursor : null)}`);
    const incoming = payload?.items || [];
    state.sessions = append ? [...state.sessions, ...incoming.filter((item) => !state.sessions.some((session) => session.id === item.id))] : incoming;
    state.nextCursor = payload?.next_cursor || null;
    renderSessionRows();
    refreshSessionSelectors();
    setNotice("libraryStatus", state.sessions.length ? `Loaded ${state.sessions.length} saved session${state.sessions.length === 1 ? "" : "s"}.` : "No saved sessions match these filters.", state.sessions.length ? "success" : "");
  } catch (error) {
    setNotice("libraryStatus", formatError(error), "error");
    if (!append) {
      state.sessions = [];
      state.nextCursor = null;
      renderSessionRows();
      refreshSessionSelectors();
    }
  }
}

async function toggleStar(session) {
  setNotice("libraryStatus", `${session.starred ? "Removing" : "Adding"} star for ${sessionLabel(session)}…`);
  try {
    const payload = await api(`/sessions/${encodeURIComponent(session.id)}`, { method: "PATCH", body: JSON.stringify({ starred: !session.starred }) });
    const changed = payload.session;
    const index = state.sessions.findIndex((item) => item.id === session.id);
    if (index >= 0) state.sessions[index] = { ...state.sessions[index], ...changed };
    renderSessionRows();
    setNotice("libraryStatus", `${sessionLabel(session)} ${session.starred ? "unstarred" : "starred"}.`, "success");
  } catch (error) {
    setNotice("libraryStatus", formatError(error), "error");
  }
}

async function deleteSession(session) {
  setNotice("libraryStatus", `Preparing an exact deletion preview for ${sessionLabel(session)}…`);
  try {
    const preview = await api(`/sessions/${encodeURIComponent(session.id)}`, { method: "DELETE" });
    const records = preview.impact?.records || {};
    const artifacts = preview.impact?.artifacts || [];
    const summary = `${records.laps ?? 0} laps, ${records.comparisons ?? 0} comparisons, and ${artifacts.length} linked files`;
    if (!window.confirm(`Delete “${sessionLabel(session)}”?\n\nThis removes ${summary}. The operation is irreversible.`)) {
      setNotice("libraryStatus", "Deletion cancelled.");
      return;
    }
    await api(`/sessions/${encodeURIComponent(session.id)}`, { method: "DELETE", headers: { "X-Pitwall-Delete-Token": preview.confirmation_token } });
    if (state.selectedSessionId === session.id) resetSelectedSession();
    await loadSessions();
    setNotice("libraryStatus", `${sessionLabel(session)} deleted.`, "success");
  } catch (error) {
    setNotice("libraryStatus", formatError(error), "error");
  }
}

function resetSelectedSession() {
  state.selectedSessionId = "";
  state.sessionDetail = null;
  state.quality = null;
  state.laps = [];
  state.candidateLapId = "";
  state.references = [];
  state.comparison = null;
  state.comparisonTrace = null;
  state.fieldCache.clear();
  refreshSessionSelectors();
}

async function openSession(sessionId, destination = "session-review") {
  await selectSession(sessionId);
  navigate(destination);
}

async function selectSession(sessionId) {
  if (!sessionId) {
    resetSelectedSession();
    renderSessionReview();
    renderFieldSummary(null);
    return;
  }
  state.selectedSessionId = sessionId;
  state.fieldCache.clear();
  refreshSessionSelectors();
  renderSessionRows();
  setNotice("sessionReviewStatus", "Loading recorded session, quality, and laps…");
  setNotice("fieldStatus", "Loading saved field summary…");
  try {
    const [detail, quality, laps, field] = await Promise.all([
      api(`/sessions/${encodeURIComponent(sessionId)}`),
      api(`/sessions/${encodeURIComponent(sessionId)}/quality`),
      api(`/sessions/${encodeURIComponent(sessionId)}/laps`),
      api(`/sessions/${encodeURIComponent(sessionId)}/field`).catch((error) => ({ _error: formatError(error) })),
    ]);
    if (state.selectedSessionId !== sessionId) return;
    state.sessionDetail = detail.session;
    state.quality = quality;
    state.laps = laps.items || [];
    state.fieldCache.set(`${sessionId}:classification`, field);
    renderSessionReview();
    renderFieldSummary(field);
    renderFieldClassification(field);
    populateLapSelectors();
  } catch (error) {
    setNotice("sessionReviewStatus", formatError(error), "error");
    setNotice("fieldStatus", formatError(error), "error");
  }
}

function summaryMetric(label, value, detail = "") {
  const card = element("div", "summary-metric");
  card.append(element("span", "", label), element("strong", "", value));
  if (detail) card.append(element("small", "availability-note", detail));
  return card;
}

function renderSessionReview() {
  const session = state.sessionDetail;
  const quality = state.quality;
  const selected = Boolean(session);
  byId("reviewReprocess").disabled = !selected;
  byId("reviewOpenField").disabled = !selected;
  byId("sessionReviewTitle").textContent = selected ? sessionLabel(session) : "Session Review";
  byId("sessionReviewContext").textContent = selected ? `${session.session_type || "Session type unavailable"} · ${formatDate(session.started_at)} · ${session.status || "status unavailable"}` : "Choose a saved session from Library or the selector below.";
  const badge = byId("reviewQualityBadge");
  badge.textContent = quality?.quality_score == null ? "Quality unavailable" : `${formatPercent(quality.quality_score)} quality`;
  badge.dataset.state = quality?.quality_score == null ? "warning" : Number(quality.quality_score) >= 0.8 ? "healthy" : "warning";
  const stats = byId("reviewStats");
  clear(stats);
  if (!selected) {
    stats.append(summaryMetric("Drivers observed", "Unavailable"), summaryMetric("Recorded laps", "Unavailable"), summaryMetric("Trace coverage", "Unavailable"), summaryMetric("Derived comparisons", "Unavailable"));
    setNotice("sessionReviewStatus", "No session selected.");
  } else {
    const lapQuality = quality?.laps || {};
    stats.append(
      summaryMetric("Drivers observed", String(quality?.participants_observed ?? session.participants?.length ?? 0), "Observed identities; revisions may be separate rows."),
      summaryMetric("Recorded laps", String(lapQuality.total ?? state.laps.length), `${lapQuality.valid ?? 0} valid`),
      summaryMetric("Trace coverage", lapQuality.mean_coverage == null ? "Unavailable" : formatPercent(lapQuality.mean_coverage), `${lapQuality.with_trace ?? 0} laps with typed traces`),
      summaryMetric("Derived comparisons", String(session.derived?.comparisons ?? 0), `${session.derived?.jobs?.length ?? 0} analysis jobs`),
    );
    const warnings = quality?.warnings || [];
    setNotice("sessionReviewStatus", warnings.length ? warnings.join(" ") : "Session catalog, quality report, and lap inventory loaded.", warnings.length ? "" : "success");
  }
  populateReviewDriverFilter();
  renderReviewLaps();
  renderReviewFindings();
}

function populateReviewDriverFilter() {
  const selected = byId("reviewDriverFilter")?.value || "";
  const participants = state.sessionDetail?.participants || [];
  replaceOptions(byId("reviewDriverFilter"), participants.map((driver) => ({ value: driver.id, label: driver.display_name || `Car ${Number(driver.car_index ?? 0) + 1}` })), "All drivers", selected);
}

function lapContext(lap) {
  const labels = [];
  if (!Boolean(lap.valid)) labels.push("Invalid lap");
  if (Number(lap.pit_context)) labels.push("Pit context");
  if (Number(lap.flag_context)) labels.push("Flag context");
  if (Number(lap.timeline_epoch) > 0) labels.push(`Timeline ${lap.timeline_epoch}`);
  return labels.length ? labels.join(" · ") : "Comparable context";
}

function renderReviewLaps() {
  const body = byId("sessionLapRows");
  clear(body);
  const filter = byId("reviewDriverFilter")?.value || "";
  const laps = state.laps.filter((lap) => !filter || lap.session_car_id === filter);
  if (!laps.length) {
    const row = element("tr");
    const cell = element("td", "empty", state.selectedSessionId ? "No laps match this driver filter." : "Choose a session to inspect its laps.");
    cell.colSpan = 6;
    row.append(cell);
    body.append(row);
    return;
  }
  laps.forEach((lap) => {
    const row = element("tr");
    row.dataset.selected = String(lap.id === state.candidateLapId);
    row.append(element("td", "", lap.display_name || `Car ${Number(lap.car_index ?? 0) + 1}`));
    row.append(element("td", "", String(lap.lap_number ?? "Unavailable")));
    row.append(element("td", "", formatLapTime(lap.lap_time_ms)));
    row.append(element("td", Boolean(lap.valid) ? "good" : "warn", lapContext(lap)));
    row.append(element("td", "", lap.coverage_ratio == null ? "Unavailable" : formatPercent(lap.coverage_ratio)));
    const action = element("td");
    const open = button("Open in Lap Lab", () => openLap(lap.id));
    open.disabled = !lap.id;
    action.append(open);
    row.append(action);
    body.append(row);
  });
}

async function requestReprocess() {
  if (!state.selectedSessionId) return;
  const action = byId("reviewReprocess");
  action.disabled = true;
  setNotice("sessionReviewStatus", "Queueing deterministic reprocessing…");
  try {
    const result = await api(`/sessions/${encodeURIComponent(state.selectedSessionId)}/reprocess`, { method: "POST" });
    setNotice("sessionReviewStatus", result.reused ? `Analysis job ${result.job.id} is already ${result.job.state}.` : `Analysis job ${result.job.id} queued.`, "success");
  } catch (error) {
    setNotice("sessionReviewStatus", formatError(error), "error");
  } finally {
    action.disabled = false;
  }
}

function populateLapSelectors() {
  const candidateOptions = state.laps.filter((lap) => lap.id).map((lap) => ({ value: lap.id, label: lapLabel(lap), disabled: !Boolean(lap.valid) }));
  replaceOptions(byId("candidateLapSelect"), candidateOptions, "Choose a valid recorded lap", state.candidateLapId);
  updateCandidateMeta();
}

function updateCandidateMeta() {
  const lap = state.laps.find((item) => item.id === state.candidateLapId);
  byId("candidateLapMeta").textContent = lap ? `${formatLapTime(lap.lap_time_ms)} · ${lap.tyre_compound || "compound unavailable"} · ${formatPercent(lap.coverage_ratio)}` : "Unavailable";
}

async function openLap(lapId) {
  state.candidateLapId = lapId;
  populateLapSelectors();
  renderReviewLaps();
  navigate("lap-lab");
  await loadReferences(lapId);
}

async function loadReferences(lapId) {
  const select = byId("referenceLapSelect");
  select.disabled = true;
  byId("createComparison").disabled = true;
  byId("referenceLapMeta").textContent = "Loading compatible references…";
  setNotice("lapLabStatus", "Checking stored laps for compatibility and trace coverage…");
  try {
    const payload = await api(`/laps/${encodeURIComponent(lapId)}/references`);
    if (state.candidateLapId !== lapId) return;
    state.references = payload.items || [];
    replaceOptions(select, state.references.map((reference) => ({ value: reference.lap_id, label: `${reference.suggested ? "Suggested · " : ""}${reference.driver || "Driver"} · Lap ${reference.lap_number ?? "—"} · ${formatLapTime(reference.lap_time_ms)} · ${reference.compatibility?.class || reference.compatibility?.classification || "compatibility unavailable"}` })), "Choose reference lap");
    select.disabled = !state.references.length;
    const suggested = state.references.find((reference) => reference.suggested) || state.references[0];
    if (suggested) select.value = suggested.lap_id;
    updateReferenceMeta();
    setNotice("lapLabStatus", state.references.length ? `${state.references.length} compatible or caveated reference candidate${state.references.length === 1 ? "" : "s"} found.` : "No compatible reference lap is available for this candidate.", state.references.length ? "success" : "");
  } catch (error) {
    state.references = [];
    replaceOptions(select, [], "No references available");
    byId("referenceLapMeta").textContent = "Unavailable";
    setNotice("lapLabStatus", formatError(error), "error");
  }
}

function updateReferenceMeta() {
  const id = byId("referenceLapSelect")?.value || "";
  const reference = state.references.find((item) => item.lap_id === id);
  byId("referenceLapMeta").textContent = reference ? `${formatLapTime(reference.lap_time_ms)} · ${(reference.reasons || []).join(" · ") || "context unavailable"}` : "Unavailable";
  byId("createComparison").disabled = !state.candidateLapId || !id;
  const compatibility = reference?.compatibility;
  const badge = byId("comparisonCompatibility");
  const classification = compatibility?.class || compatibility?.classification;
  badge.textContent = classification ? `${classification.replaceAll("_", " ")} reference${compatibility?.caveats?.length ? ` · ${compatibility.caveats.join(" · ")}` : ""}` : "Compatibility unavailable";
  badge.dataset.state = classification === "strict" ? "healthy" : classification ? "warning" : "neutral";
}

async function createComparison() {
  const referenceLapId = byId("referenceLapSelect")?.value || "";
  if (!state.candidateLapId || !referenceLapId) return;
  const reference = state.references.find((item) => item.lap_id === referenceLapId);
  const compatibility = reference?.compatibility;
  const classification = compatibility?.class || compatibility?.classification;
  const allowCaveat = classification && classification !== "strict";
  if (allowCaveat && !window.confirm(`This reference is “${classification.replaceAll("_", " ")}”. Pit Wall will preserve the caveats and may disable prescriptive coaching. Continue?`)) return;
  const action = byId("createComparison");
  action.disabled = true;
  stopPlayback();
  setNotice("lapLabStatus", "Aligning laps by distance and calculating deterministic segment evidence…");
  try {
    const comparison = await api("/comparisons", {
      method: "POST",
      body: JSON.stringify({ candidate_lap_id: state.candidateLapId, reference: { kind: "lap", lap_id: referenceLapId }, allow_caveated_reference: Boolean(allowCaveat) }),
    });
    state.comparison = comparison;
    const traceQuery = new URLSearchParams({ fields: "speed,delta,brake,throttle,steering,gear,line_n", max_points: "2400" });
    const mapQuery = new URLSearchParams({ fields: "world_x,world_z,line_n,speed,brake,throttle,steering,gear", max_points: "2400" });
    const [trace, candidateMap, referenceMap] = await Promise.all([
      api(`/comparisons/${encodeURIComponent(comparison.comparison_id)}/trace?${traceQuery}`),
      api(`/laps/${encodeURIComponent(comparison.candidate.lap_id)}/trace?${mapQuery}`),
      api(`/laps/${encodeURIComponent(comparison.reference.lap_id)}/trace?${mapQuery}`),
    ]);
    state.comparisonTrace = trace;
    state.mapTraces = { candidate: candidateMap, reference: referenceMap };
    state.cursorIndex = 0;
    configurePlayback();
    renderComparison();
    renderReviewFindings();
    setNotice("lapLabStatus", `Comparison ready · ${formatPercent(comparison.coverage_ratio)} aligned coverage · ${comparison.algorithm_bundle}.`, "success");
  } catch (error) {
    state.comparison = null;
    state.comparisonTrace = null;
    state.mapTraces = { candidate: null, reference: null };
    renderComparison();
    setNotice("lapLabStatus", formatError(error), "error");
  } finally {
    updateReferenceMeta();
    action.disabled = false;
  }
}

function renderComparison() {
  const comparison = state.comparison;
  const badge = byId("comparisonCompatibility");
  if (!comparison) {
    byId("comparisonDelta").textContent = "Unavailable";
    byId("traceCoverage").textContent = "Coverage unavailable";
    badge.textContent = "Compatibility unavailable";
    badge.dataset.state = "neutral";
    clear(byId("segmentRail"));
    byId("segmentRail").append(element("div", "empty", "No comparison loaded."));
    clear(byId("coachingFindings"));
    byId("coachingFindings").append(element("div", "empty", "No comparison loaded."));
    drawComparisonTrace();
    drawComparisonMap();
    updateInstruments();
    return;
  }
  byId("comparisonDelta").textContent = formatSeconds(comparison.lap_delta_s, true);
  byId("comparisonDelta").className = Number(comparison.lap_delta_s) > 0 ? "error" : Number(comparison.lap_delta_s) < 0 ? "good" : "";
  byId("comparisonSign").textContent = comparison.sign_convention || "Positive means the candidate arrived later.";
  const compatibility = comparison.compatibility || {};
  const classification = compatibility.class || compatibility.classification || "unavailable";
  badge.textContent = `${classification.replaceAll("_", " ")} · ${compatibility.allows_coaching === false ? "visual comparison only" : "coaching permitted"}`;
  badge.dataset.state = classification === "strict" ? "healthy" : "warning";
  byId("candidateLapMeta").textContent = `${comparison.candidate.driver || "Candidate"} · Lap ${comparison.candidate.lap_number} · ${formatLapTime(comparison.candidate.lap_time_ms)}`;
  byId("referenceLapMeta").textContent = `${comparison.reference.driver || "Reference"} · Lap ${comparison.reference.lap_number} · ${formatLapTime(comparison.reference.lap_time_ms)}`;
  byId("traceCoverage").textContent = `${formatPercent(comparison.coverage_ratio)} coverage`;
  byId("traceCoverage").dataset.state = Number(comparison.coverage_ratio) >= 0.9 ? "healthy" : "warning";
  renderSegments();
  renderCoachingFindings();
  drawComparisonTrace();
  drawComparisonMap();
  updateInstruments();
}

function renderSegments() {
  const rail = byId("segmentRail");
  clear(rail);
  const segments = state.comparison?.segments || [];
  if (!segments.length) {
    rail.append(element("div", "empty", "No segment timing is available for this comparison."));
    return;
  }
  segments.forEach((segment) => {
    const control = element("button", "segment-cell");
    control.type = "button";
    const delta = Number(segment.delta_s);
    control.dataset.result = segment.delta_s == null ? "neutral" : delta > 0.005 ? "loss" : delta < -0.005 ? "gain" : "neutral";
    control.setAttribute("aria-pressed", "false");
    control.setAttribute("aria-label", `${segment.label}, ${segment.delta_s == null ? "delta unavailable" : formatSeconds(delta, true)}, ${formatPercent(segment.coverage)} coverage`);
    control.append(element("strong", "", segment.label), element("span", delta > 0.005 ? "error" : delta < -0.005 ? "good" : "muted", segment.delta_s == null ? "Unavailable" : formatSeconds(delta, true)), element("small", "availability-note", `${Math.round(segment.start_m)}–${Math.round(segment.end_m)} m · ${formatPercent(segment.coverage)}`));
    control.addEventListener("click", () => {
      rail.querySelectorAll(".segment-cell").forEach((item) => item.setAttribute("aria-pressed", String(item === control)));
      const axis = state.comparisonTrace?.axis?.values || [];
      const index = nearestIndex(axis, Number(segment.start_m));
      setCursor(index);
      const finding = (state.comparison.findings || []).find((item) => item.segment_id === segment.segment_id);
      if (finding) document.querySelector(`[data-finding-id="${CSS.escape(finding.finding_id)}"]`)?.focus();
    });
    rail.append(control);
  });
}

function renderCoachingFindings() {
  const container = byId("coachingFindings");
  clear(container);
  const findings = state.comparison?.findings || [];
  if (!findings.length) {
    container.append(element("div", "empty", "No prescriptive finding passed the current evidence threshold. The aligned traces remain available for inspection."));
    return;
  }
  findings.forEach((finding) => {
    const card = element("article", "finding-card");
    card.tabIndex = -1;
    card.dataset.findingId = finding.finding_id;
    card.dataset.positive = String(Boolean(finding.positive));
    const title = element("h3", "", `${finding.segment_label || "Segment"} · ${(finding.type || "finding").replaceAll("_", " ")}`);
    const metadata = element("div", "field-help", `${Math.round(Number(finding.confidence || 0) * 100)}% confidence · ${finding.repeatability == null ? "repeatability unavailable" : `${Math.round(Number(finding.repeatability) * 100)}% repeatability`} · rank ${finding.rank ?? "—"}`);
    const measured = element("p", "", finding.measured_loss_s == null ? "Measured local time loss unavailable." : `Measured: ${formatSeconds(finding.measured_loss_s)} through this segment.`);
    const action = element("p", "", `Try: ${finding.action || "No prescriptive action available."}`);
    card.append(title, metadata, measured, action);
    const facts = finding.facts || [];
    if (facts.length) {
      const list = element("ul", "finding-facts");
      facts.forEach((fact) => list.append(element("li", "", fact.label ? `${fact.label}: ${fact.candidate ?? "unavailable"}${fact.unit ? ` ${fact.unit}` : ""}` : JSON.stringify(fact))));
      card.append(list);
    }
    const zoom = button("Zoom to evidence", () => {
      const segment = (state.comparison.segments || []).find((item) => item.segment_id === finding.segment_id);
      if (segment) setCursor(nearestIndex(state.comparisonTrace?.axis?.values || [], Number(segment.start_m)));
      byId("comparisonTrace")?.focus?.();
    });
    card.append(zoom);
    container.append(card);
  });
}

function renderReviewFindings() {
  const container = byId("reviewFindings");
  clear(container);
  const findings = state.comparison?.findings || [];
  const belongsToSession = state.comparison?.candidate?.session_id === state.selectedSessionId;
  if (!findings.length || !belongsToSession) {
    container.append(element("div", "empty", "Open a compatible lap comparison in Lap Lab to see deterministic findings."));
    return;
  }
  findings.slice(0, 3).forEach((finding) => {
    const card = element("article", "finding-card");
    card.dataset.positive = String(Boolean(finding.positive));
    card.append(element("strong", "", `${finding.segment_label || "Segment"} · ${(finding.type || "finding").replaceAll("_", " ")}`), element("p", "", finding.action || "Action unavailable."), element("span", "field-help", `${Math.round(Number(finding.confidence || 0) * 100)}% confidence${finding.measured_loss_s == null ? " · time attribution unavailable" : ` · ${formatSeconds(finding.measured_loss_s)} measured`}`));
    container.append(card);
  });
}

function configurePlayback() {
  const axis = state.comparisonTrace?.axis?.values || [];
  const range = byId("playbackRange");
  range.max = String(Math.max(0, axis.length - 1));
  range.value = "0";
  range.disabled = axis.length < 2;
  byId("playbackToggle").disabled = axis.length < 2;
  byId("playbackPrevious").disabled = axis.length < 2;
  byId("playbackNext").disabled = axis.length < 2;
}

function nearestIndex(values, target) {
  if (!values.length) return 0;
  let low = 0;
  let high = values.length - 1;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (Number(values[middle]) < target) low = middle + 1;
    else high = middle;
  }
  if (low > 0 && Math.abs(Number(values[low - 1]) - target) < Math.abs(Number(values[low]) - target)) return low - 1;
  return low;
}

function setCursor(index) {
  const axis = state.comparisonTrace?.axis?.values || [];
  state.cursorIndex = Math.max(0, Math.min(Number(index) || 0, Math.max(0, axis.length - 1)));
  byId("playbackRange").value = String(state.cursorIndex);
  updateInstruments();
  drawComparisonTrace();
  drawComparisonMap();
}

function startPlayback() {
  if (!state.comparisonTrace?.axis?.values?.length) return;
  if (state.playbackTimer) { stopPlayback(); return; }
  const action = byId("playbackToggle");
  action.textContent = "Pause";
  action.setAttribute("aria-pressed", "true");
  state.playbackTimer = window.setInterval(() => {
    const speed = Number(byId("playbackSpeed").value || 1);
    const axis = state.comparisonTrace.axis.values;
    const step = Math.max(1, Math.round(speed * 2));
    const next = state.cursorIndex + step;
    if (next >= axis.length) { setCursor(axis.length - 1); stopPlayback(); }
    else setCursor(next);
  }, 80);
}

function stopPlayback() {
  if (state.playbackTimer) window.clearInterval(state.playbackTimer);
  state.playbackTimer = null;
  const action = HAS_DOM ? byId("playbackToggle") : null;
  if (action) {
    action.textContent = "Play";
    action.setAttribute("aria-pressed", "false");
  }
}

function seriesValue(side, field, index = state.cursorIndex) {
  const series = state.comparisonTrace?.[side]?.series?.[field];
  if (!series || series.availability === "unavailable") return { value: null, availability: "unavailable", unit: series?.unit || "" };
  const value = series.values?.[index];
  return { value: value === null || value === undefined || !Number.isFinite(Number(value)) ? null : Number(value), availability: series.availability || "observed", unit: series.unit || "" };
}

function updateInstruments() {
  const axis = state.comparisonTrace?.axis?.values || [];
  const distance = axis[state.cursorIndex];
  byId("playbackDistance").textContent = distance == null ? "0 m" : `${Math.round(Number(distance))} m`;
  const speed = seriesValue("candidate", "speed");
  const gear = seriesValue("candidate", "gear");
  const throttle = seriesValue("candidate", "throttle");
  const brake = seriesValue("candidate", "brake");
  const steering = seriesValue("candidate", "steering");
  const delta = seriesValue("candidate", "delta");
  byId("gaugeSpeed").textContent = speed.value == null ? "Unavailable" : `${(speed.value * 3.6).toFixed(1)} km/h`;
  byId("gaugeGear").textContent = gear.value == null ? "Unavailable" : String(Math.round(gear.value));
  byId("gaugeThrottle").value = throttle.value ?? 0;
  byId("gaugeThrottleText").textContent = throttle.value == null ? "Unavailable" : `${Math.round(throttle.value * 100)}%`;
  byId("gaugeBrake").value = brake.value ?? 0;
  byId("gaugeBrakeText").textContent = brake.value == null ? "Unavailable" : `${Math.round(brake.value * 100)}%`;
  byId("gaugeSteering").textContent = steering.value == null ? "Unavailable" : `${steering.value >= 0 ? "+" : ""}${steering.value.toFixed(3)}`;
  byId("gaugeDelta").textContent = delta.value == null ? "Unavailable" : formatSeconds(delta.value, true);
  byId("gaugeDelta").className = delta.value == null ? "" : delta.value > 0 ? "error" : delta.value < 0 ? "good" : "";
  renderCursorTable({ speed, gear, throttle, brake, steering, delta });
}

function renderCursorTable(values) {
  const body = byId("cursorDataTable");
  clear(body);
  Object.entries(values).forEach(([name, candidate]) => {
    const reference = seriesValue("reference", name);
    const row = element("tr");
    row.append(element("th", "", name), element("td", "", candidate.value == null ? "Unavailable" : String(candidate.value)), element("td", "", reference.value == null ? "Unavailable" : String(reference.value)), element("td", "", candidate.availability));
    body.append(row);
  });
}

function canvasContext(canvas) {
  if (!canvas) return null;
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  return context;
}

function canvasMessage(context, canvas, message) {
  context.fillStyle = "#aeb8c7";
  context.font = "16px Segoe UI, sans-serif";
  context.textAlign = "center";
  context.fillText(message, canvas.width / 2, canvas.height / 2);
  context.textAlign = "left";
}

function numericBounds(values) {
  const finite = values.filter((value) => value !== null && value !== undefined && Number.isFinite(Number(value))).map(Number);
  if (!finite.length) return null;
  let min = Math.min(...finite);
  let max = Math.max(...finite);
  if (min === max) { min -= 1; max += 1; }
  const padding = (max - min) * 0.08;
  return [min - padding, max + padding];
}

function drawComparisonTrace() {
  const canvas = byId("comparisonTrace");
  const context = canvasContext(canvas);
  if (!context) return;
  const trace = state.comparisonTrace;
  const axis = trace?.axis?.values || [];
  const candidate = trace?.candidate?.series?.[state.traceLayer];
  const reference = trace?.reference?.series?.[state.traceLayer];
  if (!axis.length || (!candidate || candidate.availability === "unavailable") && (!reference || reference.availability === "unavailable")) {
    canvasMessage(context, canvas, `${state.traceLayer.replaceAll("_", " ")} unavailable for this comparison.`);
    return;
  }
  const pad = { left: 62, right: 20, top: 24, bottom: 40 };
  const plotWidth = canvas.width - pad.left - pad.right;
  const plotHeight = canvas.height - pad.top - pad.bottom;
  const values = [...(candidate?.values || []), ...(reference?.values || [])];
  const bounds = numericBounds(values);
  if (!bounds) { canvasMessage(context, canvas, "No finite samples are available for this signal."); return; }
  const [minY, maxY] = bounds;
  const minX = Number(axis[0]);
  const maxX = Number(axis[axis.length - 1]);
  const x = (value) => pad.left + (Number(value) - minX) / (maxX - minX || 1) * plotWidth;
  const y = (value) => pad.top + (maxY - Number(value)) / (maxY - minY || 1) * plotHeight;
  context.strokeStyle = "#303b4d";
  context.fillStyle = "#aeb8c7";
  context.font = "13px Segoe UI, sans-serif";
  for (let line = 0; line <= 4; line += 1) {
    const py = pad.top + line / 4 * plotHeight;
    context.beginPath(); context.moveTo(pad.left, py); context.lineTo(canvas.width - pad.right, py); context.stroke();
    const value = maxY - line / 4 * (maxY - minY);
    context.fillText(value.toFixed(Math.abs(value) < 10 ? 2 : 0), 7, py + 4);
  }
  const draw = (series, color, width) => {
    if (!series?.values) return;
    context.strokeStyle = color;
    context.lineWidth = width;
    context.beginPath();
    let drawing = false;
    series.values.forEach((value, index) => {
      if (value === null || value === undefined || !Number.isFinite(Number(value))) { drawing = false; return; }
      const px = x(axis[index]);
      const py = y(value);
      if (!drawing) context.moveTo(px, py); else context.lineTo(px, py);
      drawing = true;
    });
    context.stroke();
  };
  draw(reference, "#f6c85f", 3);
  draw(candidate, "#4cc2ff", 3);
  const cursorX = x(axis[state.cursorIndex] ?? axis[0]);
  context.strokeStyle = "#ffffff";
  context.lineWidth = 2;
  context.beginPath(); context.moveTo(cursorX, pad.top); context.lineTo(cursorX, canvas.height - pad.bottom); context.stroke();
  context.fillStyle = "#4cc2ff"; context.fillText("Candidate", pad.left, 16);
  context.fillStyle = "#f6c85f"; context.fillText("Reference", pad.left + 90, 16);
  context.fillStyle = "#aeb8c7"; context.fillText(`Distance (m) · ${candidate?.unit || reference?.unit || "unit unavailable"}`, canvas.width / 2 - 80, canvas.height - 10);
}

function mapPoints(trace) {
  const axis = trace?.axis?.values || [];
  const xs = trace?.series?.world_x?.values || [];
  const zs = trace?.series?.world_z?.values || [];
  return axis.map((distance, index) => ({ distance: Number(distance), x: xs[index], z: zs[index] })).filter((point) => point.x !== null && point.z !== null && Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.z))).map((point) => ({ ...point, x: Number(point.x), z: Number(point.z) }));
}

function drawComparisonMap() {
  const canvas = byId("comparisonMap");
  const context = canvasContext(canvas);
  if (!context) return;
  const candidate = mapPoints(state.mapTraces.candidate);
  const reference = mapPoints(state.mapTraces.reference);
  const points = [...candidate, ...reference];
  if (points.length < 2) {
    canvasMessage(context, canvas, "World-position track map unavailable; aligned telemetry remains usable.");
    return;
  }
  const pad = 28;
  const xs = points.map((point) => point.x);
  const zs = points.map((point) => point.z);
  const minX = Math.min(...xs); const maxX = Math.max(...xs);
  const minZ = Math.min(...zs); const maxZ = Math.max(...zs);
  const scale = Math.min((canvas.width - pad * 2) / (maxX - minX || 1), (canvas.height - pad * 2) / (maxZ - minZ || 1));
  const offsetX = (canvas.width - (maxX - minX) * scale) / 2;
  const offsetZ = (canvas.height - (maxZ - minZ) * scale) / 2;
  const project = (point) => ({ x: offsetX + (point.x - minX) * scale, y: canvas.height - offsetZ - (point.z - minZ) * scale });
  const drawLine = (line, color, width) => {
    if (!line.length) return;
    context.strokeStyle = color; context.lineWidth = width; context.beginPath();
    line.forEach((point, index) => { const projected = project(point); if (index) context.lineTo(projected.x, projected.y); else context.moveTo(projected.x, projected.y); });
    context.stroke();
  };
  drawLine(reference, "#f6c85f", 6);
  drawLine(candidate, "#4cc2ff", 3);
  const distance = Number(state.comparisonTrace?.axis?.values?.[state.cursorIndex] ?? 0);
  [{ line: reference, color: "#f6c85f", label: "R" }, { line: candidate, color: "#4cc2ff", label: "C" }].forEach(({ line, color, label }) => {
    if (!line.length) return;
    const point = line[nearestIndex(line.map((item) => item.distance), distance)];
    const projected = project(point);
    context.fillStyle = color; context.beginPath(); context.arc(projected.x, projected.y, 9, 0, Math.PI * 2); context.fill();
    context.fillStyle = "#081018"; context.font = "800 11px Segoe UI"; context.textAlign = "center"; context.fillText(label, projected.x, projected.y + 4); context.textAlign = "left";
  });
  context.fillStyle = "#4cc2ff"; context.font = "14px Segoe UI"; context.fillText("Candidate", 14, 20);
  context.fillStyle = "#f6c85f"; context.fillText("Reference", 100, 20);
}

function selectTraceLayer(layer, trigger = null) {
  state.traceLayer = layer;
  document.querySelectorAll("[data-trace]").forEach((tab) => {
    const selected = tab.dataset.trace === layer;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  const panel = byId("trace-panel");
  if (trigger) panel.setAttribute("aria-labelledby", trigger.id);
  drawComparisonTrace();
}

function tracePointer(event) {
  const canvas = byId("comparisonTrace");
  const axis = state.comparisonTrace?.axis?.values || [];
  if (!axis.length) return;
  const rectangle = canvas.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (event.clientX - rectangle.left) / rectangle.width));
  setCursor(Math.round(ratio * (axis.length - 1)));
}

function renderFieldSummary(payload) {
  const container = byId("fieldSummary");
  clear(container);
  if (!payload || payload._error) {
    container.append(summaryMetric("Cars observed", "Unavailable"), summaryMetric("Lap rows", "Unavailable"), summaryMetric("Session quality", "Unavailable"), summaryMetric("Official classification", "Unavailable"));
    setNotice("fieldStatus", payload?._error || "Choose a saved session to inspect the field.", payload?._error ? "error" : "");
    return;
  }
  container.append(
    summaryMetric("Cars observed", String(payload.cars_observed ?? 0), `${payload.classification?.length ?? 0} identity rows shown`),
    summaryMetric("Lap rows", String(payload.lap_rows ?? 0), payload.truncated ? "Safety bound reached" : "Complete bounded response"),
    summaryMetric("Session quality", payload.session?.quality_score == null ? "Unavailable" : formatPercent(payload.session.quality_score), payload.session?.status || "status unavailable"),
    summaryMetric("Official classification", payload.classification_availability === "unavailable" ? "Unavailable" : payload.classification_availability, payload.classification_reason || "Availability supplied by saved telemetry"),
  );
  const warnings = payload.warnings || [];
  setNotice("fieldStatus", warnings.length ? warnings.join(" ") : `${payload.cars_observed ?? 0} cars loaded. Metrics retain availability and sample size.`, warnings.length ? "" : "success");
}

function renderFieldClassification(payload) {
  const body = byId("fieldClassificationRows");
  clear(body);
  const entries = payload?.classification || [];
  if (!entries.length) {
    const row = element("tr");
    const cell = element("td", "empty", payload?._error || payload?.classification_reason || "No field data loaded.");
    cell.colSpan = 8;
    row.append(cell);
    body.append(row);
    return;
  }
  entries.forEach((driver) => {
    const row = element("tr");
    const name = element("td");
    name.append(element("strong", "", `${driver.is_player ? "YOU · " : ""}${driver.display_name}`), element("span", "field-help", `Car ${driver.car_index + 1} · identity revision ${driver.identity_revision}`));
    row.append(name);
    const position = element("td", "", metricText(driver.position, (value) => `P${value}`)); position.title = availabilityTitle(driver.position); row.append(position);
    const last = element("td", "", metricText(driver.last_lap_ms, formatLapTime)); last.title = availabilityTitle(driver.last_lap_ms); row.append(last);
    const best = element("td", "", metricText(driver.best_lap_ms, formatLapTime)); best.title = availabilityTitle(driver.best_lap_ms); row.append(best);
    const medianPace = element("td", "", metricText(driver.median_clean_pace_ms, formatLapTime)); medianPace.title = availabilityTitle(driver.median_clean_pace_ms); row.append(medianPace);
    const compound = element("td", "", metricText(driver.compound)); compound.title = availabilityTitle(driver.compound); row.append(compound);
    const laps = element("td", "", metricText(driver.laps_recorded)); laps.title = availabilityTitle(driver.laps_recorded); row.append(laps);
    const action = element("td"); action.append(button("Lap Lab", () => openDriverBestLap(driver.car_id))); row.append(action);
    body.append(row);
  });
}

async function loadFieldView(view, { force = false } = {}) {
  if (!state.selectedSessionId) {
    setNotice("fieldStatus", "Choose a saved session before opening a field analysis view.");
    return null;
  }
  const key = `${state.selectedSessionId}:${view}`;
  if (!force && state.fieldCache.has(key)) {
    renderFieldView(view, state.fieldCache.get(key));
    return state.fieldCache.get(key);
  }
  const suffix = { classification: "field", pace: "field/pace", corners: "field/corners", positions: "field/positions", stints: "field/stints" }[view];
  if (!suffix) return null;
  setNotice("fieldStatus", `Loading ${view.replaceAll("_", " ")} with data-quality context…`);
  try {
    const payload = await api(`/sessions/${encodeURIComponent(state.selectedSessionId)}/${suffix}`);
    state.fieldCache.set(key, payload);
    renderFieldView(view, payload);
    if (view === "classification") renderFieldSummary(payload);
    else setNotice("fieldStatus", payload.reason || `${view[0].toUpperCase()}${view.slice(1)} loaded. Sample sizes remain visible in the view.`, payload.availability === "unavailable" ? "" : "success");
    return payload;
  } catch (error) {
    setNotice("fieldStatus", formatError(error), "error");
    renderFieldView(view, { _error: formatError(error) });
    return null;
  }
}

function renderFieldView(view, payload) {
  if (view === "classification") renderFieldClassification(payload);
  if (view === "pace") renderPaceMatrix(payload);
  if (view === "corners") renderCornerMatrix(payload);
  if (view === "positions") renderPositions(payload);
  if (view === "stints") renderStints(payload);
}

function matrixTable(headers) {
  const table = element("table", "matrix-table");
  const caption = element("caption", "sr-only", "Field comparison matrix; each header includes the comparable sample size.");
  const head = element("thead");
  const row = element("tr");
  headers.forEach((header) => row.append(element("th", "", header)));
  head.append(row);
  table.append(caption, head, element("tbody"));
  return table;
}

function resultForDelta(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "unavailable";
  if (Number(value) < -0.005) return "gain";
  if (Number(value) > 0.005) return "loss";
  return "neutral";
}

function renderPaceMatrix(payload) {
  const container = byId("fieldPaceMatrix");
  clear(container);
  if (!payload || payload._error || payload.availability === "unavailable" || !(payload.drivers || []).length) {
    container.append(element("div", "empty", payload?._error || payload?.reason || "Pace matrix unavailable because no comparable field laps were stored."));
    return;
  }
  const table = matrixTable(["Driver", ...(payload.lap_numbers || []).map((lap, index) => `Lap ${lap} · n=${payload.n_by_lap?.[index] ?? 0}`)]);
  const body = table.tBodies[0];
  payload.drivers.forEach((driver, rowIndex) => {
    const row = element("tr");
    row.append(element("td", "", `${driver.display_name} · ${formatPercent(payload.coverage_by_driver?.[driver.car_id])} coverage`));
    (payload.cells?.[rowIndex] || []).forEach((cell) => {
      const tableCell = element("td", "matrix-cell");
      tableCell.dataset.result = cell.included ? resultForDelta(cell.delta_to_lap_median_s) : "unavailable";
      const label = cell.included ? `${formatSeconds(cell.delta_to_lap_median_s, true)} · ${formatLapTime(Number(cell.lap_time_s) * 1000)}` : "Unavailable";
      if (cell.lap_id) {
        const control = button(label, () => openLap(cell.lap_id), "");
        control.title = cell.included ? `Open ${driver.display_name}, lap ${payload.lap_numbers[row.children.length - 1]} in Lap Lab` : `${cell.reason || "Excluded context"}. Open trace in Lap Lab.`;
        tableCell.append(control);
      } else tableCell.textContent = label;
      if (cell.reason) tableCell.title = cell.reason;
      row.append(tableCell);
    });
    body.append(row);
  });
  container.append(table);
}

function renderCornerMatrix(payload) {
  const container = byId("fieldCornerMatrix");
  clear(container);
  if (!payload || payload._error || payload.availability === "unavailable" || !(payload.segments || []).length) {
    container.append(element("div", "empty", payload?._error || payload?.reason || "Corner matrix unavailable. Persisted absolute segment times are required; relative deltas alone are not promoted to field ranks."));
    return;
  }
  const table = matrixTable(["Driver", ...payload.segments.map((segment, index) => `${segment.label} · n=${payload.n_by_segment?.[index] ?? 0}`)]);
  const body = table.tBodies[0];
  payload.drivers.forEach((driver, rowIndex) => {
    const row = element("tr");
    row.append(element("td", "", `${driver.display_name} · ${formatPercent(payload.coverage_by_driver?.[driver.car_id])} coverage`));
    payload.segments.forEach((segment, columnIndex) => {
      const valid = Boolean(payload.valid_mask?.[rowIndex]?.[columnIndex]);
      const delta = payload.delta_to_field_median_s?.[rowIndex]?.[columnIndex];
      const rank = payload.rank?.[rowIndex]?.[columnIndex];
      const samples = payload.sample_count?.[rowIndex]?.[columnIndex] ?? 0;
      const cell = element("td", "matrix-cell", valid ? `${formatSeconds(delta, true)} · P${rank ?? "—"}` : "Unavailable");
      cell.dataset.result = valid ? resultForDelta(delta) : "unavailable";
      cell.title = valid ? `${segment.label}: ${formatSeconds(payload.median_time_s?.[rowIndex]?.[columnIndex])} median over ${samples} stored sample${samples === 1 ? "" : "s"}; field n=${payload.n_by_segment?.[columnIndex] ?? 0}` : `${segment.label}: insufficient absolute segment-time evidence.`;
      row.append(cell);
    });
    body.append(row);
  });
  container.append(table);
}

function renderPositions(payload) {
  const canvas = byId("fieldPositionChart");
  const context = canvasContext(canvas);
  const description = byId("fieldPositionDescription");
  if (!context) return;
  const usable = (payload?.series || []).filter((series) => series.availability !== "unavailable" && (series.points || []).length);
  if (!payload || payload._error || payload.availability === "unavailable" || !usable.length) {
    const reason = payload?._error || payload?.reason || "Saved position history is unavailable; zero is not substituted.";
    canvasMessage(context, canvas, reason);
    description.textContent = reason;
    return;
  }
  const pad = { left: 52, right: 18, top: 25, bottom: 38 };
  const points = usable.flatMap((series) => series.points || []);
  const maxLap = Math.max(...points.map((point) => Number(point.lap_number)), 1);
  const maxPosition = Math.max(...points.map((point) => Number(point.position)), usable.length, 2);
  const x = (lap) => pad.left + Number(lap) / maxLap * (canvas.width - pad.left - pad.right);
  const y = (position) => pad.top + (Number(position) - 1) / (maxPosition - 1 || 1) * (canvas.height - pad.top - pad.bottom);
  context.strokeStyle = "#303b4d"; context.fillStyle = "#aeb8c7"; context.font = "13px Segoe UI";
  for (let position = 1; position <= maxPosition; position += Math.max(1, Math.ceil(maxPosition / 8))) {
    const py = y(position); context.beginPath(); context.moveTo(pad.left, py); context.lineTo(canvas.width - pad.right, py); context.stroke(); context.fillText(`P${position}`, 10, py + 4);
  }
  const colors = ["#4cc2ff", "#f6c85f", "#58d68d", "#ff7777", "#b39ddb", "#80cbc4", "#ffb74d", "#90a4ae"];
  usable.forEach((series, index) => {
    context.strokeStyle = colors[index % colors.length]; context.lineWidth = series.is_player ? 5 : 2; context.beginPath();
    series.points.forEach((point, pointIndex) => { if (pointIndex) context.lineTo(x(point.lap_number), y(point.position)); else context.moveTo(x(point.lap_number), y(point.position)); });
    context.stroke();
  });
  description.textContent = `${usable.length} of ${payload.cars_observed ?? usable.length} observed cars have stored position samples. Position 1 is at the top; context events remain available in the response.`;
}

function renderStints(payload) {
  const container = byId("fieldStints");
  clear(container);
  const drivers = payload?.drivers || [];
  if (!payload || payload._error || payload.availability === "unavailable" || !drivers.length) {
    container.append(element("div", "empty", payload?._error || payload?.reason || "Stint sequences are unavailable for this session."));
    return;
  }
  drivers.forEach((driver) => {
    const card = element("article", "stint-card");
    card.append(element("h3", "", `${driver.is_player ? "YOU · " : ""}${driver.display_name}`), element("span", "field-help", `${driver.n ?? 0} stored laps · ${driver.availability}`));
    if (!(driver.stints || []).length) card.append(element("div", "empty", driver.reason || "No observed compound sequence."));
    (driver.stints || []).forEach((stint) => {
      const row = element("div", "stint-row");
      const identity = element("div");
      identity.append(element("strong", "", `Stint ${stint.ordinal} · ${stint.compound}`), element("span", "availability-note", `Laps ${stint.start_lap}–${stint.end_lap} · ${stint.clean_lap_count}/${stint.lap_count} clean`));
      const pace = element("div", "", metricText(stint.median_clean_pace_s, (value) => formatLapTime(Number(value) * 1000)));
      pace.title = availabilityTitle(stint.median_clean_pace_s);
      row.append(identity, pace);
      card.append(row);
    });
    container.append(card);
  });
}

async function openDriverBestLap(carId) {
  if (!state.selectedSessionId) return;
  setNotice("fieldStatus", "Loading this driver's saved laps and strengths…");
  try {
    const payload = await api(`/sessions/${encodeURIComponent(state.selectedSessionId)}/field/drivers/${encodeURIComponent(carId)}`);
    const best = (payload.laps || []).filter((lap) => lap.valid && lap.lap_time_ms).sort((a, b) => Number(a.lap_time_ms) - Number(b.lap_time_ms))[0];
    if (!best) {
      const reason = payload.strengths?.reason || "This driver has no valid recorded lap suitable for Lap Lab.";
      setNotice("fieldStatus", reason);
      return;
    }
    const known = state.laps.some((lap) => lap.id === best.lap_id);
    if (!known) state.laps.push({ ...best, id: best.lap_id, display_name: payload.driver.display_name, session_car_id: payload.driver.car_id, car_index: payload.driver.car_index, coverage_ratio: best.coverage, tyre_compound: best.compound });
    await openLap(best.lap_id);
  } catch (error) {
    setNotice("fieldStatus", formatError(error), "error");
  }
}

function selectFieldView(view, trigger = null) {
  state.fieldView = view;
  document.querySelectorAll("[data-field-view]").forEach((tab) => {
    const selected = tab.dataset.fieldView === view;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  document.querySelectorAll(".field-panel").forEach((panel) => {
    const selected = panel.id === `field-panel-${view}`;
    panel.hidden = !selected;
  });
  if (trigger) byId(`field-panel-${view}`)?.setAttribute("aria-labelledby", trigger.id);
  loadFieldView(view);
}

function bindRovingTabs(selector, callback) {
  const tabs = [...document.querySelectorAll(selector)];
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => callback(tab, index));
    tab.addEventListener("keydown", (event) => {
      let next = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next === null) return;
      event.preventDefault();
      tabs[next].focus();
      tabs[next].click();
    });
  });
}

async function activatePage(page) {
  if (page !== "lap-lab") stopPlayback();
  if (["library", "session-review", "lap-lab", "field"].includes(page) && !state.sessions.length) await loadSessions({ quiet: page !== "library" });
  if (page === "session-review") renderSessionReview();
  if (page === "lap-lab") {
    populateLapSelectors();
    if (state.candidateLapId && !state.references.length) loadReferences(state.candidateLapId);
    renderComparison();
  }
  if (page === "field") {
    const cached = state.fieldCache.get(`${state.selectedSessionId}:classification`);
    if (cached) { renderFieldSummary(cached); renderFieldClassification(cached); }
    if (state.selectedSessionId) loadFieldView(state.fieldView);
  }
}

function bindEvents() {
  byId("libraryFilters")?.addEventListener("submit", (event) => { event.preventDefault(); state.nextCursor = null; loadSessions(); });
  byId("libraryRefresh")?.addEventListener("click", () => loadSessions());
  byId("libraryLoadMore")?.addEventListener("click", () => loadSessions({ append: true }));
  byId("reviewSessionSelect")?.addEventListener("change", (event) => selectSession(event.target.value));
  byId("fieldSessionSelect")?.addEventListener("change", (event) => selectSession(event.target.value));
  byId("reviewDriverFilter")?.addEventListener("change", renderReviewLaps);
  byId("reviewReprocess")?.addEventListener("click", requestReprocess);
  byId("reviewOpenField")?.addEventListener("click", () => navigate("field"));
  byId("reviewOpenLibrary")?.addEventListener("click", () => navigate("library"));
  byId("candidateLapSelect")?.addEventListener("change", (event) => {
    state.candidateLapId = event.target.value;
    state.references = [];
    state.comparison = null;
    state.comparisonTrace = null;
    state.mapTraces = { candidate: null, reference: null };
    updateCandidateMeta();
    renderComparison();
    renderReviewLaps();
    if (state.candidateLapId) loadReferences(state.candidateLapId);
    else replaceOptions(byId("referenceLapSelect"), [], "Choose candidate first");
  });
  byId("referenceLapSelect")?.addEventListener("change", updateReferenceMeta);
  byId("createComparison")?.addEventListener("click", createComparison);
  byId("playbackToggle")?.addEventListener("click", startPlayback);
  byId("playbackPrevious")?.addEventListener("click", () => setCursor(state.cursorIndex - 20));
  byId("playbackNext")?.addEventListener("click", () => setCursor(state.cursorIndex + 20));
  byId("playbackRange")?.addEventListener("input", (event) => setCursor(Number(event.target.value)));
  byId("comparisonTrace")?.addEventListener("pointerdown", tracePointer);
  byId("fieldRefresh")?.addEventListener("click", () => loadFieldView(state.fieldView, { force: true }));
  bindRovingTabs("[data-trace]", (tab) => selectTraceLayer(tab.dataset.trace, tab));
  bindRovingTabs("[data-field-view]", (tab) => selectFieldView(tab.dataset.fieldView, tab));
  window.addEventListener("pitwall:pagechange", (event) => activatePage(event.detail?.page || "live"));
  window.addEventListener("beforeunload", stopPlayback);
}

function initialize() {
  if (!byId("library")) return;
  bindEvents();
  configurePlayback();
  renderComparison();
  const page = location.hash.slice(1) || "live";
  activatePage(page);
  window.pitwallWorkspaces = {
    loadSessions,
    selectSession,
    openLap,
    createComparison,
    loadFieldView,
  };
}

if (HAS_DOM) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
  else initialize();
}

export { createComparison, loadFieldView, loadReferences, loadSessions, openLap, selectSession };
