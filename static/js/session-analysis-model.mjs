// Pure chart models for the Session Analysis view.
// Everything here turns the /api/v1/sessions/{id}/analysis payload (schema
// version 2) into plain numbers and strings; nothing touches the DOM, so Node
// tests cover it. Unknown stays unknown: a null renders as "—" or as words,
// never as 0, "P0", "0:00.000" or "0 stops", and enum keys always map to
// driver-facing text.

export const SCHEMA_VERSION = 2;

export function schemaSupported(analysis) {
  return analysis?.schema_version === SCHEMA_VERSION;
}

export const COMPOUNDS = Object.freeze({
  SOFT: { fill: "#ff6969", ink: "#070b0f", letter: "S", label: "Soft" },
  MEDIUM: { fill: "#ffc15c", ink: "#070b0f", letter: "M", label: "Medium" },
  HARD: { fill: "#e6edf3", ink: "#070b0f", letter: "H", label: "Hard" },
  INTER: { fill: "#49d17d", ink: "#070b0f", letter: "I", label: "Intermediate" },
  // Dark ink: white on this blue is 3.5:1, the dark letter is 5.7:1.
  WET: { fill: "#3f86ff", ink: "#070b0f", letter: "W", label: "Wet" },
  UNKNOWN: { fill: "#2a3744", ink: "#93a4b5", letter: "?", label: "No tyre data" },
});
// The game's C1-C6 labels are real recorded compounds whose dry/wet family is
// not known here: they keep their own label on a neutral fill.
const LABELLED_COMPOUND = /^C[1-6]$/;
const LABELLED_FILL = "#8fa3b5";

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

// ------------------------------------------------------------- text

const isNumber = (value) => value !== null && value !== undefined && value !== "" && typeof value !== "boolean" && Number.isFinite(Number(value));

export function compoundInfo(compound) {
  const key = String(compound ?? "").trim().toUpperCase();
  if (key !== "UNKNOWN" && COMPOUNDS[key]) return COMPOUNDS[key];
  if (LABELLED_COMPOUND.test(key)) return { fill: LABELLED_FILL, ink: "#070b0f", letter: key, label: `${key} compound` };
  return COMPOUNDS.UNKNOWN;
}

export function formatLapTime(ms) {
  if (!isNumber(ms) || Number(ms) <= 0) return "—";
  const value = Math.round(Number(ms));
  const minutes = Math.floor(value / 60000);
  const seconds = Math.floor((value % 60000) / 1000);
  const millis = value % 1000;
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

// Short axis form: 1:32, 1:32.5.
function shortLapTime(ms) {
  return formatLapTime(ms).replace(/0+$/, "").replace(/\.$/, "");
}

export function formatSeconds(ms, digits = 3) {
  if (!isNumber(ms)) return "—";
  return `${(Math.abs(Number(ms)) / 1000).toFixed(digits)} s`;
}

// Compact signed gap for axes and the timelapse: positive is behind.
export function formatGap(ms, { leader = "Leader" } = {}) {
  if (!isNumber(ms)) return "—";
  if (Number(ms) === 0) return leader;
  const seconds = Number(ms) / 1000;
  return `${seconds > 0 ? "+" : "−"}${Math.abs(seconds).toFixed(3)} s`;
}

// The same gap in words, one convention everywhere: positive = behind.
export function gapWords(ms, { reference = "", digits = 3 } = {}) {
  if (!isNumber(ms)) return "—";
  const value = Number(ms);
  if (value === 0) return reference ? `level with ${reference}` : "level";
  if (value > 0) return `${formatSeconds(value, digits)} behind${reference ? ` ${reference}` : ""}`;
  return `${formatSeconds(value, digits)} ahead${reference ? ` of ${reference}` : ""}`;
}

// A lap against the driver's own median, in seconds.
export function deltaWords(deltaSeconds) {
  if (!isNumber(deltaSeconds)) return "—";
  const value = Number(deltaSeconds);
  if (value === 0) return "at the median";
  return `${Math.abs(value).toFixed(3)} s ${value > 0 ? "slower" : "faster"} than median`;
}

export function positionText(position) {
  return isNumber(position) && Number(position) >= 1 ? `P${Math.round(Number(position))}` : "—";
}

export function countText(count, singular, plural = `${singular}s`) {
  if (!isNumber(count)) return "—";
  return `${Number(count)} ${Number(count) === 1 ? singular : plural}`;
}

export function lapsDownText(lapsDown) {
  return isNumber(lapsDown) && Number(lapsDown) > 0 ? `+${countText(lapsDown, "lap")}` : "";
}

export const PACE_EXCLUSION_TEXT = Object.freeze({
  missing_time: "No lap time",
  lap_one: "Lap 1",
  suspended: "Race suspended",
  restart: "Restart lap",
  safety_car: "Safety car",
  vsc: "Virtual safety car",
  unconfirmed: "Awaiting confirmation after a flashback",
  driver_reported: "Excluded in a lap note",
  no_context: "Timing only, no pit or flag data",
  pit: "Pit lap",
  flags: "Flag shown",
  invalid: "Invalid lap",
  outlier: "Over 107% of median",
});

export function exclusionText(reason) {
  if (!reason) return "";
  return PACE_EXCLUSION_TEXT[reason] || "Left out of pace";
}

export const STOP_KIND_TEXT = Object.freeze({
  pit_stop: "Pit stop",
  tyre_change: "Tyre change while suspended",
  unrecorded_stop: "Stop not recorded",
});

export function stopKindText(stop) {
  if (stop?.kind === "tyre_change") return STOP_KIND_TEXT.tyre_change;
  if (stop?.kind === "unrecorded_stop") return STOP_KIND_TEXT.unrecorded_stop;
  if (stop?.kind === "pit_stop") return stop.under_neutralisation ? "Pit stop · neutralised" : STOP_KIND_TEXT.pit_stop;
  return "Tyre change";
}

export function stopLossText(stop) {
  if (stop?.kind === "unrecorded_stop") return "Unknown";
  const ms = stop?.estimated_loss_ms;
  if (!isNumber(ms)) return "—";
  return `${Number(ms) < 0 ? "−" : ""}${(Math.abs(Number(ms)) / 1000).toFixed(1)} s`;
}

export const STATUS_TEXT = Object.freeze({
  finished: "Finished",
  lapped: "Lapped",
  retired: "Retired",
  incomplete_record: "Record incomplete",
  running: "Running",
  unranked: "Only recorded car",
  no_data: "No laps recorded",
});

export function statusText(driver) {
  if (driver?.status === "lapped") return lapsDownText(driver.laps_down) || STATUS_TEXT.lapped;
  return STATUS_TEXT[driver?.status] || "";
}

// Stop counts: unknown is "Stops unknown", never 0; a retirement can be
// counted as a stop by the game, so retired cars show no count at all.
export function stopsText(driver) {
  if (!driver) return "—";
  if (driver.status === "retired") return "Retired";
  if (!isNumber(driver.pit_stops)) return "Stops unknown";
  return countText(driver.pit_stops, "stop");
}

// The game's own result statuses, from its final classification.
const RESULT_TEXT = Object.freeze({ dnf: "DNF", dsq: "Disqualified", nc: "Not classified", retired: "Retired" });

// The classified result in a table cell.
export function resultText(driver) {
  const place = positionText(driver?.finish_position);
  if (RESULT_TEXT[driver?.result]) return RESULT_TEXT[driver.result];
  switch (driver?.status) {
    case "finished": return place;
    case "lapped": return [place === "—" ? null : place, lapsDownText(driver.laps_down)].filter(Boolean).join(" · ") || STATUS_TEXT.lapped;
    case "retired": return STATUS_TEXT.retired;
    case "running": return place === "—" ? STATUS_TEXT.running : `${place} (provisional)`;
    default: return STATUS_TEXT[driver?.status] || place;
  }
}

// ------------------------------------------------------------- scales

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

// Steps a lap-time axis may use, in seconds, largest first. Quarter seconds
// fill the gap between half and fifth seconds on short axes.
const TIME_STEPS = [60, 30, 20, 10, 5, 2.5, 2, 1, 0.5, 0.25, 0.2, 0.1];

// Width of an 11 px tick label such as "1:31.25": digits about 6.4 px, the
// colon and point about 3.4 px.
function tickTextWidth(text) {
  const narrow = (String(text).match(/[.:]/g) || []).length;
  return (String(text).length - narrow) * 6.4 + narrow * 3.4;
}

// Seconds to label on a lap-time axis `length` px long: about `count` ticks,
// at least three where their labels fit (`horizontal` labels need their own
// width, stacked ones 18 px), and never a lone label.
function timeTicks(min, max, length, count, { horizontal = true } = {}) {
  const at = (step) => {
    const out = [];
    for (let k = Math.ceil(min / step - 1e-9); k * step <= max + 1e-9; k += 1) out.push(Number((k * step).toFixed(3)));
    return out;
  };
  const fits = (ticks, step) => (step / Math.max(1e-9, max - min)) * length
    >= (horizontal ? Math.max(...ticks.map((s) => tickTextWidth(shortLapTime(s * 1000)))) + 8 : 18);
  let ticks = niceTicks(min, max, count);
  if (ticks.length >= 3) return ticks;
  for (const step of TIME_STEPS) {
    const finer = at(step);
    if (finer.length > ticks.length && fits(finer, step)) ticks = finer;
    if (ticks.length >= 3) return ticks;
  }
  return ticks.length >= 2 ? ticks : TIME_STEPS.map(at).find((t) => t.length >= 2) || ticks;
}

// Lap numbers to label: lap 1, round laps, and the last lap. `maxTicks`
// thins them for narrow charts.
export function lapAxisTicks(total, maxTicks = Infinity) {
  if (total <= 1) return [1];
  const steps = [1, 2, 5, 10, 20, 50, 100];
  const preferred = total <= 12 ? 1 : total <= 40 ? 5 : total <= 80 ? 10 : 20;
  let step = preferred;
  for (const candidate of steps) {
    if (candidate < preferred) continue;
    step = candidate;
    if (Math.ceil(total / candidate) + 1 <= maxTicks) break;
  }
  const ticks = [1];
  for (let lap = step; lap < total; lap += step) if (lap - ticks[ticks.length - 1] >= step / 2) ticks.push(lap);
  // The last lap is always labelled; a round tick crowding it gives way.
  if (ticks.length > 1 && total - ticks[ticks.length - 1] < step / 2) ticks.pop();
  ticks.push(total);
  return ticks;
}

// Push labels apart vertically so none overlap; keeps them inside [top, bottom].
export function spreadLabels(items, gap, top, bottom) {
  const sorted = items.slice().sort((a, b) => a.y - b.y);
  for (let i = 1; i < sorted.length; i += 1) {
    if (sorted[i].y - sorted[i - 1].y < gap) sorted[i].y = sorted[i - 1].y + gap;
  }
  const overflow = sorted.length ? sorted[sorted.length - 1].y - bottom : 0;
  if (overflow > 0) {
    sorted[sorted.length - 1].y -= overflow;
    for (let i = sorted.length - 2; i >= 0; i -= 1) {
      if (sorted[i + 1].y - sorted[i].y < gap) sorted[i].y = sorted[i + 1].y - gap;
    }
  }
  for (const item of sorted) item.y = Math.max(top, item.y);
  return items;
}

// ------------------------------------------------------------- lookups

export function driversByIndex(analysis) {
  return new Map((analysis?.drivers || []).map((driver) => [driver.car_index, driver]));
}

export function codeOf(analysis, carIndex) {
  return driversByIndex(analysis).get(carIndex)?.code || `#${carIndex}`;
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
  return (analysis?.laps || []).reduce((max, lap) => Math.max(max, Number(lap.lap_number) || 0), 0);
}

const timed = (lap) => Boolean(lap) && isNumber(lap.lap_time_ms) && Number(lap.lap_time_ms) > 0;

// Cars with at least one completed lap, from the field summary when present.
function carsWithLaps(analysis) {
  const reported = analysis?.field?.cars_with_laps;
  if (isNumber(reported)) return Number(reported);
  return new Set((analysis?.laps || []).filter(timed).map((lap) => lap.car_index)).size;
}

// A race order needs a race and two or more recorded cars. A car on its own
// still has laps at "P1" with a 0 ms gap, but that is not an order.
export function hasRaceOrder(analysis) {
  if (!analysis?.session?.is_race) return false;
  if ((analysis.drivers || []).some((d) => d.status === "unranked")) return false;
  return carsWithLaps(analysis) >= 2;
}

// Why the race trace, positions and timelapse are not shown; "" when they are.
export function raceOrderNote(analysis) {
  if (!analysis || hasRaceOrder(analysis)) return "";
  if (!analysis.session?.is_race) return "Race trace, positions and the timelapse need a race; this session shows pace, tyres and laps.";
  if (carsWithLaps(analysis) === 0 && !(analysis.drivers || []).some((d) => d.status === "unranked")) return "No car completed a lap, so there is no race order to show.";
  return "Only one car was recorded, so there is no race order: race trace, positions and the timelapse need two or more cars.";
}

// A lap Lap Lab can play: it has a time and a telemetry trace.
export function lapOpenable(lap) {
  return Boolean(lap?.lap_id) && timed(lap) && lap.traced !== false;
}

// The player plus the cars that finished around them; without a player, the
// first four finishers. Never more than `limit`.
export function defaultFocus(analysis, limit = 4) {
  const drivers = (analysis?.drivers || []).slice();
  const placed = drivers.filter((d) => isNumber(d.finish_position)).sort((a, b) => a.finish_position - b.finish_position);
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

// Cars that can be the race-trace reference: classified finishers and, in a
// provisional session, cars still running. A retired car's gaps would end at
// its retirement and blank the trace.
export function referenceOptions(analysis) {
  return (analysis?.drivers || [])
    .filter((d) => ["finished", "lapped", "running"].includes(d.status) && Number(d.laps_completed ?? d.laps_recorded) > 0)
    .map((d) => ({
      value: String(d.car_index),
      label: [`${d.code}${d.is_player ? " (you)" : ""}`, d.status === "lapped" ? lapsDownText(d.laps_down) : null].filter(Boolean).join(" · "),
    }));
}

// Explains a reference whose record ends before the last lap.
export function referenceNote(analysis, reference) {
  if (reference === "leader" || reference === null || reference === undefined) return "";
  const carIndex = Number(reference);
  const byLap = lapLookup(analysis).get(carIndex);
  const total = lastLap(analysis);
  const last = byLap ? Math.max(...[...byLap.values()].filter((l) => isNumber(l.segment_time_ms)).map((l) => l.lap_number), 0) : 0;
  const code = codeOf(analysis, carIndex);
  if (!last) return `${code} has no timed laps to compare against.`;
  return last < total ? `Gaps to ${code} end at lap ${last}, its last timed lap.` : "";
}

// ------------------------------------------------------------- lap bands

export function lapRanges(laps) {
  const sorted = [...new Set((laps || []).map(Number).filter(Number.isFinite))].sort((a, b) => a - b);
  const runs = [];
  for (const lap of sorted) {
    const last = runs[runs.length - 1];
    if (last && lap === last[1] + 1) last[1] = lap;
    else runs.push([lap, lap]);
  }
  return runs;
}

export function formatLapRanges(laps) {
  return lapRanges(laps).map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(", ");
}

function onLaps(laps) {
  const runs = lapRanges(laps);
  const plural = runs.length > 1 || runs.some(([a, b]) => a !== b);
  return `on ${plural ? "laps" : "lap"} ${formatLapRanges(laps)}`;
}

// Safety-car and VSC laps come from race control (field-wide message laps).
export function neutralLaps(analysis) {
  const rc = analysis?.race_control;
  if (rc && typeof rc === "object") return { sc: rc.safety_car_laps || [], vsc: rc.vsc_laps || [] };
  return { sc: analysis?.neutralised_laps || [], vsc: [] };
}

// Shaded lap bands: safety car (SC), virtual safety car (VSC) and the red
// flag (the field's suspended laps). Each band carries its own label.
export function lapBands(analysis, x) {
  const bands = [];
  const add = (kind, label, laps) => {
    for (const [first, last] of lapRanges(laps)) {
      bands.push({ kind, label, first, last, x: x(first - 0.5), width: x(last + 0.5) - x(first - 0.5) });
    }
  };
  const { sc, vsc } = neutralLaps(analysis);
  add("sc", "SC", sc);
  add("vsc", "VSC", vsc);
  add("suspended", "RED", analysis?.suspended_laps || []);
  return bands;
}

// ------------------------------------------------------------- race pace

// Race pace box plot. Drivers with fewer than `minLaps` representative laps
// are listed, not boxed: a box of one lap says nothing about spread. Outliers
// far beyond the whiskers sit on the plot edge as off-scale markers, so the
// boxes keep their scale and nothing is drawn outside the chart.
export function racePaceModel(analysis, { width = 1200, height = 340, minLaps = 5, horizontal = false } = {}) {
  const drivers = driversByIndex(analysis);
  const styles = driverStyles(analysis);
  const usable = (analysis?.race_pace || []).filter((row) => isNumber(row.median_ms) && isNumber(row.whisker_low_ms) && isNumber(row.whisker_high_ms));
  const rows = usable.filter((row) => Number(row.n) >= minLaps);
  const paced = new Set((analysis?.race_pace || []).map((row) => row.car_index));
  const thin = [
    ...(analysis?.race_pace || []).filter((row) => !(Number(row.n) >= minLaps)).map((row) => ({
      car_index: row.car_index, code: drivers.get(row.car_index)?.code || `#${row.car_index}`, n: Number(row.n) || 0,
    })),
    // A driver who completed laps without a single pace lap (every lap left
    // out: no telemetry context, flags, lap 1) is listed too, never dropped.
    ...(analysis?.drivers || []).filter((d) => !paced.has(d.car_index) && Number(d.laps_completed ?? d.laps_recorded) > 0).map((d) => ({
      car_index: d.car_index, code: d.code || `#${d.car_index}`, n: 0,
    })),
  ];
  if (!rows.length) return { boxes: [], ticks: [], thin, domain: null, width, height: 0, horizontal };
  const whiskerLow = Math.min(...rows.map((r) => Number(r.whisker_low_ms)));
  const whiskerHigh = Math.max(...rows.map((r) => Number(r.whisker_high_ms)));
  const reach = Math.max(1500, (whiskerHigh - whiskerLow) * 0.5);
  const outlierTimes = rows.flatMap((r) => (r.outliers || []).map((o) => Number(o.lap_time_ms))).filter(Number.isFinite);
  const low = Math.min(whiskerLow, ...outlierTimes.filter((v) => v >= whiskerLow - reach));
  const high = Math.max(whiskerHigh, ...outlierTimes.filter((v) => v <= whiskerHigh + reach));
  const pad = Math.max(200, (high - low) * 0.06);
  const domain = [low - pad, high + pad];
  const boxOf = (row, at, label) => {
    const driver = drivers.get(row.car_index) || {};
    const code = driver.code || `#${row.car_index}`;
    const outliers = (row.outliers || []).filter((o) => isNumber(o.lap_time_ms)).map((o) => {
      const ms = Number(o.lap_time_ms);
      const off = ms > domain[1] ? "high" : ms < domain[0] ? "low" : null;
      return { pos: at(Math.min(domain[1], Math.max(domain[0], ms))), lap: o.lap_number, ms, off };
    });
    const offScale = outliers.filter((o) => o.off);
    return {
      car_index: row.car_index, code, player: Boolean(driver.is_player), colour: styles.get(row.car_index)?.colour || CONTEXT_STROKE,
      q1: at(Number(row.q1_ms)), q3: at(Number(row.q3_ms)), median: at(Number(row.median_ms)),
      whiskerLow: at(Number(row.whisker_low_ms)), whiskerHigh: at(Number(row.whisker_high_ms)),
      label, row, n: Number(row.n), nText: countText(row.n, "lap"), outliers,
      text: `${code} · median ${formatLapTime(row.median_ms)} · middle half ${formatLapTime(row.q1_ms)}–${formatLapTime(row.q3_ms)} · ${countText(row.n, "pace lap")}`
        + (outliers.length ? ` · ${countText(outliers.length, "outlier")}` : "")
        + (offScale.length ? ` (${offScale.map((o) => `lap ${o.lap} ${formatLapTime(o.ms)}`).join(", ")} beyond the scale)` : ""),
    };
  };
  if (horizontal) {
    const margin = { l: 56, r: 64, t: 8, b: 30 };
    const rowHeight = 28;
    const h = margin.t + rows.length * rowHeight + margin.b;
    const x = scale(domain[0], domain[1], margin.l, width - margin.r);
    // A phone-width axis still gets three labels where they fit.
    const length = width - margin.l - margin.r;
    const seconds = timeTicks(domain[0] / 1000, domain[1] / 1000, length, Math.max(3, Math.floor(length / 90)));
    return {
      width, height: h, horizontal: true, domain, thin,
      plot: { left: margin.l, right: width - margin.r, top: margin.t, bottom: h - margin.b },
      ticks: seconds.map((s) => s * 1000).map((v) => ({ value: v, pos: x(v), label: shortLapTime(v) })),
      boxes: rows.map((row, i) => {
        const y = margin.t + i * rowHeight;
        const box = boxOf(row, x, { x: margin.l - 8, y: y + 14 });
        return { ...box, cross: y + 10, crossStart: y + 3, crossEnd: y + 17, nLabel: { x: width - margin.r + 8, y: y + 14 } };
      }),
    };
  }
  const margin = { l: 64, r: 12, t: 12, b: 46 };
  const y = scale(domain[1], domain[0], margin.t, height - margin.b);
  const band = (width - margin.l - margin.r) / rows.length;
  const boxWidth = Math.max(6, Math.min(24, band * 0.6));
  const length = height - margin.t - margin.b;
  const seconds = timeTicks(domain[0] / 1000, domain[1] / 1000, length, Math.max(3, Math.floor(length / 48)), { horizontal: false });
  return {
    width, height, horizontal: false, domain, thin,
    ticks: seconds.map((s) => s * 1000).map((v) => ({ value: v, pos: y(v), label: shortLapTime(v) })),
    plot: { left: margin.l, right: width - margin.r, top: margin.t, bottom: height - margin.b },
    boxes: rows.map((row, i) => {
      const centre = margin.l + band * (i + 0.5);
      const box = boxOf(row, y, { x: centre, y: height - margin.b + 18 });
      return { ...box, cross: centre, crossStart: centre - boxWidth / 2, crossEnd: centre + boxWidth / 2, nLabel: { x: centre, y: height - margin.b + 33 } };
    }),
  };
}

// ------------------------------------------------------------- race trace

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
      if (reference === "leader") gap = isNumber(lap.gap_to_leader_ms) ? Number(lap.gap_to_leader_ms) : null;
      else {
        const ref = laps.get(Number(reference))?.get(lapNumber);
        if (ref && isNumber(lap.segment_time_ms) && isNumber(ref.segment_time_ms)) gap = Number(lap.segment_time_ms) - Number(ref.segment_time_ms);
      }
      if (gap !== null) points.push({ lap: lapNumber, gap });
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

function referenceName(analysis, reference) {
  return reference === "leader" ? "the leader" : codeOf(analysis, Number(reference));
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
  const margin = { l: 52, r: 44, t: 18, b: 28 };
  const plot = { left: margin.l, right: width - margin.r, top: margin.t, bottom: height - margin.b };
  const x = scale(1, Math.max(2, total), plot.left, plot.right);
  const y = scale(Math.max(-clip, minGap), maxGap, plot.top, plot.bottom);
  // Values beyond the axis are drawn where they fall and clipped to the plot
  // by the renderer; clamping them would draw a flat line that reads as data.
  const toPoints = (run) => run.map((p) => `${x(p.lap).toFixed(1)},${y(p.gap / 1000).toFixed(1)}`).join(" ");
  const lines = [];
  const endLabels = [];
  for (const [carIndex, points] of series) {
    const style = styles.get(carIndex) || { colour: CONTEXT_STROKE, dash: "" };
    const isFocus = focus.includes(carIndex);
    const runs = continuousRuns(analysis, points);
    lines.push({
      car_index: carIndex, code: drivers.get(carIndex)?.code, focus: isFocus,
      colour: isFocus ? style.colour : CONTEXT_STROKE, dash: isFocus ? style.dash : "",
      runs: runs.map(toPoints).filter((s) => s.includes(" ")),
      singles: runs.filter((r) => r.length === 1 && r[0].gap / 1000 <= maxGap).map((r) => ({ x: x(r[0].lap), y: y(r[0].gap / 1000) })),
    });
    const last = points[points.length - 1];
    if (isFocus && last && last.gap / 1000 <= maxGap) {
      endLabels.push({ car_index: carIndex, text: drivers.get(carIndex)?.code || `#${carIndex}`, colour: style.colour, x: x(last.lap) + 6, y: y(last.gap / 1000) + 4 });
    }
  }
  lines.sort((a, b) => Number(a.focus) - Number(b.focus));
  spreadLabels(endLabels, 13, plot.top + 4, plot.bottom);
  const zero = reference === "leader" ? "Leader" : drivers.get(Number(reference))?.code || "Ref";
  return {
    width, height, clip: maxGap, plot, reference: referenceName(analysis, reference),
    bands: lapBands(analysis, x).map((b) => ({ ...b, y: plot.top, height: plot.bottom - plot.top })),
    yTicks: niceTicks(Math.max(-clip, minGap), maxGap, Math.max(3, Math.floor((plot.bottom - plot.top) / 44)))
      .map((s) => ({ pos: y(s), label: s === 0 ? zero : `${s > 0 ? "+" : "−"}${Math.abs(s)} s` })),
    xTicks: lapAxisTicks(total, Math.max(3, Math.floor((plot.right - plot.left) / 34))).map((lap) => ({ pos: x(lap), label: String(lap) })),
    laps: Array.from({ length: total }, (_, i) => ({ lap: i + 1, x: x(i + 1) })),
    lines, endLabels,
  };
}

// Crosshair reading for one lap of the race trace.
export function traceReading(analysis, { focus = [], reference = "leader", lap }) {
  const series = gapSeries(analysis, reference);
  const suspended = (analysis?.suspended_laps || []).includes(lap);
  const name = referenceName(analysis, reference);
  return focus.map((carIndex) => {
    const point = (series.get(carIndex) || []).find((p) => p.lap === lap);
    let value = "—";
    if (point) value = point.gap === 0 ? (reference === "leader" ? "Leader" : String(reference) === String(carIndex) ? "Reference" : `level with ${name}`) : gapWords(point.gap, { reference: reference === "leader" ? "" : name });
    else if (suspended) value = "Race suspended";
    return { car_index: carIndex, code: codeOf(analysis, carIndex), value };
  });
}

// ------------------------------------------------------------- positions

// The label at the end of a car's position line. Lapped finishers are
// classified (never "out"); only a retirement is "out".
export function finishLabel(driver, lastLapNumber, total) {
  const code = driver?.code || "—";
  const place = isNumber(driver?.finish_position) ? `P${driver.finish_position} ` : "";
  switch (driver?.status) {
    case "finished": return `${place}${code}`;
    case "lapped": return `${place}${code} ${lapsDownText(driver.laps_down)}`.trim();
    case "retired": return `${code} · out L${lastLapNumber}`;
    case "incomplete_record": return `${code} · record ends L${lastLapNumber}`;
    case "running": return lastLapNumber < total ? `${place}${code} · L${lastLapNumber}` : `${place}${code}`;
    default: return code;
  }
}

export function positionsModel(analysis, { focus = [], width = 640, height = 360, measure = null } = {}) {
  const total = lastLap(analysis);
  const styles = driverStyles(analysis);
  const drivers = driversByIndex(analysis);
  const laps = lapLookup(analysis);
  const field = Math.max(2, ...(analysis?.laps || []).map((l) => (isNumber(l.position) ? Number(l.position) : 0)));
  const ends = [];
  for (const [carIndex, byLap] of laps) {
    const points = [...byLap.values()].filter((l) => isNumber(l.position)).sort((a, b) => a.lap_number - b.lap_number).map((l) => ({ lap: l.lap_number, position: Number(l.position) }));
    if (points.length) ends.push({ carIndex, points, text: finishLabel(drivers.get(carIndex) || { code: `#${carIndex}` }, points[points.length - 1].lap, total) });
  }
  // The right margin holds the finishing column only; a line that ends
  // earlier is labelled inside the plot. Label widths come from `measure`
  // (the view measures its own font) or a generous per-character estimate.
  const textWidth = typeof measure === "function" ? (text) => measure(text) : (text) => text.length * 7.2;
  const longest = Math.max(textWidth("P20 XXX"), ...ends.filter((e) => e.points[e.points.length - 1].lap === total).map((e) => textWidth(e.text)));
  const margin = { l: 40, r: Math.min(180, 14 + Math.ceil(longest)), t: 18, b: 28 };
  const plot = { left: margin.l, right: width - margin.r, top: margin.t, bottom: height - margin.b };
  const x = scale(1, Math.max(2, total), plot.left, plot.right);
  const y = scale(1, field, plot.top, plot.bottom);
  const lines = [];
  const labels = [];
  for (const { carIndex, points, text } of ends) {
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
      x: x(last.lap), y: y(last.position), lap: last.lap, text, tx: x(last.lap) + 8, ty: y(last.position) + 4, anchor: "start",
    });
  }
  // A label that would run into the finishing column turns to the left of
  // its dot; labels that still overlap step down until they clear.
  const boxOf = (l) => (l.anchor === "start" ? { left: l.tx, right: l.tx + textWidth(l.text) } : { left: l.tx - textWidth(l.text), right: l.tx });
  for (const label of labels) {
    if (label.lap === total || label.tx + textWidth(label.text) <= plot.right - 4) continue;
    label.anchor = "end";
    label.tx = Math.max(plot.left + textWidth(label.text), label.x - 8);
  }
  const placed = [];
  for (const label of labels.slice().sort((a, b) => a.ty - b.ty)) {
    const box = boxOf(label);
    for (let guard = 0; guard < labels.length; guard += 1) {
      const hit = placed.find((p) => Math.abs(p.ty - label.ty) < 12 && p.box.left < box.right && box.left < p.box.right);
      if (!hit) break;
      label.ty = hit.ty + 12;
    }
    placed.push({ ty: label.ty, box });
  }
  lines.sort((a, b) => Number(a.focus) - Number(b.focus));
  const yValues = [...new Set([1, ...niceTicks(1, field, Math.max(2, Math.floor((plot.bottom - plot.top) / 40))).filter((p) => p > 1 && Number.isInteger(p)), field])];
  return {
    width, height, plot,
    bands: lapBands(analysis, x).map((b) => ({ ...b, y: plot.top, height: plot.bottom - plot.top })),
    xTicks: lapAxisTicks(total, Math.max(3, Math.floor((plot.right - plot.left) / 34))).map((lap) => ({ pos: x(lap), label: String(lap) })),
    yTicks: yValues.map((p) => ({ pos: y(p), label: `P${p}` })),
    laps: Array.from({ length: total }, (_, i) => ({ lap: i + 1, x: x(i + 1) })),
    lines, labels,
  };
}

// ------------------------------------------------------------- tyre strategy

export function strategyModel(analysis, { width = 1200, rowHeight = 24, minLabelWidth = 16 } = {}) {
  const total = Math.max(1, lastLap(analysis));
  const drivers = analysis?.drivers || [];
  const stints = new Map((analysis?.stints || []).map((row) => [row.car_index, row.stints || []]));
  const margin = { l: 52, r: 12, t: 6, b: 28 };
  const x = scale(0, total, margin.l, width - margin.r);
  const rows = drivers.map((driver, i) => {
    const y = margin.t + i * rowHeight;
    const code = driver.code || `#${driver.car_index}`;
    return {
      car_index: driver.car_index, code, player: Boolean(driver.is_player), y, labelY: y + rowHeight / 2 + 4,
      stints: (stints.get(driver.car_index) || []).map((stint) => {
        const info = compoundInfo(stint.compound);
        const left = x(stint.start_lap - 1) + 1;
        const w = Math.max(2, x(stint.end_lap) - x(stint.start_lap - 1) - 2);
        // A letter only where it fits; the label always reaches the
        // accessible name and the table.
        const fits = w >= Math.max(minLabelWidth, info.letter.length * 8 + 6);
        return {
          ...stint, x: left, width: w, centre: left + w / 2, fill: info.fill, ink: info.ink, letter: fits ? info.letter : "", label: info.label,
          text: `${code} · ${info.label} · laps ${stint.start_lap}–${stint.end_lap} (${countText(stint.laps, "lap")})${isNumber(stint.tyre_age_start) && Number(stint.tyre_age_start) > 0 ? ` · ${countText(stint.tyre_age_start, "lap")} old at the start` : ""}`,
        };
      }),
    };
  });
  return {
    width, height: margin.t + rows.length * rowHeight + margin.b, rowHeight, rows, left: margin.l,
    xTicks: lapAxisTicks(total, Math.max(3, Math.floor((width - margin.l - margin.r) / 34))).map((lap) => ({ pos: x(lap), label: String(lap) })),
  };
}

// ------------------------------------------------------------- lap times

export function lapTimesModel(analysis, { focus = [], width = 640, height = 300 } = {}) {
  const total = lastLap(analysis);
  const styles = driverStyles(analysis);
  const drivers = driversByIndex(analysis);
  const racing = (analysis?.laps || []).filter((l) => focus.includes(l.car_index) && !l.pace_excluded && timed(l));
  const margin = { l: 64, r: 44, t: 18, b: 28 };
  const plot = { left: margin.l, right: width - margin.r, top: margin.t, bottom: height - margin.b };
  const x = scale(1, Math.max(2, total), plot.left, plot.right);
  if (!racing.length) return { width, height, series: [], yTicks: [], xTicks: [], bands: [], endLabels: [], plot, empty: true };
  const low = Math.min(...racing.map((l) => l.lap_time_ms));
  const high = Math.max(...racing.map((l) => l.lap_time_ms));
  const pad = Math.max(150, (high - low) * 0.08);
  const y = scale(high + pad, low - pad, plot.bottom, plot.top);
  const endLabels = [];
  const series = focus.map((carIndex, row) => {
    const style = styles.get(carIndex) || { colour: CONTEXT_STROKE, dash: "" };
    const code = drivers.get(carIndex)?.code || `#${carIndex}`;
    const points = racing.filter((l) => l.car_index === carIndex).sort((a, b) => a.lap_number - b.lap_number)
      .map((l) => ({
        lap: l.lap_number, ms: l.lap_time_ms, x: x(l.lap_number), y: y(l.lap_time_ms), compound: l.compound, lap_id: l.lap_id || null,
        openable: lapOpenable(l), row,
        text: `${code} lap ${l.lap_number} · ${formatLapTime(l.lap_time_ms)} · ${compoundInfo(l.compound).label}${l.traced === false ? " · timing only — no telemetry" : ""}`,
      }));
    const runs = continuousRuns(analysis, points).filter((r) => r.length > 1).map((r) => r.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" "));
    const last = points[points.length - 1];
    if (last) endLabels.push({ car_index: carIndex, text: code, colour: style.colour, x: last.x + 8, y: last.y + 4 });
    return { car_index: carIndex, code, colour: style.colour, dash: style.dash, points, runs };
  });
  spreadLabels(endLabels, 13, plot.top + 4, plot.bottom);
  return {
    width, height, series, plot, endLabels,
    bands: lapBands(analysis, x).map((b) => ({ ...b, y: plot.top, height: plot.bottom - plot.top })),
    yTicks: niceTicks((low - pad) / 1000, (high + pad) / 1000, Math.max(3, Math.floor((plot.bottom - plot.top) / 44))).map((s) => ({ pos: y(s * 1000), label: shortLapTime(s * 1000) })),
    xTicks: lapAxisTicks(total, Math.max(3, Math.floor((plot.right - plot.left) / 34))).map((lap) => ({ pos: x(lap), label: String(lap) })),
  };
}

// ------------------------------------------------------------- heatmap

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

function heatCell(code, lapNumber, lap, median, range) {
  const base = { lap: lapNumber, lap_id: null, delta: null, openable: false };
  if (!lap) {
    // Inside the laps the car completed, a missing row is a gap in the
    // recording, not a lap the car skipped.
    if (lapNumber <= range) return { ...base, state: "missing", fill: "none", note: "not recorded", text: `${code} lap ${lapNumber} · not recorded` };
    return { ...base, state: "none", fill: "transparent", note: "not driven", text: `${code} lap ${lapNumber} · not driven` };
  }
  const hasTime = timed(lap);
  const delta = !lap.pace_excluded && hasTime && isNumber(median) ? (Number(lap.lap_time_ms) - Number(median)) / 1000 : null;
  const notes = [];
  if (delta === null) {
    if (lap.pace_excluded && lap.pace_excluded !== "missing_time") notes.push(exclusionText(lap.pace_excluded));
    else if (hasTime && !lap.pace_excluded) notes.push("no pace median to compare");
  }
  if (hasTime && lap.traced === false) notes.push("timing only — no telemetry");
  const parts = [`${code} lap ${lapNumber}`, hasTime ? formatLapTime(lap.lap_time_ms) : "no lap time"];
  if (delta !== null) parts.push(deltaWords(delta));
  return {
    ...base, state: delta === null ? "excluded" : "pace", fill: heatColour(delta), delta,
    lap_id: lap.lap_id || null, openable: lapOpenable(lap), traced: lap.traced !== false,
    time: hasTime ? formatLapTime(lap.lap_time_ms) : "no lap time", note: notes.join(" · "),
    text: [...parts, ...notes].join(" · "),
  };
}

export function heatmapModel(analysis) {
  const total = lastLap(analysis);
  const medians = new Map((analysis?.race_pace || []).map((row) => [row.car_index, row.median_ms]));
  const laps = lapLookup(analysis);
  return {
    total,
    rows: (analysis?.drivers || []).filter((d) => laps.has(d.car_index)).map((driver) => {
      const byLap = laps.get(driver.car_index);
      const range = Math.max(...byLap.keys(), isNumber(driver.laps_completed) ? Number(driver.laps_completed) : 0);
      const code = driver.code || `#${driver.car_index}`;
      return {
        car_index: driver.car_index, code, player: Boolean(driver.is_player),
        cells: Array.from({ length: total }, (_, i) => heatCell(code, i + 1, byLap.get(i + 1), medians.get(driver.car_index), range)),
      };
    }),
  };
}

// ------------------------------------------------------------- timelapse

// One frame per lap end with a running order: places and gap to the leader.
// Suspended laps carry the frozen order with no gaps. A placed car that is
// not known to have retired (lapped, or still running in a recording that
// stopped early) keeps its place behind the lead-lap cars after its last lap,
// its laps down growing. The final frame holds every placed car in that
// order, with its laps down: the flag, or the last recorded lap of an
// unfinished recording. Only a retired car leaves the field. A single
// recorded car has no order to replay.
export function timelapseFrames(analysis) {
  if (!hasRaceOrder(analysis)) return [];
  // A lap that was started but not finished has no order; the replay ends
  // at the last lap that has one.
  const total = (analysis?.laps || []).reduce((max, lap) => (isNumber(lap.position) ? Math.max(max, Number(lap.lap_number) || 0) : max), 0);
  const laps = lapLookup(analysis);
  const drivers = driversByIndex(analysis);
  const stints = new Map((analysis?.stints || []).map((row) => [row.car_index, row.stints || []]));
  const compoundAt = (carIndex, lap) => (stints.get(carIndex) || []).find((s) => s.start_lap <= lap && lap <= s.end_lap)?.compound || null;
  const suspended = new Set(analysis?.suspended_laps || []);
  const { sc, vsc } = neutralLaps(analysis);
  const scSet = new Set(sc);
  const vscSet = new Set(vsc);
  const lastPlaced = new Map();
  for (const [carIndex, byLap] of laps) {
    let last = 0;
    for (const [lapNumber, row] of byLap) if (isNumber(row.position) && lapNumber > last) last = lapNumber;
    lastPlaced.set(carIndex, last);
  }
  const finishOf = (carIndex) => (isNumber(drivers.get(carIndex)?.finish_position) ? Number(drivers.get(carIndex).finish_position) : Infinity);
  // Placed and laps down without a retirement: a lapped finisher, or a car
  // still running when an unfinished recording stopped. A retirement or an
  // unplaced record ends where its record does.
  const staysInField = (driver) => Boolean(driver) && isNumber(driver.finish_position)
    && (driver.status === "lapped" || driver.status === "finished" || (driver.status === "running" && Number(driver.laps_down) > 0));
  const frames = [];
  for (let lap = 1; lap <= total; lap += 1) {
    const final = lap === total;
    const placed = [];
    for (const [carIndex, byLap] of laps) {
      const row = byLap.get(lap);
      if (!row || !isNumber(row.position)) continue;
      placed.push({ car_index: carIndex, order: Number(row.position), gap_ms: isNumber(row.gap_to_leader_ms) ? Number(row.gap_to_leader_ms) : null, laps_down: 0, compound: compoundAt(carIndex, lap), pit: row.pit === true });
    }
    placed.sort((a, b) => a.order - b.order);
    const carried = [];
    for (const [carIndex, last] of lastPlaced) {
      const driver = drivers.get(carIndex);
      if (!staysInField(driver) || !last || lap <= last) continue;
      const down = final && isNumber(driver.laps_down) ? Number(driver.laps_down) : lap - last;
      carried.push({ car_index: carIndex, order: Infinity, gap_ms: null, laps_down: down, compound: compoundAt(carIndex, last), pit: false });
    }
    carried.sort((a, b) => finishOf(a.car_index) - finishOf(b.car_index));
    let ordered = [...placed, ...carried];
    if (final) {
      ordered = ordered.filter((e) => drivers.get(e.car_index)?.status !== "retired")
        .map((e, i) => ({ ...e, rank: i }))
        .sort((a, b) => finishOf(a.car_index) - finishOf(b.car_index) || a.rank - b.rank);
    }
    frames.push({
      lap, final, suspended: suspended.has(lap), neutralised: scSet.has(lap) ? "SC" : vscSet.has(lap) ? "VSC" : null,
      entries: ordered.map((e, i) => {
        const finish = finishOf(e.car_index);
        const out = drivers.get(e.car_index)?.status === "retired" && lastPlaced.get(e.car_index) === lap;
        return {
          car_index: e.car_index, position: i + 1,
          label: final && Number.isFinite(finish) ? `P${finish}` : Number.isFinite(e.order) ? `P${e.order}` : `P${i + 1}`,
          gap_ms: e.gap_ms, laps_down: e.laps_down, compound: e.compound, pit: e.pit, out,
        };
      }),
    });
  }
  return frames;
}

// Blend two lap frames for smooth playback. A car missing from the next
// frame is removed only if it retired; otherwise it holds its place.
export function frameAt(frames, t) {
  if (!frames.length) return { lap: 0, entries: [] };
  const clamped = Math.min(Math.max(t, 1), frames.length);
  const lower = frames[Math.floor(clamped) - 1];
  const upper = frames[Math.min(frames.length - 1, Math.floor(clamped))];
  const f = clamped - Math.floor(clamped);
  if (!f || lower === upper) return { ...lower, t: clamped };
  const next = new Map(upper.entries.map((e) => [e.car_index, e]));
  const previous = new Set(lower.entries.map((e) => e.car_index));
  const entries = [];
  for (const e of lower.entries) {
    const n = next.get(e.car_index);
    if (!n) {
      if (!e.out) entries.push({ ...e });
      continue;
    }
    const gap = e.gap_ms != null && n.gap_ms != null ? e.gap_ms + (n.gap_ms - e.gap_ms) * f : (f < 0.5 ? e.gap_ms : n.gap_ms);
    entries.push({
      ...e, position: e.position + (n.position - e.position) * f, gap_ms: gap,
      label: f < 0.5 ? e.label : n.label, laps_down: f < 0.5 ? e.laps_down : n.laps_down, compound: f < 0.5 ? e.compound : n.compound,
    });
  }
  if (f >= 0.5) for (const n of upper.entries) if (!previous.has(n.car_index)) entries.push({ ...n });
  return {
    lap: lower.lap, t: clamped, final: false, suspended: lower.suspended || upper.suspended,
    neutralised: lower.neutralised, entries,
  };
}

// ------------------------------------------------------------- fastest laps

export function fastestModel(analysis, { width = 640, rowHeight = 24 } = {}) {
  const drivers = driversByIndex(analysis);
  const styles = driverStyles(analysis);
  const rows = (analysis?.fastest_laps || []).filter((r) => isNumber(r.lap_time_ms));
  if (!rows.length) return { rows: [], width, height: 0 };
  const fastest = Number(rows[0].lap_time_ms);
  const deltaOf = (row) => (isNumber(row.delta_to_fastest_ms) ? Number(row.delta_to_fastest_ms) : Number(row.lap_time_ms) - fastest);
  const maxDelta = Math.max(500, ...rows.map(deltaOf), ...rows.map((r) => (isNumber(r.ideal_lap_ms) ? Math.max(0, r.ideal_lap_ms - fastest) : 0)));
  const margin = { l: 52, r: 84 };
  const x = scale(0, maxDelta, margin.l, width - margin.r);
  return {
    width, height: rows.length * rowHeight + 8, left: margin.l,
    rows: rows.map((row, i) => {
      const driver = drivers.get(row.car_index) || {};
      const code = driver.code || `#${row.car_index}`;
      const delta = deltaOf(row);
      return {
        ...row, code, player: Boolean(driver.is_player), y: 4 + i * rowHeight, colour: styles.get(row.car_index)?.colour || CONTEXT_STROKE,
        barEnd: Math.max(x(delta), margin.l + 3),
        idealX: isNumber(row.ideal_lap_ms) ? x(Math.max(0, row.ideal_lap_ms - fastest)) : null,
        label: delta === 0 ? formatLapTime(row.lap_time_ms) : `+${(delta / 1000).toFixed(3)} s`,
        text: `${code} · best ${formatLapTime(row.lap_time_ms)} (lap ${row.lap_number})${delta ? ` · ${formatSeconds(delta)} behind the fastest` : " · fastest"}`
          + (isNumber(row.ideal_lap_ms) ? ` · ideal ${formatLapTime(row.ideal_lap_ms)}` : "")
          + (row.traced === false ? " · timing only — no telemetry" : ""),
      };
    }),
  };
}

export function pitStopRows(analysis) {
  const drivers = driversByIndex(analysis);
  return (analysis?.pit_stops || []).map((stop) => ({
    ...stop,
    code: drivers.get(stop.car_index)?.code || `#${stop.car_index}`,
    lapText: isNumber(stop.lap_number) ? String(stop.lap_number) : "—",
    tyres: `${compoundInfo(stop.from_compound).letter} → ${compoundInfo(stop.to_compound).letter}`,
    tyresText: `${compoundInfo(stop.from_compound).label} to ${compoundInfo(stop.to_compound).label}`,
    kindText: stopKindText(stop),
    lossText: stopLossText(stop),
  }));
}

// ------------------------------------------------------------- headline

// The runner-up beside the winner, by status: a gap for a finisher, laps
// down for a lapped car (or one still running when the recording stopped),
// and a retirement as a retirement, never as laps down.
function runnerUpText(second) {
  if (!second) return "";
  const code = second.code || `#${second.car_index}`;
  switch (second.status) {
    case "finished": return isNumber(second.gap_to_winner_ms) ? `${code} ${gapWords(second.gap_to_winner_ms)}` : code;
    case "lapped":
    case "running": return [code, lapsDownText(second.laps_down)].filter(Boolean).join(" ");
    case "retired": return `${code} retired`;
    default: return [code, STATUS_TEXT[second.status] ? STATUS_TEXT[second.status].toLowerCase() : null].filter(Boolean).join(" ");
  }
}

export function headline(analysis) {
  const drivers = analysis?.drivers || [];
  const session = analysis?.session || {};
  const field = analysis?.field || {};
  const rc = analysis?.race_control || {};
  const isRace = Boolean(session.is_race);
  const provisional = Boolean(session.provisional);
  const partial = field.complete === false;
  const recordedCars = carsWithLaps(analysis);
  const single = isRace && !hasRaceOrder(analysis);
  // The game's own result for the player contradicts the order derived from
  // lap times (penalties): the derived order is not the result.
  const disputed = analysis?.official_result?.agrees === false;
  const byCar = driversByIndex(analysis);
  const winner = analysis?.winner_car_index !== null && analysis?.winner_car_index !== undefined ? byCar.get(analysis.winner_car_index) : null;
  const second = drivers.find((d) => d.finish_position === 2);
  const player = drivers.find((d) => d.is_player);
  const fastest = (analysis?.fastest_laps || []).find((r) => isNumber(r.lap_time_ms));
  const fastestDriver = fastest ? byCar.get(fastest.car_index) : null;
  const named = (d) => d.display_name || d.code || "—";
  const kpis = [];
  const place = [session.track_name || session.display_name, session.session_type].filter(Boolean).join(" · ");
  let title = place || "Session analysis";
  if (isRace && winner && !single) {
    const official = analysis?.basis?.finish_source === "classification";
    const settled = !provisional && (official || (!partial && !disputed));
    if (settled) {
      title = second && isNumber(second.gap_to_winner_ms) && second.status === "finished"
        ? `${named(winner)} wins by ${formatSeconds(second.gap_to_winner_ms)}`
        : `${named(winner)} wins`;
    } else if (provisional) title = `Provisional order · ${named(winner)} leads`;
    else if (partial) title = `${named(winner)} leads the recorded cars`;
    else title = `${named(winner)} first on lap times`;
    kpis.push({
      label: settled ? "Winner" : provisional ? "Provisional order" : partial ? "Leading recorded car" : "First on lap times",
      value: winner.code || "—",
      detail: [runnerUpText(second), isNumber(winner.classified_at_lap) ? `order at lap ${winner.classified_at_lap}` : null].filter(Boolean).join(" · "),
    });
  }
  if (player) kpis.push(isRace ? playerResult(analysis, player, { provisional, single }) : {
    label: "Your best", value: formatLapTime(player.best_lap_ms),
    detail: isNumber(player.best_lap_ms) ? `${countText(player.laps_recorded, "lap")} recorded` : "no timed lap",
  });
  if (fastest) {
    // Only the recorded laps can be compared when the record has gaps, the
    // field is partial or the recording stopped early.
    kpis.push({
      label: analysis?.record_complete === false || partial || provisional ? "Fastest recorded lap" : "Fastest lap",
      value: formatLapTime(fastest.lap_time_ms),
      detail: [fastestDriver?.code, isNumber(fastest.lap_number) ? `lap ${fastest.lap_number}` : null, fastest.traced === false ? "timing only" : null].filter(Boolean).join(" · "),
    });
  }
  if (isNumber(analysis?.ideal_lap?.best_sectors_ms)) kpis.push({ label: "Ideal lap", value: formatLapTime(analysis.ideal_lap.best_sectors_ms), detail: "best sectors in the session" });
  const { sc, vsc } = neutralLaps(analysis);
  const suspendedLaps = analysis?.suspended_laps || [];
  const recorded = rc.available === true;
  const coveredFrom = isNumber(rc.covered_from_lap) && Number(rc.covered_from_lap) > 1 ? Number(rc.covered_from_lap) : null;
  if (isRace) {
    const parts = [];
    if (suspendedLaps.length) parts.push(`red flag ${formatLapRanges(suspendedLaps)}`);
    if (sc.length) parts.push(`safety car ${formatLapRanges(sc)}`);
    if (vsc.length) parts.push(`VSC ${formatLapRanges(vsc)}`);
    if (!recorded) parts.push("safety cars not recorded");
    else if (coveredFrom) parts.push(`race control from lap ${coveredFrom}`);
    const count = new Set([...suspendedLaps, ...sc, ...vsc]).size;
    kpis.push({
      label: "Neutralised",
      value: count ? countText(count, "lap") : recorded ? "None" : "Unknown",
      detail: parts.join(" · ") || "green all race",
    });
  }
  const subtitle = (isRace ? [
    isNumber(session.leader_laps) && Number(session.leader_laps) > 0 ? countText(session.leader_laps, "lap") : null,
    suspendedLaps.length ? `red flag ${onLaps(suspendedLaps)}` : null,
    sc.length ? `safety car ${onLaps(sc)}` : null,
    vsc.length ? `VSC ${onLaps(vsc)}` : null,
    !recorded ? "safety cars not recorded" : coveredFrom ? `race control recorded from lap ${coveredFrom}` : null,
    single ? (recordedCars === 1 || drivers.some((d) => d.status === "unranked") ? "only one car recorded" : "no completed laps recorded")
      : partial && isNumber(field.cars_with_laps) && isNumber(field.cars) ? `${field.cars_with_laps} of ${field.cars} cars recorded` : null,
    single ? null : provisional ? "provisional order: the recording did not finish" : orderSourceText(analysis),
  ] : [
    session.display_name && session.display_name !== session.track_name ? session.display_name : null,
    drivers.length ? countText(drivers.length, "driver") : null,
    provisional ? "the recording did not finish" : null,
  ]).filter(Boolean).join(" · ");
  return { title, subtitle, kpis };
}

// Where the finishing order comes from, for the headline.
export function orderSourceText(analysis) {
  switch (analysis?.basis?.finish_source) {
    case "classification": return "the game's final classification, penalties applied";
    case "game_positions": return "order from the game's recorded positions; penalties not applied";
    default: return "order derived from lap times; penalties not applied";
  }
}

function playerResult(analysis, player, { provisional, single }) {
  const official = analysis?.official_result || {};
  const officialPosition = isNumber(official.player_position) && Number(official.player_position) > 0 ? Number(official.player_position) : null;
  const detail = [];
  let label = "Your result";
  let value = "—";
  if (officialPosition) {
    value = `P${officialPosition}`;
    if (official.agrees === false) detail.push("derived order differs (penalties not applied)");
    else detail.push("official result");
  } else if (player.status === "retired") {
    value = "DNF";
  } else if (player.status === "unranked" || (single && Number(player.laps_completed) > 0)) {
    detail.push("only recorded car: no order");
  } else if (isNumber(player.finish_position)) {
    value = `P${player.finish_position}`;
    label = provisional ? "Your position (provisional)"
      : analysis?.basis?.finish_source === "game_positions" ? "Your result (recorded order)" : "Your result (derived)";
  } else if (player.status === "incomplete_record") {
    detail.push("record incomplete: no position");
  }
  if (player.status === "lapped") detail.push(lapsDownText(player.laps_down));
  detail.push(stopsText(player));
  return { label, value, detail: detail.filter(Boolean).join(" · ") };
}

// ------------------------------------------------------------- data tables
// Each builder returns { caption, headers, rows }: every cell is text, or
// { text, lapId } for a lap that can open in Lap Lab.

export function paceTable(analysis) {
  const rows = (analysis?.race_pace || []).map((row) => [
    codeOf(analysis, row.car_index), isNumber(row.n) ? String(row.n) : "—", formatLapTime(row.median_ms),
    `${formatLapTime(row.q1_ms)}–${formatLapTime(row.q3_ms)}`, formatLapTime(row.min_ms), formatLapTime(row.max_ms),
    Object.entries(row.excluded || {}).filter(([, v]) => Number(v) > 0).map(([k, v]) => `${exclusionText(k)} (${v})`).join(", ") || "None",
  ]);
  return { caption: "Race pace per driver", headers: ["Driver", "Pace laps", "Median", "Middle half", "Fastest", "Slowest kept", "Left out"], rows };
}

export function traceTable(analysis, { focus = [], reference = "leader" } = {}) {
  const total = lastLap(analysis);
  const name = referenceName(analysis, reference);
  return {
    caption: `Gap per lap to ${name} for the focus drivers`,
    headers: ["Lap", ...focus.map((c) => codeOf(analysis, c))],
    rows: Array.from({ length: total }, (_, i) => [String(i + 1), ...traceReading(analysis, { focus, reference, lap: i + 1 }).map((r) => r.value)]),
  };
}

export function positionsTable(analysis) {
  const total = lastLap(analysis);
  const laps = lapLookup(analysis);
  return {
    caption: "Position at the end of every lap",
    headers: ["Driver", "Result", ...Array.from({ length: total }, (_, i) => `L${i + 1}`)],
    rows: (analysis?.drivers || []).map((d) => [
      d.code || `#${d.car_index}`, resultText(d),
      ...Array.from({ length: total }, (_, i) => positionText(laps.get(d.car_index)?.get(i + 1)?.position)),
    ]),
  };
}

export function strategyTable(analysis) {
  const stints = new Map((analysis?.stints || []).map((row) => [row.car_index, row.stints || []]));
  return {
    caption: "Tyre stints per driver",
    headers: ["Driver", "Stops", "Stints"],
    rows: (analysis?.drivers || []).map((d) => [
      d.code || `#${d.car_index}`, stopsText(d),
      (stints.get(d.car_index) || []).map((s) => `${compoundInfo(s.compound).label} L${s.start_lap}–${s.end_lap}`).join(" · ") || "No tyre data",
    ]),
  };
}

export function lapTimesTable(analysis, { focus = [] } = {}) {
  const total = lastLap(analysis);
  const laps = lapLookup(analysis);
  return {
    caption: "Lap times of the focus drivers",
    headers: ["Lap", ...focus.map((c) => codeOf(analysis, c))],
    rows: Array.from({ length: total }, (_, i) => [String(i + 1), ...focus.map((carIndex) => {
      const lap = laps.get(carIndex)?.get(i + 1);
      if (!lap) return "—";
      if (!timed(lap)) return "No lap time";
      return lap.pace_excluded ? `${formatLapTime(lap.lap_time_ms)} (${exclusionText(lap.pace_excluded)})` : formatLapTime(lap.lap_time_ms);
    })]),
  };
}

// One driver's laps, with a 44 px route into Lap Lab for every traced lap.
export function heatmapTable(analysis, carIndex) {
  const model = heatmapModel(analysis);
  const row = model.rows.find((r) => r.car_index === carIndex) || model.rows[0];
  const code = row?.code || "";
  return {
    caption: row ? `Laps of ${code} against their median` : "Laps against the driver's median",
    car_index: row ? row.car_index : null,
    headers: ["Lap", "Time", "Against median", "Note", "Lap Lab"],
    rows: (row?.cells || []).filter((c) => c.state !== "none").map((c) => [
      String(c.lap), c.state === "missing" ? "—" : c.time, c.delta === null ? "—" : deltaWords(c.delta).replace(/ than median$/, "").replace(/^at the median$/, "at median"),
      c.state === "missing" ? "Not recorded" : c.note || "—",
      c.openable ? { text: "Open in Lap Lab", lapId: c.lap_id, label: `Open ${code} lap ${c.lap} in Lap Lab` } : c.state === "missing" ? "—" : c.traced === false ? "Timing only" : "—",
    ]),
  };
}

export function lapseTable(analysis, frames) {
  const codes = driversByIndex(analysis);
  // A recording that stopped early ends at its last recorded lap, not a flag.
  const end = analysis?.session?.provisional ? "Last recorded lap" : "Finish";
  return {
    caption: "Running order at the end of every lap",
    headers: ["Lap", "Order", "Note"],
    rows: (frames || []).map((frame) => [
      String(frame.lap),
      frame.entries.map((e) => [e.label, codes.get(e.car_index)?.code || `#${e.car_index}`, lapsDownText(e.laps_down)].filter(Boolean).join(" ")).join(", ") || "—",
      [frame.final ? end : null, frame.suspended ? "Race suspended, order frozen" : null, frame.neutralised === "SC" ? "Safety car" : frame.neutralised === "VSC" ? "Virtual safety car" : null].filter(Boolean).join(" · ") || "—",
    ]),
  };
}

export function fastestTable(analysis) {
  const model = fastestModel(analysis);
  return {
    caption: "Fastest and ideal laps",
    headers: ["Driver", "Best lap", "Lap", "Behind fastest", "Ideal lap", "Telemetry"],
    rows: model.rows.map((r) => [
      r.code, formatLapTime(r.lap_time_ms), isNumber(r.lap_number) ? String(r.lap_number) : "—",
      r.label.startsWith("+") ? r.label : "Fastest", formatLapTime(r.ideal_lap_ms), r.traced === false ? "Timing only" : "Recorded",
    ]),
  };
}
