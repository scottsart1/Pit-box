"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const fmt = (n, digits = 1) =>
  n == null || !Number.isFinite(Number(n)) ? "—" : Number(n).toFixed(digits);
const seriesNames = {
  cup: "NASCAR Cup Series",
  oreilly: "O'Reilly Auto Parts Series",
  trucks: "Craftsman Truck Series",
  arca: "ARCA Menards Series",
};
let token =
  document.querySelector('meta[name="pitbox-token"]').content ||
  sessionStorage.getItem("nascar-token") ||
  "";
const fragment = new URLSearchParams(location.hash.replace(/^#/, ""));
if (fragment.get("token")) {
  token = fragment.get("token");
  history.replaceState(null, "", location.pathname);
}
if (token) sessionStorage.setItem("nascar-token", token);
let state = null,
  view = "race",
  sessionId = null,
  voice = false,
  radioId = 0,
  busy = false,
  selectedSession = null;
let regions = [],
  previewImage = null,
  drawing = null,
  lastSequence = Date.now(),
  settingsFilled = null;
let toastTimer;
const leaderLabel = document.createElement("label");
leaderLabel.textContent = "Leader's completed laps (if lapped)";
const leaderInput = document.createElement("input");
leaderInput.type = "number";
leaderInput.name = "leader_completed_laps";
leaderInput.min = "0";
leaderInput.max = "2200";
leaderLabel.append(leaderInput);
$("observation-form").querySelector(".form-grid").append(leaderLabel);
const leaderOption = document.createElement("option");
leaderOption.value = "leader_current_lap";
leaderOption.textContent = "Leader's current lap";
$("region-field").append(leaderOption);
if (document.querySelector('meta[name="pitbox-token"]').content) {
  const quit = document.createElement("button");
  quit.className = "text-button";
  quit.textContent = "Quit NASCAR desktop";
  quit.onclick = () =>
    action(async () => {
      await api("quit", {});
      toast("NASCAR desktop stopped. Open the app to reconnect.");
    });
  document.querySelector(".sidebar-bottom").append(quit);
}
function toast(text, error = false) {
  $("toast").textContent = text;
  $("toast").className = "toast" + (error ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").classList.add("hidden"), 6500);
}
async function api(
  path,
  body,
  method = body === undefined ? "GET" : "POST",
  raw = false,
) {
  const response = await fetch("/api/" + path, {
    method,
    headers: {
      "x-pitbox-key": token,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(path === "chat" ? 35000 : 15000),
  });
  if (!response.ok) {
    let message = "The request could not be completed.";
    try {
      const data = await response.json();
      message = Array.isArray(data.detail)
        ? data.detail.map((x) => x.msg).join("; ")
        : data.detail || message;
    } catch {}
    throw new Error(message);
  }
  return raw ? response : response.json();
}
async function action(fn) {
  try {
    return await fn();
  } catch (error) {
    toast(error.message || "Something went wrong. Try again.", true);
    return null;
  }
}
function setText(id, value) {
  $(id).textContent = value;
}
function showView(name) {
  view = name;
  window.scrollTo({ top: 0, behavior: "instant" });
  document
    .querySelectorAll(".view")
    .forEach((x) => x.classList.toggle("hidden", x.id !== "view-" + name));
  document.querySelectorAll("[data-view]").forEach((x) => {
    x.classList.toggle("active", x.dataset.view === name);
    x.setAttribute("aria-current", x.dataset.view === name ? "page" : "false");
  });
  if (name === "runs") action(loadRuns);
  if (name === "strategy") fillSettings();
  if (name === "connection") action(loadConnection);
}
document
  .querySelectorAll("[data-view],[data-goto]")
  .forEach((x) =>
    x.addEventListener("click", () =>
      showView(x.dataset.view || x.dataset.goto),
    ),
  );
document.querySelector(".brand").addEventListener("click", (event) => {
  event.preventDefault();
  showView("race");
});
document
  .querySelectorAll("[data-close]")
  .forEach((x) =>
    x.addEventListener("click", () => $(x.dataset.close).close()),
  );
function openDialog(id, needsSession = false) {
  if (needsSession && (!state?.session_id || state.config.mode === "demo"))
    return toast("Start or resume a driver weekend first.");
  $(id).showModal();
}
$("new-weekend").onclick = $("welcome-start").onclick = () =>
  openDialog("weekend-dialog");
$("observe-open").onclick = () => openDialog("observation-dialog", true);
$("pit-log-open").onclick = () => openDialog("pit-dialog", true);
$("lap-log-open").onclick = () => openDialog("lap-dialog", true);
$("import-open").onclick = () => openDialog("import-dialog");
$("demo-start").onclick = () =>
  action(async () => {
    await api("demo", {});
    showView("race");
    await pollOnce();
  });
$("stop-demo").onclick = () =>
  action(async () => {
    await api("demo/stop", {});
    toast(
      "Sample replay paused. Its readings will age out. Create a weekend to use your own data.",
    );
  });

function render(s) {
  state = s;
  if (sessionId !== s.session_id) {
    sessionId = s.session_id;
    settingsFilled = null;
    radioId = s.radio.at(-1)?.id || 0;
    $("chat-history").textContent = "";
    $("chat-history").classList.add("hidden");
  }
  $("welcome").classList.toggle("hidden", !!s.session_id);
  $("race-content").classList.toggle("hidden", !s.session_id);
  $("demo-banner").classList.toggle("hidden", s.config.mode !== "demo");
  const sources = [...new Set(Object.values(s.evidence).map((x) => x.source))];
  const stale = s.stale_fields.length > 0;
  const connection = !s.session_id
    ? "Workspace ready · start a weekend"
    : s.config.mode === "demo"
      ? "Sample race · simulated evidence"
      : s.observer.running
        ? "HUD observer running"
        : sources.includes("manual")
          ? "Manual observations"
          : sources.includes("adapter")
            ? "Adapter observations"
            : "Waiting for observations";
  setText(
    "health-text",
    connection + (stale ? " · some readings are stale" : ""),
  );
  $("health-dot").className =
    s.session_id && sources.length && !stale ? "good" : "warn";
  setText(
    "engineer-mode",
    s.engineer_configured ? "Conversation + local briefs" : "Local race briefs",
  );
  setText(
    "race-series",
    (
      seriesNames[s.config.series] +
      " · " +
      s.config.session_type
    ).toUpperCase(),
  );
  setText("race-track", s.config.track);
  setText("race-name", s.config.name);
  const v = s.values,
    f = s.fuel;
  setText("position", v.position == null ? "—" : "P" + v.position);
  setText(
    "lap",
    v.completed_laps == null
      ? "— / " + s.config.total_laps
      : (v.flag === "checkered" ? v.completed_laps : v.completed_laps + 1) +
          " / " +
          s.config.total_laps,
  );
  setText(
    "flag",
    (v.flag || "unknown").replace(/^./, (c) => c.toUpperCase()),
  );
  $("flag").className =
    "color-" +
    (v.flag === "green"
      ? "green"
      : v.flag === "red"
        ? "red"
        : v.flag === "yellow"
          ? "yellow"
          : "");
  setText(
    "pit-access",
    v.pit_open === true
      ? "Reported open"
      : v.pit_open === false
        ? "Closed"
        : "Confirm in game",
  );
  setText("fuel-laps", fmt(f.fuel_laps));
  $("fuel-fill").style.width = (v.fuel_pct ?? 0) + "%";
  setText(
    "fuel-tank",
    v.fuel_pct == null ? "Tank unknown" : fmt(v.fuel_pct) + "% of tank",
  );
  setText(
    "fuel-rate",
    f.green.value == null
      ? "Burn unknown"
      : fmt(f.green.value, 2) + "% / green lap",
  );
  setText(
    "fuel-source",
    f.green.source === "measured"
      ? f.green.samples + " measured fuel laps"
      : f.green.source === "driver estimate"
        ? "Driver burn estimate"
        : "Waiting for evidence",
  );
  setText("fuel-message", f.message);
  $("fuel-message").className = "callout " + f.status;
  setText("laps-left", fmt(f.remaining_laps, 0));
  setText("stage-left", fmt(f.stage_remaining, 0));
  setText(
    "stage-label",
    v.completed_laps == null
      ? "Waiting for current lap"
      : f.stage_end
        ? "Stage ends · lap " + f.stage_end
        : f.overtime
          ? "Overtime · confirm target"
          : "Final stage / no stages",
  );
  setText("reserve", fmt(f.reserve_laps, 1) + " laps");
  setText("stops", f.minimum_stops == null ? "Unknown" : f.minimum_stops);
  setText(
    "horizon-note",
    f.overtime
      ? "Overtime has passed the configured distance. Race control determines the finish."
      : "Race settings drive this plan. A stage ending does not confirm a caution.",
  );
  const progress = Math.min(
    100,
    (((v.completed_laps || 0) + (v.lap_fraction || 0)) / s.config.total_laps) *
      100,
  );
  $("stage-track").innerHTML =
    `<div class="progress" style="width:${progress}%"></div>` +
    s.config.stage_ends
      .map(
        (n) =>
          `<span class="stage-mark" style="left:${(n / s.config.total_laps) * 100}%" title="Stage end: ${n}"></span>`,
      )
      .join("");
  setText("clean-laps", s.run.count + " clean laps");
  setText("best-lap", fmt(s.run.best_s, 3) + (s.run.best_s ? " s" : ""));
  const latest = s.run.stints.at(-1);
  setText("median-lap", fmt(latest?.median_s, 3));
  setText("spread", fmt(latest?.spread_s, 3) + (latest ? " s" : ""));
  drawPace(s.laps);
  $("tires").innerHTML = ["lf", "rf", "lr", "rr"]
    .map((w) => {
      const life = v.tires?.[w];
      const color =
        life == null
          ? "unknown"
          : life < 25
            ? "danger"
            : life < 55
              ? "warn"
              : "";
      return `<div class="tire ${color}"><span>${w.toUpperCase()}</span><strong>${fmt(life, 0)}${life == null ? "" : "%"}</strong></div>`;
    })
    .join("");
  const cars = [...(v.opponents || [])].sort((a, b) => a.position - b.position);
  setText(
    "field-count",
    cars.length
      ? cars.length + " opponents observed"
      : "No current field observations",
  );
  $("field-rows").innerHTML = cars.length
    ? cars
        .map(
          (c) =>
            `<tr><td>P${c.position}</td><td><b>#${esc(c.car)}</b><small>${esc(c.name)}</small></td><td>${c.laps_down ? "Lapped" : c.gap_s == null ? "Unknown" : "+" + fmt(c.gap_s, 2) + "s"}</td><td>${c.laps_down}</td><td>${c.last_pit_lap == null ? "Unknown" : "Lap " + c.last_pit_lap}</td><td>${c.fuel_laps == null ? "Unknown" : fmt(c.fuel_laps) + " laps"}</td></tr>`,
        )
        .join("")
    : '<tr><td class="table-empty" colspan="6">No field evidence yet. The engineer will say when an opponent’s plan is unknown.</td></tr>';
  setText("strategy-access", s.pits.access_message);
  $("pit-comparison").innerHTML =
    '<div class="table-scroll"><table><thead><tr><th>Service</th><th>Stationary</th><th>Total loss</th></tr></thead><tbody>' +
    s.pits.options
      .map(
        (p) =>
          `<tr><td>${esc(p.name)}</td><td>${p.service_s == null ? "Needs measurement" : fmt(p.service_s) + "s"}</td><td>${p.total_loss_s == null ? "Unknown" : fmt(p.total_loss_s) + "s"}</td></tr>`,
      )
      .join("") +
    "</tbody></table></div>";
  setText("observer-status", s.observer.running ? "Reading HUD" : "Stopped");
  $("data-health").innerHTML =
    `<div class="status-grid"><div>Accepted observations<b>${s.metrics.accepted}</b></div><div>Stale fields<b>${s.stale_fields.length}</b></div><div>OCR frames<b>${s.observer.frames}</b></div><div>OCR processing<b>${s.observer.latency_ms == null ? "—" : s.observer.latency_ms + " ms"}</b></div></div>` +
    (s.observer.last_error
      ? `<p class="color-yellow">${esc(s.observer.last_error)}</p>`
      : "");
  if (s.observer.running) renderReadings(s.observer.readings);
  if (view === "strategy" && settingsFilled !== s.session_id) fillSettings();
  const fresh = s.radio.filter(
    (x) => x.id > radioId && Date.now() / 1000 - x.at < 9,
  );
  for (const call of fresh) {
    setText("radio-text", (call.demo ? "Sample race: " : "") + call.text);
    speak(call.text, call.kind === "urgent");
  }
  radioId = Math.max(radioId, ...s.radio.map((x) => x.id));
}
function drawPace(laps) {
  const rows = laps
    .filter((x) => x.clean && ["green", "white"].includes(x.flag) && x.time_s)
    .slice(-45);
  if (rows.length < 2) {
    $("pace-chart").innerHTML =
      '<div class="empty">Log clean laps to see how the run develops.</div>';
    return;
  }
  const w = 600,
    h = 105,
    low = Math.min(...rows.map((x) => x.time_s)) - 0.15,
    high = Math.max(...rows.map((x) => x.time_s)) + 0.15;
  const points = rows.map(
    (x, i) =>
      `${35 + (i / (rows.length - 1)) * (w - 45)},${8 + ((high - x.time_s) / (high - low)) * (h - 22)}`,
  );
  $("pace-chart").innerHTML =
    `<svg viewBox="0 0 ${w} 125" preserveAspectRatio="none" role="img" aria-label="Clean lap times from lap ${rows[0].number} to ${rows.at(-1).number}"><defs><linearGradient id="pacefade" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#ffc15c" stop-opacity=".2"/><stop offset="1" stop-color="#ffc15c" stop-opacity="0"/></linearGradient></defs><path d="M35 8H595 M35 50H595 M35 92H595" stroke="#293745" fill="none"/><text x="0" y="12" fill="#96a8b7" font-size="9">${high.toFixed(1)}</text><text x="0" y="95" fill="#96a8b7" font-size="9">${low.toFixed(1)}</text><polygon points="35,105 ${points.join(" ")} 590,105" fill="url(#pacefade)"/><polyline points="${points.join(" ")}" stroke="#ffc15c" stroke-width="2" fill="none"/><text x="35" y="123" fill="#96a8b7" font-size="9">LAP ${rows[0].number}</text><text x="560" y="123" fill="#96a8b7" font-size="9">${rows.at(-1).number}</text></svg>`;
}
async function pollOnce() {
  const s = await api("state");
  document.body.classList.remove("offline");
  render(s);
}
async function poll() {
  try {
    await pollOnce();
  } catch (error) {
    document.body.classList.add("offline");
    setText(
      "health-text",
      "Connection lost · displayed race values are historical",
    );
    $("health-dot").className = "warn";
  } finally {
    setTimeout(poll, 1000);
  }
}
function numbers(data, names) {
  for (const key of names)
    data[key] = data[key] === "" ? null : Number(data[key]);
  return data;
}
function formData(id) {
  return Object.fromEntries(new FormData($(id)).entries());
}
function bindForm(id, fn) {
  $(id).addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter;
    if (button) button.disabled = true;
    try {
      await action(() => fn(formData(id)));
    } finally {
      if (button) button.disabled = false;
    }
  });
}
bindForm("weekend-form", async (data) => {
  data.stage_ends = data.stage_ends.trim()
    ? data.stage_ends.split(",").map((x) => Number(x.trim()))
    : [];
  if (data.stage_ends.some((x) => !Number.isInteger(x)))
    throw new Error("Use comma-separated whole lap numbers for stage ends.");
  numbers(data, ["total_laps", "fuel_burn_green", "fuel_burn_yellow"]);
  data.stage_cautions = !!data.stage_cautions;
  data.overtime_enabled = !!data.overtime_enabled;
  await api("sessions", data);
  $("weekend-dialog").close();
  showView("race");
  await pollOnce();
  toast("Weekend created. Log an observation or connect the HUD observer.");
});
bindForm("observation-form", async (data) => {
  const sample = {};
  for (const [key, value] of Object.entries(data))
    if (value !== "")
      sample[key] =
        key === "flag"
          ? value
          : ["pit_open", "in_pit"].includes(key)
            ? JSON.parse(value)
            : Number(value);
  if (!Object.keys(sample).length)
    throw new Error("Enter at least one observation.");
  const result = await api("observations", {
    session_id: state.session_id,
    sequence: ++lastSequence,
    source: "manual",
    confidence: 1,
    sample,
  });
  if (!result.accepted) throw new Error(result.reason);
  $("observation-dialog").close();
  $("observation-form").reset();
  await pollOnce();
  toast("Observation saved.");
});
function fillSettings() {
  if (!state) return;
  for (const input of $("strategy-settings").elements) {
    if (input.name) input.value = state.config[input.name] ?? "";
  }
  settingsFilled = state.session_id;
}
bindForm("strategy-settings", async (data) => {
  if (!state?.session_id || state.config.mode === "demo")
    throw new Error("Start a driver weekend to save race assumptions.");
  for (const key of Object.keys(data))
    data[key] =
      key === "service_parallel"
        ? data[key] === "true"
        : data[key] === ""
          ? null
          : Number(data[key]);
  await api("config", { ...state.config, ...data }, "PUT");
  await pollOnce();
  toast("Race assumptions saved.");
});
bindForm("scenario-form", async (data) => {
  if (!state?.session_id)
    throw new Error("Start a weekend or sample race first.");
  numbers(data, Object.keys(data));
  const result = await api("scenario", data);
  const p = result.plan;
  $("scenario-result").innerHTML =
    `<div class="scenario-number">${p.margin_pct == null ? "More evidence needed" : (p.margin_pct >= 0 ? "+" : "") + fmt(p.margin_pct) + "% tank margin"}</div><p>${esc(p.message)}</p><p class="muted">Assumes ${data.yellow_laps} yellow laps, ${data.saving_percent}% green fuel saving and ${data.reserve_laps} reserve laps. ${state.config.overtime_enabled ? "" : "Overtime is disabled in the race settings."}</p>`;
});
bindForm("handling-form", async (data) => {
  data.available_controls = [
    ...$("handling-form").querySelectorAll('input[name="control"]:checked'),
  ].map((x) => x.value);
  delete data.control;
  const r = await api("handling", data);
  $("handling-result").innerHTML =
    `<article class="card result-card"><div class="eyebrow">${esc(r.scope.toUpperCase())} REVIEW · ${esc(r.goal.replace("_", " ").toUpperCase())}</div><h2>${esc(r.diagnosis)}</h2><p>${esc(r.driving_check)}</p>${r.actions.map((x) => `<div class="test-action"><span class="label">${esc(x.control)}</span><h3>${esc(x.action)}</h3><p class="muted">${esc(x.why)}</p></div>`).join("")}<h3 style="margin-top:24px">Prove the change</h3><ol>${r.test_plan.map((x) => `<li>${esc(x)}</li>`).join("")}</ol><p class="muted">${esc(r.caveat)}</p></article>`;
});
bindForm("pit-form", async (data) => {
  numbers(data, ["fuel_added_pct", "damage_s"]);
  await api("pits", data);
  $("pit-dialog").close();
  await pollOnce();
  toast("Stop recorded. A new stint has started.");
});
bindForm("lap-form", async (data) => {
  numbers(data, ["number", "time_s", "fuel_used_pct"]);
  data.clean = !!data.clean;
  data.stint = state.stint;
  await api("laps", data);
  $("lap-dialog").close();
  await pollOnce();
  toast("Lap recorded.");
  loadRuns();
});

function speak(text, urgent = false) {
  if (!voice) return;
  if (window.NascarNative?.speak) {
    window.NascarNative.speak(text);
    return;
  }
  if (!window.speechSynthesis) return;
  if (urgent || speechSynthesis.pending) speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.06;
  utterance.lang = "en-US";
  speechSynthesis.speak(utterance);
}
$("radio-toggle").onclick = () => {
  voice = !voice;
  setText("radio-toggle", voice ? "Radio on" : "Radio off");
  $("radio-toggle").setAttribute("aria-pressed", String(voice));
  if (voice) speak("Crew chief radio ready.");
  else {
    window.speechSynthesis?.cancel();
    window.NascarNative?.stopSpeech?.();
  }
};
function reply(result) {
  setText("radio-text", result.text);
  speak(result.text);
}
document
  .querySelectorAll("[data-brief]")
  .forEach(
    (x) =>
      (x.onclick = () =>
        action(async () => reply(await api("brief/" + x.dataset.brief)))),
  );
bindForm("chat-form", async () => {
  if (busy) return;
  const message = $("chat-message").value.trim();
  if (!message) return;
  busy = true;
  setText("radio-text", "Looking at the race evidence…");
  const p = document.createElement("p");
  p.textContent = "You: " + message;
  $("chat-history").append(p);
  while ($("chat-history").children.length > 8)
    $("chat-history").firstChild.remove();
  $("chat-history").classList.remove("hidden");
  try {
    const result = await api("chat", { message });
    reply(result);
    $("chat-message").value = "";
  } catch (error) {
    setText(
      "radio-text",
      "The question could not be completed. Local brief buttons are still available.",
    );
    throw error;
  } finally {
    busy = false;
  }
});
let recognition = null;
window.nascarSpeechResult = (text) => {
  $("microphone").classList.remove("recording");
  $("chat-message").value = text;
  $("chat-form").requestSubmit();
};
window.nascarSpeechError = (text) => {
  $("microphone").classList.remove("recording");
  toast(text, true);
};
$("microphone").onclick = () => {
  window.speechSynthesis?.cancel();
  window.NascarNative?.stopSpeech?.();
  if (window.NascarNative?.listen) {
    $("microphone").classList.add("recording");
    window.NascarNative.listen();
    return;
  }
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition)
    return toast(
      "Speech recognition is unavailable in this browser. Use Chrome or the Android companion, or type your question.",
    );
  if (recognition) {
    recognition.stop();
    recognition = null;
    return;
  }
  recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.interimResults = false;
  recognition.onresult = (e) =>
    window.nascarSpeechResult(e.results[0][0].transcript);
  recognition.onerror = (e) =>
    window.nascarSpeechError(
      "Microphone: " + e.error + ". You can type your question instead.",
    );
  recognition.onend = () => {
    recognition = null;
    $("microphone").classList.remove("recording");
  };
  recognition.start();
  $("microphone").classList.add("recording");
};
async function loadRuns() {
  const rows = await api("sessions");
  $("session-list").innerHTML = rows.length
    ? rows
        .map(
          (x) =>
            `<div class="session-row"><div><b>${esc(x.config.name)}</b><small>${esc(x.config.track)} · ${x.lap_count} laps · ${x.config.mode === "demo" ? "SAMPLE · " : ""}${new Date(x.created * 1000).toLocaleDateString()}</small></div><button data-session="${x.id}">Review →</button></div>`,
        )
        .join("")
    : '<p class="result-placeholder">Your first weekend will appear here. Sessions save automatically.</p>';
  $("session-list")
    .querySelectorAll("[data-session]")
    .forEach(
      (x) => (x.onclick = () => action(() => loadSession(x.dataset.session))),
    );
}
async function loadSession(id) {
  const r = await api("sessions/" + id);
  selectedSession = r;
  $("export-session").classList.remove("hidden");
  const laps = r.laps.filter((x) => x.clean && x.time_s && x.flag === "green");
  $("session-detail").innerHTML =
    `<div class="eyebrow">${r.config.mode === "demo" ? "SAMPLE RACE" : "SAVED WEEKEND"}</div><h2>${esc(r.config.name)}</h2><p>${esc(r.config.track)} · ${esc(seriesNames[r.config.series])}</p><div class="mini-stats"><div><span class="label">RECORDED LAPS</span><b>${r.laps.length}</b></div><div><span class="label">BEST CLEAN</span><b>${laps.length ? fmt(Math.min(...laps.map((x) => x.time_s)), 3) : "—"}</b></div></div><div class="table-scroll" style="margin-top:20px"><table><thead><tr><th>Lap</th><th>Time</th><th>Fuel %</th><th>Stint</th><th>Flag</th></tr></thead><tbody>${r.laps
      .slice(-60)
      .map(
        (x) =>
          `<tr><td>${x.number}</td><td>${fmt(x.time_s, 3)}</td><td>${fmt(x.fuel_used_pct, 2)}</td><td>${x.stint}</td><td>${esc(x.flag)}${x.clean ? "" : " · excluded"}</td></tr>`,
      )
      .join(
        "",
      )}</tbody></table></div><h3 style="margin-top:20px">Notebook</h3>${r.notes.length ? r.notes.map((n) => `<p class="muted"><b>${esc(n.kind)}</b> · ${esc(n.kind === "handling" ? n.data.review.diagnosis : JSON.stringify(n.data))}</p>`).join("") : '<p class="muted">No handling notes or pit stops logged.</p>'}<button class="button" id="resume-session">Resume this weekend</button><p class="muted">Saved observations remain historical until new evidence arrives.</p>`;
  $("resume-session").onclick = () =>
    action(async () => {
      await api("sessions/" + id + "/resume", {});
      await pollOnce();
      showView("race");
      toast(
        "Weekend resumed. Refresh the observations before making live calls.",
      );
    });
}
$("refresh-runs").onclick = () => action(loadRuns);
async function download(blob, name) {
  if (window.NascarNative?.saveFile) {
    window.NascarNative.saveFile(name, blob.type, await blob.text());
    return;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("export-session").onclick = () => {
  if (selectedSession)
    download(
      new Blob([JSON.stringify(selectedSession, null, 2)], {
        type: "application/json",
      }),
      "nascar-weekend-" + selectedSession.id.slice(0, 8) + ".json",
    );
};
$("template-download").onclick = () =>
  action(async () =>
    download(
      await (await api("template.csv", undefined, "GET", true)).blob(),
      "nascar-laps-template.csv",
    ),
  );
bindForm("import-form", async (data) => {
  const file = $("import-file").files[0];
  if (!file) throw new Error("Choose a CSV file.");
  if (file.size > 500000)
    throw new Error("CSV files must be smaller than 500 KB.");
  const text = await file.text();
  const r = await api("import", {
    config: {
      name: file.name.replace(/\.csv$/i, ""),
      track: data.track,
      track_type: data.track_type,
      session_type: "practice",
    },
    csv: text,
  });
  $("import-dialog").close();
  await pollOnce();
  showView("runs");
  await loadSession(r.id);
  toast(r.laps + " laps imported into a separate weekend.");
});
async function loadConnection() {
  try {
    const r = await api("settings");
    $("engineer-settings").elements.model.value = r.model;
    setText(
      "key-status",
      r.configured
        ? "Key configured. Stored for this NASCAR installation."
        : "No key configured. Local race tools are ready.",
    );
  } catch (error) {
    setText("key-status", "Manage the API key on the desktop.");
  }
  const c = await api("observer/config");
  if (!regions.length) {
    regions = c.regions;
    drawCalibration();
  }
}
bindForm("engineer-settings", async (data) => {
  if (!data.api_key) delete data.api_key;
  const r = await api("settings", data, "PUT");
  $("engineer-settings").elements.api_key.value = "";
  setText(
    "key-status",
    r.configured
      ? "Key configured. Ready for a question."
      : "No key configured.",
  );
  toast("Engineer settings saved.");
});
$("clear-key").onclick = () =>
  action(async () => {
    await api(
      "settings",
      { api_key: "", model: $("engineer-settings").elements.model.value },
      "PUT",
    );
    await loadConnection();
    toast("Saved key removed.");
  });
function renderPairing(connections, index = 0) {
  const r = connections[index];
  $("pairing-detail").innerHTML =
    `<p>Scan this code with your tablet camera, then open the NASCAR companion.</p>${connections.length > 1 ? `<label>PC network address<select id="pairing-network">${connections.map((c, i) => `<option value="${i}" ${i === index ? "selected" : ""}>${esc(c.address)}</option>`).join("")}</select></label><p class="muted">Choose the address on the same home network as your tablet.</p>` : ""}<img src="data:image/svg+xml;base64,${r.qr_base64}" alt="Tablet pairing QR code" width="210" height="210"><p>PC address: <b>${esc(r.address)}</b></p><button id="copy-invitation" class="button">Copy connection invitation</button><label>Or copy and paste into the companion<input readonly id="pairing-invitation" value="${esc(r.url)}"></label><details><summary>Verify the connection</summary><p>Certificate fingerprint:<br><code>${esc(r.fingerprint)}</code></p></details><p class="muted">This invitation grants access to the workspace. Keep it on your devices.</p>`;
  if ($("pairing-network"))
    $("pairing-network").onchange = (e) =>
      renderPairing(connections, Number(e.target.value));
  $("copy-invitation").onclick = () =>
    action(async () => {
      await navigator.clipboard.writeText(r.url);
      toast("Invitation copied. Paste it into the NASCAR companion.");
    });
}
$("show-pairing").onclick = () =>
  action(async () => {
    const r = await api("pairing");
    if (!r.url) {
      $("pairing-detail").textContent =
        "No local network address is available. Connect the PC to your home network and restart the NASCAR desktop with tablet connections enabled.";
      return;
    }
    renderPairing(r.connections || [r]);
  });
$("refresh-windows").onclick = () =>
  action(async () => {
    const rows = await api("windows");
    $("window-select").innerHTML =
      '<option value="">Choose the game window</option>' +
      rows
        .map((x) => `<option value="${x.id}">${esc(x.title)}</option>`)
        .join("");
  });
function windowId() {
  const id = Number($("window-select").value);
  if (!id) throw new Error("Choose the game window first.");
  return id;
}
$("capture-preview").onclick = () =>
  action(async () => {
    const response = await api(
      "windows/" + windowId() + "/frame",
      undefined,
      "GET",
      true,
    );
    const url = URL.createObjectURL(await response.blob());
    const img = new Image();
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = reject;
      img.src = url;
    });
    URL.revokeObjectURL(url);
    previewImage = img;
    $("calibration").width = img.width;
    $("calibration").height = img.height;
    drawCalibration();
    toast("Select a field, then drag a box around its value.");
  });
function drawCalibration() {
  const canvas = $("calibration"),
    ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (previewImage)
    ctx.drawImage(previewImage, 0, 0, canvas.width, canvas.height);
  for (const r of regions) {
    ctx.strokeStyle = "#ffc15c";
    ctx.lineWidth = 3;
    ctx.strokeRect(
      r.x * canvas.width,
      r.y * canvas.height,
      r.width * canvas.width,
      r.height * canvas.height,
    );
    ctx.fillStyle = "#ffc15c";
    ctx.font = "18px Segoe UI";
    ctx.fillText(
      r.field,
      r.x * canvas.width + 3,
      Math.max(18, r.y * canvas.height - 5),
    );
  }
  $("region-list").innerHTML = regions.length
    ? regions
        .map(
          (r, i) =>
            `<span class="region-chip">${esc(r.field)}<button data-remove="${i}" aria-label="Remove ${esc(r.field)} region">×</button></span>`,
        )
        .join("")
    : "No regions selected.";
  $("region-list")
    .querySelectorAll("[data-remove]")
    .forEach(
      (x) =>
        (x.onclick = () => {
          regions.splice(Number(x.dataset.remove), 1);
          drawCalibration();
        }),
    );
}
function point(event) {
  const r = $("calibration").getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(0.999, (event.clientX - r.left) / r.width)),
    y: Math.max(0, Math.min(0.999, (event.clientY - r.top) / r.height)),
  };
}
$("calibration").onpointerdown = (e) => {
  if (!previewImage) return;
  drawing = point(e);
  $("calibration").setPointerCapture(e.pointerId);
};
$("calibration").onpointermove = (e) => {
  if (!drawing) return;
  drawCalibration();
  const p = point(e),
    c = $("calibration"),
    ctx = c.getContext("2d");
  ctx.strokeStyle = "#49d17d";
  ctx.lineWidth = 3;
  ctx.strokeRect(
    drawing.x * c.width,
    drawing.y * c.height,
    (p.x - drawing.x) * c.width,
    (p.y - drawing.y) * c.height,
  );
};
$("calibration").onpointerup = (e) => {
  if (!drawing) return;
  const p = point(e);
  const region = {
    field: $("region-field").value,
    x: Math.min(p.x, drawing.x),
    y: Math.min(p.y, drawing.y),
    width: Math.abs(p.x - drawing.x),
    height: Math.abs(p.y - drawing.y),
  };
  drawing = null;
  if (region.width > 0.005 && region.height > 0.005) {
    regions = regions.filter((x) => x.field !== region.field);
    regions.push(region);
  }
  drawCalibration();
};
function ocrConfig() {
  if (!regions.length) throw new Error("Draw at least one HUD region.");
  return { window_id: windowId(), regions, interval_s: 1 };
}
function renderReadings(r) {
  $("ocr-readings").textContent =
    Object.entries(r)
      .map(([k, v]) => `${k}: ${v.value ?? "unreadable"}  [${v.text}]`)
      .join("\n") || "No readable regions yet.";
}
$("test-regions").onclick = () =>
  action(async () =>
    renderReadings(await api("observer/preview", ocrConfig())),
  );
$("start-observer").onclick = () =>
  action(async () => {
    await api("observer/start", ocrConfig());
    toast(
      "HUD observer started. Readings need two consistent frames before use.",
    );
    await pollOnce();
  });
$("stop-observer").onclick = () =>
  action(async () => {
    await api("observer/stop", {});
    toast("Observer stopped.");
    await pollOnce();
  });

action(async () => {
  const c = await api("catalog");
  setText("version", "NASCAR workspace · " + c.version);
  $("tracks").innerHTML = c.tracks
    .map((t) => `<option value="${esc(t.name)}"></option>`)
    .join("");
  $("weekend-form").elements.track.onchange = () => {
    const t = c.tracks.find(
      (x) => x.name === $("weekend-form").elements.track.value,
    );
    if (t) $("weekend-form").elements.track_type.value = t.type;
  };
});
poll();
