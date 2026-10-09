// Session Analysis view: post-session charts for one saved session.
// Data: GET /api/v1/sessions/{id}/analysis (schema version 2). Chart maths
// lives in session-analysis-model.mjs; this file draws every chart at the
// width of its own container (SVG units are CSS pixels, so text never
// shrinks), wires the controls, keeps one Tab stop per chart and keeps
// requests from overwriting each other. It talks to Library and Lap Lab only
// through window events: pitwall:analyze-session, pitwall:open-lap,
// pitwall:pagechange and pitwall:session-deleted.
import * as M from "./session-analysis-model.mjs";

const HAS_DOM = typeof window !== "undefined" && typeof document !== "undefined";
const API_ROOT = "/api/v1";
const SVG_NS = "http://www.w3.org/2000/svg";
const MAX_FOCUS = 6;
const STORAGE_SESSION = "pitwall.analysis.session";
const CHOOSE_MESSAGE = "Choose a saved session, or press Analyze session in Library or Session Review.";
const DEFAULT_TITLE = "Session analysis";
const DEFAULT_SUBTITLE = "Race pace, race trace, positions, tyre strategy, lap times and the timelapse for one saved session.";
const DEFAULT_PACE_BASIS = "Pace uses representative laps only; laps over 107% of the driver's median are hidden unless you turn that off.";
const STRATEGY_HEADING = "Stints in finishing order";
const CHARTS = ["analysisPaceChart", "analysisTraceChart", "analysisPositionsChart", "analysisStrategyChart", "analysisLapTimesChart", "analysisHeatmapChart", "analysisLapseChart", "analysisFastestChart"];
const TABLES = ["analysisPaceTable", "analysisTraceTable", "analysisPositionsTable", "analysisStrategyTable", "analysisLapTimesTable", "analysisHeatmapTable", "analysisLapseTable", "analysisFastestTable"];
const NOTES = ["analysisPaceThin", "analysisTraceNote"];
const MARK_SELECTOR = "[data-key]";
let clipCounter = 0;

const state = {
  sessions: [],
  sessionsLoaded: false,
  sessionsRequest: 0,
  sessionId: "",
  analysis: null,
  request: 0,
  focus: [],
  hideOutliers: true,
  reference: "leader",
  width: 0,
  resizeFrame: 0,
  keyboard: false,
  restoring: false,
  heatDriver: null,
  tip: null,
  charts: new Map(),
  lapse: { t: 1, playing: false, speed: 1, frames: [], raf: 0, timer: 0, last: 0, view: null },
};

const byId = (id) => document.getElementById(id);
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = String(text);
  return node;
}

function svgElement(tag, attrs = {}, text = "") {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false || value === "") continue;
    node.setAttribute(name, String(value));
  }
  if (text !== "") node.textContent = String(text);
  return node;
}

// SVG units are CSS pixels: the chart is drawn at its container's width, so
// 11 px text stays 11 px on a phone. Interactive charts are groups (their
// marks are focusable); a chart without focusable marks is an image.
function svgRoot(width, height, label, { interactive = false, focusable = false } = {}) {
  return svgElement("svg", {
    width, height, viewBox: `0 0 ${width} ${height}`, class: "sa-svg",
    role: interactive ? "group" : "img", "aria-label": label,
    tabindex: focusable ? 0 : null, "data-key": focusable ? "chart" : null,
  });
}

function widthOf(container, fallback = 640) {
  const width = Math.floor(container?.clientWidth || 0);
  return width >= 200 ? width : fallback;
}

function emptyNote(message) {
  return element("div", "empty", message);
}

function setStatus(message, tone = "") {
  const node = byId("analysisStatus");
  if (!node) return;
  node.textContent = message;
  if (tone) node.dataset.tone = tone;
  else delete node.dataset.tone;
}

function errorText(error) {
  return error instanceof Error ? error.message : String(error);
}

async function api(path) {
  let response;
  try {
    response = await fetch(`${API_ROOT}${path}`, { headers: { Accept: "application/json" } });
  } catch (error) {
    throw Object.assign(new Error(`The local API is unavailable: ${errorText(error)}`), { status: 0 });
  }
  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
  if (!response.ok) {
    const detail = payload?.detail;
    const message = detail?.message || (typeof detail === "string" ? detail : null) || `${response.status} ${response.statusText}`;
    throw Object.assign(new Error(message), { status: response.status });
  }
  return payload;
}

function remember(sessionId) {
  try {
    if (sessionId) localStorage.setItem(STORAGE_SESSION, sessionId);
    else localStorage.removeItem(STORAGE_SESSION);
  } catch { /* storage may be unavailable */ }
}

function recall() {
  try { return localStorage.getItem(STORAGE_SESSION) || ""; } catch { return ""; }
}

function forget(sessionId) {
  try { if (sessionId && localStorage.getItem(STORAGE_SESSION) === sessionId) localStorage.removeItem(STORAGE_SESSION); } catch { /* storage may be unavailable */ }
}

function codeOf(carIndex) {
  return M.codeOf(state.analysis, carIndex);
}

function isRace() {
  return Boolean(state.analysis?.session?.is_race);
}

// The race trace, positions and timelapse need a race order: a race with two
// or more recorded cars.
function hasOrder() {
  return M.hasRaceOrder(state.analysis);
}

// ------------------------------------------------------------- tooltips

function tipOf(container) {
  let tip = container.querySelector(":scope > .sa-tip");
  if (!tip) {
    tip = element("div", "sa-tip");
    tip.hidden = true;
    container.append(tip);
  }
  return tip;
}

// The part of the screen where a chart's tooltip can be seen: the viewport
// cut down by every scrolling or clipping ancestor. In the app the analysis
// page scrolls under the top bar and tabs, so its top edge is well below the
// viewport's.
function visibleBox(container) {
  const box = {
    left: 0, top: 0,
    right: document.documentElement.clientWidth || window.innerWidth || 0,
    bottom: document.documentElement.clientHeight || window.innerHeight || 0,
  };
  for (let node = container.parentElement; node && node !== document.body && node !== document.documentElement; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (!/auto|scroll|hidden|clip/.test(`${style.overflowX} ${style.overflowY}`)) continue;
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    box.left = Math.max(box.left, rect.left);
    box.top = Math.max(box.top, rect.top);
    box.right = Math.min(box.right, rect.right);
    box.bottom = Math.min(box.bottom, rect.bottom);
  }
  return box;
}

// Tooltips sit at their mark inside the chart's non-scrolling wrapper and
// stay inside the visible part of the page. A tap leaves one open until the
// next tap elsewhere; only a keyboard focus change is announced.
function showTip(container, anchor, lines, { key = "", sticky = false, live = false, lapId = "", openLabel = "" } = {}) {
  const tip = tipOf(container);
  const nodes = lines.map((line, i) => (typeof line === "string" ? element(i === 0 ? "strong" : "div", "", line) : line));
  if (lapId) {
    const open = element("button", "button ghost sa-tip-open", "Open in Lap Lab");
    open.type = "button";
    open.dataset.lapId = lapId;
    if (openLabel) open.setAttribute("aria-label", openLabel);
    nodes.push(open);
  }
  tip.replaceChildren(...nodes);
  tip.setAttribute("aria-live", live ? "polite" : "off");
  tip.classList.toggle("sa-tip-sticky", Boolean(sticky));
  tip.hidden = false;
  state.tip = { container, key, sticky };
  placeTip(tip, container, anchor);
}

// Above the mark where it fits in the visible box, else below it, else on
// the roomier side; always clamped inside the visible box.
function placeTip(tip, container, anchor) {
  // Measured at the container's left edge, so its width never depends on
  // where it sat before, then held at that width: an auto-width reading moved
  // towards the right edge wraps again and grows taller than the space it
  // was placed in (wider system fonts made it cover its own mark).
  tip.style.left = "0px";
  tip.style.top = "0px";
  tip.style.width = "";
  const box = container.getBoundingClientRect();
  const rect = typeof anchor?.getBoundingClientRect === "function" ? anchor.getBoundingClientRect() : anchor || box;
  const clip = visibleBox(container);
  const width = tip.offsetWidth || 220;
  tip.style.width = `${width}px`;
  const height = tip.offsetHeight || 48;
  const left = clamp((rect.left + rect.right) / 2 - width / 2, clip.left + 4, Math.max(clip.left + 4, clip.right - width - 4));
  const above = rect.top - height - 8;
  const below = rect.bottom + 8;
  const fits = (top) => top >= clip.top + 4 && top + height <= clip.bottom - 4;
  let top = above;
  if (!fits(above)) top = fits(below) || rect.top - clip.top < clip.bottom - rect.bottom ? below : above;
  top = clamp(top, clip.top + 4, Math.max(clip.top + 4, clip.bottom - height - 4));
  tip.style.left = `${Math.round(left - box.left)}px`;
  tip.style.top = `${Math.round(top - box.top)}px`;
}

// Keeps a reading at its mark while the heatmap scrolls sideways under it,
// and closes it once the mark has left the scroller's visible part.
function followTip(container, scroller) {
  const tip = state.tip;
  if (!tip || tip.container !== container || !tip.key) return;
  const mark = container.querySelector(`[data-key="${tip.key}"]`);
  const node = container.querySelector(":scope > .sa-tip");
  if (!mark || !node || node.hidden) return;
  const view = scroller.getBoundingClientRect();
  const rect = mark.getBoundingClientRect();
  if (rect.right <= view.left || rect.left >= view.right) { hideTip(container, true); return; }
  placeTip(node, container, markAnchor(mark));
}

// What a mark's reading points at: its shape, not the labels grouped with it
// (a race-pace row also holds the driver code and the lap count).
function markAnchor(mark) {
  return mark.querySelector?.("[data-anchor]") || mark;
}

function hideTip(container, force = false) {
  if (!container) return;
  if (state.tip?.container === container) {
    if (state.tip.sticky && !force) return;
    state.tip = null;
  }
  const tip = container.querySelector(":scope > .sa-tip");
  if (tip) tip.hidden = true;
}

function hideAllTips() {
  for (const id of CHARTS) hideTip(byId(id), true);
  state.tip = null;
}

// ------------------------------------------------------------- chart focus

// Replaces a chart's content, keeping keyboard focus on the same mark.
function mount(container, ...nodes) {
  const active = document.activeElement;
  const key = active && active !== document.body && container.contains(active) ? active.getAttribute?.("data-key") : null;
  hideTip(container, true);
  container.replaceChildren(...nodes);
  if (key) {
    const target = container.querySelector(`[data-key="${key}"]`);
    if (target) {
      makeCurrent(container, target);
      state.restoring = true;
      try { target.focus({ preventScroll: true }); } finally { state.restoring = false; }
    }
  }
}

function marksOf(container) {
  return [...container.querySelectorAll(`svg ${MARK_SELECTOR}`)].filter((node) => node.getAttribute("data-key") !== "chart");
}

// One Tab stop per chart: every mark is tabindex -1 except the current one.
function makeCurrent(container, mark) {
  if (!mark || mark.getAttribute("data-key") === "chart") return;
  for (const node of marksOf(container)) node.setAttribute("tabindex", node === mark ? "0" : "-1");
  const config = state.charts.get(container.id);
  if (config) config.current = mark.getAttribute("data-key");
}

function rovingStart(container, previous) {
  const marks = marksOf(container);
  const target = (previous && marks.find((m) => m.getAttribute("data-key") === previous)) || marks[0];
  if (target) makeCurrent(container, target);
}

function neighbour(marks, mark, key) {
  const row = Number(mark.getAttribute("data-row") || 0);
  const col = Number(mark.getAttribute("data-col") || 0);
  const pos = (m) => ({ r: Number(m.getAttribute("data-row") || 0), c: Number(m.getAttribute("data-col") || 0) });
  const sameRow = marks.filter((m) => pos(m).r === row).sort((a, b) => pos(a).c - pos(b).c);
  if (key === "Home") return sameRow[0];
  if (key === "End") return sameRow[sameRow.length - 1];
  if (key === "ArrowRight") return sameRow.find((m) => pos(m).c > col);
  if (key === "ArrowLeft") return [...sameRow].reverse().find((m) => pos(m).c < col);
  const rows = [...new Set(marks.map((m) => pos(m).r))].sort((a, b) => a - b);
  const nextRow = key === "ArrowDown" ? rows.find((r) => r > row) : [...rows].reverse().find((r) => r < row);
  if (nextRow === undefined) return null;
  return marks.filter((m) => pos(m).r === nextRow).sort((a, b) => Math.abs(pos(a).c - col) - Math.abs(pos(b).c - col))[0];
}

function markTip(container, mark, { sticky = false, live = false } = {}) {
  const lapId = mark.getAttribute("data-lap-id") || "";
  showTip(container, markAnchor(mark), [mark.getAttribute("data-tip") || mark.getAttribute("aria-label") || ""], {
    key: mark.getAttribute("data-key"), sticky, live, lapId: sticky && lapId ? lapId : "",
    openLabel: lapId ? `Open ${mark.getAttribute("data-tip")} in Lap Lab` : "",
  });
}

function crosshairTip(container, config, lap, { sticky = false, live = false } = {}) {
  const point = config.laps.find((l) => l.lap === lap) || config.laps[0];
  if (!point) return;
  config.lap = point.lap;
  config.line.setAttribute("x1", point.x);
  config.line.setAttribute("x2", point.x);
  config.line.setAttribute("visibility", "visible");
  const box = config.svg.getBoundingClientRect();
  const k = box.width ? box.width / Number(config.svg.getAttribute("width")) : 1;
  const x = box.left + point.x * k;
  // The reading hangs from the top of the plot, or from the top of the
  // visible area when a tall chart's top has scrolled out of view.
  const plotTop = box.top + config.plot.top * k;
  const top = clamp(visibleBox(container).top, plotTop, Math.max(plotTop, box.top + config.plot.bottom * k));
  const rows = config.rows(point.lap).map((item) => {
    const row = element("div", "sa-tip-row");
    const key = element("span", "sa-key-line");
    key.style.background = item.colour || M.CONTEXT_STROKE;
    row.append(key, element("b", "", item.value), element("span", "", item.label));
    return row;
  });
  showTip(container, { left: x, right: x, top, bottom: top }, [element("strong", "", `Lap ${point.lap}`), ...(rows.length ? rows : [element("div", "", "Add a focus driver to read values.")])], { key: `lap-${point.lap}`, sticky, live });
}

function lapFromPointer(config, event) {
  const box = config.svg.getBoundingClientRect();
  const k = box.width ? box.width / Number(config.svg.getAttribute("width")) : 1;
  const px = (event.clientX - box.left) / k;
  let nearest = config.laps[0];
  for (const lap of config.laps) if (Math.abs(lap.x - px) < Math.abs(nearest.x - px)) nearest = lap;
  return nearest?.lap;
}

function hideCrosshair(container) {
  const config = state.charts.get(container.id);
  if (config?.kind === "crosshair") config.line?.setAttribute("visibility", "hidden");
}

// Pointer, touch and keyboard handlers, bound once per chart container and
// reading the chart's current configuration. Nothing acts on pointerdown:
// a mouse hover shows a reading, a tap or click pins it, and a lap opens in
// Lap Lab only on click, Enter or Space.
function bindChart(container, config) {
  const previous = state.charts.get(container.id);
  state.charts.set(container.id, { ...config, current: previous?.current || null });
  if (config.kind === "marks") rovingStart(container, previous?.current);
  if (container.dataset.chartBound) return;
  container.dataset.chartBound = "1";
  const current = () => state.charts.get(container.id);
  const markAt = (target) => {
    const mark = target?.closest?.(MARK_SELECTOR);
    return mark && container.contains(mark) && mark.getAttribute("data-key") !== "chart" ? mark : null;
  };
  const inTip = (target) => Boolean(target?.closest?.(".sa-tip"));
  // Remembers which reading was showing before this press (a record, not an
  // action): focus on press must not turn a first tap into an open.
  container.addEventListener("pointerdown", () => {
    const config = current();
    if (config) config.tipBefore = state.tip?.container === container ? state.tip.key : null;
  });
  container.addEventListener("pointermove", (event) => {
    if (event.pointerType === "touch" || inTip(event.target)) return;
    const config = current();
    if (!config) return;
    if (config.kind === "crosshair") {
      if (event.target?.closest?.(".sa-hit")) crosshairTip(container, config, lapFromPointer(config, event));
      return;
    }
    const mark = markAt(event.target);
    if (mark) {
      if (state.tip?.container !== container || state.tip.key !== mark.getAttribute("data-key")) markTip(container, mark);
    } else if (!state.tip?.sticky) hideTip(container);
  });
  container.addEventListener("pointerleave", (event) => {
    if (event.pointerType === "touch" || state.tip?.sticky) return;
    hideTip(container);
    hideCrosshair(container);
  });
  container.addEventListener("click", (event) => {
    const open = event.target?.closest?.(".sa-tip-open");
    if (open) { event.preventDefault(); openLapInLapLab(open.dataset.lapId); return; }
    if (inTip(event.target)) return;
    const config = current();
    if (!config) return;
    const before = config.tipBefore === undefined ? (state.tip?.container === container ? state.tip.key : null) : config.tipBefore;
    config.tipBefore = undefined;
    if (config.kind === "crosshair") {
      if (event.target?.closest?.(".sa-hit")) crosshairTip(container, config, lapFromPointer(config, event), { sticky: true });
      else { hideTip(container, true); hideCrosshair(container); }
      return;
    }
    const mark = markAt(event.target);
    if (!mark) { hideTip(container, true); return; }
    makeCurrent(container, mark);
    const key = mark.getAttribute("data-key");
    const lapId = mark.getAttribute("data-lap-id");
    // A mark that was already showing its reading before this press (a
    // hover or an earlier tap) opens; otherwise the first tap shows it.
    if (lapId && before === key) { openLapInLapLab(lapId); return; }
    markTip(container, mark, { sticky: true });
  });
  container.addEventListener("keydown", (event) => {
    const config = current();
    if (!config) return;
    if (event.key === "Escape") { hideTip(container, true); hideCrosshair(container); return; }
    if (config.kind === "crosshair") {
      if (event.target !== config.svg) return;
      const laps = config.laps;
      const at = Math.max(0, laps.findIndex((l) => l.lap === config.lap));
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
      let next = null;
      if (step) next = laps[clamp(at + step, 0, laps.length - 1)];
      if (event.key === "Home") next = laps[0];
      if (event.key === "End") next = laps[laps.length - 1];
      if (event.key === "Enter" || event.key === " ") next = laps[at];
      if (!next) return;
      event.preventDefault();
      crosshairTip(container, config, next.lap, { live: true });
      return;
    }
    const mark = markAt(event.target);
    if (!mark) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      const lapId = mark.getAttribute("data-lap-id");
      if (lapId) openLapInLapLab(lapId);
      else markTip(container, mark, { live: true });
      return;
    }
    const next = neighbour(marksOf(container), mark, event.key);
    if (!["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (next) { makeCurrent(container, next); next.focus(); }
  });
  container.addEventListener("focusin", (event) => {
    const config = current();
    if (!config || state.restoring) return;
    if (config.kind === "crosshair") {
      if (event.target === config.svg) crosshairTip(container, config, config.lap || config.laps[0]?.lap, { live: state.keyboard });
      return;
    }
    const mark = markAt(event.target);
    if (!mark) return;
    makeCurrent(container, mark);
    if (state.tip?.container === container && state.tip.key === mark.getAttribute("data-key") && state.tip.sticky) return;
    markTip(container, mark, { live: state.keyboard });
  });
  container.addEventListener("focusout", (event) => {
    if (event.relatedTarget && container.contains(event.relatedTarget)) return;
    if (state.tip?.container === container && state.tip.sticky) return;
    hideTip(container);
    hideCrosshair(container);
  });
}

// ------------------------------------------------------------- tables

function tableCell(cell) {
  const td = element("td");
  if (cell && typeof cell === "object") {
    const button = element("button", "button ghost sa-open", cell.text);
    button.type = "button";
    button.dataset.lapId = cell.lapId;
    if (cell.label) button.setAttribute("aria-label", cell.label);
    button.addEventListener("click", () => openLapInLapLab(cell.lapId));
    td.append(button);
  } else td.textContent = cell ?? "—";
  return td;
}

function dataTable(table, { before = [] } = {}) {
  const details = element("details", "sa-table-details");
  const summary = element("summary", "", "Data table");
  summary.append(element("span", "sr-only", `: ${table.caption}`));
  const wrap = element("div", "table-scroll");
  const node = element("table", "table workspace-table sa-table");
  const head = element("thead");
  const headRow = element("tr");
  table.headers.forEach((h) => { const th = element("th", "", h); th.scope = "col"; headRow.append(th); });
  head.append(headRow);
  const body = element("tbody");
  table.rows.forEach((cells) => {
    const tr = element("tr");
    cells.forEach((cell, i) => {
      if (i === 0) { const th = element("th", "", cell); th.scope = "row"; tr.append(th); } else tr.append(tableCell(cell));
    });
    body.append(tr);
  });
  node.append(element("caption", "sr-only", table.caption), head, body);
  wrap.append(node);
  details.append(summary, ...before, wrap);
  return details;
}

// Keeps an open table open across re-renders, and focus on its controls.
function placeTable(id, table, options = {}) {
  const slot = byId(id);
  if (!slot) return;
  if (!table || !table.rows.length) { slot.replaceChildren(); return; }
  const previous = slot.querySelector(":scope > details");
  const wasOpen = Boolean(previous?.open);
  const active = document.activeElement;
  const focusId = active && slot.contains(active) ? active.id || (active.tagName === "SUMMARY" ? "summary" : "") : "";
  const details = dataTable(table, options);
  details.open = wasOpen;
  slot.replaceChildren(details);
  if (focusId === "summary") details.querySelector("summary")?.focus({ preventScroll: true });
  else if (focusId) byId(focusId)?.focus({ preventScroll: true });
}

// ------------------------------------------------------------- drawing

function clipPath(svg, plot) {
  const id = `saClip${++clipCounter}`;
  const defs = svgElement("defs");
  const clip = svgElement("clipPath", { id });
  clip.append(svgElement("rect", { x: plot.left, y: plot.top - 2, width: Math.max(1, plot.right - plot.left), height: Math.max(1, plot.bottom - plot.top + 4) }));
  defs.append(clip);
  svg.append(defs);
  return `url(#${id})`;
}

// Bands shade the plot; their SC / VSC / RED labels sit in the margin above
// it, clear of the lines, and give way rather than overlap each other.
function drawBands(svg, bands, plot) {
  for (const band of bands || []) {
    svg.append(svgElement("rect", { class: `sa-band sa-band-${band.kind}`, x: band.x, y: band.y, width: Math.max(1, band.width), height: band.height }));
  }
  let lastRight = -Infinity;
  for (const band of [...(bands || [])].sort((a, b) => a.x - b.x)) {
    const half = (band.label.length * 7.5) / 2;
    const centre = clamp(band.x + Math.max(1, band.width) / 2, plot.left + half, plot.right - half);
    if (centre - half < lastRight + 4) continue;
    lastRight = centre + half;
    svg.append(svgElement("text", { class: `sa-band-label sa-band-label-${band.kind}`, x: centre, y: plot.top - 4, "text-anchor": "middle" }, band.label));
  }
}

function drawAxes(svg, { plot, xTicks = [], yTicks = [], horizontalGrid = true }) {
  for (const tick of yTicks) {
    if (horizontalGrid) svg.append(svgElement("line", { class: "sa-grid", x1: plot.left, x2: plot.right, y1: tick.pos, y2: tick.pos }));
    svg.append(svgElement("text", { class: "sa-tick", x: plot.left - 8, y: tick.pos + 4, "text-anchor": "end" }, tick.label));
  }
  for (const tick of xTicks) svg.append(svgElement("text", { class: "sa-tick", x: tick.pos, y: plot.bottom + 18, "text-anchor": "middle" }, tick.label));
}

function crosshairParts(svg, plot) {
  const line = svgElement("line", { class: "sa-crosshair", x1: plot.left, x2: plot.left, y1: plot.top, y2: plot.bottom, visibility: "hidden" });
  const hit = svgElement("rect", { class: "sa-hit", x: plot.left, y: plot.top, width: Math.max(1, plot.right - plot.left), height: Math.max(1, plot.bottom - plot.top) });
  svg.append(line, hit);
  return line;
}

function renderPace() {
  const container = byId("analysisPaceChart");
  if (!container) return;
  const width = widthOf(container);
  // Columns while each driver has room for its code and lap count; rows otherwise.
  const boxed = (state.analysis.race_pace || []).filter((row) => Number(row.n) >= 5).length;
  const horizontal = width < 600 || (width - 76) / Math.max(1, boxed) < 46;
  const model = M.racePaceModel(state.analysis, { width, height: clamp(Math.round(width * 0.34), 260, 340), horizontal });
  const thin = byId("analysisPaceThin");
  if (thin) thin.textContent = model.thin.length ? `Too few pace laps to box: ${model.thin.map((t) => `${t.code} (${t.n})`).join(", ")}.` : "";
  placeTable("analysisPaceTable", M.paceTable(state.analysis));
  if (!model.boxes.length) { mount(container, emptyNote("Not enough pace laps to show a spread for any driver.")); bindChart(container, { kind: "none" }); return; }
  const svg = svgRoot(model.width, model.height, "Race pace: lap-time spread per driver, fastest median first. Arrow keys move between drivers.", { interactive: true });
  const plot = model.plot;
  if (horizontal) {
    for (const tick of model.ticks) {
      svg.append(svgElement("line", { class: "sa-grid", x1: tick.pos, x2: tick.pos, y1: plot.top, y2: plot.bottom }));
      svg.append(svgElement("text", { class: "sa-tick", x: tick.pos, y: model.height - 8, "text-anchor": "middle" }, tick.label));
    }
  } else drawAxes(svg, { plot, yTicks: model.ticks });
  const clip = clipPath(svg, plot);
  model.boxes.forEach((box, i) => {
    const g = svgElement("g", {
      class: box.player ? "sa-box sa-mark sa-player" : "sa-box sa-mark", tabindex: -1, role: "img",
      "data-key": `box-${box.car_index}`, "data-row": 0, "data-col": i, "aria-label": box.text, "data-tip": box.text,
    });
    // The reading points at the box (a row also holds the code and lap
    // count), or in columns at the whiskers above it.
    const shapes = svgElement("g", { "clip-path": clip, "data-anchor": model.horizontal ? null : "box" });
    const stroke = { stroke: box.colour, "stroke-width": 2 };
    if (model.horizontal) {
      shapes.append(svgElement("line", { x1: box.whiskerLow, x2: box.whiskerHigh, y1: box.cross, y2: box.cross, ...stroke }));
      shapes.append(svgElement("rect", { x: Math.min(box.q1, box.q3), y: box.crossStart, width: Math.max(3, Math.abs(box.q3 - box.q1)), height: box.crossEnd - box.crossStart, rx: 4, fill: box.colour, "data-anchor": "box" }));
      shapes.append(svgElement("line", { class: "sa-median", x1: box.median, x2: box.median, y1: box.crossStart, y2: box.crossEnd }));
      g.append(svgElement("text", { class: box.player ? "sa-label sa-label-player" : "sa-label", x: box.label.x, y: box.label.y, "text-anchor": "end" }, box.code));
      g.append(svgElement("text", { class: "sa-value", x: box.nLabel.x, y: box.nLabel.y }, box.nText));
    } else {
      shapes.append(svgElement("line", { x1: box.cross, x2: box.cross, y1: box.whiskerHigh, y2: box.whiskerLow, ...stroke }));
      shapes.append(svgElement("line", { x1: box.cross - 6, x2: box.cross + 6, y1: box.whiskerHigh, y2: box.whiskerHigh, ...stroke }));
      shapes.append(svgElement("line", { x1: box.cross - 6, x2: box.cross + 6, y1: box.whiskerLow, y2: box.whiskerLow, ...stroke }));
      shapes.append(svgElement("rect", { x: box.crossStart, y: Math.min(box.q1, box.q3), width: box.crossEnd - box.crossStart, height: Math.max(3, Math.abs(box.q1 - box.q3)), rx: 4, fill: box.colour }));
      shapes.append(svgElement("line", { class: "sa-median", x1: box.crossStart, x2: box.crossEnd, y1: box.median, y2: box.median }));
      g.append(svgElement("text", { class: box.player ? "sa-label sa-label-player" : "sa-label", x: box.label.x, y: box.label.y, "text-anchor": "middle" }, box.code));
      g.append(svgElement("text", { class: "sa-value", x: box.nLabel.x, y: box.nLabel.y, "text-anchor": "middle" }, box.nText));
    }
    for (const outlier of box.outliers) {
      const at = model.horizontal ? { x: outlier.pos, y: box.cross } : { x: box.cross, y: outlier.pos };
      if (outlier.off) {
        // Off the scale: a marker on the plot edge pointing outward.
        const d = model.horizontal
          ? (outlier.off === "high" ? `M${at.x} ${at.y}l-8 -5v10z` : `M${at.x} ${at.y}l8 -5v10z`)
          : (outlier.off === "high" ? `M${at.x} ${at.y}l-5 8h10z` : `M${at.x} ${at.y}l-5 -8h10z`);
        shapes.append(svgElement("path", { class: "sa-outlier sa-outlier-off", d, stroke: box.colour }));
      } else shapes.append(svgElement("circle", { class: "sa-outlier", cx: at.x, cy: at.y, r: 4, stroke: box.colour }));
    }
    g.prepend(shapes);
    svg.append(g);
  });
  mount(container, svg);
  bindChart(container, { kind: "marks" });
}

function raceControlNote() {
  const rc = state.analysis?.race_control;
  if (!isRace() || !rc) return "";
  if (rc.available !== true) return "Safety cars were not recorded for this session.";
  if (Number(rc.covered_from_lap) > 1) return `Safety cars before lap ${rc.covered_from_lap} were not recorded.`;
  return "";
}

function renderTrace() {
  const container = byId("analysisTraceChart");
  if (!container) return;
  const width = widthOf(container);
  const model = M.raceTraceModel(state.analysis, { focus: state.focus, reference: state.reference, width, height: clamp(Math.round(width * 0.5), 220, 340) });
  const note = byId("analysisTraceNote");
  if (note) note.textContent = [M.referenceNote(state.analysis, state.reference), raceControlNote()].filter(Boolean).join(" ");
  placeTable("analysisTraceTable", M.traceTable(state.analysis, { focus: state.focus, reference: state.reference }));
  if (!model.laps.length) { mount(container, emptyNote("No laps to trace.")); bindChart(container, { kind: "none" }); return; }
  const svg = svgRoot(model.width, model.height, `Race trace: gap per lap to ${model.reference}, positive is behind. Arrow keys step through laps.`, { interactive: true, focusable: true });
  drawBands(svg, model.bands, model.plot);
  drawAxes(svg, { plot: model.plot, xTicks: model.xTicks, yTicks: model.yTicks });
  // Cars beyond the axis leave the plot at its edge instead of lying along it.
  const lines = svgElement("g", { "clip-path": clipPath(svg, model.plot) });
  svg.append(lines);
  for (const line of model.lines) {
    for (const points of line.runs) lines.append(svgElement("polyline", { class: line.focus ? "sa-line sa-line-focus" : "sa-line", points, stroke: line.colour, "stroke-dasharray": line.dash || null }));
    if (line.focus) for (const single of line.singles) lines.append(svgElement("circle", { cx: single.x, cy: single.y, r: 4, fill: line.colour, class: "sa-dot" }));
  }
  for (const label of model.endLabels) svg.append(svgElement("text", { class: "sa-label sa-label-strong", x: label.x, y: label.y }, label.text));
  const line = crosshairParts(svg, model.plot);
  mount(container, svg);
  const styles = M.driverStyles(state.analysis);
  bindChart(container, {
    kind: "crosshair", svg, line, plot: model.plot, laps: model.laps, lap: state.charts.get(container.id)?.lap || null,
    rows: (lap) => M.traceReading(state.analysis, { focus: state.focus, reference: state.reference, lap }).map((r) => ({ colour: styles.get(r.car_index)?.colour, label: r.code, value: r.value })),
  });
}

// Width of a 12 px chart label in the font the page really uses, so labels
// fit whatever font a device falls back to; null where canvas is missing.
let measureContext;
function labelMeasure(container) {
  if (measureContext === undefined) {
    try { measureContext = document.createElement("canvas").getContext("2d") || null; } catch { measureContext = null; }
  }
  if (!measureContext) return null;
  const font = `800 12px ${getComputedStyle(container).fontFamily || "sans-serif"}`;
  return (text) => { measureContext.font = font; return measureContext.measureText(text).width; };
}

function renderPositions() {
  const container = byId("analysisPositionsChart");
  if (!container) return;
  const width = widthOf(container);
  const field = Math.max(2, ...(state.analysis.laps || []).map((l) => Number(l.position) || 0));
  const model = M.positionsModel(state.analysis, { focus: state.focus, width, height: clamp(field * 18 + 48, 200, 460), measure: labelMeasure(container) });
  placeTable("analysisPositionsTable", M.positionsTable(state.analysis));
  if (!model.lines.length) { mount(container, emptyNote("No lap positions could be derived for this session.")); bindChart(container, { kind: "none" }); return; }
  const svg = svgRoot(model.width, model.height, "Position at the end of every lap; lapped finishers keep their place. Arrow keys step through laps.", { interactive: true, focusable: true });
  drawBands(svg, model.bands, model.plot);
  drawAxes(svg, { plot: model.plot, xTicks: model.xTicks, yTicks: model.yTicks, horizontalGrid: false });
  for (const line of model.lines) for (const points of line.runs) svg.append(svgElement("polyline", { class: line.focus ? "sa-line sa-line-focus" : "sa-line", points, stroke: line.colour, "stroke-dasharray": line.dash || null }));
  for (const label of model.labels) {
    svg.append(svgElement("circle", { cx: label.x, cy: label.y, r: 4, fill: label.colour, class: "sa-dot" }));
    svg.append(svgElement("text", { class: label.focus || label.player ? "sa-label sa-label-strong" : "sa-label", x: label.tx, y: label.ty, "text-anchor": label.anchor }, label.text));
  }
  const line = crosshairParts(svg, model.plot);
  mount(container, svg);
  const laps = M.lapLookup(state.analysis);
  const styles = M.driverStyles(state.analysis);
  bindChart(container, {
    kind: "crosshair", svg, line, plot: model.plot, laps: model.laps, lap: state.charts.get(container.id)?.lap || null,
    rows: (lap) => state.focus.map((carIndex) => ({ colour: styles.get(carIndex)?.colour, label: codeOf(carIndex), value: M.positionText(laps.get(carIndex)?.get(lap)?.position) })),
  });
}

// Drivers come in finishing order in a race with an order, otherwise most
// laps first (the backend's order).
function strategyOrderText() {
  return hasOrder() ? "in finishing order" : "most laps first";
}

function renderStrategy() {
  const container = byId("analysisStrategyChart");
  if (!container) return;
  const width = widthOf(container);
  const model = M.strategyModel(state.analysis, { width, rowHeight: 24 });
  placeTable("analysisStrategyTable", M.strategyTable(state.analysis));
  if (!model.rows.some((r) => r.stints.length)) { mount(container, emptyNote("No tyre data was stored for this session.")); bindChart(container, { kind: "none" }); return; }
  const svg = svgRoot(model.width, model.height, `Tyre strategy: stints per driver, ${strategyOrderText()}. Arrow keys move between stints and drivers.`, { interactive: true });
  model.rows.forEach((row, r) => {
    svg.append(svgElement("text", { class: row.player ? "sa-label sa-label-player" : "sa-label", x: model.left - 6, y: row.labelY, "text-anchor": "end" }, row.code));
    row.stints.forEach((stint, s) => {
      const g = svgElement("g", { tabindex: -1, role: "img", class: "sa-stint sa-mark", "data-key": `stint-${row.car_index}-${s}`, "data-row": r, "data-col": s, "aria-label": stint.text, "data-tip": stint.text });
      g.append(svgElement("rect", { x: stint.x, y: row.y + 4, width: stint.width, height: model.rowHeight - 8, rx: 4, fill: stint.fill }));
      if (stint.letter) g.append(svgElement("text", { class: "sa-stint-letter", x: stint.centre, y: row.labelY, fill: stint.ink, "text-anchor": "middle" }, stint.letter));
      svg.append(g);
    });
  });
  for (const tick of model.xTicks) svg.append(svgElement("text", { class: "sa-tick", x: tick.pos, y: model.height - 8, "text-anchor": "middle" }, tick.label));
  mount(container, svg);
  bindChart(container, { kind: "marks" });
}

function renderLapTimes() {
  const container = byId("analysisLapTimesChart");
  if (!container) return;
  const width = widthOf(container);
  const model = M.lapTimesModel(state.analysis, { focus: state.focus, width, height: clamp(Math.round(width * 0.48), 220, 320) });
  placeTable("analysisLapTimesTable", M.lapTimesTable(state.analysis, { focus: state.focus }));
  if (model.empty) { mount(container, emptyNote(state.focus.length ? "No pace laps for the focus drivers." : "Add a focus driver to see lap times.")); bindChart(container, { kind: "none" }); return; }
  const svg = svgRoot(model.width, model.height, "Lap times of pace laps for the focus drivers. Arrow keys move between laps and drivers; Enter opens a lap in Lap Lab.", { interactive: true });
  drawBands(svg, model.bands, model.plot);
  drawAxes(svg, { plot: model.plot, xTicks: model.xTicks, yTicks: model.yTicks });
  for (const series of model.series) {
    for (const points of series.runs) svg.append(svgElement("polyline", { class: "sa-line sa-line-focus", points, stroke: series.colour, "stroke-dasharray": series.dash || null }));
  }
  for (const series of model.series) {
    for (const point of series.points) {
      svg.append(svgElement("circle", {
        class: point.openable ? "sa-dot sa-mark sa-link" : "sa-dot sa-mark", cx: point.x, cy: point.y, r: 4.5, fill: series.colour, tabindex: -1,
        role: point.openable ? "button" : "img", "data-key": `lt-${series.car_index}-${point.lap}`, "data-row": point.row, "data-col": point.lap,
        "aria-label": point.openable ? `${point.text}. Press Enter to open in Lap Lab` : point.text, "data-tip": point.text, "data-lap-id": point.openable ? point.lap_id : null,
      }));
    }
  }
  for (const label of model.endLabels) svg.append(svgElement("text", { class: "sa-label sa-label-strong", x: label.x, y: label.y }, label.text));
  mount(container, svg);
  bindChart(container, { kind: "marks" });
}

function renderHeatmap() {
  const container = byId("analysisHeatmapChart");
  if (!container) return;
  const model = M.heatmapModel(state.analysis);
  placeHeatTable();
  if (!model.rows.length || !model.total) { mount(container, emptyNote("No laps to map.")); bindChart(container, { kind: "none" }); return; }
  const width = widthOf(container);
  const labelWidth = 46;
  const cell = clamp(Math.floor((width - labelWidth - 10) / model.total) - 2, 14, 28);
  const pitch = 22;
  const height = model.rows.length * pitch + 26;
  const gridWidth = model.total * (cell + 2) + 8;
  const labels = svgElement("svg", { width: labelWidth, height, viewBox: `0 0 ${labelWidth} ${height}`, class: "sa-svg sa-heat-labels", "aria-hidden": "true" });
  const svg = svgRoot(gridWidth, height, "Lap-time heatmap: each lap against the driver's own median. Arrow keys move between laps and drivers; Enter opens a recorded lap in Lap Lab.", { interactive: true });
  model.rows.forEach((row, r) => {
    const y = 4 + r * pitch;
    labels.append(svgElement("text", { class: row.player ? "sa-label sa-label-player" : "sa-label", x: labelWidth - 6, y: y + 13, "text-anchor": "end" }, row.code));
    row.cells.forEach((c, i) => {
      if (c.state === "none") return;
      const missing = c.state === "missing";
      const label = c.openable ? `${c.text}. Select again or press Enter to open in Lap Lab` : c.text;
      svg.append(svgElement("rect", {
        class: `sa-cell sa-mark${c.openable ? " sa-link" : ""}${missing ? " sa-cell-missing" : c.state === "excluded" ? " sa-cell-excluded" : ""}`,
        x: 2 + i * (cell + 2), y, width: cell, height: 18, rx: 2, fill: missing ? null : c.fill, tabindex: -1,
        role: c.openable ? "button" : "img", "data-key": `heat-${row.car_index}-${c.lap}`, "data-row": r, "data-col": c.lap,
        "aria-label": label, "data-tip": c.text, "data-lap-id": c.openable ? c.lap_id : null,
      }));
    });
  });
  for (const lap of M.lapAxisTicks(model.total, Math.max(3, Math.floor(gridWidth / 30)))) {
    svg.append(svgElement("text", { class: "sa-tick", x: 2 + (lap - 1) * (cell + 2) + cell / 2, y: height - 6, "text-anchor": "middle" }, String(lap)));
  }
  const previousScroll = container.querySelector(".sa-heat-scroll")?.scrollLeft || 0;
  const scroller = element("div", "sa-heat-scroll");
  scroller.append(svg);
  mount(container, labels, scroller);
  scroller.scrollLeft = previousScroll;
  // The reading sits outside the scroller: keep it on its cell while the
  // grid moves sideways.
  scroller.addEventListener("scroll", () => followTip(container, scroller), { passive: true });
  bindChart(container, { kind: "marks" });
}

// The heatmap's data table lists one driver's laps, each traced lap with a
// 44 px Open in Lap Lab button: the touch route for cells smaller than that.
function placeHeatTable() {
  const drivers = (state.analysis?.drivers || []).filter((d) => (state.analysis.laps || []).some((l) => l.car_index === d.car_index));
  if (!drivers.length) { placeTable("analysisHeatmapTable", null); return; }
  if (!drivers.some((d) => d.car_index === state.heatDriver)) state.heatDriver = (drivers.find((d) => d.is_player) || drivers.find((d) => state.focus.includes(d.car_index)) || drivers[0]).car_index;
  const table = M.heatmapTable(state.analysis, state.heatDriver);
  const picker = element("label", "sa-inline sa-heat-picker", "Driver ");
  const select = element("select");
  select.id = "analysisHeatmapDriver";
  for (const d of drivers) select.append(new Option(`${d.code}${d.is_player ? " (you)" : ""}`, String(d.car_index)));
  select.value = String(state.heatDriver);
  select.addEventListener("change", () => { state.heatDriver = Number(select.value); placeHeatTable(); });
  picker.append(select);
  placeTable("analysisHeatmapTable", { ...table, rows: table.rows.length ? table.rows : [["—", "No laps", "—", "—", "—"]] }, { before: [picker] });
}

function renderFastest() {
  const container = byId("analysisFastestChart");
  if (!container) return;
  const width = widthOf(container);
  const model = M.fastestModel(state.analysis, { width, rowHeight: 24 });
  placeTable("analysisFastestTable", M.fastestTable(state.analysis));
  if (!model.rows.length) { mount(container, emptyNote("No valid timed laps.")); bindChart(container, { kind: "none" }); return; }
  const svg = svgRoot(model.width, model.height, "Best lap per driver behind the fastest; the tick marks the best sectors combined. Arrow keys move between drivers.", { interactive: true });
  model.rows.forEach((row, i) => {
    const g = svgElement("g", { tabindex: -1, role: "img", class: "sa-bar sa-mark", "data-key": `best-${row.car_index}`, "data-row": 0, "data-col": i, "aria-label": row.text, "data-tip": row.text });
    g.append(svgElement("text", { class: row.player ? "sa-label sa-label-player" : "sa-label", x: model.left - 6, y: row.y + 13, "text-anchor": "end" }, row.code));
    g.append(svgElement("rect", { x: model.left, y: row.y + 2, width: Math.max(3, row.barEnd - model.left), height: 16, rx: 4, fill: row.colour }));
    if (row.idealX !== null) g.append(svgElement("line", { class: "sa-ideal", x1: row.idealX, x2: row.idealX, y1: row.y, y2: row.y + 20 }));
    g.append(svgElement("text", { class: "sa-value", x: Math.max(row.barEnd, row.idealX ?? 0) + 8, y: row.y + 14 }, row.label));
    svg.append(g);
  });
  mount(container, svg);
  bindChart(container, { kind: "marks" });
}

function renderStops() {
  const body = byId("analysisStopsRows");
  if (!body) return;
  const rows = M.pitStopRows(state.analysis);
  body.replaceChildren();
  if (!rows.length) {
    const tr = element("tr");
    const td = element("td", "empty", "No pit stops or tyre changes were recorded.");
    td.colSpan = 5;
    tr.append(td);
    body.append(tr);
    return;
  }
  for (const stop of rows) {
    const tr = element("tr");
    const tyres = element("td", "sa-nowrap");
    tyres.append(element("span", "", stop.tyres), element("span", "sr-only", ` (${stop.tyresText})`));
    tr.append(element("td", "", stop.code), element("td", "", stop.lapText), tyres, element("td", "", stop.kindText), element("td", "", stop.lossText));
    body.append(tr);
  }
}

// ------------------------------------------------------------- timelapse

function reducedMotion() {
  return Boolean(HAS_DOM && window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
}

function renderLapse() {
  const container = byId("analysisLapseChart");
  if (!container) return;
  const frames = state.lapse.frames;
  const total = frames.length;
  const frame = M.frameAt(frames, state.lapse.t);
  const range = byId("analysisLapseRange");
  const label = byId("analysisLapseLabel");
  const lap = Math.min(total, Math.max(1, Math.floor(state.lapse.t)));
  if (range && document.activeElement !== range) range.value = String(lap);
  const context = frame.suspended ? " · suspended" : frame.neutralised === "SC" ? " · safety car" : frame.neutralised === "VSC" ? " · VSC" : "";
  if (label) label.textContent = total ? `Lap ${lap} of ${total}${context}` : "No laps";
  if (!frame.entries?.length) {
    state.lapse.view = null;
    mount(container, emptyNote(state.analysis ? "No running order to replay." : "No session analysed."));
    return;
  }
  const width = widthOf(container);
  let view = state.lapse.view;
  if (!view || view.width !== width || view.analysis !== state.analysis || !container.contains(view.svg)) view = state.lapse.view = buildLapse(container, width);
  updateLapse(view, frame);
}

// The timelapse is built once per session and width; each frame only moves
// the existing marks, so playback never rebuilds the SVG.
function buildLapse(container, width) {
  const analysis = state.analysis;
  const rows = Math.max(1, ...state.lapse.frames.map((f) => f.entries.length));
  const rowHeight = 22;
  const head = state.lapse.frames.some((f) => f.suspended) ? 24 : 6;
  const height = head + rows * rowHeight + 28;
  const left = 96;
  const right = width - 92;
  const svg = svgRoot(width, height, "Race timelapse");
  const grid = svgElement("g");
  const note = svgElement("text", { class: "sa-suspended-note", x: 8, y: 16, visibility: "hidden" }, "Race suspended · order frozen");
  svg.append(grid, note);
  const styles = M.driverStyles(analysis);
  const cars = new Map();
  for (const driver of analysis.drivers || []) {
    const colour = styles.get(driver.car_index)?.colour || M.CONTEXT_STROKE;
    const g = svgElement("g", { visibility: "hidden" });
    const trail = svgElement("line", { class: "sa-lapse-trail", x1: left, x2: left, y1: 0, y2: 0, stroke: colour });
    const place = svgElement("text", { class: "sa-tick", x: 34, y: 4, "text-anchor": "end" });
    const code = svgElement("text", { class: driver.is_player ? "sa-label sa-label-player" : "sa-label", x: left - 10, y: 4, "text-anchor": "end" }, driver.code || `#${driver.car_index}`);
    const dot = svgElement("circle", { class: "sa-dot", cx: left, cy: 0, r: 7, fill: colour });
    const badge = svgElement("rect", { x: left + 11, y: -7, width: 22, height: 14, rx: 3 });
    const letter = svgElement("text", { class: "sa-stint-letter", x: left + 22, y: 4, "text-anchor": "middle" });
    const gap = svgElement("text", { class: "sa-value", x: left + 38, y: 4 });
    g.append(trail, place, code, dot, badge, letter, gap);
    svg.append(g);
    cars.set(driver.car_index, { g, trail, place, dot, badge, letter, gap, state: {} });
  }
  mount(container, svg);
  return { container, svg, width, height, head, rowHeight, left, right, grid, note, cars, analysis, span: null, suspended: null };
}

function setOnce(car, key, value, apply) {
  if (car.state[key] === value) return;
  car.state[key] = value;
  apply(value);
}

function updateLapse(view, frame) {
  const gaps = frame.entries.filter((e) => !e.laps_down && e.gap_ms != null).map((e) => e.gap_ms / 1000);
  const span = Math.min(Math.ceil(Math.max(10, ...gaps) / 10) * 10, 120);
  const x = M.scale(0, span, view.left, view.right);
  if (view.span !== span || view.suspended !== Boolean(frame.suspended)) {
    view.span = span;
    view.suspended = Boolean(frame.suspended);
    const ticks = frame.suspended ? [] : M.niceTicks(0, span, Math.max(2, Math.floor((view.right - view.left) / 70)));
    view.grid.replaceChildren(...ticks.flatMap((tick) => [
      svgElement("line", { class: "sa-grid", x1: x(tick), x2: x(tick), y1: view.head, y2: view.height - 22 }),
      svgElement("text", { class: "sa-tick", x: x(tick), y: view.height - 6, "text-anchor": "middle" }, tick === 0 ? "Leader" : `+${tick} s`),
    ]));
  }
  view.note.setAttribute("visibility", frame.suspended ? "visible" : "hidden");
  const seen = new Set();
  for (const entry of frame.entries) {
    const car = view.cars.get(entry.car_index);
    if (!car) continue;
    seen.add(entry.car_index);
    const y = view.head + 12 + (entry.position - 1) * view.rowHeight;
    const gap = entry.gap_ms == null ? null : entry.gap_ms / 1000;
    const cx = entry.laps_down > 0 ? view.right : gap === null ? view.left : x(Math.min(span, gap));
    const compound = M.compoundInfo(entry.compound);
    car.g.setAttribute("transform", `translate(0 ${y.toFixed(1)})`);
    setOnce(car, "visible", true, () => car.g.setAttribute("visibility", "visible"));
    car.trail.setAttribute("x2", cx.toFixed(1));
    car.dot.setAttribute("cx", cx.toFixed(1));
    car.badge.setAttribute("x", (cx + 11).toFixed(1));
    car.letter.setAttribute("x", (cx + 22).toFixed(1));
    car.gap.setAttribute("x", (cx + 38).toFixed(1));
    setOnce(car, "compound", compound.letter, () => {
      car.badge.setAttribute("fill", compound.fill);
      car.letter.setAttribute("fill", compound.ink);
      car.letter.textContent = compound.letter;
    });
    const gapText = entry.laps_down > 0 ? M.lapsDownText(entry.laps_down) : gap === null ? "" : gap === 0 ? "Leader" : `+${gap.toFixed(1)}`;
    setOnce(car, "gap", gapText, (text) => { car.gap.textContent = text; });
    setOnce(car, "place", entry.label || `P${Math.round(entry.position)}`, (text) => { car.place.textContent = text; });
  }
  for (const [carIndex, car] of view.cars) {
    if (!seen.has(carIndex)) setOnce(car, "visible", false, () => car.g.setAttribute("visibility", "hidden"));
  }
  const leaders = frame.entries.slice(0, 3).map((e) => `${e.label || `P${Math.round(e.position)}`} ${codeOf(e.car_index)}`).join(", ");
  view.svg.setAttribute("aria-label", `Race timelapse at lap ${frame.lap}${frame.suspended ? ", race suspended" : ""}: ${leaders}. The data table lists the order for every lap.`);
}

function playButton(playing) {
  const button = byId("analysisLapsePlay");
  if (!button) return;
  button.textContent = playing ? "Pause" : "Play";
  button.setAttribute("aria-pressed", String(playing));
}

function stopLapse() {
  state.lapse.playing = false;
  if (state.lapse.raf && HAS_DOM) cancelAnimationFrame(state.lapse.raf);
  if (state.lapse.timer && HAS_DOM) clearTimeout(state.lapse.timer);
  state.lapse.raf = 0;
  state.lapse.timer = 0;
  state.lapse.last = 0;
  if (HAS_DOM) playButton(false);
}

function stepLapse(now) {
  state.lapse.raf = 0;
  if (!state.lapse.playing) return;
  const elapsed = state.lapse.last ? (now - state.lapse.last) / 1000 : 0;
  state.lapse.last = now;
  state.lapse.t = Math.min(state.lapse.frames.length, state.lapse.t + elapsed * state.lapse.speed);
  renderLapse();
  if (state.lapse.t >= state.lapse.frames.length) { stopLapse(); return; }
  state.lapse.raf = requestAnimationFrame(stepLapse);
}

// Reduced motion steps a lap at a time through one cancellable timeout.
function scheduleLapseStep() {
  if (state.lapse.timer) clearTimeout(state.lapse.timer);
  state.lapse.timer = setTimeout(() => {
    state.lapse.timer = 0;
    if (!state.lapse.playing) return;
    state.lapse.t = Math.min(state.lapse.frames.length, Math.floor(state.lapse.t) + 1);
    renderLapse();
    if (state.lapse.t >= state.lapse.frames.length) stopLapse();
    else scheduleLapseStep();
  }, 1000 / state.lapse.speed);
}

function toggleLapse() {
  if (state.lapse.playing) { stopLapse(); return; }
  if (!state.lapse.frames.length) return;
  if (state.lapse.t >= state.lapse.frames.length) state.lapse.t = 1;
  state.lapse.playing = true;
  state.lapse.last = 0;
  playButton(true);
  if (reducedMotion()) scheduleLapseStep();
  else state.lapse.raf = requestAnimationFrame(stepLapse);
}

function resetLapse(frames) {
  stopLapse();
  state.lapse.frames = frames;
  state.lapse.t = frames.length || 1;
  state.lapse.view = null;
  const range = byId("analysisLapseRange");
  if (range) {
    range.max = String(Math.max(1, frames.length));
    range.value = range.max;
    range.disabled = !frames.length;
  }
  const label = byId("analysisLapseLabel");
  if (label && !frames.length) label.textContent = "No laps";
  const play = byId("analysisLapsePlay");
  if (play) play.disabled = !frames.length;
  placeTable("analysisLapseTable", frames.length && state.analysis ? M.lapseTable(state.analysis, frames) : null);
}

// ------------------------------------------------------------- page

function renderHeader() {
  const head = M.headline(state.analysis);
  byId("analysisTitle").textContent = head.title;
  byId("analysisSubtitle").textContent = head.subtitle || "";
  byId("analysisKpis").replaceChildren(...head.kpis.map((kpi) => {
    const card = element("div", "summary-metric sa-kpi");
    card.append(element("span", "", kpi.label), element("strong", "", kpi.value), element("small", "availability-note", kpi.detail || ""));
    return card;
  }));
  const best = byId("analysisOpenBest");
  if (best) {
    const player = (state.analysis?.drivers || []).find((d) => d.is_player);
    const ready = Boolean(player?.best_lap_id && player.best_lap_traced !== false);
    best.disabled = !ready;
    best.title = ready ? "" : player ? "Your best lap has no telemetry trace to play." : "No player car was recorded in this session.";
  }
}

function renderFocus() {
  const container = byId("analysisFocus");
  const add = byId("analysisAddDriver");
  if (!container || !add) return;
  const styles = M.driverStyles(state.analysis);
  const drivers = M.driversByIndex(state.analysis);
  container.replaceChildren(...state.focus.map((carIndex) => {
    const driver = drivers.get(carIndex) || {};
    const chip = element("button", "sa-chip");
    chip.type = "button";
    chip.dataset.car = String(carIndex);
    chip.setAttribute("aria-label", `Remove ${driver.code || codeOf(carIndex)} from focus`);
    const key = element("span", "sa-key-line");
    key.style.background = styles.get(carIndex)?.colour || M.CONTEXT_STROKE;
    if (styles.get(carIndex)?.dashed) key.classList.add("sa-key-dashed");
    chip.append(key, element("span", "", `${driver.code || codeOf(carIndex)}${driver.is_player ? " · you" : ""}`), element("span", "sa-chip-x", "×"));
    chip.addEventListener("click", () => removeFocus(carIndex));
    return chip;
  }));
  add.replaceChildren(new Option(state.focus.length >= MAX_FOCUS ? `Focus is full (${MAX_FOCUS})` : "Add driver…", ""));
  for (const driver of state.analysis?.drivers || []) {
    if (state.focus.includes(driver.car_index)) continue;
    const extra = isRace() ? (driver.status === "finished" ? M.positionText(driver.finish_position) : M.resultText(driver)) : "";
    add.append(new Option([driver.code, driver.display_name, extra && extra !== "—" ? extra : null].filter(Boolean).join(" · "), String(driver.car_index)));
  }
  add.disabled = !state.analysis || state.focus.length >= MAX_FOCUS;
  const reset = byId("analysisResetFocus");
  if (reset) reset.disabled = !state.analysis;
  const reference = byId("analysisReference");
  if (reference) {
    const options = M.referenceOptions(state.analysis);
    reference.replaceChildren(new Option("Race leader", "leader"), ...options.map((o) => new Option(o.label, o.value)));
    if (!options.some((o) => o.value === String(state.reference))) state.reference = "leader";
    reference.value = String(state.reference);
    reference.disabled = !hasOrder();
  }
}

function removeFocus(carIndex) {
  const at = state.focus.indexOf(carIndex);
  state.focus = state.focus.filter((c) => c !== carIndex);
  renderFocusDependent();
  // Keep keyboard focus in the chip row: the chip that took its place, or Add driver.
  const chips = [...(byId("analysisFocus")?.querySelectorAll(".sa-chip") || [])];
  (chips[Math.min(at, chips.length - 1)] || byId("analysisAddDriver"))?.focus({ preventScroll: true });
}

function safely(render) {
  try {
    render();
  } catch (error) {
    console.error("Session Analysis could not draw a chart", error);
  }
}

function renderFocusDependent() {
  renderFocus();
  if (!state.analysis) return;
  if (hasOrder()) { safely(renderTrace); safely(renderPositions); }
  safely(renderLapTimes);
}

function renderCharts() {
  if (!state.analysis) return;
  safely(renderPace);
  safely(renderStrategy);
  safely(renderLapTimes);
  safely(renderHeatmap);
  safely(renderFastest);
  safely(renderStops);
  if (hasOrder()) { safely(renderTrace); safely(renderPositions); safely(renderLapse); }
}

function paceCaption(analysis) {
  const basis = String(analysis?.basis?.pace_laps || "").trim();
  return basis ? `Pace ${basis}.` : DEFAULT_PACE_BASIS;
}

// Race-only captions need a race; the race trace, positions, timelapse and
// gap reference also need a race order (two or more recorded cars), so a
// session without one says why instead of claiming places.
function showSections(analysis) {
  const race = Boolean(analysis?.session?.is_race);
  const ordered = M.hasRaceOrder(analysis);
  document.querySelectorAll("#session-analysis [data-analysis-race-only]").forEach((node) => { node.hidden = !race; });
  document.querySelectorAll("#session-analysis [data-analysis-order-only]").forEach((node) => { node.hidden = !ordered; });
  const note = byId("analysisRaceOnlyNote");
  if (note) {
    note.textContent = M.raceOrderNote(analysis);
    note.hidden = ordered;
  }
  const strategy = byId("analysisStrategyHeading");
  if (strategy) strategy.textContent = ordered ? STRATEGY_HEADING : "Stints per driver, most laps first";
}

function renderAll() {
  const analysis = state.analysis;
  if (!analysis) return;
  showSections(analysis);
  const basis = byId("analysisPaceBasis");
  if (basis) basis.textContent = paceCaption(analysis);
  renderHeader();
  renderFocus();
  state.width = rootWidth();
  renderCharts();
  const warnings = (analysis.warnings || []).filter((w) => typeof w === "string" && w);
  const drivers = (analysis.drivers || []).length;
  const laps = (analysis.laps || []).length;
  if (warnings.length) setStatus(warnings.join(" "), "warning");
  else setStatus(`Analysis of ${M.countText(drivers, "car")} and ${M.countText(laps, "lap")}.`, "success");
}

// Every chart, table, note and headline back to its empty state, so nothing
// from a previous session stays on screen while another loads or fails.
function clearView({ loading = false } = {}) {
  stopLapse();
  hideAllTips();
  const placeholder = loading ? "Loading…" : "No session analysed.";
  const title = byId("analysisTitle");
  if (title) title.textContent = loading ? "Loading session analysis…" : DEFAULT_TITLE;
  const subtitle = byId("analysisSubtitle");
  if (subtitle) subtitle.textContent = loading ? "" : DEFAULT_SUBTITLE;
  byId("analysisKpis")?.replaceChildren();
  const best = byId("analysisOpenBest");
  if (best) { best.disabled = true; best.title = ""; }
  for (const id of CHARTS) {
    const node = byId(id);
    if (node) node.replaceChildren(emptyNote(placeholder));
    state.charts.delete(id);
  }
  for (const id of TABLES) byId(id)?.replaceChildren();
  for (const id of NOTES) { const node = byId(id); if (node) node.textContent = ""; }
  const basis = byId("analysisPaceBasis");
  if (basis) basis.textContent = DEFAULT_PACE_BASIS;
  const stops = byId("analysisStopsRows");
  if (stops) {
    const tr = element("tr");
    const td = element("td", "empty", placeholder);
    td.colSpan = 5;
    tr.append(td);
    stops.replaceChildren(tr);
  }
  document.querySelectorAll("#session-analysis [data-analysis-race-only], #session-analysis [data-analysis-order-only]").forEach((node) => { node.hidden = false; });
  const raceNote = byId("analysisRaceOnlyNote");
  if (raceNote) raceNote.hidden = true;
  const strategy = byId("analysisStrategyHeading");
  if (strategy) strategy.textContent = STRATEGY_HEADING;
  state.focus = [];
  state.reference = "leader";
  state.heatDriver = null;
  renderFocus();
  resetLapse([]);
}

function rootWidth() {
  return Math.round(byId("session-analysis")?.clientWidth || 0);
}

function openLapInLapLab(lapId) {
  if (!lapId || !HAS_DOM) return;
  stopLapse();
  hideAllTips();
  window.dispatchEvent(new CustomEvent("pitwall:open-lap", { detail: { sessionId: state.sessionId, lapId } }));
}

function sessionLabel(session) {
  return [session.display_name || session.track_name || "Saved session", session.session_type, session.started_at ? new Date(session.started_at).toLocaleDateString() : null].filter(Boolean).join(" · ");
}

function populateSessions() {
  const select = byId("analysisSessionSelect");
  if (!select) return;
  const current = state.sessionId;
  select.replaceChildren(new Option("Choose a saved session", ""));
  for (const session of state.sessions) select.append(new Option(sessionLabel(session), String(session.id)));
  if (current && ![...select.options].some((o) => o.value === current)) {
    const known = state.analysis?.session ? sessionLabel(state.analysis.session) : "Selected session";
    select.append(new Option(known, current));
  }
  select.value = current;
}

async function loadSessions() {
  const request = ++state.sessionsRequest;
  try {
    const payload = await api("/sessions?limit=50");
    if (request !== state.sessionsRequest) return;
    state.sessions = Array.isArray(payload?.items) ? payload.items : [];
    state.sessionsLoaded = true;
    populateSessions();
  } catch (error) {
    if (request !== state.sessionsRequest) return;
    if (!state.analysis && !state.sessionId) setStatus(`Saved sessions are unavailable: ${errorText(error)}`, "error");
  }
}

export async function openAnalysis(sessionId) {
  if (!HAS_DOM) return;
  const id = sessionId === null || sessionId === undefined ? "" : String(sessionId);
  const sameSession = Boolean(id) && id === state.sessionId && Boolean(state.analysis);
  stopLapse();
  state.sessionId = id;
  remember(id);
  populateSessions();
  const request = ++state.request;
  const root = byId("session-analysis");
  if (!id) {
    state.analysis = null;
    root?.classList.remove("sa-loading");
    root?.removeAttribute("aria-busy");
    clearView();
    setStatus(CHOOSE_MESSAGE);
    return;
  }
  if (!sameSession) {
    state.analysis = null;
    clearView({ loading: true });
  }
  root?.classList.add("sa-loading");
  root?.setAttribute("aria-busy", "true");
  setStatus(sameSession ? "Updating the analysis…" : "Analysing the session…");
  try {
    const query = new URLSearchParams({ hide_outliers: String(state.hideOutliers) });
    const analysis = await api(`/sessions/${encodeURIComponent(id)}/analysis?${query}`);
    if (request !== state.request) return;
    if (!M.schemaSupported(analysis)) {
      state.analysis = null;
      clearView();
      setStatus("This analysis was made by a different version of the app. Reload the app to view this analysis.", "error");
      return;
    }
    const keepFocus = sameSession && String(state.analysis?.session_id) === String(analysis.session_id);
    const known = new Set((analysis.drivers || []).map((d) => d.car_index));
    const focus = keepFocus ? state.focus.filter((c) => known.has(c)) : M.defaultFocus(analysis);
    state.analysis = analysis;
    state.focus = focus;
    if (!keepFocus) { state.reference = "leader"; state.heatDriver = null; }
    // No frames without a race order (practice, or a single recorded car).
    resetLapse(M.timelapseFrames(analysis));
    populateSessions();
    renderAll();
  } catch (error) {
    if (request !== state.request) return;
    state.analysis = null;
    if (error?.status === 404) {
      // The session is gone: forget it, show the chooser and refresh the list.
      forget(id);
      state.sessionId = "";
      state.sessions = state.sessions.filter((s) => String(s.id) !== id);
      populateSessions();
      clearView();
      setStatus("That session is no longer saved. Choose another saved session.", "error");
      loadSessions();
      return;
    }
    clearView();
    setStatus(`${errorText(error)} Press Refresh to try again.`, "error");
  } finally {
    if (request === state.request) {
      root?.classList.remove("sa-loading");
      root?.removeAttribute("aria-busy");
    }
  }
}

function refresh() {
  loadSessions();
  const id = state.sessionId || recall();
  if (id) openAnalysis(id);
  else setStatus(CHOOSE_MESSAGE);
}

function viewVisible() {
  const root = byId("session-analysis");
  const page = byId("analysis");
  return Boolean(root && !root.hidden && (!page || !page.hidden));
}

// Showing the view: load the session list once and reopen the remembered
// session when nothing is chosen yet.
function activate() {
  if (!state.sessionsLoaded) loadSessions();
  if (!state.sessionId) {
    const remembered = recall();
    if (remembered) openAnalysis(remembered);
  }
  scheduleResize();
}

function leave() {
  stopLapse();
  hideAllTips();
}

function sessionDeleted(sessionId) {
  const id = sessionId === null || sessionId === undefined ? "" : String(sessionId);
  if (!id) return;
  stopLapse();
  forget(id);
  state.sessions = state.sessions.filter((s) => String(s.id) !== id);
  if (id === state.sessionId) {
    state.request += 1;
    state.sessionId = "";
    state.analysis = null;
    byId("session-analysis")?.classList.remove("sa-loading");
    byId("session-analysis")?.removeAttribute("aria-busy");
    clearView();
    setStatus("The analysed session was deleted. Choose another saved session.");
  }
  populateSessions();
  if (state.sessionsLoaded) loadSessions();
}

// One ResizeObserver; the redraw waits for the next animation frame, so a
// resize never loops and rotation redraws at the new width.
function scheduleResize() {
  if (!HAS_DOM || state.resizeFrame) return;
  state.resizeFrame = requestAnimationFrame(() => {
    state.resizeFrame = 0;
    const width = rootWidth();
    if (!width || width === state.width) return;
    state.width = width;
    if (state.analysis) {
      hideAllTips();
      renderCharts();
    }
  });
}

function watchWidth() {
  const root = byId("session-analysis");
  if (!root) return;
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(scheduleResize).observe(root);
  else window.addEventListener("resize", scheduleResize);
}

function bind() {
  byId("analysisSessionSelect")?.addEventListener("change", (event) => openAnalysis(event.target.value));
  byId("analysisRefresh")?.addEventListener("click", refresh);
  byId("analysisAddDriver")?.addEventListener("change", (event) => {
    const value = event.target.value;
    if (value === "" || !state.analysis) return;
    const carIndex = Number(value);
    if (!state.focus.includes(carIndex) && state.focus.length < MAX_FOCUS) state.focus = [...state.focus, carIndex];
    renderFocusDependent();
    byId("analysisAddDriver")?.focus({ preventScroll: true });
  });
  byId("analysisResetFocus")?.addEventListener("click", () => {
    if (!state.analysis) return;
    state.focus = M.defaultFocus(state.analysis);
    renderFocusDependent();
  });
  byId("analysisHideOutliers")?.addEventListener("change", (event) => {
    state.hideOutliers = Boolean(event.target.checked);
    if (state.sessionId) openAnalysis(state.sessionId);
  });
  byId("analysisReference")?.addEventListener("change", (event) => {
    state.reference = event.target.value === "leader" ? "leader" : Number(event.target.value);
    safely(renderTrace);
  });
  byId("analysisLapsePlay")?.addEventListener("click", toggleLapse);
  byId("analysisLapseRange")?.addEventListener("input", (event) => {
    stopLapse();
    state.lapse.t = Number(event.target.value) || 1;
    renderLapse();
  });
  byId("analysisLapseSpeed")?.addEventListener("change", (event) => {
    state.lapse.speed = Number(event.target.value) || 1;
    if (state.lapse.playing && state.lapse.timer) scheduleLapseStep();
  });
  byId("analysisOpenBest")?.addEventListener("click", () => {
    const player = (state.analysis?.drivers || []).find((d) => d.is_player);
    if (player?.best_lap_id && player.best_lap_traced !== false) openLapInLapLab(player.best_lap_id);
  });
  window.addEventListener("pitwall:analyze-session", (event) => {
    const sessionId = event.detail?.sessionId;
    if (sessionId === null || sessionId === undefined || sessionId === "") return;
    openAnalysis(sessionId);
  });
  // Leaving the view stops the animation: no hidden work on a tablet.
  window.addEventListener("pitwall:pagechange", (event) => {
    if (event.detail?.page === "session-analysis") activate();
    else leave();
  });
  window.addEventListener("pitwall:session-deleted", (event) => sessionDeleted(event.detail?.sessionId));
  window.addEventListener("pagehide", leave);
  document.addEventListener("visibilitychange", () => { if (document.hidden) leave(); });
  // Modality only (never an action): a keyboard focus change is announced,
  // a tap is not.
  document.addEventListener("keydown", () => { state.keyboard = true; }, true);
  document.addEventListener("pointerdown", () => { state.keyboard = false; }, true);
  // A pinned reading stays until the next tap elsewhere.
  document.addEventListener("click", (event) => {
    const tip = state.tip;
    if (tip?.sticky && !tip.container.contains(event.target)) {
      hideTip(tip.container, true);
      hideCrosshair(tip.container);
    }
  }, true);
}

if (HAS_DOM && byId("session-analysis")) {
  bind();
  watchWidth();
  clearView();
  setStatus(CHOOSE_MESSAGE);
  // The module loads deferred: a reload at #session-analysis (or a stored
  // analysis view) may already show the view before this script runs.
  if (viewVisible()) activate();
}

export const __test = { state, openAnalysis, stopLapse, renderLapse };
