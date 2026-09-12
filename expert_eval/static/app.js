"use strict";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];

const OUTCOME_TEXT = {
  algo_miss: "You marked a DANA, but the algorithm found none.",
  algo_false_alarm: "You saw no DANA, but the algorithm detected one.",
  location_mismatch: "You both saw a DANA, but in different places.",
  partial_match: "Partly matched: the systems do not correspond one to one.",
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  config: null,
  expert: null,
  sessionId: null,
  cases: [],
  index: 0,
  // current case
  hasDana: null,
  clicks: [],
  frame: 0,
  framesViewed: new Set(),
  shownAt: 0,
  playTimer: null,
  busy: false,
};

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function api(path, data) {
  const opts = data === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  };
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  let payload = null;
  try { payload = await res.json(); } catch (_) { /* non-JSON error page */ }
  if (!res.ok) {
    const err = new Error((payload && payload.error) || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return payload;
}

function showError(el, err) {
  el.textContent = err ? (err.message || String(err)) : "";
  el.hidden = !err;
}

function show(screen) {
  $$(".screen").forEach((s) => { s.hidden = s.id !== `screen-${screen}`; });
  const inSession = screen === "case";
  $("#btn-end").hidden = !inSession;
  $("#btn-logout").hidden = !state.expert || inSession;
  window.scrollTo(0, 0);
}

// ---------------------------------------------------------------------------
// Resume support (per browser, keyed by expert)
// ---------------------------------------------------------------------------
function storeKey() { return state.expert ? `dana-session-${state.expert.id}` : null; }
function saveProgress() {
  try {
    localStorage.setItem(storeKey(), JSON.stringify({
      sessionId: state.sessionId, cases: state.cases, index: state.index,
    }));
  } catch (_) { /* storage unavailable */ }
}
function loadProgress() {
  try { return JSON.parse(localStorage.getItem(storeKey()) || "null"); } catch (_) { return null; }
}
function clearProgress() {
  try { localStorage.removeItem(storeKey()); } catch (_) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Geometry: image fraction <-> lon/lat (PlateCarree axes, linear)
// ---------------------------------------------------------------------------
function fracToLonLat(fx, fy) {
  const g = state.config.geometry;
  const ux = (fx - g.ax_left) / (g.ax_right - g.ax_left);
  const uy = (fy - g.ax_top) / (g.ax_bottom - g.ax_top);
  if (ux < 0 || ux > 1 || uy < 0 || uy > 1) return null;
  return {
    lon: g.lon_min + ux * (g.lon_max - g.lon_min),
    lat: g.lat_max - uy * (g.lat_max - g.lat_min),
  };
}
function lonLatToFrac(lon, lat) {
  const g = state.config.geometry;
  return {
    fx: g.ax_left + (lon - g.lon_min) / (g.lon_max - g.lon_min) * (g.ax_right - g.ax_left),
    fy: g.ax_top + (g.lat_max - lat) / (g.lat_max - g.lat_min) * (g.ax_bottom - g.ax_top),
  };
}
function inBox(c) {
  const b = state.config.box;
  return c.lon >= b[0] && c.lon <= b[1] && c.lat >= b[2] && c.lat <= b[3];
}
function fmtLonLat(c) {
  const lat = `${Math.abs(c.lat).toFixed(1)}°${c.lat >= 0 ? "N" : "S"}`;
  const lon = `${Math.abs(c.lon).toFixed(1)}°${c.lon >= 0 ? "E" : "W"}`;
  return `${lat}, ${lon}`;
}
function drawMarkers(container, clicks, opts = {}) {
  container.innerHTML = "";
  clicks.forEach((c, i) => {
    const { fx, fy } = lonLatToFrac(c.lon, c.lat);
    const m = document.createElement("div");
    m.className = "marker";
    if (opts.matched) m.classList.add(opts.matched[i] ? "matched" : "unmatched");
    m.style.left = `${fx * 100}%`;
    m.style.top = `${fy * 100}%`;
    m.textContent = String(i + 1);
    if (opts.onRemove) {
      m.title = "Click to remove";
      m.addEventListener("click", (ev) => { ev.stopPropagation(); opts.onRemove(i); });
    }
    container.appendChild(m);
  });
}

// ---------------------------------------------------------------------------
// Sign-in
// ---------------------------------------------------------------------------
function setupSignin() {
  $$(".tab").forEach((t) => t.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
    $("#form-code").hidden = t.dataset.tab !== "code";
    $("#form-profile").hidden = t.dataset.tab !== "profile";
    showError($("#signin-error"), null);
  }));
  const sel = $("#form-profile select[name=experience]");
  sel.innerHTML = `<option value="">Choose…</option>` +
    state.config.experience_options.map((o) => `<option>${o}</option>`).join("");

  const submit = async (ev, payload) => {
    ev.preventDefault();
    try {
      const r = await api("/api/login", payload);
      onSignedIn(r.expert);
    } catch (err) { showError($("#signin-error"), err); }
  };
  $("#form-code").addEventListener("submit", (ev) =>
    submit(ev, { code: new FormData(ev.target).get("code") }));
  $("#form-profile").addEventListener("submit", (ev) =>
    submit(ev, Object.fromEntries(new FormData(ev.target))));
}

function onSignedIn(expert) {
  state.expert = expert;
  $("#who").textContent = expert.label ? `Signed in as ${expert.label}` : "";
  const sel = $("#n-cases");
  sel.innerHTML = state.config.session_sizes
    .map((n) => `<option value="${n}" ${n === state.config.default_cases ? "selected" : ""}>${n}</option>`)
    .join("");
  const saved = loadProgress();
  $("#btn-resume").hidden = !(saved && saved.index < saved.cases.length);
  showError($("#intro-error"), null);
  show("intro");
}

// ---------------------------------------------------------------------------
// Session and cases
// ---------------------------------------------------------------------------
async function startSession() {
  try {
    const r = await api("/api/session", { n_cases: Number($("#n-cases").value) });
    state.sessionId = r.session_id;
    state.cases = r.cases;
    state.index = 0;
    saveProgress();
    preload(state.cases);
    showCase();
  } catch (err) { showError($("#intro-error"), err); }
}

function resumeSession() {
  const saved = loadProgress();
  if (!saved) return;
  Object.assign(state, { sessionId: saved.sessionId, cases: saved.cases, index: saved.index });
  preload(state.cases.slice(state.index));
  showCase();
}

function preload(cases) {
  cases.forEach((c) => { const im = new Image(); im.src = c.frames[c.center_index]; });
}

function currentCase() { return state.cases[state.index]; }

function showCase() {
  const c = currentCase();
  if (!c) { endSession(); return; }
  stopPlay();
  state.hasDana = null;
  state.clicks = [];
  state.frame = c.center_index;
  state.framesViewed = new Set([0]);
  state.shownAt = performance.now();
  c.frames.forEach((f) => { const im = new Image(); im.src = f; });

  $("#progress-text").textContent = `Map ${state.index + 1} of ${state.cases.length}`;
  $("#progress-bar").style.width = `${(state.index / state.cases.length) * 100}%`;
  $("#case-month").textContent = `Month: ${MONTHS[c.month - 1]}`;
  $("#chk-unsure").checked = false;
  showError($("#case-error"), null);
  const slider = $("#frame-slider");
  slider.max = String(c.frames.length - 1);
  const single = c.frames.length <= 1;
  $$(".loop button, .loop input").forEach((el) => { el.disabled = single; });
  updateFrame();
  updateAnswerUI();
  show("case");
}

function frameOffsetHours(c, i) { return (i - c.center_index) * c.dt_hours; }
function offsetLabel(h) { return h === 0 ? "T+0" : `T${h > 0 ? "+" : "−"}${Math.abs(h)} h`; }

function updateFrame() {
  const c = currentCase();
  const i = state.frame;
  $("#map-img").src = c.frames[i];
  $("#frame-slider").value = String(i);
  const h = frameOffsetHours(c, i);
  const lbl = $("#frame-label");
  lbl.textContent = offsetLabel(h);
  lbl.classList.toggle("center", h === 0);
  state.framesViewed.add(h);
  const off = i !== c.center_index;
  $("#offframe-note").hidden = !off;
  $("#offframe-label").textContent = offsetLabel(h);
  $("#map-wrap").classList.toggle("offcenter", off);
  $("#map-wrap").classList.toggle("clickable", !off && state.hasDana === true);
}

function setFrame(i) {
  const c = currentCase();
  state.frame = Math.max(0, Math.min(c.frames.length - 1, i));
  updateFrame();
}

function togglePlay() {
  if (state.playTimer) { stopPlay(); return; }
  $("#btn-play").textContent = "❚❚ Pause";
  state.playTimer = setInterval(() => {
    const c = currentCase();
    setFrame((state.frame + 1) % c.frames.length);
  }, 700);
}
function stopPlay() {
  if (state.playTimer) clearInterval(state.playTimer);
  state.playTimer = null;
  $("#btn-play").textContent = "▶ Play";
}

function updateAnswerUI() {
  $("#btn-yes").classList.toggle("selected", state.hasDana === true);
  $("#btn-no").classList.toggle("selected", state.hasDana === false);
  $("#click-help").hidden = state.hasDana !== true;
  const list = $("#marker-list");
  list.innerHTML = "";
  state.clicks.forEach((c, i) => {
    const li = document.createElement("li");
    li.textContent = fmtLonLat(c) + (inBox(c) ? "" : " (outside box)");
    const rm = document.createElement("button");
    rm.className = "btn btn-small";
    rm.textContent = "Remove";
    rm.addEventListener("click", () => removeClick(i));
    li.appendChild(rm);
    list.appendChild(li);
  });
  $("#outside-warning").hidden = !state.clicks.some((c) => !inBox(c));
  drawMarkers($("#markers"), state.clicks, { onRemove: removeClick });
  $("#btn-submit").disabled = state.busy || state.hasDana === null ||
    (state.hasDana === true && state.clicks.length === 0);
  $("#btn-submit").textContent = state.hasDana === true && state.clicks.length === 0
    ? "Mark at least one centre" : "Submit and next";
  $("#map-wrap").classList.toggle("clickable",
    state.hasDana === true && state.frame === currentCase().center_index);
}

function removeClick(i) {
  state.clicks.splice(i, 1);
  updateAnswerUI();
}

function onMapClick(ev) {
  const c = currentCase();
  if (state.hasDana !== true || state.frame !== c.center_index) return;
  const rect = $("#map-img").getBoundingClientRect();
  const ll = fracToLonLat((ev.clientX - rect.left) / rect.width, (ev.clientY - rect.top) / rect.height);
  if (!ll) return;
  state.clicks.push(ll);
  updateAnswerUI();
}

async function submitAnswer() {
  const c = currentCase();
  if (state.busy) return;
  state.busy = true;
  updateAnswerUI();
  try {
    await api("/api/answer", {
      session_id: state.sessionId,
      case_id: c.case_id,
      has_dana: state.hasDana,
      clicks: state.hasDana ? state.clicks : [],
      unsure: $("#chk-unsure").checked,
      frames_viewed: Array.from(state.framesViewed).sort((a, b) => a - b),
      response_ms: Math.round(performance.now() - state.shownAt),
    });
  } catch (err) {
    if (err.status !== 409) {  // 409 = already answered (e.g. after a resume): just move on
      state.busy = false;
      updateAnswerUI();
      showError($("#case-error"), err);
      return;
    }
  }
  state.busy = false;
  state.index += 1;
  saveProgress();
  if (state.index >= state.cases.length) endSession();
  else showCase();
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------
async function endSession() {
  stopPlay();
  try {
    const s = await api(`/api/session/${state.sessionId}/end`, {});
    clearProgress();
    renderSummary(s);
  } catch (err) {
    showError($("#case-error"), err);
  }
}

function pct(a, b) { return b ? `${Math.round((100 * a) / b)}%` : "–"; }

function stat(big, small, text) {
  return `<div class="stat"><div class="big">${big}${small ? ` <small>${small}</small>` : ""}</div><p>${text}</p></div>`;
}

function renderSummary(s) {
  const endedEarly = s.end_reason === "ended_early";
  $("#sum-lead").textContent = s.n_answered === 0
    ? "You ended the session before answering any map."
    : `You judged ${s.n_answered} map${s.n_answered === 1 ? "" : "s"}` +
      (endedEarly ? ` (of ${s.n_planned} planned)` : "") +
      (s.n_unsure ? `, ${s.n_unsure} marked as unsure.` : ".");

  const cards = [];
  if (s.n_answered) {
    cards.push(stat(`${s.agree_maps}/${s.n_answered}`, pct(s.agree_maps, s.n_answered),
      "maps where the algorithm fully agreed with you."));
  }
  if (s.expert_yes_maps) {
    cards.push(stat(`${s.algo_found_maps}/${s.expert_yes_maps}`, pct(s.algo_found_maps, s.expert_yes_maps),
      `of the maps where you saw a DANA, the algorithm also detected one.`));
    cards.push(stat(`${s.systems_located}/${s.expert_systems}`, pct(s.systems_located, s.expert_systems),
      `of the DANA centres you marked fall inside an algorithm detection.` +
      (s.algo_extra_systems ? ` The algorithm also detected ${s.algo_extra_systems} system(s) you did not mark.` : "")));
  }
  if (s.expert_no_maps) {
    cards.push(stat(`${s.algo_flagged_maps}/${s.expert_no_maps}`, "",
      `of the maps where you saw no DANA were flagged as DANA by the algorithm (false alarms).`));
  }
  $("#stats").innerHTML = cards.join("");
  const ae = s.all_experts;
  $("#sum-community").textContent = ae.n_responses
    ? `Across all experts so far, the algorithm agrees on ${pct(ae.agreement_rate * ae.n_responses, ae.n_responses)} of ${ae.n_responses} answers.`
    : "";
  $("#btn-more").textContent = `Continue with ${state.config.extend_by} more maps`;

  const gal = $("#disagreements");
  gal.innerHTML = "";
  s.disagreements.forEach((d) => gal.appendChild(renderDisagreement(d)));
  $("#disagreements-block").hidden = s.disagreements.length === 0;
  show("summary");
}

function renderDisagreement(d) {
  const node = $("#tpl-disagreement").content.firstElementChild.cloneNode(true);
  $(".dis-frame", node).src = d.frame;
  const ov = $(".dis-overlay", node);
  ov.src = d.overlay;
  $(".toggle-overlay", node).addEventListener("change", (ev) => { ov.hidden = !ev.target.checked; });
  const markers = $(".markers", node);
  markers.classList.add("static");
  drawMarkers(markers, d.clicks, { matched: d.click_matched });
  $(".dis-title", node).textContent = OUTCOME_TEXT[d.outcome] || d.outcome;
  const bits = [MONTHS[(d.month || 1) - 1]];
  if (d.algo_n_cols) bits.push(`algorithm systems: ${d.algo_n_cols}`);
  if (d.clicks.length) bits.push(`your markers: ${d.clicks.length}`);
  if (d.unsure) bits.push("you marked it as unsure");
  $(".dis-detail", node).textContent = bits.join(" · ");

  const name = `chg-${d.response_id}`;
  $$("input[type=radio]", node).forEach((r) => { r.name = name; });
  const chips = $(".chips", node);
  const selected = new Set(d.review ? d.review.reasons : []);
  d.reason_options.forEach((opt) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip" + (selected.has(opt) ? " on" : "");
    b.textContent = opt;
    b.addEventListener("click", () => {
      if (selected.has(opt)) selected.delete(opt); else selected.add(opt);
      b.classList.toggle("on");
    });
    chips.appendChild(b);
  });
  const ta = $("textarea", node);
  if (d.review) {
    ta.value = d.review.comment || "";
    const r = $(`input[type=radio][value="${d.review.changed}"]`, node);
    if (r) r.checked = true;
  }
  $(".btn-save", node).addEventListener("click", async () => {
    const chk = $("input[type=radio]:checked", node);
    try {
      await api("/api/review", {
        response_id: d.response_id,
        changed: chk ? chk.value === "1" : null,
        reasons: Array.from(selected),
        comment: ta.value,
      });
      $(".saved", node).hidden = false;
      $(".saved", node).textContent = "Saved ✓";
    } catch (err) {
      $(".saved", node).hidden = false;
      $(".saved", node).textContent = err.message;
    }
  });
  return node;
}

async function extendSession() {
  try {
    const r = await api(`/api/session/${state.sessionId}/extend`, { n_cases: state.config.extend_by });
    state.cases = state.cases.concat(r.cases);
    state.index = r.offset;
    saveProgress();
    preload(r.cases);
    showCase();
  } catch (err) {
    alert(err.message);
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
async function init() {
  try {
    state.config = await api("/api/config");
  } catch (err) {
    document.querySelector("main").innerHTML = `<div class="card narrow"><h1>Not available</h1><p>${err.message}</p></div>`;
    return;
  }
  setupSignin();
  $("#btn-start").addEventListener("click", startSession);
  $("#btn-resume").addEventListener("click", resumeSession);
  $("#btn-yes").addEventListener("click", () => { state.hasDana = true; updateAnswerUI(); updateFrame(); });
  $("#btn-no").addEventListener("click", () => { state.hasDana = false; state.clicks = []; updateAnswerUI(); updateFrame(); });
  $("#btn-submit").addEventListener("click", submitAnswer);
  $("#map-wrap").addEventListener("click", onMapClick);
  $("#btn-prev").addEventListener("click", () => { stopPlay(); setFrame(state.frame - 1); });
  $("#btn-next").addEventListener("click", () => { stopPlay(); setFrame(state.frame + 1); });
  $("#btn-play").addEventListener("click", togglePlay);
  $("#btn-back-center").addEventListener("click", (ev) => {
    ev.stopPropagation(); stopPlay(); setFrame(currentCase().center_index);
  });
  $("#frame-slider").addEventListener("input", (ev) => { stopPlay(); setFrame(Number(ev.target.value)); });
  $("#btn-end").addEventListener("click", () => {
    if (confirm("End the training now and see your results?")) endSession();
  });
  $("#btn-more").addEventListener("click", extendSession);
  $("#btn-finish").addEventListener("click", () => show("done"));
  $("#btn-again").addEventListener("click", () => onSignedIn(state.expert));
  $("#btn-logout").addEventListener("click", async () => {
    await api("/api/logout", {});
    state.expert = null;
    $("#who").textContent = "";
    show("signin");
  });
  document.addEventListener("keydown", (ev) => {
    if ($("#screen-case").hidden || ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA") return;
    if (ev.key === "ArrowLeft") { stopPlay(); setFrame(state.frame - 1); }
    if (ev.key === "ArrowRight") { stopPlay(); setFrame(state.frame + 1); }
  });

  if (state.config.expert) onSignedIn(state.config.expert);
  else show("signin");
}

init();
