// Pure chart models for the Session Analysis view.
// Everything here turns the /api/v1/sessions/{id}/analysis payload into plain
// numbers and strings; nothing touches the DOM, so Node tests cover it.

export const COMPOUNDS = Object.freeze({
  SOFT: { fill: "#ff6969", ink: "#070b0f", letter: "S", label: "Soft" },
  MEDIUM: { fill: "#ffc15c", ink: "#070b0f", letter: "M", label: "Medium" },
  HARD: { fill: "#e6edf3", ink: "#070b0f", letter: "H", label: "Hard" },
  INTER: { fill: "#49d17d", ink: "#070b0f", letter: "I", label: "Intermediate" },
  WET: { fill: "#3f86ff", ink: "#ffffff", letter: "W", label: "Wet" },
  UNKNOWN: { fill: "#2a3744", ink: "#93a4b5", letter: "?", label: "No tyre data" },
});

// Team colours by the names the backend reports (identity.TEAM_NAMES).
const TEAM_COLOURS = {
  mercedes: "#27F4D2", ferrari: "#E8002D", "red bull racing": "#3671C6", williams: "#64C4FF",
  "aston martin": "#229971", alpine: "#FF87BC", rb: "#6692FF", haas: "#B6BABD", mclaren: "#FF8000",
  sauber: "#52E252", audi: "#52E252", cadillac: "#D4AF37",
};
// The 2026 season pack reports team ids 476-486, which the UDP table in use
// does not name. Their rosters follow the F1 25 team order offset by 476 with
// Cadillac last, so the ids are used for colour only - never shown as names.
const TEAM_ID_COLOURS = {
  0: "#27F4D2", 1: "#E8002D", 2: "#3671C6", 3: "#64C4FF", 4: "#229971", 5: "#FF87BC", 6: "#6692FF",
  7: "#B6BABD", 8: "#FF8000", 9: "#52E252",
  476: "#27F4D2", 477: "#E8002D", 478: "#3671C6", 479: "#64C4FF", 480: "#229971", 481: "#FF87BC",
  482: "#6692FF", 483: "#B6BABD", 484: "#FF8000", 485: "#52E252", 486: "#D4AF37",
};
const FALLBACK_COLOURS = ["#9b8cff", "#ff9f6e", "#4fd1c5", "#f6c453", "#e879f9", "#7dd3fc", "#a3e635", "#fb7185"];
export const CONTEXT_STROKE = "#33414e";
export const SURFACE = "#121b24";

export function compoundInfo(compound) {
  return COMPOUNDS[String(compound || "").toUpperCase()] || COMPOUNDS.UNKNOWN;
}

export function formatLapTime(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(Number(ms)) || Number(ms) <= 0) return "—";
  const value = Math.round(Number(ms));
  const minutes = Math.floor(value / 60000);
  const seconds = Math.floor((value % 60000) / 1000);
  const millis = value % 1000;
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

export function formatGap(ms, { leader = "Leader" } = {}) {
  if (ms === null || ms === undefined || !Number.isFinite(Number(ms))) return "—";
  if (Number(ms) === 0) return leader;
  const seconds = Number(ms) / 1000;
  return `${seconds > 0 ? "+" : "−"}${Math.abs(seconds).toFixed(3)} s`;
}

export function scale(domain0, domain1, range0, range1) {
  const span = domain1 - domain0 || 1;
  return (value) => range0 + ((value - domain0) / span) * (range1 - range0);
}

// Round tick steps (1, 2, 5 x 10^n) that cover [min, max] with about `count` ticks.
export function niceTicks(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (max < min) [min, max] = [max, min];
  if (max === min) return [min];
  const raw = (max - min) / Math.max(1, count);
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const ratio = raw / magnitude;
  const step = (ratio < 1.5 ? 1 : ratio < 3 ? 2 : ratio < 7 ? 5 : 10) * magnitude;
  const ticks = [];
  for (let value = Math.ceil(min / step) * step; value <= max + step * 1e-9; value += step) {
    ticks.push(Number(value.toFixed(10)));
  }
  return ticks;
}

export function driversByIndex(analysis) {
  return new Map((analysis?.drivers || []).map((driver) => [driver.car_index, driver]));
}

// Colour per car: team colour where known; teammates share it and the second
// car is drawn dashed. Unknown teams take fallback colours in a fixed order.
export function driverStyles(analysis) {
  const styles = new Map();
  const seenTeams = new Map();
  let fallback = 0;
  const unknownTeamColour = new Map();
  for (const driver of analysis?.drivers || []) {
    const name = String(driver.team_name || "").toLowerCase().replace(/ '\d\d$/, "");
    let colour = TEAM_COLOURS[name] || TEAM_ID_COLOURS[driver.team_id];
    const teamKey = driver.team_id ?? `car-${driver.car_index}`;
    if (!colour) {
      if (!unknownTeamColour.has(teamKey)) unknownTeamColour.set(teamKey, FALLBACK_COLOURS[fallback++ % FALLBACK_COLOURS.length]);
      colour = unknownTeamColour.get(teamKey);
    }
    const teammateIndex = seenTeams.get(teamKey) || 0;
    seenTeams.set(teamKey, teammateIndex + 1);
    styles.set(driver.car_index, { colour, dashed: teammateIndex > 0, dash: teammateIndex > 0 ? "7 5" : "" });
  }
  return styles;
}

export function lapLookup(analysis) {
  const byCar = new Map();
  for (const lap of analysis?.laps || []) {
    if (!byCar.has(lap.car_index)) byCar.set(lap.car_index, new Map());
    byCar.get(lap.car_index).set(lap.lap_number, lap);
  }
  return byCar;
}

export function lastLap(analysis) {
  return (analysis?.laps || []).reduce((max, lap) => Math.max(max, lap.lap_number || 0), 0);
}

// The player plus the cars that finished around them; without a player, the
// first four finishers. Never more than `limit`.
export function defaultFocus(analysis, limit = 4) {
  const drivers = (analysis?.drivers || []).slice();
  const placed = drivers.filter((d) => d.finish_position).sort((a, b) => a.finish_position - b.finish_position);
  const ordered = placed.length ? placed : drivers;
  const player = drivers.find((d) => d.is_player);
  if (!player) return ordered.slice(0, limit).map((d) => d.car_index);
  const at = ordered.findIndex((d) => d.car_index === player.car_index);
  if (at < 0) return [player.car_index, ...ordered.filter((d) => !d.is_player).slice(0, limit - 1).map((d) => d.car_index)];
  const picked = [player.car_index];
  for (let step = 1; picked.length < limit && (at - step >= 0 || at + step < ordered.length); step += 1) {
    if (at - step >= 0) picked.push(ordered[at - step].car_index);
    if (picked.length < limit && at + step < ordered.length) picked.push(ordered[at + step].car_index);
  }
  return picked;
}

// Shaded lap bands: safety-car (neutralised) laps and suspended laps.
export function lapBands(analysis, x) {
  const bands = [];
  const runs = (laps) => {
    const sorted = [...new Set(laps)].sort((a, b) => a - b);
    const out = [];
    for (const lap of sorted) {
      const last = out[out.length - 1];
      if (last && lap === last[1] + 1) last[1] = lap;
      else out.push([lap, lap]);
    }
    return out;
  };
  for (const [first, last] of runs(analysis?.neutralised_laps || [])) {
    bands.push({ kind: "neutralised", first, last, x: x(first - 0.5), width: x(last + 0.5) - x(first - 0.5), label: "SC" });
  }
  for (const [first, last] of runs(analysis?.suspended_laps || [])) {
    bands.push({ kind: "suspended", first, last, x: x(first - 0.5), width: x(last + 0.5) - x(first - 0.5), label: "Red flag" });
  }
  return bands;
}

export function lapAxisTicks(total) {
  if (total <= 1) return [1];
  const step = total <= 12 ? 1 : total <= 40 ? 5 : total <= 80 ? 10 : 20;
  const ticks = [1];
  for (let lap = step; lap < total; lap += step) if (lap - ticks[ticks.length - 1] >= step / 2) ticks.push(lap);
  // The last lap is always labelled; a round tick crowding it gives way.
  if (ticks.length > 1 && total - ticks[ticks.length - 1] < step / 2) ticks.pop();
  ticks.push(total);
  return ticks;
}

// Race pace box plot. Drivers with fewer than `minLaps` representative laps
// are listed, not boxed: a box of one lap says nothing about spread.
export function racePaceModel(analysis, { width = 1200, height = 340, minLaps = 5, horizontal = false } = {}) {
  const drivers = driversByIndex(analysis);
  const styles = driverStyles(analysis);
  const rows = (analysis?.race_pace || []).filter((row) => row.n >= minLaps);
  const thin = (analysis?.race_pace || []).filter((row) => row.n < minLaps).map((row) => ({
    car_index: row.car_index, code: drivers.get(row.car_index)?.code || `#${row.car_index}`, n: row.n,
  }));
  if (!rows.length) return { boxes: [], ticks: [], thin, domain: null };
  const low = Math.min(...rows.map((r) => r.whisker_low_ms));
  const high = Math.max(...rows.map((r) => r.whisker_high_ms));
  const pad = Math.max(200, (high - low) * 0.06);
  const domain = [low - pad, high + pad];
  const margin = horizontal ? { l: 52, r: 16, t: 8, b: 28 } : { l: 64, r: 12, t: 12, b: 32 };
  const tickValues = niceTicks(domain[0] / 1000, domain[1] / 1000, horizontal ? 4 : 6).map((s) => s * 1000);
  if (horizontal) {
    const rowHeight = 26;
    const h = margin.t + rows.length * rowHeight + margin.b;
    const x = scale(domain[0], domain[1], margin.l, width - margin.r);
    return {
      width, height: h, horizontal: true, domain, thin,
      ticks: tickValues.map((v) => ({ value: v, pos: x(v), label: formatLapTime(v).replace(/0+$/, "").replace(/\.$/, "") })),
      boxes: rows.map((row, i) => {
        const y = margin.t + i * rowHeight;
        const driver = drivers.get(row.car_index) || {};
        return {
          car_index: row.car_index, code: driver.code, player: Boolean(driver.is_player), colour: styles.get(row.car_index)?.colour || CONTEXT_STROKE,
          cross: y + 7, crossStart: y, crossEnd: y + 14,
          q1: x(row.q1_ms), q3: x(row.q3_ms), median: x(row.median_ms), whiskerLow: x(row.whisker_low_ms), whiskerHigh: x(row.whisker_high_ms),
          label: { x: margin.l - 8, y: y + 11 }, row,
          outliers: (row.outliers || []).map((o) => ({ pos: x(o.lap_time_ms), lap: o.lap_number, ms: o.lap_time_ms })),
        };
      }),
    };
  }
  const y = scale(domain[1], domain[0], margin.t, height - margin.b);
  const band = (width - margin.l - margin.r) / rows.length;
  const boxWidth = Math.min(24, band * 0.6);
  return {
    width, height, horizontal: false, domain, thin,
    ticks: tickValues.map((v) => ({ value: v, pos: y(v), label: formatLapTime(v).replace(/0+$/, "").replace(/\.$/, "") })),
    plot: { left: margin.l, right: width - margin.r, top: margin.t, bottom: height - margin.b },
    boxes: rows.map((row, i) => {
      const centre = margin.l + band * (i + 0.5);
      const driver = drivers.get(row.car_index) || {};
      return {
        car_index: row.car_index, code: driver.code, player: Boolean(driver.is_player), colour: styles.get(row.car_index)?.colour || CONTEXT_STROKE,
        cross: centre, crossStart: centre - boxWidth / 2, crossEnd: centre + boxWidth / 2,
        q1: y(row.q1_ms), q3: y(row.q3_ms), median: y(row.median_ms), whiskerLow: y(row.whisker_low_ms), whiskerHigh: y(row.whisker_high_ms),
        label: { x: centre, y: height - margin.b + 18 }, row,
        outliers: (row.outliers || []).map((o) => ({ pos: y(o.lap_time_ms), lap: o.lap_number, ms: o.lap_time_ms })),
      };
    }),
  };
}

// Gap per lap to a reference: the race leader, or a chosen car (within the
// same racing segment, so a restart never compares across the red flag).
export function gapSeries(analysis, reference = "leader") {
  const laps = lapLookup(analysis);
  const series = new Map();
  for (const [carIndex, byLap] of laps) {
    const points = [];
    for (const [lapNumber, lap] of [...byLap.entries()].sort((a, b) => a[0] - b[0])) {
      if (lap.suspended) continue;
      let gap = null;
      if (reference === "leader") gap = lap.gap_to_leader_ms;
      else {
        const ref = laps.get(Number(reference))?.get(lapNumber);
        if (ref && lap.segment_time_ms != null && ref.segment_time_ms != null) gap = lap.segment_time_ms - ref.segment_time_ms;
      }
      if (gap !== null && gap !== undefined) points.push({ lap: lapNumber, gap });
    }
    series.set(carIndex, points);
  }
  return series;
}

function segmentOf(analysis, lapNumber) {
  return (analysis?.segments || []).findIndex((s) => lapNumber >= s.first_lap && lapNumber <= s.last_lap);
}

// Split a lap series into runs that never cross a suspension or a missing lap.
export function continuousRuns(analysis, points) {
  const runs = [];
  let current = [];
  let previous = null;
  for (const point of points) {
    const breakHere = previous && (point.lap !== previous.lap + 1 || segmentOf(analysis, point.lap) !== segmentOf(analysis, previous.lap));
    if (breakHere) { if (current.length) runs.push(current); current = []; }
    current.push(point);
    previous = point;
  }
  if (current.length) runs.push(current);
  return runs;
}

export function raceTraceModel(analysis, { focus = [], reference = "leader", width = 640, height = 320, clipSeconds = null } = {}) {
  const total = lastLap(analysis);
  const styles = driverStyles(analysis);
  const drivers = driversByIndex(analysis);
  const series = gapSeries(analysis, reference);
  const values = [...series.values()].flat().map((p) => p.gap / 1000);
  const focusValues = [...series.entries()].filter(([car]) => focus.includes(car)).flatMap(([, pts]) => pts.map((p) => p.gap / 1000));
  const minGap = Math.min(0, ...values);
  let maxGap = Math.max(1, ...values);
  // Lapped and stopped cars stretch the axis; clip to what the focus needs.
  const clip = clipSeconds ?? Math.max(10, Math.ceil((Math.max(1, ...focusValues, 0) * 1.25) / 5) * 5);
  maxGap = Math.min(maxGap, clip);
  const margin = { l: 56, r: 12, t: 12, b: 28 };
  const x = scale(1, Math.max(2, total), margin.l, width - margin.r);
  const y = scale(Math.max(-clip, minGap), maxGap, margin.t, height - margin.b);
  // Values beyond the axis are drawn where they fall and clipped to the plot
  // by the renderer; clamping them would draw a flat line that reads as data.
  const toPoints = (run) => run.map((p) => `${x(p.lap).toFixed(1)},${y(p.gap / 1000).toFixed(1)}`).join(" ");
  const lines = [];
  for (const [carIndex, points] of series) {
    const style = styles.get(carIndex) || { colour: CONTEXT_STROKE, dash: "" };
    lines.push({
      car_index: carIndex, code: drivers.get(carIndex)?.code, focus: focus.includes(carIndex),
      colour: focus.includes(carIndex) ? style.colour : CONTEXT_STROKE, dash: focus.includes(carIndex) ? style.dash : "",
      runs: continuousRuns(analysis, points).map(toPoints).filter((s) => s.includes(" ")),
      singles: continuousRuns(analysis, points).filter((r) => r.length === 1 && r[0].gap / 1000 <= maxGap).map((r) => ({ x: x(r[0].lap), y: y(r[0].gap / 1000) })),
    });
  }
  lines.sort((a, b) => Number(a.focus) - Number(b.focus));
  return {
    width, height, clip: maxGap,
    plot: { left: margin.l, right: width - margin.r, top: margin.t, bottom: height - margin.b },
    bands: lapBands(analysis, x).map((b) => ({ ...b, y: margin.t, height: height - margin.t - margin.b })),
    yTicks: niceTicks(Math.max(-clip, minGap), maxGap, 5).map((s) => ({ pos: y(s), label: s === 0 ? (reference === "leader" ? "Leader" : drivers.get(Number(reference))?.code || "Ref") : `${s > 0 ? "+" : "−"}${Math.abs(s)} s` })),
    xTicks: lapAxisTicks(total).map((lap) => ({ pos: x(lap), label: String(lap) })),
    lines,
  };
}

export function positionsModel(analysis, { focus = [], width = 640, height = 360 } = {}) {
  const total = lastLap(analysis);
  const styles = driverStyles(analysis);
  const drivers = driversByIndex(analysis);
  const laps = lapLookup(analysis);
  const field = Math.max(2, ...(analysis?.laps || []).map((l) => l.position || 0));
  const margin = { l: 36, r: 84, t: 12, b: 28 };
  const x = scale(1, Math.max(2, total), margin.l, width - margin.r);
  const y = scale(1, field, margin.t, height - margin.b);
  const lines = [];
  const labels = [];
  for (const [carIndex, byLap] of laps) {
    const points = [...byLap.values()].filter((l) => l.position).sort((a, b) => a.lap_number - b.lap_number).map((l) => ({ lap: l.lap_number, position: l.position }));
    if (!points.length) continue;
    const style = styles.get(carIndex) || { colour: CONTEXT_STROKE, dash: "" };
    const isFocus = focus.includes(carIndex);
    const runs = [];
    let current = [];
    for (const point of points) {
      if (current.length && point.lap !== current[current.length - 1].lap + 1) { runs.push(current); current = []; }
      current.push(point);
    }
    runs.push(current);
    lines.push({
      car_index: carIndex, focus: isFocus, colour: isFocus ? style.colour : CONTEXT_STROKE, dash: isFocus ? style.dash : "",
      runs: runs.filter((r) => r.length > 1).map((r) => r.map((p) => `${x(p.lap).toFixed(1)},${y(p.position).toFixed(1)}`).join(" ")),
    });
    const last = points[points.length - 1];
    const driver = drivers.get(carIndex) || {};
    labels.push({
      car_index: carIndex, focus: isFocus, player: Boolean(driver.is_player), colour: isFocus ? style.colour : "#5d6d7c",
      x: x(last.lap), y: y(last.position),
      text: last.lap === total ? `P${last.position} ${driver.code}` : `${driver.code} · out L${last.lap}`,
    });
  }
  lines.sort((a, b) => Number(a.focus) - Number(b.focus));
  return {
    width, height,
    plot: { left: margin.l, right: width - margin.r, top: margin.t, bottom: height - margin.b },
    bands: lapBands(analysis, x).map((b) => ({ ...b, y: margin.t, height: height - margin.t - margin.b })),
    xTicks: lapAxisTicks(total).map((lap) => ({ pos: x(lap), label: String(lap) })),
    yTicks: [1, ...niceTicks(1, field, 4).filter((p) => p > 1 && Number.isInteger(p)), field].filter((v, i, a) => a.indexOf(v) === i).map((p) => ({ pos: y(p), label: `P${p}` })),
    lines, labels,
  };
}

export function strategyModel(analysis, { width = 1200, rowHeight = 24, minLabelWidth = 20 } = {}) {
  const total = Math.max(1, lastLap(analysis));
  const drivers = analysis?.drivers || [];
  const stints = new Map((analysis?.stints || []).map((row) => [row.car_index, row.stints || []]));
  const margin = { l: 56, r: 12, t: 6, b: 28 };
  const x = scale(0, total, margin.l, width - margin.r);
  const rows = drivers.map((driver, i) => {
    const y = margin.t + i * rowHeight;
    return {
      car_index: driver.car_index, code: driver.code, player: Boolean(driver.is_player), y, labelY: y + rowHeight / 2 + 4,
      stints: (stints.get(driver.car_index) || []).map((stint) => {
        const info = compoundInfo(stint.compound);
        const left = x(stint.start_lap - 1) + 1;
        const w = Math.max(2, x(stint.end_lap) - x(stint.start_lap - 1) - 2);
        return { ...stint, x: left, width: w, centre: left + w / 2, fill: info.fill, ink: info.ink, letter: w >= minLabelWidth ? info.letter : "", label: info.label };
      }),
    };
  });
  return {
    width, height: margin.t + rows.length * rowHeight + margin.b, rowHeight, rows,
    xTicks: lapAxisTicks(total).map((lap) => ({ pos: x(lap), label: String(lap) })),
  };
}

export function lapTimesModel(analysis, { focus = [], width = 640, height = 300 } = {}) {
  const total = lastLap(analysis);
  const styles = driverStyles(analysis);
  const drivers = driversByIndex(analysis);
  const racing = (analysis?.laps || []).filter((l) => focus.includes(l.car_index) && !l.pace_excluded && l.lap_time_ms);
  const margin = { l: 64, r: 12, t: 12, b: 28 };
  const x = scale(1, Math.max(2, total), margin.l, width - margin.r);
  if (!racing.length) return { width, height, series: [], yTicks: [], xTicks: [], bands: [], empty: true };
  const low = Math.min(...racing.map((l) => l.lap_time_ms));
  const high = Math.max(...racing.map((l) => l.lap_time_ms));
  const pad = Math.max(150, (high - low) * 0.08);
  const y = scale(high + pad, low - pad, height - margin.b, margin.t);
  const series = focus.map((carIndex) => {
    const style = styles.get(carIndex) || { colour: CONTEXT_STROKE, dash: "" };
    const points = racing.filter((l) => l.car_index === carIndex).sort((a, b) => a.lap_number - b.lap_number)
      .map((l) => ({ lap: l.lap_number, ms: l.lap_time_ms, x: x(l.lap_number), y: y(l.lap_time_ms), compound: l.compound, lap_id: l.lap_id }));
    const runs = continuousRuns(analysis, points).filter((r) => r.length > 1).map((r) => r.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" "));
    return { car_index: carIndex, code: drivers.get(carIndex)?.code, colour: style.colour, dash: style.dash, points, runs };
  });
  return {
    width, height, series,
    plot: { left: margin.l, right: width - margin.r, top: margin.t, bottom: height - margin.b },
    bands: lapBands(analysis, x).map((b) => ({ ...b, y: margin.t, height: height - margin.t - margin.b })),
    yTicks: niceTicks((low - pad) / 1000, (high + pad) / 1000, 5).map((s) => ({ pos: y(s * 1000), label: formatLapTime(s * 1000).replace(/0+$/, "").replace(/\.$/, "") })),
    xTicks: lapAxisTicks(total).map((lap) => ({ pos: x(lap), label: String(lap) })),
  };
}

// Diverging steps around a driver's own median: blue faster, orange slower,
// a neutral grey middle. Not-a-racing-lap cells are dark and say why.
export const HEAT_STEPS = Object.freeze([
  { max: -1.0, fill: "#2f6fd6", label: "1 s or more faster" },
  { max: -0.3, fill: "#5b8fe0", label: "0.3–1 s faster" },
  { max: 0.3, fill: "#2a3541", label: "within 0.3 s" },
  { max: 1.0, fill: "#d9893d", label: "0.3–1 s slower" },
  { max: Infinity, fill: "#f0a24a", label: "1 s or more slower" },
]);
export const HEAT_EXCLUDED = "#0d141b";

export function heatColour(deltaSeconds) {
  if (deltaSeconds === null || deltaSeconds === undefined || !Number.isFinite(deltaSeconds)) return HEAT_EXCLUDED;
  return HEAT_STEPS.find((step) => deltaSeconds < step.max || (step.max === -1.0 && deltaSeconds <= -1.0)).fill;
}

const EXCLUSION_TEXT = {
  lap_one: "lap 1", restart: "restart lap", suspended: "race suspended", pit: "pit lap", neutralised: "safety car",
  missing_time: "no lap time", outlier: "over 107% of median", invalid: "invalid lap",
};

export function heatmapModel(analysis) {
  const total = lastLap(analysis);
  const medians = new Map((analysis?.race_pace || []).map((row) => [row.car_index, row.median_ms]));
  const laps = lapLookup(analysis);
  return {
    total,
    rows: (analysis?.drivers || []).filter((d) => laps.has(d.car_index)).map((driver) => ({
      car_index: driver.car_index, code: driver.code, player: Boolean(driver.is_player),
      cells: Array.from({ length: total }, (_, i) => {
        const lap = laps.get(driver.car_index).get(i + 1);
        const median = medians.get(driver.car_index);
        if (!lap) return { lap: i + 1, fill: "transparent", text: "not driven", lap_id: null, delta: null };
        const delta = !lap.pace_excluded && lap.lap_time_ms && median ? (lap.lap_time_ms - median) / 1000 : null;
        const reason = lap.pace_excluded ? EXCLUSION_TEXT[lap.pace_excluded] || lap.pace_excluded : null;
        return {
          lap: i + 1, lap_id: lap.lap_id, delta, fill: heatColour(delta),
          text: `${driver.code} lap ${i + 1} · ${formatLapTime(lap.lap_time_ms)}${delta === null ? ` · ${reason || "no median"}` : ` · ${delta > 0 ? "+" : delta < 0 ? "−" : "±"}${Math.abs(delta).toFixed(3)} s vs median`}`,
        };
      }),
    })),
  };
}

// One frame per lap end: running order and gap to the leader. Suspended laps
// carry the frozen order with no gaps. frameAt() blends two laps for smooth
// playback; a car only moves between laps it actually completed.
export function timelapseFrames(analysis) {
  const total = lastLap(analysis);
  const laps = lapLookup(analysis);
  const stints = new Map((analysis?.stints || []).map((row) => [row.car_index, row.stints || []]));
  const compoundAt = (carIndex, lap) => (stints.get(carIndex) || []).find((s) => s.start_lap <= lap && lap <= s.end_lap)?.compound || null;
  const frames = [];
  for (let lap = 1; lap <= total; lap += 1) {
    const entries = [];
    for (const [carIndex, byLap] of laps) {
      const row = byLap.get(lap);
      if (!row || !row.position) continue;
      entries.push({ car_index: carIndex, position: row.position, gap_ms: row.gap_to_leader_ms, compound: compoundAt(carIndex, lap), pit: Boolean(row.pit) });
    }
    entries.sort((a, b) => a.position - b.position);
    frames.push({ lap, suspended: (analysis?.suspended_laps || []).includes(lap), neutralised: (analysis?.neutralised_laps || []).includes(lap), entries });
  }
  return frames;
}

export function frameAt(frames, t) {
  if (!frames.length) return { lap: 0, entries: [] };
  const clamped = Math.min(Math.max(t, 1), frames.length);
  const lower = frames[Math.floor(clamped) - 1];
  const upper = frames[Math.min(frames.length - 1, Math.floor(clamped))];
  const f = clamped - Math.floor(clamped);
  if (!f || lower === upper) return { ...lower, t: clamped };
  const next = new Map(upper.entries.map((e) => [e.car_index, e]));
  return {
    lap: lower.lap, t: clamped, suspended: lower.suspended || upper.suspended, neutralised: lower.neutralised,
    entries: lower.entries.filter((e) => next.has(e.car_index)).map((e) => {
      const n = next.get(e.car_index);
      const gap = e.gap_ms != null && n.gap_ms != null ? e.gap_ms + (n.gap_ms - e.gap_ms) * f : (f < 0.5 ? e.gap_ms : n.gap_ms);
      return { ...e, position: e.position + (n.position - e.position) * f, gap_ms: gap, compound: f < 0.5 ? e.compound : n.compound };
    }),
  };
}

export function fastestModel(analysis, { width = 640, rowHeight = 24 } = {}) {
  const drivers = driversByIndex(analysis);
  const styles = driverStyles(analysis);
  const rows = analysis?.fastest_laps || [];
  if (!rows.length) return { rows: [], width, height: 40 };
  const fastest = rows[0].lap_time_ms;
  const maxDelta = Math.max(500, ...rows.map((r) => r.delta_to_fastest_ms || 0), ...rows.map((r) => (r.ideal_lap_ms ? Math.max(0, r.ideal_lap_ms - fastest) : 0)));
  const margin = { l: 56, r: 96 };
  const x = scale(0, maxDelta, margin.l, width - margin.r);
  return {
    width, height: rows.length * rowHeight + 8,
    rows: rows.map((row, i) => {
      const driver = drivers.get(row.car_index) || {};
      return {
        ...row, code: driver.code, player: Boolean(driver.is_player), y: 4 + i * rowHeight, colour: styles.get(row.car_index)?.colour || CONTEXT_STROKE,
        barEnd: Math.max(x(row.delta_to_fastest_ms), margin.l + 3),
        idealX: row.ideal_lap_ms ? x(Math.max(0, row.ideal_lap_ms - fastest)) : null,
        label: row.delta_to_fastest_ms === 0 ? formatLapTime(row.lap_time_ms) : `+${(row.delta_to_fastest_ms / 1000).toFixed(3)}`,
      };
    }),
    left: margin.l,
  };
}

export function pitStopRows(analysis) {
  const drivers = driversByIndex(analysis);
  return (analysis?.pit_stops || []).map((stop) => ({
    ...stop,
    code: drivers.get(stop.car_index)?.code || `#${stop.car_index}`,
    tyres: `${compoundInfo(stop.from_compound).letter} → ${compoundInfo(stop.to_compound).letter}`,
    kindText: stop.kind === "tyre_change" ? "Tyre change while suspended" : stop.under_neutralisation ? "Pit stop · neutralised" : "Pit stop",
    lossText: stop.estimated_loss_ms == null ? "—" : `${(stop.estimated_loss_ms / 1000).toFixed(1)} s`,
  }));
}

function lapRuns(laps) {
  const sorted = [...new Set(laps)].sort((a, b) => a - b);
  const runs = [];
  for (const lap of sorted) {
    const last = runs[runs.length - 1];
    if (last && lap === last[1] + 1) last[1] = lap;
    else runs.push([lap, lap]);
  }
  return runs.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(", ");
}

export function headline(analysis) {
  const drivers = analysis?.drivers || [];
  const session = analysis?.session || {};
  const winner = drivers.find((d) => d.car_index === analysis?.winner_car_index);
  const second = drivers.find((d) => d.finish_position === 2);
  const player = drivers.find((d) => d.is_player);
  const fastest = (analysis?.fastest_laps || [])[0];
  const fastestDriver = fastest ? drivers.find((d) => d.car_index === fastest.car_index) : null;
  const kpis = [];
  let title = session.track_name ? `${session.track_name} · ${session.session_type || "Session"}` : session.session_type || "Session analysis";
  if (session.is_race && winner) {
    title = second && second.gap_to_winner_ms != null
      ? `${winner.display_name || winner.code} wins by ${(second.gap_to_winner_ms / 1000).toFixed(3)} s`
      : `${winner.display_name || winner.code} wins`;
    kpis.push({ label: "Winner", value: winner.code, detail: second ? `${second.code} ${formatGap(second.gap_to_winner_ms)}` : "" });
  }
  if (player) {
    const result = session.is_race
      ? (player.finish_position ? `P${player.finish_position}` : player.status === "retired" ? "DNF" : "—")
      : formatLapTime(player.best_lap_ms);
    const detail = session.is_race
      ? [player.status === "lapped" ? `${player.laps_down} lap${player.laps_down === 1 ? "" : "s"} down` : null, `${player.pit_stops} stop${player.pit_stops === 1 ? "" : "s"}`].filter(Boolean).join(" · ")
      : `best lap · ${player.laps_recorded} laps`;
    kpis.push({ label: session.is_race ? "Your result" : "Your best", value: result, detail });
  }
  if (fastest) kpis.push({ label: "Fastest lap", value: formatLapTime(fastest.lap_time_ms), detail: `${fastestDriver?.code || ""} · lap ${fastest.lap_number}` });
  if (analysis?.ideal_lap?.best_sectors_ms) kpis.push({ label: "Ideal lap", value: formatLapTime(analysis.ideal_lap.best_sectors_ms), detail: "best sectors in the session" });
  const neutral = (analysis?.neutralised_laps || []).length + (analysis?.suspended_laps || []).length;
  if (session.is_race) {
    const parts = [];
    if ((analysis?.suspended_laps || []).length) parts.push(`red flag ${lapRuns(analysis.suspended_laps)}`);
    if ((analysis?.neutralised_laps || []).length) parts.push(`safety car ${lapRuns(analysis.neutralised_laps)}`);
    kpis.push({ label: "Neutralised", value: `${neutral} lap${neutral === 1 ? "" : "s"}`, detail: parts.join(" · ") || "green all race" });
  }
  const subtitle = [
    session.is_race && session.leader_laps ? `${session.leader_laps} laps` : null,
    (analysis?.suspended_laps || []).length ? `red flag on laps ${lapRuns(analysis.suspended_laps)}` : null,
    (analysis?.neutralised_laps || []).length ? `safety car on laps ${lapRuns(analysis.neutralised_laps)}` : null,
    session.is_race ? "finishing order derived from lap times" : null,
  ].filter(Boolean).join(" · ");
  return { title, subtitle, kpis };
}
