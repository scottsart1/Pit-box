/* A local, optional guide. Only explicit action buttons change app settings. */
(() => {
  "use strict";
  const get = id => document.getElementById(id);
  const dialog = get("onboardingDialog");
  if (!dialog) return;
  get("settingsGroups").before(get("onboardingSettings"));
  if (typeof dialog.showModal !== "function") {
    get("onboardingSavedStatus").textContent = i18n.t("This browser needs an update to show the walkthrough. You can still set up your key and telemetry in Connection.");
    get("onboardingRestart").textContent = i18n.t("Open Connection setup");
    get("onboardingRestart").addEventListener("click", () => document.querySelector('.tab[data-page="connection"]')?.click());
    return;
  }
  const STORAGE_KEY = "pitwall.onboarding.v1";
  const viewed = value => value === "finished" || value === "skipped";
  const bridge = typeof window.PitBoxDashboardPreferences?.postMessage === "function" ? window.PitBoxDashboardPreferences : null;
  let persistenceReady = false, dismissalVersion = 0, bridgeSequence = 0;
  const pending = new Map();
  if (bridge) bridge.onmessage = event => {
    let response; try { response = JSON.parse(event.data); } catch { return; }
    const request = pending.get(response?.id);
    if (!request || response.op !== request.op) return;
    pending.delete(response.id); clearTimeout(request.timer);
    if (response.ok === true) request.resolve(response);
    else request.reject(new Error("Walkthrough storage rejected the request."));
  };
  function nativePreference(op, value) {
    return new Promise((resolve, reject) => {
      const id = "onboarding-" + (++bridgeSequence), timer = setTimeout(() => {
        pending.delete(id); reject(new Error("Walkthrough storage did not respond."));
      }, 4000);
      pending.set(id, {op, resolve, reject, timer});
      try { bridge.postMessage(JSON.stringify({id, op, ...(value === undefined ? {} : {value})})); }
      catch (error) { pending.delete(id); clearTimeout(timer); reject(error); }
    });
  }
  const titles = [i18n.t("Give your engineer a voice"), i18n.t("Connect your PS5 & map L3"), i18n.t("Say hello to Mark"), i18n.t("Take a look around")];
  let step = 0, state = null, stateAt = 0, configured = null, keyBusy = false, radioBusy = false;
  let seen = false, autoSuppressed = false, previousFocus = null, statusGeneration = 0;
  let activityTimer = null;
  let network = null, networkAt = 0, networkAttempt = 0, networkBusy = false;
  let networkError = "";
  try { seen = viewed(localStorage.getItem(STORAGE_KEY)); } catch { /* still usable without storage */ }
  get("onboardingSavedStatus").textContent = seen ? i18n.t("Walkthrough already viewed. You can reopen it anytime.") : i18n.t("All three steps are skippable.");
  async function restoreViewed() {
    let local = null;
    try { local = localStorage.getItem(STORAGE_KEY); } catch { /* Native storage may still work. */ }
    if (bridge) {
      try {
        const loaded = await nativePreference("onboarding_load");
        // An explicit dismissal made while loading takes precedence.
        if (dismissalVersion === 0) {
          if (viewed(loaded.value)) {
            seen = true;
            try { localStorage.setItem(STORAGE_KEY, loaded.value); } catch { /* Native record remains authoritative. */ }
          } else if (loaded.value === null && viewed(local)) {
            // Migrate only a confirmed absent native record, never after a
            // timeout/error or an invalid reply from the bridge.
            await nativePreference("onboarding_save", local);
          }
        }
      } catch { /* Retain the browser flag; failed reads never write defaults. */ }
    }
    persistenceReady = true;
    if (seen && dismissalVersion === 0) get("onboardingSavedStatus").textContent = i18n.t("Walkthrough already viewed. You can reopen it anytime.");
    considerAutoOpen();
  }

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(url, {cache: "no-store", credentials: "same-origin", ...options, signal: controller.signal});
      if (!response.ok) {
        if (response.status === 403) throw new Error(i18n.t("Open setup on the device running {app} to change this setting; remote dashboards cannot save keys.", {app: "Your Pit Box"}));
        if (response.status === 400 || response.status === 422) throw new Error(i18n.t("The request was not accepted. Check the full key and your OpenAI account, or skip for now."));
        throw new Error(i18n.t("The app could not complete this step. Try again or skip for now."));
      }
      return await response.json();
    } catch (error) {
      if (error.name === "AbortError") throw new Error(i18n.t("The request timed out. Check the setting before retrying; it may have reached the app."));
      if (error instanceof TypeError) throw new Error(i18n.t("Cannot reach the app. Check the connection, or skip for now."));
      throw error;
    } finally { clearTimeout(timeout); }
  }

  // A one-time offer, on the first screen, to show the app in the browser's
  // language. Each offer is written in its own language: an English page has
  // no catalog to translate it with.
  const LANGUAGE_KEY = "pitbox.languageSuggestion.v1";
  const LANGUAGE_OFFERS = {
    fr: {label: "Langue", offer: "Afficher Your Pit Box en français", dismiss: "Ne plus proposer", failed: "Impossible de changer la langue. Réessaie."},
    de: {label: "Sprache", offer: "Your Pit Box auf Deutsch anzeigen", dismiss: "Nicht mehr anzeigen", failed: "Die Sprache konnte nicht geändert werden. Bitte versuch es erneut."},
    es: {label: "Idioma", offer: "Ver Your Pit Box en español", dismiss: "No volver a mostrar", failed: "No se pudo cambiar el idioma. Inténtalo de nuevo."},
  };
  function offerLanguage() {
    const browser = /^(fr|de|es)(?:-|$)/i.exec((typeof navigator !== "undefined" && navigator.language) || "");
    if (i18n.lang !== "en" || !browser) return;
    try { if (localStorage.getItem(LANGUAGE_KEY)) return; } catch { return; /* A dismissal could not be remembered. */ }
    const code = browser[1].toLowerCase(), text = LANGUAGE_OFFERS[code];
    const make = (tag, className, content) => { const element = document.createElement(tag); if (className) element.className = className; if (content) element.textContent = content; return element; };
    const row = make("div", "", ""), label = make("span", "label", text.label);
    const accept = make("button", "button ghost", text.offer), dismiss = make("button", "button ghost", "×");
    const actions = make("span", "", ""), status = make("p", "small", "");
    row.lang = code; row.setAttribute("translate", "no");
    row.style.cssText = "display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 14px;padding:6px 6px 6px 12px;border:1px solid var(--line,#263542);border-radius:10px;background:#080f16";
    label.style.marginRight = "auto";
    // The two buttons stay together when a narrow screen wraps the row.
    actions.style.cssText = "display:flex;gap:8px;margin-left:auto";
    accept.type = dismiss.type = "button";
    dismiss.title = text.dismiss; dismiss.setAttribute("aria-label", text.dismiss); dismiss.style.cssText = "flex:0 0 auto;min-width:44px";
    status.setAttribute("role", "status"); status.hidden = true; status.style.cssText = "flex-basis:100%;margin:0";
    actions.append(accept, dismiss);
    row.append(label, actions, status);
    get("onboardingStep0").prepend(row);
    accept.addEventListener("click", async () => {
      accept.disabled = true; status.hidden = true;
      try {
        await request("/api/v1/app-settings", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ui_language: code})});
        try { localStorage.setItem(LANGUAGE_KEY, "accepted"); } catch { /* The setting itself is saved. */ }
        // An explicit ?lang= in the address would override the new setting.
        const address = new URL(location.href);
        if (address.searchParams.has("lang")) { address.searchParams.delete("lang"); location.replace(address.href); }
        else location.reload();
      } catch {
        accept.disabled = false; status.textContent = text.failed; status.hidden = false;
      }
    });
    dismiss.addEventListener("click", () => {
      try { localStorage.setItem(LANGUAGE_KEY, "dismissed"); } catch { /* Offered again next time. */ }
      row.remove();
      get("onboardingTitle").focus();
    });
  }

  function freshState() { return state && Date.now() - stateAt < 5000; }

  function usableIPv4(value) {
    if (typeof value !== "string" || !/^\d{1,3}(\.\d{1,3}){3}$/.test(value)) return false;
    const parts = value.split(".").map(Number);
    return parts.every(n => n >= 0 && n <= 255) && parts[0] > 0 && parts[0] < 224
      && parts[0] !== 127 && !(parts[0] === 169 && parts[1] === 254);
  }
  function networkValues() {
    if (!network || Date.now() - networkAt > 15000 || networkError) return {ip: null, port: null};
    const listener = network.listener || {}, recommended = network.recommendation?.console_destination_ipv4;
    // A wildcard is a bind instruction, never an address to type on the PS5.
    // A specific listener binding wins over an unrelated adapter recommendation.
    const destination = listener.bind_host === "0.0.0.0" ? recommended : listener.bind_host;
    return {ip: usableIPv4(destination) ? destination : null,
      port: Number.isInteger(listener.port) && listener.port > 0 && listener.port <= 65535 ? listener.port : null};
  }
  function renderNetwork() {
    const {ip, port} = networkValues();
    get("onboardingUdpIp").textContent = ip || i18n.t("Unavailable");
    get("onboardingUdpPort").textContent = port === null ? i18n.t("Unavailable") : String(port);
    get("onboardingCopyIp").disabled = !ip;
    get("onboardingCopyPort").disabled = port === null;
    get("onboardingRefreshNetwork").disabled = networkBusy;
    const listener = network?.listener || {};
    get("onboardingNetworkStatus").textContent = networkError || (!network ? i18n.t("Reading this device's network settings…")
      : Date.now() - networkAt > 15000 ? i18n.t("Network details are stale. Refresh before changing your PS5 settings.")
      : !ip ? i18n.t("No reachable LAN address for this listener. Connect this device to Wi-Fi/Ethernet and refresh; a loopback-only listener cannot receive from PS5.")
      : port === null ? i18n.t("The listener did not return a valid UDP port. Check advanced Connection settings.")
      : listener.state === "receiving" ? i18n.t("Receiving telemetry. Keep this address and port on your PS5.")
      : listener.state === "listening" ? i18n.t("Listening—waiting for game telemetry. Enter the address and port above, then start an on-track session.")
      : listener.state === "stale" ? i18n.t("Telemetry was received, but is not fresh right now. Check the game is still running.")
      : listener.state === "off" ? i18n.t("These are the configured details, but the listener is off. Start it in advanced Connection settings.")
      : i18n.t("The listener needs attention. Check advanced Connection settings if nothing arrives."));
  }
  async function refreshNetwork(force = false) {
    if (networkBusy) return;
    networkBusy = true; networkAttempt = Date.now(); renderNetwork();
    try {
      if (force) await request("/api/v1/network/interfaces");
      network = await request("/api/v1/network/status");
      networkAt = Date.now(); networkError = "";
    } catch {
      network = null;
      networkError = i18n.t("Network details could not be read. Refresh to retry; do not enter an old or guessed IP address.");
    } finally { networkBusy = false; renderNetwork(); }
  }
  async function copyNetwork(kind) {
    const value = networkValues()[kind];
    if (value === null) return;
    try {
      await navigator.clipboard.writeText(String(value));
      get("onboardingCopyStatus").textContent = kind === "ip" ? i18n.t("UDP IP copied.") : i18n.t("UDP port copied.");
    } catch { get("onboardingCopyStatus").textContent = i18n.t("Clipboard unavailable. You can select the value above or type it on the PS5."); }
  }
  function renderLive() {
    const fresh = freshState();
    const telemetry = fresh && state.connected === true && !state.telemetry_stale && !state.game_paused;
    get("onboardingCalibrate").disabled = radioBusy || !telemetry;
    get("onboardingPttStatus").textContent = !fresh ? i18n.t("Live status unavailable. Reconnect to the app before calibrating.")
      : !telemetry ? i18n.t("Waiting for an unpaused, live telemetry feed. Check the UDP details above and park safely in an on-track session.")
      : Number(state.ptt_mask) > 0 ? i18n.t("A radio button is mapped. {status}", {status: state.ptt_status || i18n.t("You can recalibrate if your game binding changed.")})
      : i18n.t("Telemetry is arriving. {status}", {status: state.ptt_status || i18n.t("Select Start calibration.")});
    const micError = fresh && /microphone|audio/i.test(state.last_error || "");
    get("onboardingEnableMark").disabled = radioBusy || configured !== true || !fresh;
    get("onboardingEnableMark").textContent = fresh && state.wake_enabled ? i18n.t("Reapply hands-free setting") : i18n.t("Enable hands-free radio");
    get("onboardingWakeStatus").textContent = !fresh ? i18n.t("Live radio status unavailable. Reconnect to the app.")
      : configured !== true ? i18n.t("Save an OpenAI key first, or skip voice setup for now.")
      : micError ? i18n.t("Microphone/audio needs attention. Check device permissions and audio settings, then restart the app.")
      : (state.wake_enabled ? i18n.t("Hands-free radio on · {status}", {status: state.wake_status || i18n.t("status unavailable")})
        : i18n.t("Hands-free radio off · {status}", {status: state.wake_status || i18n.t("status unavailable")}));
    const rms = fresh && Number.isFinite(state.wake_input_rms) ? Math.max(0, state.wake_input_rms) : 0;
    get("onboardingMicMeter").value = Math.min(1, rms / Math.max(300, Number(state?.wake_threshold_rms) || 0));
    get("onboardingMicStatus").textContent = !fresh ? i18n.t("No fresh microphone status.")
      : rms > 0 ? i18n.t("Microphone activity detected. This does not yet confirm a successful radio check.")
      : i18n.t("No input observed right now. Speak normally and check permissions if the meter stays still.");
    const phrase = String(state?.wake_phrase || "Mark").trim().slice(0,60) || "Mark";
    get("onboardingExample").textContent = `“${phrase.charAt(0).toUpperCase() + phrase.slice(1)}, radio check.”`;
  }

  async function refreshKey() {
    const generation = ++statusGeneration;
    try {
      const data = await request("/api/v1/credentials/openai");
      if (generation !== statusGeneration) return;
      configured = data.configured === true;
      get("onboardingKeyStatus").textContent = configured
        ? i18n.t("An OpenAI key is already configured. You can keep it and continue.")
        : i18n.t("No OpenAI key configured yet. Save one below, or skip this step.");
      if (data.source === "environment") get("onboardingKeyStatus").textContent += " " + i18n.t("A system environment key takes priority again after restart; manage it in Connection.");
    } catch { if (generation === statusGeneration) get("onboardingKeyStatus").textContent = i18n.t("Could not check the saved key. You can try saving, or skip for now."); }
    renderLive();
  }

  function showStep(index) {
    get("onboardingKey").value = "";
    step = index;
    for (let i = 0; i < 4; i++) get(`onboardingStep${i}`).hidden = i !== step;
    get("onboardingProgress").textContent = step === 3 ? i18n.t("Optional · Explore the app") : i18n.t("Step {step} of 3", {step: step + 1});
    get("onboardingTitle").textContent = titles[step];
    get("onboardingBack").hidden = step === 0;
    get("onboardingNext").hidden = step === 3;
    get("onboardingNext").textContent = step === 2 ? i18n.t("Finish setup walkthrough") : i18n.t("Continue");
    get("onboardingSkipStep").textContent = step === 3 ? i18n.t("Skip tour") : i18n.t("Skip this step");
    dialog.scrollTop = 0;
    get("onboardingTitle").focus();
    renderLive();
    if (step === 1) { renderNetwork(); refreshNetwork(); }
  }

  function close(reason = "skipped") {
    seen = true;
    const version = ++dismissalVersion;
    get("onboardingKey").value = "";
    let saved = true;
    // Only a viewed/skipped flag is stored. Never persist keys or live data here.
    try { localStorage.setItem(STORAGE_KEY, reason); } catch { saved = false; }
    get("onboardingSavedStatus").textContent = saved
      ? i18n.t("Walkthrough closed. Reopen it anytime; only settings you explicitly saved were changed.")
      : i18n.t("Walkthrough closed. This browser could not remember that choice, so it may appear again next time.");
    if (bridge) nativePreference("onboarding_save", reason).then(() => {
      if (version === dismissalVersion) get("onboardingSavedStatus").textContent = i18n.t("Walkthrough closed. Reopen it anytime; only settings you explicitly saved were changed.");
    }).catch(() => {
      if (version === dismissalVersion) get("onboardingSavedStatus").textContent = i18n.t("Walkthrough closed. Device storage is unavailable, so it may appear again after an app restart.");
    });
    clearInterval(activityTimer); activityTimer = null;
    dialog.close();
    previousFocus?.focus();
  }

  function open() {
    if (dialog.open) return;
    window.dispatchEvent(new CustomEvent("pitwall:tour-stop"));
    seen = true; // Don't auto-reopen in this page, even if storage is unavailable.
    previousFocus = document.activeElement;
    dialog.showModal();
    showStep(0);
    refreshKey();
    activityTimer = setInterval(() => {
      renderLive(); // Expire stale diagnostics if the connection drops.
      if (step === 1) {
        renderNetwork();
        if (Date.now() - networkAttempt >= 5000) refreshNetwork();
      }
    }, 1000);
  }

  function considerAutoOpen() {
    if (seen || autoSuppressed || !state) return;
    // Never interrupt an existing live session, including later in this page load.
    if (state.connected && !state.game_paused) { autoSuppressed = true; return; }
    if (!persistenceReady) return;
    const boot = get("bootOverlay"), usage = get("usagePrompt"), usageToggle = get("usageToggle");
    if (boot && !boot.classList.contains("done")) return;
    // Let the existing privacy choice finish first; no consent defaults change.
    if (usage && (!usage.hidden || usageToggle?.disabled)) return;
    open();
  }

  function receiveState(next) {
    if (!next || typeof next !== "object") return;
    state = next; stateAt = Date.now();
    if (dialog.open) renderLive();
    considerAutoOpen();
  }
  window.addEventListener("pitwall:state", event => receiveState(event.detail));
  const observer = new MutationObserver(considerAutoOpen);
  for (const id of ["bootOverlay", "usagePrompt", "usageToggle"]) {
    if (get(id)) observer.observe(get(id), {attributes: true, attributeFilter: ["class", "hidden", "disabled"]});
  }
  request("/api/state").then(data => { if (!state) receiveState(data); }).catch(() => {});
  get("onboardingRestart").addEventListener("click", open);
  get("onboardingClose").addEventListener("click", () => close());
  dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
  get("onboardingBack").addEventListener("click", () => showStep(Math.max(0, step - 1)));
  get("onboardingNext").addEventListener("click", () => { if (step < 3) showStep(step + 1); });
  get("onboardingSkipStep").addEventListener("click", () => step < 3 ? showStep(step + 1) : close("finished"));
  get("onboardingBeginTour").addEventListener("click", () => {
    close("finished");
    window.dispatchEvent(new CustomEvent("pitwall:tour-start"));
  });
  get("onboardingRefreshNetwork").addEventListener("click", () => refreshNetwork(true));
  get("onboardingCopyIp").addEventListener("click", () => copyNetwork("ip"));
  get("onboardingCopyPort").addEventListener("click", () => copyNetwork("port"));
  get("onboardingConnection").addEventListener("click", () => {
    close();
    document.querySelector('.tab[data-page="connection"]')?.click();
  });

  get("onboardingKeyForm").addEventListener("submit", async event => {
    event.preventDefault();
    if (keyBusy) return;
    const apiKey = get("onboardingKey").value.trim();
    if (!apiKey) { get("onboardingKeyStatus").textContent = i18n.t("Paste your key first, or skip this step."); return; }
    keyBusy = true; ++statusGeneration;
    get("onboardingSaveKey").disabled = true;
    get("onboardingKey").value = "";
    get("onboardingKeyStatus").textContent = i18n.t("Checking the key with OpenAI before saving…");
    try {
      const data = await request("/api/v1/credentials/openai", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({api_key: apiKey, verify: true})});
      configured = data.configured === true;
      get("onboardingKeyStatus").textContent = configured ? i18n.t("Key checked and saved. You can continue to radio setup.") : i18n.t("The app did not confirm a saved key. Check Connection, or skip for now.");
      if (data.source === "environment") get("onboardingKeyStatus").textContent += " " + i18n.t("A system environment key will take priority after restart.");
    } catch (error) { get("onboardingKeyStatus").textContent = error.message; }
    finally { keyBusy = false; get("onboardingSaveKey").disabled = false; renderLive(); }
  });

  async function radioAction(kind) {
    if (radioBusy) return;
    const calibration = kind === "calibrate";
    const result = get(calibration ? "onboardingCalibrationResult" : "onboardingWakeResult");
    radioBusy = true; renderLive(); result.textContent = i18n.t("Applying…");
    try {
      const data = await request(calibration ? "/api/ptt/calibrate" : "/api/wake/config", {
        method: "POST", ...(calibration ? {} : {headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled: true})}),
      });
      if (calibration && data.ok !== true) throw new Error(i18n.t("Calibration was not started. Check the live telemetry feed, then try again or skip."));
      result.textContent = calibration
        ? i18n.t("Calibration started—not completed yet. Press and hold only the mapped button, then release it; watch the live status above.")
        : data.enabled === true ? i18n.t("Hands-free radio enabled. Now check microphone activity and try the phrase below.")
        : i18n.t("Hands-free radio was not enabled. Allow microphone access and restart the app, or skip for now.");
    } catch (error) { result.textContent = error.message; }
    finally { radioBusy = false; renderLive(); }
  }
  get("onboardingCalibrate").addEventListener("click", () => radioAction("calibrate"));
  get("onboardingEnableMark").addEventListener("click", () => radioAction("wake"));
  offerLanguage();
  restoreViewed();
})();
