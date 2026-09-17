(() => {
  "use strict";
  const BASE = "https://pitwall-activation.sarthakvij123450.workers.dev";
  const el = id => document.getElementById(id);
  const form = el("usageReportLogin"), field = el("usageReportKey"), status = el("usageReportStatus"), report = el("usageReportData");
  const number = value => new Intl.NumberFormat().format(value);
  const tables = ["usageJourneyRows", "usageRetentionRows", "usageFeatureRows", "usageVersionRows"];
  let key = "", pending = null, generation = 0;
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const platform = value => value === "windows" || value === "android";
  const events = new Set(["app_started", "app_used", "racing", "engineer", "voice", "analysis", "transfer"]);

  function clear() {
    report.hidden = true;
    for (const id of tables) el(id).replaceChildren();
    el("usageReportUpdated").textContent = "";
    el("usageReportPeriod").textContent = "";
  }
  function lock() {
    generation++;
    pending?.abort();
    pending = null;
    key = field.value = "";
    clear();
    form.hidden = false;
    el("usageReportRefresh").disabled = false;
    status.textContent = "Report locked.";
  }
  function validate(data) {
    const fail = () => { throw new Error("Usage report data could not be read."); };
    if (data?.metric !== "opted_in_installations" || !Number.isFinite(Date.parse(data.generated_at)) ||
        !/^\d{4}-\d{2}-\d{2}$/.test(data.activity_since)) fail();
    for (const name of ["installations", "features", "recent", "versions", "retention"]) if (!Array.isArray(data[name])) fail();
    if (data.installations.length > 2 || data.features.length > 14 || data.recent.length > 2 || data.versions.length > 50 || data.retention.length !== 3) fail();
    for (const row of data.installations) if (!platform(row.platform) || !count(row.installations) || !count(row.new_30d)) fail();
    for (const row of data.features) if (!platform(row.platform) || !events.has(row.event) || !count(row.installations)) fail();
    for (const row of data.recent) if (!platform(row.platform) || !count(row.active_7d) || !count(row.active_today)) fail();
    for (const row of data.versions) if (!platform(row.platform) || typeof row.version !== "string" || row.version.length > 40 || !count(row.installations)) fail();
    data.retention.forEach((cohort, index) => {
      if (cohort.day !== [1, 7, 30][index] || !Array.isArray(cohort.platforms) || cohort.platforms.length > 2) fail();
      for (const row of cohort.platforms) if (!platform(row.platform) || !count(row.eligible) || !count(row.returned) || row.returned > row.eligible) fail();
    });
  }
  function addRow(id, cells) {
    const row = document.createElement("tr");
    cells.forEach((value, index) => {
      const cell = document.createElement(index ? "td" : "th");
      if (!index) cell.scope = "row";
      cell.textContent = String(value);
      row.appendChild(cell);
    });
    el(id).appendChild(row);
  }
  function render(data, downloads) {
    clear();
    const pair = (rows, fieldName, filter = () => true) => ["windows", "android"].map(p => rows.filter(row => row.platform === p && filter(row)).reduce((n, row) => n + row[fieldName], 0));
    const metric = (name, values, table = "usageJourneyRows") => addRow(table, [name, ...values.map(number), number(values[0] + values[1])]);
    if (downloads) metric("Download starts (not installations)", pair(downloads.daily, "starts", row => row.day >= data.activity_since));
    else addRow("usageJourneyRows", ["Download starts (temporarily unavailable)", "—", "—", "—"]);
    metric("First reported use · opted-in installs", pair(data.installations, "new_30d"));
    metric("Active use · last 30 days", pair(data.features, "installations", row => row.event === "app_used"));
    metric("Active use · last 7 days", pair(data.recent, "active_7d"));
    metric("Active use · today", pair(data.recent, "active_today"));
    metric("Working driving telemetry", pair(data.features, "installations", row => row.event === "racing"));
    for (const [event, label] of [["engineer", "Engineer reply delivered"], ["voice", "Voice command received"], ["analysis", "Lap comparison opened / created"], ["transfer", "History transfer started (beta)"]]) {
      metric(label, pair(data.features, "installations", row => row.event === event), "usageFeatureRows");
    }
    const retention = (returned, eligible) => eligible ? `${number(returned)} / ${number(eligible)} (${(100 * returned / eligible).toFixed(1)}%)` : "Not yet eligible";
    for (const cohort of data.retention) {
      const returned = pair(cohort.platforms, "returned"), eligible = pair(cohort.platforms, "eligible");
      addRow("usageRetentionRows", [`Day ${cohort.day}`, retention(returned[0], eligible[0]), retention(returned[1], eligible[1]), retention(returned[0] + returned[1], eligible[0] + eligible[1])]);
    }
    for (const row of data.versions) addRow("usageVersionRows", [row.platform === "windows" ? "Windows" : "Android", row.version, number(row.installations)]);
    if (!data.versions.length) addRow("usageVersionRows", ["No usage reports received yet", "—", "—"]);
    el("usageReportUpdated").textContent = `Updated ${new Date(data.generated_at).toLocaleString()}`;
    el("usageReportPeriod").textContent = `${data.activity_since} through ${data.generated_at.slice(0, 10)}, UTC. Voluntary reporting only.`;
    report.hidden = false;
    form.hidden = true;
  }
  async function load() {
    pending?.abort();
    const current = ++generation;
    const controller = new AbortController();
    pending = controller;
    const timeout = setTimeout(() => controller.abort(), 15000);
    el("usageReportRefresh").disabled = true;
    status.textContent = "Loading usage report…";
    try {
      const options = {headers: {Authorization: `Bearer ${key}`}, cache: "no-store", credentials: "omit", signal: controller.signal};
      const [response, downloadResponse] = await Promise.all([fetch(BASE + "/usage-stats", options), fetch(BASE + "/download-stats", options).catch(() => null)]);
      if (response.status === 401) throw new Error("Access key not accepted. Use the dedicated report key.");
      if (!response.ok) throw new Error("Usage reporting is unavailable or not deployed yet. No usage totals can be shown.");
      const data = await response.json();
      validate(data);
      let downloads = null;
      if (downloadResponse?.ok) {
        const candidate = await downloadResponse.json().catch(() => null);
        if (candidate?.metric === "download_starts" && Array.isArray(candidate.daily) && candidate.daily.length <= 60 &&
            candidate.daily.every(row => platform(row.platform) && count(row.starts) && /^\d{4}-\d{2}-\d{2}$/.test(row.day))) downloads = candidate;
      }
      if (current !== generation) return;
      render(data, downloads);
      status.textContent = data.installations.length ? "Reporting installations loaded. These are not unique people." : "No opted-in usage reports yet. Existing downloads cannot reveal past app use.";
    } catch (error) {
      if (current !== generation) return;
      clear();
      form.hidden = false;
      key = "";
      status.textContent = error.name === "AbortError" ? "Report timed out. Enter your key to retry." : error instanceof TypeError ? "Could not reach the report. Check your connection." : error.message;
    } finally {
      clearTimeout(timeout);
      if (current === generation) { pending = null; el("usageReportRefresh").disabled = false; }
    }
  }
  form.addEventListener("submit", event => {
    event.preventDefault();
    key = field.value.trim();
    field.value = "";
    if (key) load();
  });
  el("usageReportRefresh").addEventListener("click", load);
  el("usageReportLock").addEventListener("click", lock);
  window.addEventListener("pagehide", lock);
})();
