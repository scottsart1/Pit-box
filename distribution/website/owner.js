(() => {
  "use strict";
  const BASE = "https://pitwall-activation.sarthakvij123450.workers.dev";
  const el = id => document.getElementById(id);
  const number = value => new Intl.NumberFormat().format(value);
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const platforms = ["windows", "android"];
  const titles = {app_started: "Opened the app", app_used: "Actively used the app", racing: "Qualifying driving telemetry", engineer: "Engineer replies", voice: "Voice commands", analysis: "Lap comparisons", transfer: "History-transfer starts"};
  const tableIds = ["downloadPlatformRows", "downloadDailyRows", "historyDailyRows", "emailRows", "signupSourceRows", "usageSignalRows", "retentionRows", "versionRows"];
  const metricIds = ["metricRecorded", "metricDownloads", "metricRecentDownloads", "metricEmails", "metricActive", "metricRacing", "metricRetention"];
  const accountTableIds = ["accountRows", "accountPlatformRows", "accountDailyRows"];
  const accountMetricIds = ["metricProfiles", "metricSignIns", "metricReturning", "metricNewProfiles"];
  const profilePlatforms = {android: "Android", windows: "Windows", macos: "macOS", other: "Other"};
  const profileId = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const requests = new Set();
  let key = "", generation = 0, busy = false, copying = false;
  let lastInteraction = Date.now(), hiddenAt = null, emailSnapshot = null, emailNext = null, emailCount = 0;
  let historyProfile = null, historyRequest = 0, historyOpener = null;
  const text = (id, value) => { el(id).textContent = value; };
  const interact = () => { lastInteraction = Date.now(); };
  for (const event of ["pointerdown", "keydown", "touchstart"]) window.addEventListener(event, interact, {passive: true});

  function clearAccounts() {
    for (const id of accountTableIds) el(id).replaceChildren();
    for (const id of accountMetricIds) text(id, "—");
  }
  function closeHistory(restoreFocus = false) {
    if (restoreFocus && historyOpener?.isConnected) historyOpener.focus();
    historyProfile = historyOpener = null; historyRequest++;
    el("accountHistoryRows").replaceChildren();
    text("accountHistoryTitle", "Profile sign-ins"); text("accountHistoryStatus", "");
    el("accountHistory").hidden = true;
  }
  function clearData() {
    for (const id of tableIds) el(id).replaceChildren();
    for (const id of metricIds) text(id, "—");
    clearAccounts(); closeHistory();
    for (const id of ["ownerUpdated", "metricRecordedSources", "metricPlatformSplit", "metricNewEmails", "historyOverview", "historyCoverage", "downloadAvailability", "usageAvailability", "emailStatus", "accountStatus"]) text(id, "");
    el("historyDetails").hidden = true;
    el("emailCopyText").value = "";
    el("emailCopyFallback").hidden = true;
    el("loadMoreEmails").hidden = true;
    el("loadMoreEmails").disabled = false;
    el("copyAllEmails").disabled = true;
    emailSnapshot = emailNext = null;
    emailCount = 0;
  }
  function lock(message = "Locked. No private data is loaded.") {
    generation++;
    for (const request of requests) request.abort();
    requests.clear();
    key = el("ownerKey").value = "";
    busy = copying = false;
    clearData();
    el("ownerData").hidden = el("ownerLock").hidden = true;
    el("ownerLoginPanel").hidden = false;
    el("ownerOpen").disabled = el("ownerRefresh").disabled = false;
    text("ownerStatus", message);
  }
  function expired() {
    return Date.now() - lastInteraction >= 15 * 60000 || (hiddenAt !== null && Date.now() - hiddenAt >= 5 * 60000);
  }
  async function api(path, current) {
    if (!key || current !== generation) throw new Error("Locked");
    if (expired()) { lock("Session locked. Enter your key again."); throw new Error("Locked"); }
    const controller = new AbortController();
    requests.add(controller);
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(BASE + path, {headers: {Authorization: `Bearer ${key}`}, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer", redirect: "error", signal: controller.signal});
      if (current !== generation) throw new Error("Locked");
      if (response.status === 401) { lock("Access key not accepted. Use your private owner dashboard key."); throw new Error("Unauthorized"); }
      if (!response.ok) throw new Error(response.status === 429 ? "Request limit reached. Please wait one minute." : "This data is temporarily unavailable. Please retry.");
      const result = await response.json();
      if (current !== generation) throw new Error("Locked");
      return result;
    } finally { clearTimeout(timer); requests.delete(controller); }
  }
  function row(id, values) {
    const tr = document.createElement("tr");
    for (const value of values) { const td = document.createElement("td"); td.textContent = String(value); tr.appendChild(td); }
    el(id).appendChild(tr);
    return tr;
  }
  function array(value, max = 100) { if (!Array.isArray(value) || value.length > max) throw new Error("Invalid report data."); return value; }
  function counted(value) { if (!count(value)) throw new Error("Invalid report count."); return value; }
  function validateHistory(history, downloads, generatedAt) {
    const bad = () => { throw new Error("Historical download data could not be verified."); };
    const dates = [history.from, history.until, history.recovered_at];
    if (history.metric !== "historical_file_requests" || history.source !== "Cloudflare R2 analytics" ||
        !dates.every(value => typeof value === "string" && Number.isFinite(Date.parse(value)))) bad();
    const from = Date.parse(history.from), until = Date.parse(history.until);
    if (from >= until || until - from > 366 * 86400000 || until > Date.parse(generatedAt) ||
        (downloads.first_recorded_at !== null && until > Date.parse(downloads.first_recorded_at))) bad();
    const firstDay = new Date(from).toISOString().slice(0, 10), lastDay = new Date(until - 1).toISOString().slice(0, 10);
    const totals = {windows: 0, android: 0, all: 0}, seen = new Set();
    for (const r of array(history.daily, 730)) {
      const identity = `${r.day}/${r.platform}`;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(r.day) || !Number.isFinite(Date.parse(r.day)) ||
          new Date(r.day).toISOString().slice(0, 10) !== r.day || r.day < firstDay || r.day > lastDay ||
          !platforms.includes(r.platform) || !count(r.requests) || seen.has(identity)) bad();
      seen.add(identity); totals[r.platform] += r.requests; totals.all += r.requests;
    }
    for (const p of [...platforms, "all"]) {
      if (!count(totals[p]) || totals[p] !== history.totals?.[p] || !count(totals[p] + downloads.totals[p])) bad();
    }
  }
  function validateOverview(data) {
    if (!data || !Number.isFinite(Date.parse(data.generated_at))) throw new Error("Invalid report data.");
    if (data.downloads !== null) {
      const d = data.downloads;
      if (d?.metric !== "download_starts" || !d.totals) throw new Error("Invalid download data.");
      for (const p of [...platforms, "all"]) counted(d.totals[p]);
      if (d.totals.all !== d.totals.windows + d.totals.android) throw new Error("Download totals do not reconcile.");
      if (d.first_recorded_at !== null && !Number.isFinite(Date.parse(d.first_recorded_at))) throw new Error("Invalid download coverage.");
      for (const r of array(d.daily, 60)) if (!platforms.includes(r.platform) || !/^\d{4}-\d{2}-\d{2}$/.test(r.day) || !count(r.starts)) throw new Error("Invalid daily downloads.");
      if (d.history) validateHistory(d.history, d, data.generated_at);
    }
    if (data.subscribers !== null) {
      const s = data.subscribers;
      if (s?.metric !== "newsletter_subscribers") throw new Error("Invalid subscriber data.");
      counted(s.total); counted(s.new_30d);
      for (const r of array(s.sources, 50)) if (typeof r.source !== "string" || !count(r.total)) throw new Error("Invalid signup sources.");
    }
    if (data.usage !== null) {
      const u = data.usage;
      if (u?.metric !== "opted_in_installations") throw new Error("Invalid usage data.");
      for (const r of array(u.installations, 2)) if (!platforms.includes(r.platform) || !count(r.installations) || !count(r.new_30d)) throw new Error("Invalid installation totals.");
      for (const r of array(u.features, 14)) if (!platforms.includes(r.platform) || !Object.hasOwn(titles, r.event) || !count(r.installations)) throw new Error("Invalid feature counts.");
      for (const r of array(u.recent, 2)) if (!platforms.includes(r.platform) || !count(r.active_7d) || !count(r.active_today)) throw new Error("Invalid active counts.");
      if (array(u.retention, 3).length !== 3) throw new Error("Invalid retention.");
      u.retention.forEach((c, i) => {
        if (c.day !== [1, 7, 30][i]) throw new Error("Invalid cohort.");
        for (const r of array(c.platforms, 2)) if (!platforms.includes(r.platform) || !count(r.eligible) || !count(r.returned) || r.returned > r.eligible) throw new Error("Invalid retention counts.");
      });
      for (const r of array(u.versions, 50)) if (!platforms.includes(r.platform) || typeof r.version !== "string" || r.version.length > 40 || !count(r.installations)) throw new Error("Invalid versions.");
    }
  }
  const sum = (rows, field) => rows.reduce((total, r) => total + r[field], 0);
  const returnRate = rows => {
    const eligible = sum(rows, "eligible"), returned = sum(rows, "returned");
    return eligible ? `${number(returned)} / ${number(eligible)} (${(100 * returned / eligible).toFixed(1)}%)` : "Not yet eligible";
  };
  // Compare numeric version components, then SemVer prerelease identifiers.
  // Unknown/unversioned reports follow numbered releases.
  function compareVersionsNewestFirst(left, right) {
    const parse = value => /^v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/i.exec(value.trim());
    const a = parse(left), b = parse(right);
    if (!a || !b) return a ? -1 : b ? 1 : right.localeCompare(left, "en", {numeric: true});
    const ac = a[1].split(".").map(BigInt), bc = b[1].split(".").map(BigInt);
    for (let i = 0; i < Math.max(ac.length, bc.length); i++) {
      const av = ac[i] ?? 0n, bv = bc[i] ?? 0n;
      if (av !== bv) return av > bv ? -1 : 1;
    }
    if (a[2] === b[2]) return 0;
    if (!a[2] || !b[2]) return a[2] ? 1 : -1; // Stable release precedes its prereleases.
    const ap = a[2].split("."), bp = b[2].split(".");
    for (let i = 0; i < Math.max(ap.length, bp.length); i++) {
      if (ap[i] === bp[i]) continue;
      if (ap[i] === undefined || bp[i] === undefined) return ap[i] === undefined ? 1 : -1;
      const an = /^\d+$/.test(ap[i]), bn = /^\d+$/.test(bp[i]);
      if (an && bn) {
        const av = BigInt(ap[i]), bv = BigInt(bp[i]);
        if (av !== bv) return av > bv ? -1 : 1;
      } else if (an !== bn) return an ? 1 : -1;
      else return ap[i] > bp[i] ? -1 : 1;
    }
    return 0;
  }
  function renderOverview(data) {
    validateOverview(data);
    for (const id of tableIds.filter(id => id !== "emailRows")) el(id).replaceChildren();
    for (const id of metricIds) text(id, "—");
    el("historyDetails").hidden = true;
    text("metricPlatformSplit", "Windows / Android"); text("metricNewEmails", "Release-news signups");
    text("ownerUpdated", `Updated ${new Date(data.generated_at).toLocaleString()} · dates below use UTC`);
    const d = data.downloads;
    text("downloadAvailability", d ? (d.first_recorded_at ? `Live tracking since ${d.first_recorded_at.slice(0, 10)}.` : "No live download starts recorded yet.") : "Download data unavailable—not zero.");
    text("historyOverview", d?.history ? `${number(d.history.totals.all)} historical file reads · Windows ${number(d.history.totals.windows)} · Android ${number(d.history.totals.android)}` : "Historical snapshot unavailable—not zero.");
    text("historyCoverage", d?.history ? `${d.history.from} to ${d.history.until} (end exclusive) · Recovered ${new Date(d.history.recovered_at).toISOString().slice(0, 10)} from Cloudflare R2 analytics. Outside this period, history is not included.` : "");
    text("metricRecordedSources", d?.history ? `${number(d.history.totals.all)} historical reads + ${number(d.totals.all)} live starts · different counting methods` : "History unavailable; combined activity cannot be shown.");
    if (d) {
      if (d.history) {
        text("metricRecorded", number(d.history.totals.all + d.totals.all));
        const from = Date.parse(new Date(d.history.from).toISOString().slice(0, 10));
        const until = Date.parse(new Date(Date.parse(d.history.until) - 1).toISOString().slice(0, 10));
        const days = new Map();
        for (const r of d.history.daily) {
          if (!days.has(r.day)) days.set(r.day, {windows: 0, android: 0});
          days.get(r.day)[r.platform] = r.requests;
        }
        for (let stamp = until; stamp >= from; stamp -= 86400000) {
          const day = new Date(stamp).toISOString().slice(0, 10), values = days.get(day) || {windows: 0, android: 0};
          row("historyDailyRows", [day, number(values.windows), number(values.android), number(values.windows + values.android)]);
        }
        el("historyDetails").hidden = false;
      }
      text("metricDownloads", number(d.totals.all));
      text("metricRecentDownloads", number(sum(d.daily, "starts")));
      const recent = Object.fromEntries(platforms.map(p => [p, sum(d.daily.filter(r => r.platform === p), "starts")]));
      text("metricPlatformSplit", `${number(recent.windows)} Windows · ${number(recent.android)} Android`);
      for (const p of platforms) row("downloadPlatformRows", [p === "windows" ? "Windows" : "Android", d.history ? number(d.history.totals[p]) : "—", number(d.totals[p]), d.history ? number(d.totals[p] + d.history.totals[p]) : "—", number(recent[p])]);
      const today = new Date(data.generated_at).toISOString().slice(0, 10), daily = [];
      for (let i = 0; i < 30; i++) {
        const day = new Date(Date.parse(today) - i * 86400000).toISOString().slice(0, 10);
        const started = d.first_recorded_at && day >= d.first_recorded_at.slice(0, 10);
        const values = platforms.map(p => sum(d.daily.filter(r => r.day === day && r.platform === p), "starts"));
        daily.push({day, started, values, total: values[0] + values[1]});
      }
      const max = Math.max(1, ...daily.map(r => r.total));
      for (const r of daily) {
        const tr = row("downloadDailyRows", [r.day, ...r.values.map(v => r.started ? number(v) : "—"), r.started ? number(r.total) : "—"]);
        const td = document.createElement("td"), bar = document.createElement("progress");
        if (r.started) { bar.max = max; bar.value = r.total; bar.setAttribute("aria-label", `${r.total} starts on ${r.day}`); td.appendChild(bar); }
        tr.appendChild(td);
      }
    }
    const s = data.subscribers;
    if (s) {
      text("metricEmails", number(s.total)); text("metricNewEmails", `${number(s.new_30d)} new in the last 30 days`);
      for (const source of s.sources) row("signupSourceRows", [source.source, number(source.total)]);
    }
    const u = data.usage;
    text("usageAvailability", u ? (u.installations.length ? "Based on installations that enabled sharing. Daily feature flags, not individual actions." : "No opted-in usage reports yet. This does not mean nobody is using the apps.") : "Usage data unavailable—not zero.");
    if (u) {
      text("metricActive", number(sum(u.recent, "active_7d")));
      text("metricRacing", number(sum(u.features.filter(r => r.event === "racing"), "installations")));
      text("metricRetention", returnRate(u.retention[1].platforms));
      const addSignal = (title, values) => row("usageSignalRows", [title, ...values.map(number), number(values[0] + values[1])]);
      addSignal("First reported use", platforms.map(p => sum(u.installations.filter(r => r.platform === p), "new_30d")));
      for (const [event, title] of Object.entries(titles)) addSignal(title, platforms.map(p => sum(u.features.filter(r => r.platform === p && r.event === event), "installations")));
      for (const c of u.retention) row("retentionRows", [`Day ${c.day}`, ...platforms.map(p => returnRate(c.platforms.filter(r => r.platform === p))), returnRate(c.platforms)]);
      const versions = [...u.versions].sort((a, b) => compareVersionsNewestFirst(a.version, b.version) || platforms.indexOf(a.platform) - platforms.indexOf(b.platform));
      for (const v of versions) row("versionRows", [v.platform, v.version, number(v.installations)]);
    }
    text("ownerStatus", [d, s, u].every(Boolean) ? "Connected. Collecting automatically; no manual imports needed." : "Connected, but some reports are unavailable. Missing data is not shown as zero.");
  }
  function validateEmails(data) {
    if (!data || !count(data.snapshot) || !count(data.total) || (data.next !== null && (!count(data.next) || data.next <= 0 || data.next > data.snapshot))) throw new Error("Invalid email page.");
    for (const r of array(data.subscribers, 200)) if (typeof r.email !== "string" || r.email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(r.email) || typeof r.created_at !== "string" || !Number.isFinite(Date.parse(r.created_at)) || (r.source !== null && typeof r.source !== "string")) throw new Error("Invalid email record.");
    return data;
  }
  async function loadEmails(current, append = false) {
    const query = append ? `?limit=100&snapshot=${emailSnapshot}&before=${emailNext}` : "?limit=100";
    const data = validateEmails(await api("/owner/subscribers" + query, current));
    if (current !== generation) return;
    if (!append) { el("emailRows").replaceChildren(); emailCount = 0; }
    emailSnapshot = data.snapshot; emailNext = data.next; emailCount += data.subscribers.length;
    for (const item of data.subscribers) row("emailRows", [item.email, item.created_at.replace("T", " ").slice(0, 19), item.source || "unknown"]);
    text("emailStatus", data.total ? `Showing ${number(emailCount)} of ${number(data.total)} subscribers.` : "No newsletter signups yet.");
    el("loadMoreEmails").hidden = emailNext === null;
    el("copyAllEmails").disabled = data.total === 0;
  }
  const utc = (seconds, length = 19) => new Date(seconds * 1000).toISOString().slice(0, length).replace("T", " ");
  const profilePlatform = value => { if (!Object.hasOwn(profilePlatforms, value)) throw new Error("Invalid profile report."); return profilePlatforms[value]; };
  const profileVersion = value => value === null || (typeof value === "string" && value.length <= 64);
  function validateAccounts(data) {
    const bad = () => { throw new Error("Invalid profile report."); };
    const s = data?.summary;
    if (data?.metric !== "profile_sign_ins" || !s || ![s.accounts, s.starts, s.returning, s.new_accounts].every(count)
        || s.returning > s.accounts || s.new_accounts > s.accounts || s.accounts > s.starts) bad();
    const seen = new Set();
    for (const r of array(data.platforms, 4)) {
      profilePlatform(r.platform);
      if (seen.has(r.platform) || !count(r.accounts) || !count(r.starts) || r.accounts > r.starts) bad();
      seen.add(r.platform);
    }
    const ids = new Set();
    for (const r of array(data.accounts, 200)) {
      profilePlatform(r.platform);
      if (typeof r.account_id !== "string" || !profileId.test(r.account_id) || ids.has(r.account_id)
          || ![r.starts, r.starts_30d, r.active_days, r.first_start, r.last_start].every(count)
          || r.starts < 1 || r.starts_30d > r.starts || r.active_days < 1 || r.active_days > r.starts
          || r.first_start > r.last_start || !profileVersion(r.version)) bad();
      ids.add(r.account_id);
    }
    for (const r of array(data.daily, 31)) {
      if (typeof r.day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(r.day) || !count(r.starts) || !count(r.accounts) || r.accounts > r.starts) bad();
    }
    return data;
  }
  async function loadAccounts(current) {
    try {
      const data = validateAccounts(await api("/owner/accounts", current));
      if (current !== generation) return;
      clearAccounts();
      const s = data.summary;
      text("metricProfiles", number(s.accounts)); text("metricSignIns", number(s.starts));
      text("metricReturning", number(s.returning)); text("metricNewProfiles", number(s.new_accounts));
      for (const item of data.accounts) {
        const tr = row("accountRows", [item.account_id, profilePlatform(item.platform), number(item.starts_30d), number(item.starts),
          number(item.active_days), utc(item.first_start, 16), utc(item.last_start, 16), item.version || "—"]);
        const td = document.createElement("td"), button = document.createElement("button");
        button.type = "button"; button.className = "button ghost"; button.textContent = "Sign-ins";
        button.setAttribute("aria-label", `Show every sign-in for profile ${item.account_id}`);
        button.setAttribute("aria-controls", "accountHistory");
        button.addEventListener("click", () => { interact(); historyOpener = button; return openHistory(item.account_id); });
        td.appendChild(button); tr.appendChild(td);
      }
      for (const r of data.platforms) row("accountPlatformRows", [profilePlatform(r.platform), number(r.accounts), number(r.starts)]);
      for (const r of data.daily) row("accountDailyRows", [r.day, number(r.accounts), number(r.starts)]);
      text("accountStatus", s.accounts || data.accounts.length
        ? `${number(s.accounts)} profiles signed in ${number(s.starts)} times in the last 30 days. Choose Sign-ins on a profile to follow it over time.`
        : "No profile sign-ins recorded yet. Sign-ins made offline appear after the device reconnects.");
      if (historyProfile) await openHistory(historyProfile, false);
    } catch (error) {
      if (current !== generation) return;
      clearAccounts();
      text("accountStatus", "Profile sign-ins are unavailable. This does not mean zero sign-ins.");
    }
  }
  function validateHistoryReport(data, id) {
    const bad = () => { throw new Error("Invalid profile history."); };
    const s = data?.summary;
    if (data?.metric !== "profile_sign_in_history" || data.account_id !== id || !s || !count(s.starts) || !count(s.active_days)
        || s.active_days > s.starts || !count(data.limit)) bad();
    if (s.starts ? !count(s.first_start) || !count(s.last_start) || s.first_start > s.last_start : s.first_start !== null || s.last_start !== null) bad();
    const rows = array(data.sign_ins, 500);
    if (rows.length > s.starts || rows.length > data.limit || (s.starts && !rows.length)) bad();
    let previous = Infinity;
    for (const r of rows) {
      profilePlatform(r.platform);
      if (!count(r.occurred_at) || !count(r.received_at) || r.occurred_at > previous || !profileVersion(r.version) || r.version === null) bad();
      previous = r.occurred_at;
    }
    return data;
  }
  async function openHistory(id, focus = true) {
    if (!key || !profileId.test(id)) return;
    const current = generation, request = ++historyRequest;
    const changed = historyProfile !== id;
    historyProfile = id;
    el("accountHistory").hidden = false;
    text("accountHistoryTitle", `Profile ${id}`);
    if (changed) { el("accountHistoryRows").replaceChildren(); text("accountHistoryStatus", "Loading sign-ins…"); }
    if (focus) el("accountHistoryTitle").focus();
    try {
      const data = validateHistoryReport(await api(`/owner/accounts/${id}`, current), id);
      if (current !== generation || request !== historyRequest) return;
      el("accountHistoryRows").replaceChildren();
      for (const r of data.sign_ins) row("accountHistoryRows", [utc(r.occurred_at), utc(r.received_at), profilePlatform(r.platform), r.version]);
      const s = data.summary;
      text("accountHistoryStatus", !s.starts ? "No sign-ins from this profile in the last 180 days."
        : `${number(s.starts)} sign-ins on ${number(s.active_days)} days, from ${utc(s.first_start)} to ${utc(s.last_start)}.`
          + (data.sign_ins.length < s.starts ? ` Showing the latest ${number(data.sign_ins.length)}.` : ""));
    } catch (error) {
      if (current !== generation || request !== historyRequest) return;
      el("accountHistoryRows").replaceChildren();
      text("accountHistoryStatus", error.name === "AbortError" ? "Loading this profile timed out. Retry shortly." : "This profile's sign-ins are unavailable. This does not mean zero sign-ins.");
    }
  }
  async function refresh() {
    if (!key || busy || copying) return;
    if (expired()) { lock("Session locked after inactivity. Enter your key again."); return; }
    const current = generation;
    busy = true; el("ownerRefresh").disabled = true;
    try {
      const data = await api("/owner/overview", current);
      if (current !== generation) return;
      renderOverview(data);
      el("ownerLoginPanel").hidden = true; el("ownerData").hidden = el("ownerLock").hidden = false;
      await loadAccounts(current);
      try { await loadEmails(current); }
      catch (error) {
        if (current !== generation) return;
        el("emailRows").replaceChildren(); el("copyAllEmails").disabled = true; el("loadMoreEmails").hidden = true;
        text("emailStatus", error.message);
      }
    } catch (error) {
      if (current !== generation) return;
      clearData();
      text("ownerStatus", error.name === "AbortError" ? "Refresh timed out. No stale numbers are displayed; retry shortly." : error.message);
      if (el("ownerData").hidden) key = "";
    } finally {
      if (current === generation) { busy = false; el("ownerRefresh").disabled = el("ownerOpen").disabled = false; }
    }
  }
  async function copyAll() {
    if (!key || copying || busy) return;
    const current = generation;
    copying = true; el("copyAllEmails").disabled = true;
    el("emailCopyText").value = ""; el("emailCopyFallback").hidden = true;
    const addresses = new Set();
    try {
      let path = "/owner/subscribers?limit=200", snapshot = null, expectedTotal = null, previous = Infinity;
      do {
        const data = validateEmails(await api(path, current));
        if (current !== generation) return;
        if (data.total > 50000) throw new Error("This list is too large for a safe browser copy. No partial list was copied.");
        snapshot ??= data.snapshot;
        expectedTotal ??= data.total;
        if (expectedTotal !== data.total) throw new Error("The subscriber list changed during copying. Retry; no partial list was copied.");
        if (snapshot !== data.snapshot || (data.next !== null && data.next >= previous)) throw new Error("Email pagination changed. Retry; no partial list was copied.");
        for (const item of data.subscribers) addresses.add(item.email);
        if (addresses.size > 50000) throw new Error("Copy limit reached. No partial list was copied.");
        text("emailStatus", `Preparing all emails… ${number(addresses.size)} loaded.`);
        previous = data.next;
        path = data.next === null ? null : `/owner/subscribers?limit=200&snapshot=${snapshot}&before=${data.next}`;
      } while (path);
      if (current !== generation || !key) return;
      if (addresses.size !== expectedTotal) throw new Error("The complete subscriber list could not be verified. No partial list was copied.");
      const value = [...addresses].join("\n");
      if (!value) { text("emailStatus", "No email addresses to copy."); return; }
      try {
        if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
        await navigator.clipboard.writeText(value);
        if (current === generation) text("emailStatus", `Copied all ${number(addresses.size)} email addresses, one per line. No email was sent.`);
      } catch {
        if (current !== generation) return;
        el("emailCopyText").value = value; el("emailCopyFallback").hidden = false;
        text("emailStatus", `All ${number(addresses.size)} addresses are ready below. Your browser blocked automatic copying.`);
      }
    } catch (error) { if (current === generation) text("emailStatus", error.name === "AbortError" ? "Email loading timed out. Nothing was copied." : error.message); }
    finally { addresses.clear(); if (current === generation) { copying = false; el("copyAllEmails").disabled = false; } }
  }
  el("ownerLogin").addEventListener("submit", event => {
    event.preventDefault();
    const submitted = el("ownerKey").value.trim();
    lock();
    if (!submitted) return;
    key = submitted;
    interact();
    el("ownerOpen").disabled = true;
    text("ownerStatus", "Opening private dashboard…");
    refresh();
  });
  el("ownerRefresh").addEventListener("click", () => { interact(); return refresh(); });
  el("ownerLock").addEventListener("click", () => lock());
  el("copyAllEmails").addEventListener("click", copyAll);
  el("accountHistoryClose").addEventListener("click", () => { interact(); closeHistory(true); });
  el("selectEmails").addEventListener("click", () => { el("emailCopyText").focus(); el("emailCopyText").select(); });
  el("loadMoreEmails").addEventListener("click", async () => {
    if (busy || copying || emailNext === null) return;
    const current = generation; busy = true; el("loadMoreEmails").disabled = true;
    try { await loadEmails(current, true); } catch (error) { if (current === generation) text("emailStatus", error.message); }
    finally { if (current === generation) { busy = false; el("loadMoreEmails").disabled = false; } }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") hiddenAt = Date.now();
    else { if (key && expired()) lock("Dashboard locked while hidden. Enter your key again."); hiddenAt = null; if (key && el("ownerAuto").checked) refresh(); }
  });
  window.addEventListener("pagehide", () => lock());
  setInterval(() => {
    if (!key) return;
    if (expired()) { lock("Dashboard locked after inactivity. Enter your key again."); return; }
    if (document.visibilityState === "visible" && el("ownerAuto").checked) refresh();
  }, 60000);
})();
