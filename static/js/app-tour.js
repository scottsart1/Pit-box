/* Optional navigation-only tour. Never clicks an action or submits a form. */
(() => {
  "use strict";
  const get = id => document.getElementById(id);
  const panel = get("appTour"), app = document.querySelector(".app");
  if (!panel || !app) return;
  const stops = [
    {page: "live", target: "traceCard", title: "Drive · your race at a glance",
      text: "Follow live timing, tyres, fuel and lap targets. Use Dashboard to arrange your cards."},
    {page: "live", target: "radio", title: "Mark · your race engineer",
      text: "Type a question, hold your mapped L3 button, or say ‘Mark’. Voice needs an OpenAI key and microphone access."},
    {page: "driver-dashboard", target: "tab-driver-dashboard", title: "Driver Dashboard · a dedicated driving display",
      text: "Choose a driving layout for your phone, tablet or laptop, then customize the display."},
    {page: "strategy", target: "stratCallHeading", title: "Strategy · plan your pit stops",
      text: "Compare pit plans and what-if scenarios. Discuss a plan with Mark, then choose what to run."},
    {page: "connection", target: "connectionHeading", title: "Connection · check the real feed",
      text: "Check your UDP address, port and connection health. Live forwarding is beta; devices do not auto-sync."},
    {page: "connection", target: "transferPanel", title: "Transfer history · take your sessions with you",
      text: "Copy saved sessions between paired devices on the same network. Beta: check the copy before deleting originals. Keys stay private."},
    {page: "analysis", view: "library", target: "libraryTableHeading", title: "Library · revisit previous sessions",
      text: "Browse saved races, qualifying and practice. Open a session to review its laps and recording quality."},
    {page: "analysis", view: "lap-lab", target: "traceHeading", title: "Lap Lab · find where time is won or lost",
      text: "Pick recorded laps to compare speed, throttle and braking. Missing telemetry stays unavailable."},
    {page: "analysis", view: "field", target: "tab-field", title: "Field · compare the competition",
      text: "Compare the field's recorded pace, corners and stints. New installs capture all cars by default."},
    {page: "setup", target: "tab-setup", title: "Setup Lab · tailor the car",
      text: "Choose a track and profile for setup recommendations. Apply the changes in the game yourself."},
    {page: "settings", target: "onboardingSettings", title: "Settings · make it yours",
      text: "Adjust radio and recording preferences, or replay this tour. Privacy and App updates are at the bottom."},
  ];
  let index = -1, highlighted = null, origin = null, oldSpace = "";
  function navigate(page, view) {
    get(`tab-${page}`)?.click();
    if (view) get(`tab-${view}`)?.click();
  }
  function reserveSpace() {
    if (index < 0) return;
    app.style.setProperty("--app-tour-space", `${Math.ceil(panel.getBoundingClientRect().height) + 12}px`);
  }
  function clearHighlight() {
    highlighted?.classList.remove("app-tour-highlight"); highlighted = null;
  }
  function show(position) {
    clearHighlight(); index = position;
    const stop = stops[index];
    navigate(stop.page, stop.view);
    get("appTourTitle").textContent = stop.title;
    get("appTourText").textContent = stop.text;
    get("appTourProgress").textContent = `App tour · ${index + 1} of ${stops.length}`;
    get("appTourBack").disabled = index === 0;
    get("appTourNext").textContent = index === stops.length - 1 ? "Finish tour" : "Next";
    panel.scrollTop = 0; reserveSpace();
    // A user-hidden dashboard card stays hidden. Highlight its workspace tab instead.
    const target = get(stop.target);
    highlighted = target && target.getClientRects().length ? target : get(`tab-${stop.view || stop.page}`);
    highlighted?.classList.add("app-tour-highlight");
    highlighted?.scrollIntoView({block: "nearest", inline: "nearest", behavior: "instant"});
    get("appTourTitle").focus({preventScroll: true});
  }
  function stop(reason = "skipped") {
    if (index < 0) return;
    index = -1; clearHighlight(); panel.hidden = true;
    app.classList.remove("app-tour-active");
    if (oldSpace) app.style.setProperty("--app-tour-space", oldSpace);
    else app.style.removeProperty("--app-tour-space");
    try { localStorage.setItem("pitwall.app-tour.v1", reason); } catch { /* still skippable */ }
    if (origin) {
      navigate(origin.page, origin.view);
      if (origin.focus?.isConnected && !get("onboardingDialog")?.contains(origin.focus)) origin.focus.focus();
      else get(`tab-${origin.page}`)?.focus();
    }
  }
  function start() {
    if (index >= 0 || get("onboardingDialog")?.open) return;
    const page = document.querySelector(".tab.active")?.dataset.page || "live";
    origin = {page, view: page === "analysis" ? document.querySelector(".analysis-subnav .field-tab.active")?.dataset.page : null, focus: document.activeElement};
    oldSpace = app.style.getPropertyValue("--app-tour-space");
    app.classList.add("app-tour-active"); panel.hidden = false;
    show(0);
  }
  get("appTourBegin").addEventListener("click", start);
  get("appTourSkip").addEventListener("click", () => stop());
  get("appTourBack").addEventListener("click", () => { if (index > 0) show(index - 1); });
  get("appTourNext").addEventListener("click", () => {
    if (index < 0) return;
    if (index < stops.length - 1) show(index + 1); else stop("finished");
  });
  window.addEventListener("pitwall:tour-start", start);
  window.addEventListener("pitwall:tour-stop", () => stop());
  window.addEventListener("keydown", event => {
    if (index >= 0 && event.key === "Escape") { event.preventDefault(); stop(); }
  });
  window.addEventListener("resize", reserveSpace);
  if (typeof ResizeObserver === "function") new ResizeObserver(reserveSpace).observe(panel);
})();
