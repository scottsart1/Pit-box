/* A local, optional guide. Only explicit action buttons change app settings. */
(() => {
  "use strict";
  const get = id => document.getElementById(id);
  const dialog = get("onboardingDialog");
  if (!dialog) return;
  get("settingsGroups").before(get("onboardingSettings"));
  if (typeof dialog.showModal !== "function") {
    get("onboardingSavedStatus").textContent = "This browser needs an update to show the walkthrough. You can still set up your key and telemetry in Connection.";
    get("onboardingRestart").textContent = "Open Connection setup";
    get("onboardingRestart").addEventListener("click", () => document.querySelector('.tab[data-page="connection"]')?.click());
    return;
  }
  const STORAGE_KEY = "pitwall.onboarding.v1";
  const titles = ["Give your engineer a voice", "Connect your PS5 & map L3", "Say hello to Mark", "Take a look around"];
  let step = 0, state = null, stateAt = 0, configured = null, keyBusy = false, radioBusy = false;
  let seen = false, autoSuppressed = false, previousFocus = null, statusGeneration = 0;
  let activityTimer = null;
  let network = null, networkAt = 0, networkAttempt = 0, networkBusy = false;
  let networkError = "";
  try { seen = ["finished", "skipped"].includes(localStorage.getItem(STORAGE_KEY)); } catch { /* still usable without storage */ }
  get("onboardingSavedStatus").textContent = seen ? "Walkthrough already viewed. You can reopen it anytime." : "All three steps are skippable.";

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(url, {cache: "no-store", credentials: "same-origin", ...options, signal: controller.signal});
      if (!response.ok) {
        if (response.status === 403) throw new Error("Open setup on the device running Your Pit Box to change this setting; remote dashboards cannot save keys.");
        if (response.status === 400 || response.status === 422) throw new Error("The request was not accepted. Check the full key and your OpenAI account, or skip for now.");
        throw new Error("The app could not complete this step. Try again or skip for now.");
      }
      return await response.json();
    } catch (error) {
      if (error.name === "AbortError") throw new Error("The request timed out. Check the setting before retrying; it may have reached the app.");
      if (error instanceof TypeError) throw new Error("Cannot reach the app. Check the connection, or skip for now.");
      throw error;
    } finally { clearTimeout(timeout); }
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
    get("onboardingUdpIp").textContent = ip || "Unavailable";
    get("onboardingUdpPort").textContent = port === null ? "Unavailable" : String(port);
    get("onboardingCopyIp").disabled = !ip;
    get("onboardingCopyPort").disabled = port === null;
    get("onboardingRefreshNetwork").disabled = networkBusy;
    const listener = network?.listener || {};
    get("onboardingNetworkStatus").textContent = networkError || (!network ? "Reading this device's network settings…"
      : Date.now() - networkAt > 15000 ? "Network details are stale. Refresh before changing your PS5 settings."
      : !ip ? "No reachable LAN address for this listener. Connect this device to Wi-Fi/Ethernet and refresh; a loopback-only listener cannot receive from PS5."
      : port === null ? "The listener did not return a valid UDP port. Check advanced Connection settings."
      : listener.state === "receiving" ? "Receiving telemetry. Keep this address and port on your PS5."
      : listener.state === "listening" ? "Listening—waiting for game telemetry. Enter the address and port above, then start an on-track session."
      : listener.state === "stale" ? "Telemetry was received, but is not fresh right now. Check the game is still running."
      : listener.state === "off" ? "These are the configured details, but the listener is off. Start it in advanced Connection settings."
      : "The listener needs attention. Check advanced Connection settings if nothing arrives.");
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
      networkError = "Network details could not be read. Refresh to retry; do not enter an old or guessed IP address.";
    } finally { networkBusy = false; renderNetwork(); }
  }
  async function copyNetwork(kind) {
    const value = networkValues()[kind];
    if (value === null) return;
    try {
      await navigator.clipboard.writeText(String(value));
      get("onboardingCopyStatus").textContent = kind === "ip" ? "UDP IP copied." : "UDP port copied.";
    } catch { get("onboardingCopyStatus").textContent = "Clipboard unavailable. You can select the value above or type it on the PS5."; }
  }
  function renderLive() {
    const fresh = freshState();
    const telemetry = fresh && state.connected === true && !state.telemetry_stale && !state.game_paused;
    get("onboardingCalibrate").disabled = radioBusy || !telemetry;
    get("onboardingPttStatus").textContent = !fresh ? "Live status unavailable. Reconnect to the app before calibrating."
      : !telemetry ? "Waiting for an unpaused, live telemetry feed. Check the UDP details above and park safely in an on-track session."
      : Number(state.ptt_mask) > 0 ? `A radio button is mapped. ${state.ptt_status || "You can recalibrate if your game binding changed."}`
      : `Telemetry is arriving. ${state.ptt_status || "Select Start calibration."}`;
    const micError = fresh && /microphone|audio/i.test(state.last_error || "");
    get("onboardingEnableMark").disabled = radioBusy || configured !== true || !fresh;
    get("onboardingEnableMark").textContent = fresh && state.wake_enabled ? "Reapply hands-free setting" : "Enable hands-free radio";
    get("onboardingWakeStatus").textContent = !fresh ? "Live radio status unavailable. Reconnect to the app."
      : configured !== true ? "Save an OpenAI key first, or skip voice setup for now."
      : micError ? "Microphone/audio needs attention. Check device permissions and audio settings, then restart the app."
      : `Hands-free radio ${state.wake_enabled ? "on" : "off"} · ${state.wake_status || "status unavailable"}`;
    const rms = fresh && Number.isFinite(state.wake_input_rms) ? Math.max(0, state.wake_input_rms) : 0;
    get("onboardingMicMeter").value = Math.min(1, rms / Math.max(300, Number(state?.wake_threshold_rms) || 0));
    get("onboardingMicStatus").textContent = !fresh ? "No fresh microphone status."
      : rms > 0 ? "Microphone activity detected. This does not yet confirm a successful radio check."
      : "No input observed right now. Speak normally and check permissions if the meter stays still.";
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
        ? "An OpenAI key is already configured. You can keep it and continue."
        : "No OpenAI key configured yet. Save one below, or skip this step.";
      if (data.source === "environment") get("onboardingKeyStatus").textContent += " A system environment key takes priority again after restart; manage it in Connection.";
    } catch { if (generation === statusGeneration) get("onboardingKeyStatus").textContent = "Could not check the saved key. You can try saving, or skip for now."; }
    renderLive();
  }

  function showStep(index) {
    get("onboardingKey").value = "";
    step = index;
    for (let i = 0; i < 4; i++) get(`onboardingStep${i}`).hidden = i !== step;
    get("onboardingProgress").textContent = step === 3 ? "Optional · Explore the app" : `Step ${step + 1} of 3`;
    get("onboardingTitle").textContent = titles[step];
    get("onboardingBack").hidden = step === 0;
    get("onboardingNext").hidden = step === 3;
    get("onboardingNext").textContent = step === 2 ? "Finish setup walkthrough" : "Continue";
    get("onboardingSkipStep").textContent = step === 3 ? "Skip tour" : "Skip this step";
    dialog.scrollTop = 0;
    get("onboardingTitle").focus();
    renderLive();
    if (step === 1) { renderNetwork(); refreshNetwork(); }
  }

  function close(reason = "skipped") {
    seen = true;
    get("onboardingKey").value = "";
    let saved = true;
    // Only a viewed/skipped flag is stored. Never persist keys or live data here.
    try { localStorage.setItem(STORAGE_KEY, reason); } catch { saved = false; }
    get("onboardingSavedStatus").textContent = saved
      ? "Walkthrough closed. Reopen it anytime; only settings you explicitly saved were changed."
      : "Walkthrough closed. This browser could not remember that choice, so it may appear again next time.";
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
    if (!apiKey) { get("onboardingKeyStatus").textContent = "Paste your key first, or skip this step."; return; }
    keyBusy = true; ++statusGeneration;
    get("onboardingSaveKey").disabled = true;
    get("onboardingKey").value = "";
    get("onboardingKeyStatus").textContent = "Checking the key with OpenAI before saving…";
    try {
      const data = await request("/api/v1/credentials/openai", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({api_key: apiKey, verify: true})});
      configured = data.configured === true;
      get("onboardingKeyStatus").textContent = configured ? "Key checked and saved. You can continue to radio setup." : "The app did not confirm a saved key. Check Connection, or skip for now.";
      if (data.source === "environment") get("onboardingKeyStatus").textContent += " A system environment key will take priority after restart.";
    } catch (error) { get("onboardingKeyStatus").textContent = error.message; }
    finally { keyBusy = false; get("onboardingSaveKey").disabled = false; renderLive(); }
  });

  async function radioAction(kind) {
    if (radioBusy) return;
    const calibration = kind === "calibrate";
    const result = get(calibration ? "onboardingCalibrationResult" : "onboardingWakeResult");
    radioBusy = true; renderLive(); result.textContent = "Applying…";
    try {
      const data = await request(calibration ? "/api/ptt/calibrate" : "/api/wake/config", {
        method: "POST", ...(calibration ? {} : {headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled: true})}),
      });
      if (calibration && data.ok !== true) throw new Error("Calibration was not started. Check the live telemetry feed, then try again or skip.");
      result.textContent = calibration
        ? "Calibration started—not completed yet. Press and hold only the mapped button, then release it; watch the live status above."
        : data.enabled === true ? "Hands-free radio enabled. Now check microphone activity and try the phrase below."
        : "Hands-free radio was not enabled. Allow microphone access and restart the app, or skip for now.";
    } catch (error) { result.textContent = error.message; }
    finally { radioBusy = false; renderLive(); }
  }
  get("onboardingCalibrate").addEventListener("click", () => radioAction("calibrate"));
  get("onboardingEnableMark").addEventListener("click", () => radioAction("wake"));
})();
