// Session Analysis view: post-session charts for one saved session.
// Data: GET /api/v1/sessions/{id}/analysis. Chart maths lives in
// session-analysis-model.mjs; this file only draws, wires controls and keeps
// requests from overwriting each other.
import * as M from "./session-analysis-model.mjs";

const HAS_DOM = typeof window !== "undefined" && typeof document !== "undefined";
const API_ROOT = "/api/v1";
const SVG_NS = "http://www.w3.org/2000/svg";
const MAX_FOCUS = 6;
const COMPACT_WIDTH = 700;
const STORAGE_SESSION = "pitwall.analysis.session";
let clipCounter = 0;

const state = {
  sessions: [],
  sessionId: "",
  analysis: null,
  request: 0,
  focus: [],
  hideOutliers: true,
  reference: "leader",
  compact: false,
  lapse: { t: 1, playing: false, speed: 1, frames: [], raf: 0, last: 0 },
};

const byId = (id) => document.getElementById(id);

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

function svgRoot(width, height, label) {
  return svgElement("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": label, class: "sa-svg", preserveAspectRatio: "xMinYMin meet" });
}

function setStatus(message, tone = "") {
  const node = byId("analysisStatus");
  if (!node) return;
  node.textContent = message;
  if (tone) node.dataset.tone = tone;
  else delete node.dataset.tone;
}

async function api(path) {
  let response;
  try {
    response = await fetch(`${API_ROOT}${path}`, { headers: { Accept: "application/json" } });
  } catch (error) {
    throw new Error(`The local API is unavailable: ${error instanceof Error ? error.message : error}`);
  }
  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(detail?.message || (typeof detail === "string" ? detail : null) || `${response.status} ${response.statusText}`);
  }
  return payload;
}

function drivers() {
  return M.driversByIndex(state.analysis);
}

function codeOf(carIndex) {
  return drivers().get(carIndex)?.code || `#${carIndex}`;
}

// ------------------------------------------------------------- tooltips

function showTip(container, html, event) {
  let tip = container.querySelector(":scope > .sa-tip");
  if (!tip) {
    tip = element("div", "sa-tip");
    tip.setAttribute("role", "status");
    container.append(tip);
  }
  tip.replaceChildren(...html);
  tip.hidden = false;
  const box = container.getBoundingClientRect();
  const x = (event?.clientX ?? box.left + box.width / 2) - box.left;
  const y = (event?.clientY ?? box.top + 20) - box.top;
  const width = tip.offsetWidth || 180;
  tip.style.left = `${Math.max(4, Math.min(box.width - width - 4, x + 12))}px`;
  tip.style.top = `${Math.max(4, y - 12 - (tip.offsetHeight || 40))}px`;
}

function hideTip(container) {
  const tip = container.querySelector(":scope > .sa-tip");
  if (tip) tip.hidden = true;
}

// Marks carry data-tip text. Pointer, touch and keyboard focus all show it.
function bindMarkTips(container) {
  if (container.dataset.tipsBound) return;
  container.dataset.tipsBound = "1";
  const reveal = (event) => {
    const mark = event.target?.closest?.("[data-tip]");
    if (!mark || !container.contains(mark)) { hideTip(container); return; }
    showTip(container, [element("strong", "", mark.dataset.tip)], event.type === "focusin" ? null : event);
  };
  container.addEventListener("pointermove", reveal);
  container.addEventListener("pointerdown", reveal);
  container.addEventListener("focusin", reveal);
  container.addEventListener("pointerleave", () => hideTip(container));
  container.addEventListener("focusout", () => hideTip(container));
}

// Line charts: a crosshair snaps to the nearest lap and lists every focus series.
function bindCrosshair(container, svg, { plot, laps, rows }) {
  const line = svgElement("line", { class: "sa-crosshair", x1: 0, x2: 0, y1: plot.top, y2: plot.bottom, visibility: "hidden" });
  svg.append(line);
  const hit = svgElement("rect", { class: "sa-hit", x: plot.left, y: plot.top, width: plot.right - plot.left, height: plot.bottom - plot.top });
  svg.append(hit);
  const move = (event) => {
    const box = svg.getBoundingClientRect();
    const scaleX = svg.viewBox.baseVal.width / box.width;
    const px = (event.clientX - box.left) * scaleX;
    let nearest = laps[0];
    for (const lap of laps) if (Math.abs(lap.x - px) < Math.abs(nearest.x - px)) nearest = lap;
    if (!nearest) return;
    line.setAttribute("x1", nearest.x); line.setAttribute("x2", nearest.x); line.setAttribute("visibility", "visible");
    const items = rows(nearest.lap);
    showTip(container, [element("strong", "", `Lap ${nearest.lap}`), ...items.map((item) => {
      const row = element("div", "sa-tip-row");
      const key = element("span", "sa-key-line"); key.style.background = item.colour;
      row.append(key, element("b", "", item.value), element("span", "", item.label));
      return row;
    })], event);
  };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerdown", move);
  hit.addEventListener("pointerleave", () => { line.setAttribute("visibility", "hidden"); hideTip(container); });
}

// --------------------------------------------------------------- tables

function dataTable(caption, headers, rows) {
  const details = element("details", "sa-table-details");
  const summary = element("summary", "", "Data table");
  const wrap = element("div", "table-scroll");
  wrap.tabIndex = 0;
  const table = element("table", "table workspace-table sa-table");
  const cap = element("caption", "sr-only", caption);
  const head = element("thead");
  const headRow = element("tr");
  headers.forEach((h) => { const th = element("th", "", h); th.scope = "col"; headRow.append(th); });
  head.append(headRow);
  const body = element("tbody");
  rows.forEach((cells) => { const tr = element("tr"); cells.forEach((c) => tr.append(element("td", "", c))); body.append(tr); });
  table.append(cap, head, body);
  wrap.append(table);
  details.append(summary, wrap);
  return details;
}

function placeTable(id, table) {
  const slot = byId(id);
  if (slot) slot.replaceChildren(table);
}

function emptyChart(container, message) {
  container.replaceChildren(element("div", "empty", message));
}

// ------------------------------------------------------------- charts

function drawBands(svg, bands) {
  for (const band of bands || []) {
    svg.append(svgElement("rect", { class: `sa-band sa-band-${band.kind}`, x: band.x, y: band.y, width: Math.max(1, band.width), height: band.height }));
    if (band.kind === "suspended") svg.append(svgElement("text", { class: "sa-band-label", x: band.x + band.width / 2, y: band.y + 12, "text-anchor": "middle" }, "RED"));
  }
}

function drawAxes(svg, { plot, xTicks = [], yTicks = [], xLabel = "", horizontalGrid = true }) {
  for (const tick of yTicks) {
    if (horizontalGrid) svg.append(svgElement("line", { class: "sa-grid", x1: plot.left, x2: plot.right, y1: tick.pos, y2: tick.pos }));
    svg.append(svgElement("text", { class: "sa-tick", x: plot.left - 8, y: tick.pos + 4, "text-anchor": "end" }, tick.label));
  }
  for (const tick of xTicks) svg.append(svgElement("text", { class: "sa-tick", x: tick.pos, y: plot.bottom + 18, "text-anchor": "middle" }, tick.label));
  if (xLabel) svg.append(svgElement("text", { class: "sa-tick", x: plot.right, y: plot.bottom + 18, "text-anchor": "end" }, ""));
}

function renderPace() {
  const container = byId("analysisPaceChart");
  if (!container) return;
  const model = M.racePaceModel(state.analysis, { width: state.compact ? 360 : 1200, height: 340, horizontal: state.compact });
  if (!model.boxes.length) { emptyChart(container, "Not enough racing laps to show a spread for any driver."); placeTable("analysisPaceTable", element("span")); return; }
  const svg = svgRoot(model.width, model.height, "Race pace: lap-time spread per driver, fastest median first");
  const plot = model.plot || { left: 52, right: model.width - 16, top: 8, bottom: model.height - 28 };
  if (model.horizontal) {
    for (const tick of model.ticks) {
      svg.append(svgElement("line", { class: "sa-grid", x1: tick.pos, x2: tick.pos, y1: 4, y2: model.height - 24 }));
      svg.append(svgElement("text", { class: "sa-tick", x: tick.pos, y: model.height - 8, "text-anchor": "middle" }, tick.label));
    }
  } else {
    drawAxes(svg, { plot, yTicks: model.ticks });
  }
  for (const box of model.boxes) {
    const g = svgElement("g", { class: box.player ? "sa-box sa-player" : "sa-box", tabindex: 0, "data-tip": `${box.code} · median ${M.formatLapTime(box.row.median_ms)} · middle half ${M.formatLapTime(box.row.q1_ms)}–${M.formatLapTime(box.row.q3_ms)} · ${box.row.n} laps` });
    const stroke = { stroke: box.colour, "stroke-width": 2 };
    if (model.horizontal) {
      g.append(svgElement("line", { x1: box.whiskerLow, x2: box.whiskerHigh, y1: box.cross, y2: box.cross, ...stroke }));
      g.append(svgElement("rect", { x: box.q1, y: box.crossStart, width: Math.max(3, box.q3 - box.q1), height: box.crossEnd - box.crossStart, rx: 4, fill: box.colour }));
      g.append(svgElement("line", { class: "sa-median", x1: box.median, x2: box.median, y1: box.crossStart, y2: box.crossEnd }));
      g.append(svgElement("text", { class: box.player ? "sa-label sa-label-player" : "sa-label", x: box.label.x, y: box.label.y, "text-anchor": "end" }, box.code));
    } else {
      g.append(svgElement("line", { x1: box.cross, x2: box.cross, y1: box.whiskerHigh, y2: box.whiskerLow, ...stroke }));
      g.append(svgElement("line", { x1: box.cross - 6, x2: box.cross + 6, y1: box.whiskerHigh, y2: box.whiskerHigh, ...stroke }));
      g.append(svgElement("line", { x1: box.cross - 6, x2: box.cross + 6, y1: box.whiskerLow, y2: box.whiskerLow, ...stroke }));
      g.append(svgElement("rect", { x: box.crossStart, y: box.q3, width: box.crossEnd - box.crossStart, height: Math.max(3, box.q1 - box.q3), rx: 4, fill: box.colour }));
      g.append(svgElement("line", { class: "sa-median", x1: box.crossStart, x2: box.crossEnd, y1: box.median, y2: box.median }));
      g.append(svgElement("text", { class: box.player ? "sa-label sa-label-player" : "sa-label", x: box.label.x, y: box.label.y, "text-anchor": "middle" }, box.code));
    }
    for (const outlier of box.outliers) {
      const at = model.horizontal ? { cx: outlier.pos, cy: box.cross } : { cx: box.cross, cy: outlier.pos };
      g.append(svgElement("circle", { class: "sa-outlier", ...at, r: 4, stroke: box.colour }));
    }
    svg.append(g);
  }
  container.replaceChildren(svg);
  bindMarkTips(container);
  const thin = byId("analysisPaceThin");
  if (thin) thin.textContent = model.thin.length ? `Too few racing laps to box: ${model.thin.map((t) => `${t.code} (${t.n})`).join(", ")}.` : "";
  placeTable("analysisPaceTable", dataTable("Race pace per driver", ["Driver", "Racing laps", "Median", "Middle half", "Fastest", "Slowest kept", "Left out"], (state.analysis.race_pace || []).map((row) => [
    codeOf(row.car_index), String(row.n), M.formatLapTime(row.median_ms), `${M.formatLapTime(row.q1_ms)}–${M.formatLapTime(row.q3_ms)}`,
    M.formatLapTime(row.min_ms), M.formatLapTime(row.max_ms), Object.entries(row.excluded || {}).map(([k, v]) => `${k.replace("_", " ")} ${v}`).join(", ") || "none",
  ])));
}

function renderTrace() {
  const container = byId("analysisTraceChart");
  if (!container) return;
  const model = M.raceTraceModel(state.analysis, { focus: state.focus, reference: state.reference, width: 640, height: 320 });
  const svg = svgRoot(model.width, model.height, "Race trace: gap per lap");
  drawBands(svg, model.bands);
  drawAxes(svg, { plot: model.plot, xTicks: model.xTicks, yTicks: model.yTicks });
  // Cars beyond the axis leave the plot at its edge instead of lying along it.
  const clipId = `saTraceClip${++clipCounter}`;
  const defs = svgElement("defs");
  const clip = svgElement("clipPath", { id: clipId });
  clip.append(svgElement("rect", { x: model.plot.left, y: model.plot.top - 2, width: model.plot.right - model.plot.left, height: model.plot.bottom - model.plot.top + 4 }));
  defs.append(clip);
  svg.append(defs);
  const lines = svgElement("g", { "clip-path": `url(#${clipId})` });
  svg.append(lines);
  for (const line of model.lines) {
    for (const points of line.runs) {
      lines.append(svgElement("polyline", { class: line.focus ? "sa-line sa-line-focus" : "sa-line", points, stroke: line.colour, "stroke-dasharray": line.dash || null }));
    }
    if (line.focus) for (const single of line.singles) svg.append(svgElement("circle", { cx: single.x, cy: single.y, r: 4, fill: line.colour, class: "sa-dot" }));
  }
  container.replaceChildren(svg);
  const series = M.gapSeries(state.analysis, state.reference);
  const total = M.lastLap(state.analysis);
  const x = M.scale(1, Math.max(2, total), model.plot.left, model.plot.right);
  const styles = M.driverStyles(state.analysis);
  bindCrosshair(container, svg, {
    plot: model.plot,
    laps: Array.from({ length: total }, (_, i) => ({ lap: i + 1, x: x(i + 1) })),
    rows: (lap) => state.focus.map((carIndex) => {
      const point = (series.get(carIndex) || []).find((p) => p.lap === lap);
      return { colour: styles.get(carIndex)?.colour, label: codeOf(carIndex), value: point ? M.formatGap(point.gap) : (state.analysis.suspended_laps || []).includes(lap) ? "suspended" : "—" };
    }),
  });
  placeTable("analysisTraceTable", dataTable("Gap per lap for focus drivers", ["Lap", ...state.focus.map(codeOf)], Array.from({ length: total }, (_, i) => [
    String(i + 1), ...state.focus.map((carIndex) => { const p = (series.get(carIndex) || []).find((pt) => pt.lap === i + 1); return p ? M.formatGap(p.gap) : "—"; }),
  ])));
}

function renderPositions() {
  const container = byId("analysisPositionsChart");
  if (!container) return;
  const model = M.positionsModel(state.analysis, { focus: state.focus, width: 640, height: 380 });
  if (!model.lines.length) { emptyChart(container, "No lap positions could be derived for this session."); return; }
  const svg = svgRoot(model.width, model.height, "Position at the end of every lap");
  drawBands(svg, model.bands);
  drawAxes(svg, { plot: model.plot, xTicks: model.xTicks, yTicks: model.yTicks, horizontalGrid: false });
  for (const line of model.lines) for (const points of line.runs) svg.append(svgElement("polyline", { class: line.focus ? "sa-line sa-line-focus" : "sa-line", points, stroke: line.colour, "stroke-dasharray": line.dash || null }));
  for (const label of model.labels) {
    svg.append(svgElement("circle", { cx: label.x, cy: label.y, r: 4, fill: label.colour, class: "sa-dot" }));
    svg.append(svgElement("text", { class: label.focus || label.player ? "sa-label sa-label-strong" : "sa-label", x: label.x + 8, y: label.y + 4 }, label.text));
  }
  container.replaceChildren(svg);
  const laps = M.lapLookup(state.analysis);
  const total = M.lastLap(state.analysis);
  const x = M.scale(1, Math.max(2, total), model.plot.left, model.plot.right);
  const styles = M.driverStyles(state.analysis);
  bindCrosshair(container, svg, {
    plot: model.plot,
    laps: Array.from({ length: total }, (_, i) => ({ lap: i + 1, x: x(i + 1) })),
    rows: (lap) => state.focus.map((carIndex) => ({ colour: styles.get(carIndex)?.colour, label: codeOf(carIndex), value: laps.get(carIndex)?.get(lap)?.position ? `P${laps.get(carIndex).get(lap).position}` : "—" })),
  });
  placeTable("analysisPositionsTable", dataTable("Position per lap", ["Driver", ...Array.from({ length: total }, (_, i) => `L${i + 1}`)], (state.analysis.drivers || []).map((d) => [
    d.code, ...Array.from({ length: total }, (_, i) => { const p = laps.get(d.car_index)?.get(i + 1)?.position; return p ? String(p) : "—"; }),
  ])));
}

function renderStrategy() {
  const container = byId("analysisStrategyChart");
  if (!container) return;
  const model = M.strategyModel(state.analysis, { width: state.compact ? 360 : 1200, rowHeight: 24, minLabelWidth: 20 });
  if (!model.rows.some((r) => r.stints.length)) { emptyChart(container, "No tyre data was stored for this session."); return; }
  const svg = svgRoot(model.width, model.height, "Tyre strategy: stints per driver in finishing order");
  model.rows.forEach((row) => {
    svg.append(svgElement("text", { class: row.player ? "sa-label sa-label-player" : "sa-label", x: 48, y: row.labelY, "text-anchor": "end" }, row.code));
    for (const stint of row.stints) {
      const g = svgElement("g", { tabindex: 0, class: "sa-stint", "data-tip": `${row.code} · ${stint.label} · laps ${stint.start_lap}–${stint.end_lap} (${stint.laps})${stint.tyre_age_start ? ` · ${stint.tyre_age_start} laps old at start` : ""}` });
      g.append(svgElement("rect", { x: stint.x, y: row.y + 4, width: stint.width, height: model.rowHeight - 8, rx: 4, fill: stint.fill }));
      if (stint.letter) g.append(svgElement("text", { class: "sa-stint-letter", x: stint.centre, y: row.labelY, fill: stint.ink, "text-anchor": "middle" }, stint.letter));
      svg.append(g);
    }
  });
  for (const tick of model.xTicks) svg.append(svgElement("text", { class: "sa-tick", x: tick.pos, y: model.height - 8, "text-anchor": "middle" }, tick.label));
  container.replaceChildren(svg);
  bindMarkTips(container);
  placeTable("analysisStrategyTable", dataTable("Tyre stints per driver", ["Driver", "Stints"], model.rows.map((row) => [row.code, row.stints.map((s) => `${s.label} L${s.start_lap}–${s.end_lap}`).join(" · ") || "no tyre data"])));
}

function renderLapTimes() {
  const container = byId("analysisLapTimesChart");
  if (!container) return;
  const model = M.lapTimesModel(state.analysis, { focus: state.focus, width: 640, height: 300 });
  if (model.empty) { emptyChart(container, "No racing laps for the focus drivers."); return; }
  const svg = svgRoot(model.width, model.height, "Lap times of racing laps for focus drivers");
  drawBands(svg, model.bands);
  drawAxes(svg, { plot: model.plot, xTicks: model.xTicks, yTicks: model.yTicks });
  for (const series of model.series) {
    for (const points of series.runs) svg.append(svgElement("polyline", { class: "sa-line sa-line-focus", points, stroke: series.colour, "stroke-dasharray": series.dash || null }));
    for (const point of series.points) {
      svg.append(svgElement("circle", { class: "sa-dot", cx: point.x, cy: point.y, r: 4, fill: series.colour, tabindex: 0, "data-tip": `${series.code} lap ${point.lap} · ${M.formatLapTime(point.ms)} · ${M.compoundInfo(point.compound).label}` }));
    }
  }
  container.replaceChildren(svg);
  bindMarkTips(container);
}

function renderHeatmap() {
  const container = byId("analysisHeatmapChart");
  if (!container) return;
  const model = M.heatmapModel(state.analysis);
  if (!model.rows.length) { emptyChart(container, "No laps to map."); return; }
  const cell = Math.max(10, Math.min(26, Math.floor(560 / Math.max(1, model.total))));
  const left = 48;
  const width = left + model.total * (cell + 2) + 8;
  const height = model.rows.length * 20 + 28;
  const svg = svgRoot(width, height, "Lap-time heatmap: each lap against the driver's median");
  model.rows.forEach((row, r) => {
    const y = 4 + r * 20;
    svg.append(svgElement("text", { class: row.player ? "sa-label sa-label-player" : "sa-label", x: left - 8, y: y + 12, "text-anchor": "end" }, row.code));
    row.cells.forEach((c, i) => {
      if (c.fill === "transparent") return;
      const rect = svgElement("rect", { class: c.lap_id ? "sa-cell sa-cell-link" : "sa-cell", x: left + i * (cell + 2), y, width: cell, height: 16, rx: 2, fill: c.fill, "data-tip": c.text, "data-lap-id": c.lap_id || null, tabindex: c.lap_id ? 0 : null, role: c.lap_id ? "button" : null, "aria-label": c.lap_id ? `${c.text}. Open in Lap Lab` : null });
      svg.append(rect);
    });
  });
  for (const lap of M.lapAxisTicks(model.total)) svg.append(svgElement("text", { class: "sa-tick", x: left + (lap - 1) * (cell + 2) + cell / 2, y: height - 6, "text-anchor": "middle" }, String(lap)));
  container.replaceChildren(svg);
  bindMarkTips(container);
  const open = (event) => {
    const target = event.target?.closest?.("[data-lap-id]");
    if (!target) return;
    if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    openLapInLapLab(target.dataset.lapId);
  };
  svg.addEventListener("click", open);
  svg.addEventListener("keydown", open);
}

function renderFastest() {
  const container = byId("analysisFastestChart");
  if (!container) return;
  const model = M.fastestModel(state.analysis, { width: 640, rowHeight: 24 });
  if (!model.rows.length) { emptyChart(container, "No valid timed laps."); return; }
  const svg = svgRoot(model.width, model.height, "Best lap per driver behind the fastest; tick marks the best sectors combined");
  for (const row of model.rows) {
    const g = svgElement("g", { tabindex: 0, class: "sa-bar", "data-tip": `${row.code} · best ${M.formatLapTime(row.lap_time_ms)} (lap ${row.lap_number})${row.ideal_lap_ms ? ` · ideal ${M.formatLapTime(row.ideal_lap_ms)}` : ""}` });
    g.append(svgElement("text", { class: row.player ? "sa-label sa-label-player" : "sa-label", x: model.left - 8, y: row.y + 12, "text-anchor": "end" }, row.code));
    g.append(svgElement("rect", { x: model.left, y: row.y + 2, width: Math.max(3, row.barEnd - model.left), height: 14, rx: 4, fill: row.colour }));
    if (row.idealX !== null) g.append(svgElement("line", { class: "sa-ideal", x1: row.idealX, x2: row.idealX, y1: row.y, y2: row.y + 18 }));
    g.append(svgElement("text", { class: "sa-value", x: row.barEnd + 8, y: row.y + 13 }, row.label));
    svg.append(g);
  }
  container.replaceChildren(svg);
  bindMarkTips(container);
  placeTable("analysisFastestTable", dataTable("Fastest and ideal laps", ["Driver", "Best lap", "Lap", "Behind fastest", "Ideal lap"], model.rows.map((r) => [r.code, M.formatLapTime(r.lap_time_ms), String(r.lap_number), r.delta_to_fastest_ms ? `+${(r.delta_to_fastest_ms / 1000).toFixed(3)} s` : "fastest", M.formatLapTime(r.ideal_lap_ms)])));
}

function renderStops() {
  const body = byId("analysisStopsRows");
  if (!body) return;
  const rows = M.pitStopRows(state.analysis);
  body.replaceChildren();
  if (!rows.length) {
    const tr = element("tr"); const td = element("td", "empty", "No pit stops or tyre changes were recorded."); td.colSpan = 5; tr.append(td); body.append(tr); return;
  }
  for (const stop of rows) {
    const tr = element("tr");
    [stop.code, String(stop.lap_number), stop.tyres, stop.kindText, stop.lossText].forEach((value) => tr.append(element("td", "", value)));
    body.append(tr);
  }
}

// ------------------------------------------------------------- timelapse

function renderLapse() {
  const container = byId("analysisLapseChart");
  if (!container) return;
  const frame = M.frameAt(state.lapse.frames, state.lapse.t);
  const range = byId("analysisLapseRange");
  const label = byId("analysisLapseLabel");
  const total = state.lapse.frames.length;
  if (range && document.activeElement !== range) range.value = String(Math.round(state.lapse.t));
  if (label) label.textContent = total ? `Lap ${Math.min(total, Math.floor(state.lapse.t))} of ${total}${frame.suspended ? " · suspended" : frame.neutralised ? " · safety car" : ""}` : "No laps";
  if (!frame.entries.length) { emptyChart(container, "No running order to replay."); return; }
  const styles = M.driverStyles(state.analysis);
  const width = state.compact ? 360 : 1200;
  const rowHeight = 22;
  const rows = Math.max(...state.lapse.frames.map((f) => f.entries.length), 1);
  const height = rows * rowHeight + 30;
  const left = state.compact ? 86 : 104;
  const maxGap = Math.max(10, ...frame.entries.map((e) => (e.gap_ms || 0) / 1000));
  const span = Math.min(Math.ceil(maxGap / 10) * 10, 120);
  const x = M.scale(0, span, left, width - (state.compact ? 60 : 90));
  let svg = container.querySelector("svg");
  if (!svg || svg.dataset.rows !== String(rows) || svg.dataset.width !== String(width)) {
    svg = svgRoot(width, height, "Race timelapse: running order and gap to the leader");
    svg.dataset.rows = String(rows); svg.dataset.width = String(width);
    container.replaceChildren(svg);
  }
  svg.replaceChildren();
  if (frame.suspended) {
    svg.append(svgElement("text", { class: "sa-suspended-note", x: (left + width) / 2, y: Math.min(height / 2, 120), "text-anchor": "middle" }, "Race suspended · order frozen"));
  }
  for (const tick of frame.suspended ? [] : M.niceTicks(0, span, state.compact ? 3 : 6)) {
    svg.append(svgElement("line", { class: "sa-grid", x1: x(tick), x2: x(tick), y1: 8, y2: height - 22 }));
    svg.append(svgElement("text", { class: "sa-tick", x: x(tick), y: height - 6, "text-anchor": "middle" }, tick === 0 ? "Leader" : `+${tick} s`));
  }
  for (const entry of frame.entries) {
    const y = 10 + (entry.position - 1) * rowHeight;
    const colour = styles.get(entry.car_index)?.colour || M.CONTEXT_STROKE;
    const gap = entry.gap_ms == null ? null : entry.gap_ms / 1000;
    const cx = gap == null ? left : x(Math.min(span, gap));
    const driver = drivers().get(entry.car_index) || {};
    const compound = M.compoundInfo(entry.compound);
    svg.append(svgElement("text", { class: "sa-tick", x: 32, y: y + 4, "text-anchor": "end" }, `P${Math.round(entry.position)}`));
    svg.append(svgElement("text", { class: driver.is_player ? "sa-label sa-label-player" : "sa-label", x: left - 10, y: y + 4, "text-anchor": "end" }, driver.code || ""));
    svg.append(svgElement("line", { class: "sa-lapse-trail", x1: left, x2: cx, y1: y, y2: y, stroke: colour }));
    svg.append(svgElement("circle", { class: "sa-dot", cx, cy: y, r: 7, fill: colour, "data-tip": `${driver.code} · P${Math.round(entry.position)} · ${gap == null ? (frame.suspended ? "suspended" : "gap unknown") : M.formatGap(entry.gap_ms)} · ${compound.label}` }));
    svg.append(svgElement("rect", { x: cx + 11, y: y - 7, width: 16, height: 14, rx: 3, fill: compound.fill }));
    svg.append(svgElement("text", { class: "sa-stint-letter", x: cx + 19, y: y + 4, fill: compound.ink, "text-anchor": "middle" }, compound.letter));
    if (!state.compact || entry.position <= 3) svg.append(svgElement("text", { class: "sa-value", x: cx + 33, y: y + 4 }, gap == null ? "" : gap === 0 ? "Leader" : `+${gap.toFixed(1)}`));
  }
  bindMarkTips(container);
}

function stopLapse() {
  state.lapse.playing = false;
  if (state.lapse.raf && HAS_DOM) cancelAnimationFrame(state.lapse.raf);
  state.lapse.raf = 0;
  const button = byId("analysisLapsePlay");
  if (button) { button.textContent = "Play"; button.setAttribute("aria-pressed", "false"); }
}

function stepLapse(now) {
  if (!state.lapse.playing) return;
  const elapsed = state.lapse.last ? (now - state.lapse.last) / 1000 : 0;
  state.lapse.last = now;
  state.lapse.t = Math.min(state.lapse.frames.length, state.lapse.t + elapsed * state.lapse.speed);
  renderLapse();
  if (state.lapse.t >= state.lapse.frames.length) { stopLapse(); return; }
  state.lapse.raf = requestAnimationFrame(stepLapse);
}

function toggleLapse() {
  if (state.lapse.playing) { stopLapse(); return; }
  if (!state.lapse.frames.length) return;
  if (state.lapse.t >= state.lapse.frames.length) state.lapse.t = 1;
  state.lapse.playing = true;
  state.lapse.last = 0;
  const button = byId("analysisLapsePlay");
  if (button) { button.textContent = "Pause"; button.setAttribute("aria-pressed", "true"); }
  // Honour reduced motion: step a lap at a time instead of animating.
  if (HAS_DOM && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    const tick = () => {
      if (!state.lapse.playing) return;
      state.lapse.t = Math.min(state.lapse.frames.length, Math.floor(state.lapse.t) + 1);
      renderLapse();
      if (state.lapse.t >= state.lapse.frames.length) stopLapse();
      else setTimeout(tick, 1000 / state.lapse.speed);
    };
    setTimeout(tick, 1000 / state.lapse.speed);
    return;
  }
  state.lapse.raf = requestAnimationFrame(stepLapse);
}

// ------------------------------------------------------------- page

function renderHeader() {
  const head = M.headline(state.analysis);
  byId("analysisTitle").textContent = head.title;
  byId("analysisSubtitle").textContent = head.subtitle || "";
  const kpis = byId("analysisKpis");
  kpis.replaceChildren(...head.kpis.map((kpi) => {
    const card = element("div", "summary-metric sa-kpi");
    card.append(element("span", "", kpi.label), element("strong", "", kpi.value), element("small", "availability-note", kpi.detail || ""));
    return card;
  }));
}

function renderFocus() {
  const container = byId("analysisFocus");
  const add = byId("analysisAddDriver");
  if (!container || !add) return;
  const styles = M.driverStyles(state.analysis);
  container.replaceChildren(...state.focus.map((carIndex) => {
    const driver = drivers().get(carIndex) || {};
    const chip = element("button", "sa-chip");
    chip.type = "button";
    chip.setAttribute("aria-label", `Remove ${driver.code || carIndex} from focus`);
    const key = element("span", "sa-key-line");
    key.style.background = styles.get(carIndex)?.colour || M.CONTEXT_STROKE;
    if (styles.get(carIndex)?.dashed) key.classList.add("sa-key-dashed");
    chip.append(key, element("span", "", `${driver.code || carIndex}${driver.is_player ? " · you" : ""}`), element("span", "sa-chip-x", "×"));
    chip.addEventListener("click", () => { state.focus = state.focus.filter((c) => c !== carIndex); renderFocusDependent(); });
    return chip;
  }));
  add.replaceChildren(new Option(state.focus.length >= MAX_FOCUS ? `Focus is full (${MAX_FOCUS})` : "Add driver…", ""));
  for (const driver of state.analysis?.drivers || []) {
    if (state.focus.includes(driver.car_index)) continue;
    add.append(new Option(`${driver.code} · ${driver.display_name}${driver.finish_position ? ` · P${driver.finish_position}` : ""}`, String(driver.car_index)));
  }
  add.disabled = state.focus.length >= MAX_FOCUS;
  const reference = byId("analysisReference");
  if (reference) {
    const previous = state.reference;
    reference.replaceChildren(new Option("Race leader", "leader"), ...(state.analysis?.drivers || []).filter((d) => d.laps_completed).map((d) => new Option(`${d.code}${d.is_player ? " (you)" : ""}`, String(d.car_index))));
    reference.value = [...reference.options].some((o) => o.value === String(previous)) ? String(previous) : "leader";
    reference.disabled = !state.analysis?.session?.is_race;
  }
}

function renderFocusDependent() {
  renderFocus();
  if (!state.analysis?.session?.is_race) return;
  renderTrace();
  renderPositions();
  renderLapTimes();
}

function renderAll() {
  if (!state.analysis) return;
  const isRace = Boolean(state.analysis.session?.is_race);
  document.querySelectorAll("[data-analysis-race-only]").forEach((node) => { node.hidden = !isRace; });
  const note = byId("analysisRaceOnlyNote");
  if (note) note.hidden = isRace;
  renderHeader();
  renderFocus();
  renderPace();
  renderStrategy();
  renderHeatmap();
  renderFastest();
  renderStops();
  if (isRace) {
    renderTrace();
    renderPositions();
    renderLapTimes();
    state.lapse.frames = M.timelapseFrames(state.analysis);
    state.lapse.t = state.lapse.frames.length || 1;
    const range = byId("analysisLapseRange");
    if (range) { range.max = String(Math.max(1, state.lapse.frames.length)); range.value = range.max; range.disabled = !state.lapse.frames.length; }
    renderLapse();
  } else {
    renderLapTimesForPractice();
  }
  const warnings = state.analysis.warnings || [];
  setStatus(warnings.length ? warnings.join(" ") : `Analysis of ${state.analysis.drivers.length} cars and ${state.analysis.laps.length} laps.`, warnings.length ? "" : "success");
}

// Outside a race the lap-time chart is still the most useful view; it uses
// the same model with the current focus.
function renderLapTimesForPractice() {
  renderLapTimes();
}

function openLapInLapLab(lapId) {
  if (!lapId || !HAS_DOM) return;
  stopLapse();
  window.dispatchEvent(new CustomEvent("pitwall:open-lap", { detail: { sessionId: state.sessionId, lapId } }));
}

function populateSessions() {
  const select = byId("analysisSessionSelect");
  if (!select) return;
  const current = state.sessionId;
  select.replaceChildren(new Option("Choose a saved session", ""));
  for (const session of state.sessions) {
    const label = [session.display_name || session.track_name || `Track ${session.track_id ?? "?"}`, session.session_type, session.started_at ? new Date(session.started_at).toLocaleDateString() : null].filter(Boolean).join(" · ");
    select.append(new Option(label, session.id));
  }
  if (current && ![...select.options].some((o) => o.value === current)) select.append(new Option(current, current));
  select.value = current;
}

async function loadSessions() {
  try {
    const payload = await api("/sessions?limit=50");
    state.sessions = payload?.items || [];
    populateSessions();
  } catch (error) {
    setStatus(`Saved sessions are unavailable: ${error instanceof Error ? error.message : error}`, "error");
  }
}

export async function openAnalysis(sessionId) {
  if (!HAS_DOM) return;
  stopLapse();
  state.sessionId = sessionId || "";
  try { if (sessionId) localStorage.setItem(STORAGE_SESSION, sessionId); } catch { /* storage may be unavailable */ }
  populateSessions();
  const request = ++state.request;
  const root = byId("session-analysis");
  if (!sessionId) {
    state.analysis = null;
    root?.classList.remove("sa-loaded");
    setStatus("Choose a saved session, or press Analyze session in Library or Session Review.");
    return;
  }
  root?.classList.add("sa-loading");
  setStatus("Analysing the session…");
  try {
    const query = new URLSearchParams({ hide_outliers: String(state.hideOutliers) });
    const analysis = await api(`/sessions/${encodeURIComponent(sessionId)}/analysis?${query}`);
    if (request !== state.request) return;
    const sameSession = state.analysis?.session_id === analysis.session_id;
    state.analysis = analysis;
    const known = new Set((analysis.drivers || []).map((d) => d.car_index));
    state.focus = sameSession ? state.focus.filter((c) => known.has(c)) : M.defaultFocus(analysis);
    if (!sameSession) state.reference = "leader";
    root?.classList.add("sa-loaded");
    renderAll();
  } catch (error) {
    if (request !== state.request) return;
    state.analysis = null;
    root?.classList.remove("sa-loaded");
    setStatus(error instanceof Error ? error.message : String(error), "error");
  } finally {
    if (request === state.request) root?.classList.remove("sa-loading");
  }
}

function watchWidth() {
  const root = byId("session-analysis");
  if (!root || typeof ResizeObserver === "undefined") { state.compact = (window.innerWidth || 1200) < COMPACT_WIDTH; return; }
  const observer = new ResizeObserver((entries) => {
    const width = entries[0]?.contentRect?.width || 0;
    if (!width) return;
    const compact = width < COMPACT_WIDTH;
    if (compact === state.compact) return;
    state.compact = compact;
    if (state.analysis) { renderPace(); renderStrategy(); if (state.analysis.session?.is_race) renderLapse(); }
  });
  observer.observe(root);
}

function bind() {
  byId("analysisSessionSelect")?.addEventListener("change", (event) => openAnalysis(event.target.value));
  byId("analysisRefresh")?.addEventListener("click", () => openAnalysis(state.sessionId));
  byId("analysisAddDriver")?.addEventListener("change", (event) => {
    const value = event.target.value;
    if (value === "") return;
    const carIndex = Number(value);
    if (!state.focus.includes(carIndex) && state.focus.length < MAX_FOCUS) state.focus = [...state.focus, carIndex];
    renderFocusDependent();
  });
  byId("analysisResetFocus")?.addEventListener("click", () => { state.focus = M.defaultFocus(state.analysis); renderFocusDependent(); });
  byId("analysisHideOutliers")?.addEventListener("change", (event) => { state.hideOutliers = Boolean(event.target.checked); openAnalysis(state.sessionId); });
  byId("analysisReference")?.addEventListener("change", (event) => { state.reference = event.target.value === "leader" ? "leader" : Number(event.target.value); renderTrace(); });
  byId("analysisLapsePlay")?.addEventListener("click", toggleLapse);
  byId("analysisLapseRange")?.addEventListener("input", (event) => { stopLapse(); state.lapse.t = Number(event.target.value) || 1; renderLapse(); });
  byId("analysisLapseSpeed")?.addEventListener("change", (event) => { state.lapse.speed = Number(event.target.value) || 1; });
  byId("analysisOpenBest")?.addEventListener("click", () => {
    const player = (state.analysis?.drivers || []).find((d) => d.is_player) || (state.analysis?.drivers || [])[0];
    if (player?.best_lap_id) openLapInLapLab(player.best_lap_id);
  });
  window.addEventListener("pitwall:analyze-session", (event) => {
    const sessionId = event.detail?.sessionId;
    if (!sessionId) return;
    openAnalysis(sessionId);
  });
  // Leaving the view stops the animation: no hidden work on a tablet.
  window.addEventListener("pitwall:pagechange", (event) => {
    if (event.detail?.page !== "session-analysis") { stopLapse(); return; }
    if (!state.sessions.length) loadSessions();
    if (!state.sessionId) {
      let remembered = "";
      try { remembered = localStorage.getItem(STORAGE_SESSION) || ""; } catch { remembered = ""; }
      if (remembered) openAnalysis(remembered);
    }
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopLapse(); });
}

if (HAS_DOM && byId("session-analysis")) {
  watchWidth();
  bind();
}

export const __test = { state };
