/* Naija Solar — bespoke frontend. Home + app views, full 5-language i18n, animated background,
   testimonials carousel, accounts + history, all talking to the FastAPI backend (server.py). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let CFG = {}, ME = null, authMode = "signup", rateShown = false, narrSeq = 0;
  const S = { lang: "en", sel: {}, state: "Lagos", geolat: "" };
  const LANG_LABELS = { en: "English", pcm: "Naijá Pidgin", yo: "Yorùbá", ha: "Hausa", ig: "Ìgbò" };
  const EXAMPLES = [["ex_home", "1 fridge, 2 standing fans, 6 bulbs, 1 TV"],
    ["ex_shop", "1 chest freezer, 1 air conditioner, 4 bulbs, 1 TV, 1 decoder"],
    ["ex_family", "1 fridge, 1 air conditioner, 3 fans, 8 bulbs, 1 TV, 1 washing machine"]];

  const T = (k) => { const s = CFG.strings; return (s && ((s[S.lang] && s[S.lang][k]) || (s.en && s.en[k]))) || null; };
  function status(msg, spin) { $("status").innerHTML = msg ? (spin ? '<span class="spin"></span> ' : "") + msg : ""; }

  // ── light / dark theme ───────────────────────────────────────────────────────
  const ICON_SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.1"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.1 5.1l1.4 1.4M17.5 17.5l1.4 1.4M18.9 5.1l-1.4 1.4M6.5 17.5l-1.4 1.4"/></svg>';
  const ICON_MOON = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M21 12.9A9 9 0 1 1 11.1 3a7 7 0 0 0 9.9 9.9z"/></svg>';
  const curTheme = () => (document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light");
  function paintTheme() {
    const dark = curTheme() === "dark", btn = $("themeBtn"); if (!btn) return;
    btn.innerHTML = dark ? ICON_SUN : ICON_MOON;   // shows the mode you'll switch to
    btn.title = dark ? (T("theme_light") || "Light mode") : (T("theme_dark") || "Dark mode");
    btn.setAttribute("aria-label", btn.title);
  }
  function setTheme(t) { document.documentElement.setAttribute("data-theme", t); try { localStorage.setItem("ns-theme", t); } catch (e) {} paintTheme(); }
  $("themeBtn").onclick = () => setTheme(curTheme() === "dark" ? "light" : "dark");
  async function api(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    return r.json();
  }

  // ── i18n: apply to every marked element ─────────────────────────────────────
  function applyLang(L) {
    S.lang = L;
    document.querySelectorAll(".langbtn").forEach((b) => b.classList.toggle("on", b.dataset.lang === L));
    if (CFG.hero) $("hero") && ($("hero").innerHTML = CFG.hero[L] || "");
    if (CFG.steps) $("guide") && ($("guide").innerHTML = CFG.steps[L] || "");
    document.querySelectorAll("[data-i18n]").forEach((el) => { const v = T(el.dataset.i18n); if (v != null) el.textContent = v; });
    document.querySelectorAll("[data-i18n-ph]").forEach((el) => { const v = T(el.dataset.i18nPh); if (v != null) el.placeholder = v; });
    const ui = (CFG.ui && (CFG.ui[L] || CFG.ui.en)) || {};
    if (ui.type) $("textIn").placeholder = ui.type;
    $("picker").options[0] && (($("picker").options[0]).textContent = T("pick") || "Pick an appliance…");
    document.querySelectorAll("#exrow .exbtn").forEach((b) => { const v = T(b.dataset.k); if (v) b.textContent = v; });
    if (CFG.guideTitle && $("guideTitle")) $("guideTitle").textContent = CFG.guideTitle[L] || CFG.guideTitle.en;
    renderAcct();
    paintTheme();
    if (authMode) refreshAuthToggle();
    if (Object.keys(S.sel).length) narrate();   // re-speak in the new language
  }

  // ── views ───────────────────────────────────────────────────────────────────
  let warmed = false;
  function warmTTS() { if (warmed) return; warmed = true; fetch("/api/warm", { method: "POST" }).catch(() => {}); }
  function showView(name) {
    document.querySelectorAll(".view").forEach((v) => { v.hidden = v.id !== name; });
    if (name === "app") warmTTS();   // wake the voice while they read, so audio is ready sooner
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ── first-run guided tour ─────────────────────────────────────────────────────
  const TOUR_DEF = () => [
    { sel: ".miccard .imode", t: T("speak") || "Speak your appliances", b: T("voicehint") || "Tap Record, say what you run, and it sizes itself." },
    { sel: "#textIn", t: T("type") || "Or type them", b: ((CFG.ui && (CFG.ui[S.lang] || CFG.ui.en)) || {}).type || "Like: 1 fridge, 2 fans, 6 bulbs" },
    { sel: "#exrow", t: T("ex_h") || "New here? Tap an example", b: T("ex_sub") || "A full result in one click." },
    { sel: "#dropZone", t: T("photo") || "Or snap a photo", b: T("photo_drop") || "Add photos of your room and it reads the appliances." },
    { sel: "#langrow", t: T("chooseLang") || "Choose your language", b: T("tour_lang") || "Switch anytime. The whole app and the spoken plan follow." },
  ];
  let tourList = [], tourI = 0;
  const clearTourHi = () => document.querySelectorAll(".tourhi").forEach((e) => e.classList.remove("tourhi"));
  function tourEnd() { clearTourHi(); $("tourBox").hidden = true; $("tourOv").hidden = true; try { localStorage.setItem("ns-tour", "1"); } catch (e) {} }
  function tourShow() {
    clearTourHi();
    if (tourI >= tourList.length) return tourEnd();
    const step = tourList[tourI], el = document.querySelector(step.sel);
    if (!el) { tourI++; return tourShow(); }
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => {
      el.classList.add("tourhi");
      const box = $("tourBox");
      box.innerHTML = `<div class="tourstep">${tourI + 1} / ${tourList.length}</div><div class="tourt">${step.t}</div><div class="tourb">${step.b}</div>`
        + `<div class="tourbtns"><button class="tourskip" id="tourSkip">${T("tour_skip") || "Skip"}</button>`
        + `<button class="tournext" id="tourNext">${tourI === tourList.length - 1 ? (T("tour_done") || "Got it") : (T("tour_next") || "Next →")}</button></div>`;
      box.hidden = false;
      const r = el.getBoundingClientRect(), bh = box.offsetHeight, bw = box.offsetWidth;
      const below = r.bottom + 14 + bh < window.innerHeight;
      box.style.top = (below ? r.bottom + 14 : Math.max(12, r.top - bh - 14)) + "px";
      box.style.left = Math.min(Math.max(12, r.left + r.width / 2 - bw / 2), window.innerWidth - bw - 12) + "px";
      $("tourNext").onclick = () => { tourI++; tourShow(); };
      $("tourSkip").onclick = tourEnd;
    }, 340);
  }
  function startTour() { showView("app"); tourList = TOUR_DEF(); tourI = 0; $("tourOv").hidden = false; tourShow(); }
  function maybeTour() { try { if (localStorage.getItem("ns-tour")) return; } catch (e) {} setTimeout(startTour, 480); }

  // ── render a full result ─────────────────────────────────────────────────────
  function applistHtml(sel) {
    const e = Object.entries(sel);
    if (!e.length) return '<div class="adjnote">No appliances yet. Pick one above and tap Add.</div>';
    return '<div class="chips applchips">' + e.map(([k, v]) => `<span class="achip">${k.split(" (")[0]} <b>×${v}</b></span>`).join("") + "</div>";
  }
  function renderApplist() { $("applist").innerHTML = applistHtml(S.sel); }
  // a short, human list of the current appliances, e.g. "1 fridge, 2 standing fans, 6 bulbs"
  function selSummary(sel) {
    return Object.entries(sel || {}).map(([k, v]) => { const n = k.split(" (")[0]; return v + " " + (v > 1 ? n + "s" : n); }).join(", ");
  }

  function render(b) {
    S.sel = b.sel || {};
    $("content").innerHTML = (b.chips || "") + (b.tiles || "") + (b.cards || "");
    const _det = $("detected"); if (_det) { _det.hidden = true; _det.innerHTML = ""; }   // the "from your photo" callout is shown only on the photo path
    $("system").innerHTML = b.system || "";
    $("breakdown").innerHTML = b.breakdown || "";
    $("twod").innerHTML = b.twod || "";
    $("vendors").innerHTML = b.vendors || "";
    $("narrText").textContent = b.narration || "";   // written plan shows immediately
    $("sumbar").innerHTML = b.summary || "";
    if (b.count) $("ucount").innerHTML = b.count;
    renderApplist();
    $("result").classList.add("show");
    try { const a = (b.house || "").split("|"); if (window.renderHouse) window.renderHouse(parseInt(a[0]) || 0, parseInt(a[1]) || 0, a[2] || ""); } catch (e) {}
    if (ME && b.userCount != null) { ME.count = b.userCount; renderAcct(); }
    setSaved(b.saved);
    status("");
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    narrate();
    if (!rateShown) { rateShown = true; setTimeout(openRate, 2200); }   // ask for a rating after first result
  }

  async function narrate() {
    if (!Object.keys(S.sel).length) return;
    const au = $("narrAudio"), st = $("narrStatus");
    const body = { appliances: S.sel, state: S.state, geolat: S.geolat, lang: S.lang };
    const my = ++narrSeq;
    // 1) the written explanation appears instantly (same words the voice reads)
    try { const j = await api("/api/narration", body); if (my === narrSeq && j.text) $("narrText").textContent = j.text; } catch (e) {}
    // 2) the voice loads in the background with a clear loading state
    au.hidden = true; st.innerHTML = '<span class="spin"></span> ' + (T("narr_loading") || "Preparing the voice…");
    try {
      const res = await fetch("/api/narrate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (my !== narrSeq) return;                 // a newer narration (e.g. language switch) superseded this one
      if (!res.ok) throw 0;
      au.src = URL.createObjectURL(await res.blob());
      au.hidden = false; st.innerHTML = "";
      au.play().catch(() => {});                  // autoplay may be blocked; the player is visible to tap
    } catch (e) {
      if (my === narrSeq) st.innerHTML = '<span class="narrfail">' + (T("narr_fail") || "The voice is taking a moment. You can read your plan above, or try the play button.") + "</span>";
    }
  }

  async function size(text) {
    if (!text || !text.trim()) { status("Type your appliances, tap an example, or use voice."); return; }
    showView("app");
    status("Listening and sizing your system…", true);
    try {
      const b = await api("/api/size", { text, state: S.state, geolat: S.geolat, lang: S.lang });
      if (!b.ok) { status(b.msg || 'Tell me your appliances, like "1 fridge, 2 fans, 6 bulbs".'); return; }
      render(b);
    } catch (e) { status("Something went wrong. Please try again."); }
  }

  // voice
  let mediaRec = null, chunks = [], recording = false;
  function setRec(on) { $("recBtn").classList.toggle("rec", on); $("recLbl").textContent = on ? (T("stop") || "Stop") : (T("record") || "Record"); }
  $("recBtn").addEventListener("click", async () => {
    if (recording && mediaRec) { mediaRec.stop(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRec = new MediaRecorder(stream); chunks = [];
      mediaRec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      mediaRec.onstop = async () => {
        recording = false; setRec(false); stream.getTracks().forEach((t) => t.stop());
        status("Transcribing…", true);
        try {
          const wav = await blobToWav(new Blob(chunks));
          const fd = new FormData(); fd.append("file", wav, "rec.wav");
          const j = await (await fetch("/api/asr", { method: "POST", body: fd })).json();
          if (j.text && j.text.trim()) { $("textIn").value = j.text.trim(); size(j.text.trim()); }
          else status("Could not hear that. Try again or type.");
        } catch (e) { status("Could not process the audio. Please type instead."); }
      };
      mediaRec.start(); recording = true; setRec(true); status("Listening… tap Stop when done.");
    } catch (e) { status("Microphone blocked. Allow mic access, or type your appliances."); }
  });
  async function blobToWav(blob) {
    const Ctx = window.AudioContext || window.webkitAudioContext; const ctx = new Ctx();
    const buf = await ctx.decodeAudioData(await blob.arrayBuffer());
    const len = buf.length, ch = buf.numberOfChannels, sr = buf.sampleRate; const mono = new Float32Array(len);
    for (let c = 0; c < ch; c++) { const d = buf.getChannelData(c); for (let i = 0; i < len; i++) mono[i] += d[i] / ch; }
    const out = new DataView(new ArrayBuffer(44 + len * 2));
    const ws = (o, s) => { for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i)); };
    ws(0, "RIFF"); out.setUint32(4, 36 + len * 2, true); ws(8, "WAVE"); ws(12, "fmt ");
    out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
    out.setUint32(24, sr, true); out.setUint32(28, sr * 2, true); out.setUint16(32, 2, true);
    out.setUint16(34, 16, true); ws(36, "data"); out.setUint32(40, len * 2, true);
    let o = 44; for (let i = 0; i < len; i++) { let s = Math.max(-1, Math.min(1, mono[i])); out.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true); o += 2; }
    try { ctx.close(); } catch (e) {}
    return new Blob([out], { type: "audio/wav" });
  }

  // photos
  let photoFiles = [];
  $("fileIn").addEventListener("change", (e) => {
    photoFiles = Array.from(e.target.files).slice(0, 5);
    $("thumbs").innerHTML = photoFiles.map((f) => `<img src="${URL.createObjectURL(f)}">`).join("");
  });
  $("photoBtn").addEventListener("click", async () => {
    if (!photoFiles.length) { status("Add a photo of your appliances first."); return; }
    status("Reading your photo…", true);
    try {
      const fd = new FormData(); photoFiles.forEach((f) => fd.append("files", f));
      fd.append("state", S.state); fd.append("lang", S.lang); fd.append("geolat", S.geolat);
      const b = await (await fetch("/api/vision", { method: "POST", body: fd })).json();
      if (!b.ok) { status(b.msg || "Could not spot appliances. Try a clearer photo or type them."); return; }
      render(b);
      // show exactly what the photo reader extracted, so the user can confirm or adjust
      const det = $("detected"), sum = selSummary(S.sel);
      if (det && sum) {
        det.innerHTML = '<div class="dethd">📷 ' + (T("photo_spotted") || "From your photo, we spotted") + ":</div>"
          + '<div class="detlist">' + sum + "</div>"
          + '<div class="dethint">' + (T("photo_adjust") || "Not quite right? Adjust the appliances below.") + "</div>";
        det.hidden = false;
      }
    } catch (e) { status("Could not read the photo. Please type your appliances."); }
  });

  // start a fresh sizing: clear the appliances, inputs, photos and result, and return to the input
  function newSizing() {
    narrSeq++;                                   // cancel any voice still loading
    S.sel = {};
    $("textIn").value = "";
    photoFiles = []; $("thumbs").innerHTML = ""; try { $("fileIn").value = ""; } catch (e) {}
    $("result").classList.remove("show");
    $("content").innerHTML = "";
    const det = $("detected"); if (det) { det.hidden = true; det.innerHTML = ""; }
    $("narrText").textContent = ""; $("narrStatus").innerHTML = "";
    const au = $("narrAudio"); if (au) { try { au.pause(); } catch (e) {} au.hidden = true; au.removeAttribute("src"); }
    $("qaOut").textContent = ""; $("qaIn").value = "";
    renderApplist();
    status("");
    const mc = document.querySelector(".miccard"); if (mc) mc.scrollIntoView({ behavior: "smooth", block: "start" });
    setTimeout(() => { try { $("textIn").focus({ preventScroll: true }); } catch (e) {} }, 320);
  }

  $("sizeBtn").addEventListener("click", () => size($("textIn").value));
  $("textIn").addEventListener("keydown", (e) => { if (e.key === "Enter") size($("textIn").value); });

  // tabs
  document.querySelectorAll(".tabbtn").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tabbtn").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("on", p.id === b.dataset.tab));
  }));

  // adjust / q&a / feedback / geo
  $("addBtn").addEventListener("click", () => { const n = $("picker").value, q = parseInt($("qty").value) || 1; if (n) { S.sel[n] = q; renderApplist(); } });
  $("clearBtn").addEventListener("click", () => { S.sel = {}; renderApplist(); });
  $("recalcBtn").addEventListener("click", async () => {
    if (!Object.keys(S.sel).length) { status("Add an appliance first."); return; }
    status("Updating your sizing…", true);
    const b = await api("/api/recalc", { appliances: S.sel, state: S.state, geolat: S.geolat, lang: S.lang });
    if (b.ok) render(b); else status(b.msg || "");
  });
  async function ask() {
    const q = $("qaIn").value.trim(); if (!q) return;
    if (!Object.keys(S.sel).length) { $("qaOut").textContent = "Size your appliances first, then I can answer."; return; }
    $("qaOut").innerHTML = '<span class="spin"></span> Thinking…';
    try { const j = await api("/api/ask", { question: q, appliances: S.sel, state: S.state, geolat: S.geolat, lang: S.lang }); $("qaOut").textContent = j.answer || ""; }
    catch (e) { $("qaOut").textContent = "Could not answer just now. Please try again."; }
  }
  $("qaBtn").addEventListener("click", ask);
  $("qaIn").addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });
  async function feedback(r) { const j = await api("/api/feedback", { rating: r, comment: $("fbComment").value }); $("fbMsg").innerHTML = j.html || ""; loadTestimonials(); }
  $("fbUp").addEventListener("click", () => feedback("up"));
  $("fbDown").addEventListener("click", () => feedback("down"));
  $("emailBtn").addEventListener("click", async () => { const j = await api("/api/email", { email: $("emailIn").value }); $("emailMsg").innerHTML = j.html || ""; });
  $("geoBtn").addEventListener("click", () => {
    if (!navigator.geolocation) return; $("locnote").textContent = "Finding your location…";
    navigator.geolocation.getCurrentPosition(async (pos) => {
      S.geolat = String(pos.coords.latitude);
      try { const j = await api("/api/geo", { geolat: S.geolat }); $("locnote").innerHTML = j.note || ""; } catch (e) {}
      if (Object.keys(S.sel).length) { const b = await api("/api/recalc", { appliances: S.sel, state: S.state, geolat: S.geolat, lang: S.lang }); if (b.ok) render(b); }
    }, () => { $("locnote").textContent = "Could not get your location. Pick your state above."; });
  });

  // ── accounts ────────────────────────────────────────────────────────────────
  function renderAcct() {
    const el = $("acct");
    if (ME) {
      const initial = (ME.name || ME.email || "?").trim().charAt(0).toUpperCase();
      el.innerHTML = `<div class="userchip" id="userChip"><div class="av">${initial}</div><div class="un">${ME.name || ME.email}</div></div>
        <div class="menu" id="userMenu" hidden><div class="mi">${ME.email}</div>
        <button id="menuHist">📁 ${T("myResults") || "My results"} (${ME.count || 0})</button><button id="menuLogout">${T("logout") || "Log out"}</button></div>`;
      $("userChip").onclick = (e) => { e.stopPropagation(); $("userMenu").hidden = !$("userMenu").hidden; };
      $("menuHist").onclick = openHistory; $("menuLogout").onclick = logout;
    } else {
      el.innerHTML = `<button class="acctbtn" id="signinBtn">${T("signin") || "Sign in"}</button>`;
      $("signinBtn").onclick = () => openAuth("login");
    }
  }
  document.addEventListener("click", () => { const m = $("userMenu"); if (m) m.hidden = true; });
  async function refreshAuth() { try { ME = (await (await fetch("/api/auth/me")).json()).user; } catch (e) { ME = null; } renderAcct(); }
  function refreshAuthToggle() {
    const su = authMode === "signup";
    $("authTitle").textContent = su ? (T("acc_create") || "Create your account") : (T("acc_welcome") || "Welcome back");
    $("authSub").textContent = su ? (T("acc_create_sub") || "") : (T("acc_welcome_sub") || "");
    $("auName").style.display = su ? "" : "none";
    $("authSubmit").textContent = su ? (T("btn_create") || "Create account") : (T("btn_signin") || "Sign in");
    $("authToggleWrap").innerHTML = su ? `${T("have_acc") || "Already have an account?"} <a id="authToggle">${T("link_signin") || "Sign in"}</a>`
      : `${T("new_here") || "New here?"} <a id="authToggle">${T("link_create") || "Create an account"}</a>`;
    $("authToggle").onclick = () => openAuth(su ? "login" : "signup");
  }
  function openAuth(mode) { authMode = mode; $("authErr").style.display = "none"; refreshAuthToggle(); $("authModal").hidden = false; setTimeout(() => $("auEmail").focus(), 50); }
  $("authClose").onclick = () => { $("authModal").hidden = true; };
  $("authModal").addEventListener("click", (e) => { if (e.target.id === "authModal") $("authModal").hidden = true; });
  async function submitAuth() {
    const j = await api(authMode === "signup" ? "/api/auth/signup" : "/api/auth/login",
      { email: $("auEmail").value.trim(), password: $("auPass").value, name: $("auName").value.trim() });
    if (!j.ok) { $("authErr").textContent = j.error || "Could not sign you in."; $("authErr").style.display = "block"; return; }
    ME = j.user; $("authModal").hidden = true; $("auPass").value = ""; renderAcct();
    if (Object.keys(S.sel).length) { const r = await api("/api/me/save", { appliances: S.sel, state: S.state, lang: S.lang }); if (r.ok) { ME.count = r.count; renderAcct(); setSaved(true); } }
  }
  $("authSubmit").onclick = submitAuth;
  $("auPass").addEventListener("keydown", (e) => { if (e.key === "Enter") submitAuth(); });
  async function logout() { try { await fetch("/api/auth/logout", { method: "POST" }); } catch (e) {} ME = null; renderAcct(); setSaved(undefined); }
  async function openHistory() {
    const m = $("userMenu"); if (m) m.hidden = true; let sz = [];
    try { sz = (await (await fetch("/api/me/sizings")).json()).sizings || []; } catch (e) {}
    $("histList").innerHTML = sz.length ? sz.map((s, i) => {
      const d = new Date((s.ts || 0) * 1000);
      const when = d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " · " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
      return `<div class="histitem" data-i="${i}"><div class="ht">${s.label || "Solar plan"}</div>
        <div class="hm"><span><b>${s.panels}</b> panels</span><span><b>${s.kva}</b> kVA</span><span><b>₦${(s.cost || 0).toLocaleString()}</b></span><span>${when}</span></div></div>`;
    }).join("") : `<div class="histempty">${T("hist_empty") || "No saved results yet."}</div>`;
    document.querySelectorAll("#histList .histitem").forEach((it) => { it.onclick = () => loadSizing(sz[+it.dataset.i]); });
    $("histModal").hidden = false;
  }
  $("histClose").onclick = () => { $("histModal").hidden = true; };
  $("histModal").addEventListener("click", (e) => { if (e.target.id === "histModal") $("histModal").hidden = true; });
  async function loadSizing(s) {
    if (!s) return; $("histModal").hidden = true; showView("app"); S.sel = s.appliances || {};
    if (s.state) { S.state = s.state; $("state").value = s.state; } if (s.lang) applyLang(s.lang);
    status("Opening your saved result…", true);
    const b = await api("/api/recalc", { appliances: S.sel, state: S.state, lang: S.lang });
    if (b.ok) render(b); else status("Could not open that result.");
  }
  function setSaved(saved) {
    const el = $("savednote");
    if (saved) el.innerHTML = `<div class="ok">✓ ${T("saved") || "Saved to your results"}${ME && ME.count ? " (" + ME.count + ")" : ""}</div>`;
    else if (!ME) el.innerHTML = `<div class="prompt" id="savePrompt"><span>💾 ${T("save_prompt") || "Sign in to save this result"}</span><span>→</span></div>`;
    else el.innerHTML = "";
    const sp = $("savePrompt"); if (sp) sp.onclick = () => openAuth("signup");
  }

  // ── rating prompt (records feedback from anyone) ─────────────────────────────
  let rateStars = 0;
  function drawStars() { $("stars").innerHTML = [1, 2, 3, 4, 5].map((n) => `<span class="star${n <= rateStars ? " on" : ""}" data-n="${n}">★</span>`).join(""); document.querySelectorAll("#stars .star").forEach((s) => s.onclick = () => { rateStars = +s.dataset.n; drawStars(); }); }
  function openRate() { rateStars = 0; drawStars(); $("rateMsg").innerHTML = ""; if (ME) $("rateName").value = ME.name || ""; $("rateModal").hidden = false; }
  $("rateClose").onclick = () => { $("rateModal").hidden = true; };
  $("rateModal").addEventListener("click", (e) => { if (e.target.id === "rateModal") $("rateModal").hidden = true; });
  $("rateSend").addEventListener("click", async () => {
    if (!rateStars) { $("rateMsg").innerHTML = '<div class="warnmsg">Tap the stars to rate.</div>'; return; }
    await api("/api/feedback", { stars: rateStars, comment: $("rateComment").value, name: $("rateName").value, rating: rateStars >= 4 ? "up" : "down" });
    $("rateMsg").innerHTML = `<div class="okmsg">${T("rate_thanks") || "Thank you! 🙏"}</div>`;
    setTimeout(() => { $("rateModal").hidden = true; }, 1100); loadTestimonials();
  });

  // ── testimonials carousel ────────────────────────────────────────────────────
  async function loadTestimonials() {
    let items = [];
    try { items = (await (await fetch("/api/testimonials")).json()).items || []; } catch (e) {}
    if (!items.length) return;
    const card = (t) => {
      const stars = "★".repeat(Math.max(1, Math.min(5, t.rating || 5)));
      const lang = LANG_LABELS[t.lang] || "";
      return `<div class="tcard"><div class="trow"><span class="trate">${stars}</span>${lang ? `<span class="tlang">🌍 ${lang}</span>` : ""}</div>`
        + `<div class="ttext">“${(t.text || "").replace(/</g, "&lt;")}”</div><div class="tname">${(t.name || "A user").replace(/</g, "&lt;")}</div></div>`;
    };
    const html = items.map(card).join("");
    $("testiTrack").innerHTML = html + html;   // duplicate for a seamless loop
  }

  // ── animated solar background: a rising sun, panels, bulbs, leaves & sparkles ──
  function solarBg() {
    const bg = $("solarbg"); if (!bg) return;
    const rays = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330]
      .map((a) => `<rect x="30.3" y="1.5" width="3.4" height="9.5" rx="1.7" transform="rotate(${a} 32 32)"/>`).join("");
    const sun = `<svg viewBox="0 0 64 64"><g fill="#ffb12e">${rays}</g><circle cx="32" cy="32" r="13.5" fill="url(#bgSun)"/><circle cx="32" cy="32" r="13.5" fill="none" stroke="rgba(255,255,255,.4)" stroke-width="1"/></svg>`;
    const spark = `<svg viewBox="0 0 24 24"><path d="M12 1l2.1 8.9L23 12l-8.9 2.1L12 23l-2.1-8.9L1 12l8.9-2.1z" fill="url(#bgSpark)"/></svg>`;
    const panel = `<svg viewBox="0 0 64 46"><rect x="3" y="3" width="58" height="40" rx="3.5" fill="url(#bgPanel)"/><g stroke="rgba(255,255,255,.5)" stroke-width="1.3"><line x1="22.3" y1="3" x2="22.3" y2="43"/><line x1="41.6" y1="3" x2="41.6" y2="43"/><line x1="3" y1="23" x2="61" y2="23"/></g><path d="M5 4 L27 4 L12 42 L5 42Z" fill="rgba(255,255,255,.14)"/></svg>`;
    const bulb = `<svg viewBox="0 0 36 44"><path d="M18 2C8.6 2 2 9 2 17c0 6.6 5 9.6 6.6 13.4h18.8C29 26.6 34 23.6 34 17 34 9 27.4 2 18 2z" fill="url(#bgBulb)"/><rect x="11" y="31.5" width="14" height="3.6" rx="1.4" fill="#dd9f33"/><rect x="13" y="37" width="10" height="3.6" rx="1.4" fill="#dd9f33"/><path d="M13.5 25l2.6-8h3.8l2.6 8" fill="none" stroke="rgba(150,105,25,.6)" stroke-width="1.5"/></svg>`;
    const leaf = `<svg viewBox="0 0 32 32"><path d="M28 4C12 4 4 13 4 28 19 28 28 19 28 4z" fill="url(#bgLeaf)"/><path d="M9 23C14 18 20 12 25 7" stroke="rgba(255,255,255,.55)" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>`;
    const T = { sun, spark, panel, bulb, leaf };
    // [kind, left%, top%, size px, opacity]
    const items = [
      ["panel", 5, 60, 62, .26], ["sun", 86, 14, 46, .3], ["spark", 91, 32, 24, .34], ["bulb", 79, 74, 44, .24],
      ["spark", 13, 83, 22, .32], ["leaf", 3, 38, 42, .24], ["panel", 67, 43, 44, .2], ["spark", 53, 91, 28, .3],
      ["sun", 28, 6, 32, .24], ["bulb", 16, 19, 36, .22], ["leaf", 93, 57, 32, .24], ["spark", 43, 49, 18, .24], ["panel", 25, 78, 48, .18]
    ];
    const defs = '<svg class="defs" aria-hidden="true"><defs>' +
      '<radialGradient id="bgSun" cx="48%" cy="42%" r="62%"><stop offset="0%" stop-color="#fff4cf"/><stop offset="52%" stop-color="#ffc24d"/><stop offset="100%" stop-color="#ff9a1f"/></radialGradient>' +
      '<linearGradient id="bgSpark" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#fff1bf"/><stop offset="100%" stop-color="#ffce5a"/></linearGradient>' +
      '<linearGradient id="bgPanel" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#4378ef"/><stop offset="100%" stop-color="#1e3aa8"/></linearGradient>' +
      '<radialGradient id="bgBulb" cx="50%" cy="38%" r="62%"><stop offset="0%" stop-color="#fff6cc"/><stop offset="100%" stop-color="#ffce4f"/></radialGradient>' +
      '<linearGradient id="bgLeaf" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#4ade80"/><stop offset="100%" stop-color="#059669"/></linearGradient>' +
      '</defs></svg>';
    let html = defs + '<div class="glow g1"></div><div class="glow g2"></div><div class="glow g3"></div>';
    html += `<span class="herosun"><span class="spin">${sun}</span></span>`;
    html += items.map((it, i) => {
      const [k, x, y, s, op] = it;
      const cls = k === "spark" ? "float tw" : "float";
      const inner = k === "sun" ? `<span class="spin slow">${T[k]}</span>` : T[k];
      return `<span class="${cls}" style="left:${x}%;top:${y}%;width:${s}px;height:${s}px;--op:${op};animation-duration:${15 + (i % 6) * 3}s;animation-delay:-${i * 1.6}s">${inner}</span>`;
    }).join("");
    bg.innerHTML = html;
  }

  // ── boot ────────────────────────────────────────────────────────────────────
  async function boot() {
    solarBg();
    paintTheme();
    try { CFG = await (await fetch("/api/config")).json(); } catch (e) { status("Could not load. Refresh the page."); return; }
    $("logo").innerHTML = CFG.logo || "";
    $("logo").onclick = () => showView("home");
    if (CFG.count) $("ucount").innerHTML = CFG.count;
    try { const st = await (await fetch("/api/stats")).json(); if ($("hsCount")) $("hsCount").textContent = (st.sizings || 0).toLocaleString(); } catch (e) {}
    const order = ["en", "pcm", "yo", "ha", "ig"];
    $("langrow").innerHTML = order.map((L) => `<button class="langbtn${L === "en" ? " on" : ""}" data-lang="${L}">${LANG_LABELS[L]}</button>`).join("");
    document.querySelectorAll(".langbtn").forEach((b) => b.addEventListener("click", () => applyLang(b.dataset.lang)));
    $("state").innerHTML = (CFG.states || ["Lagos"]).map((s) => `<option${s === "Lagos" ? " selected" : ""}>${s}</option>`).join("");
    $("state").addEventListener("change", (e) => { S.state = e.target.value; });
    $("exrow").innerHTML = EXAMPLES.map((e, i) => `<button class="exbtn" data-i="${i}" data-k="${e[0]}">${e[0]}</button>`).join("");
    document.querySelectorAll("#exrow .exbtn").forEach((b) => b.addEventListener("click", () => { const v = EXAMPLES[+b.dataset.i][1]; $("textIn").value = v; size(v); }));
    $("picker").innerHTML = '<option value="">Pick an appliance…</option>' + (CFG.appliances || []).map((a) => `<option>${a}</option>`).join("");
    $("ctaSize").onclick = () => { showView("app"); maybeTour(); };
    $("ctaSize2").onclick = () => { showView("app"); maybeTour(); };
    $("ctaHow").onclick = () => { const h = $("howSec"); if (h) h.scrollIntoView({ behavior: "smooth" }); };
    $("backHome").onclick = () => showView("home");
    $("newSizing").onclick = newSizing;
    $("tourBtn").onclick = startTour;
    $("tourOv").onclick = tourEnd;
    applyLang("en");
    await refreshAuth();
    loadTestimonials();
    showView("home");
  }
  boot();
})();
