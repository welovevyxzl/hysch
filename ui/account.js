// Inloggning med e-postkod och Mitt konto. Allt som kostar pengar avgörs av servern: här visas bara svaret.
const LG = { email: "", resolve: null, busy: false, timer: null, pane: "email" };
const AC = { data: null, email: "", busy: false, onboarded: false };
const CODE_LEN = 6;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
function escapeHtml(s) { return s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

function lgBars() {
  const box = $("lgBars");
  if (box.childElementCount) return;
  for (let k = 0; k < 64; k++) {
    const i = document.createElement("i"), x = k / 63, env = 0.3 + 0.7 * Math.sin(Math.PI * x) ** 0.8;
    i.style.cssText = `--h:${(env * (0.4 + Math.random() * 0.6)).toFixed(2)};--d:${(-Math.random() * 3).toFixed(2)}s;--s:${(1.8 + Math.random() * 2).toFixed(2)}s`;
    box.append(i);
  }
}

// ---------- paneler: e-post och kod glider över i varandra, och kortet växer eller krymper mjukt
function lgPane(which) {
  LG.pane = which;
  const panes = { email: $("lgEmailPane"), code: $("lgCodePane") };
  for (const [k, el] of Object.entries(panes)) {
    el.classList.toggle("on", k === which);
    el.classList.toggle("before", k !== which && which === "code");
  }
  const h = panes[which].offsetHeight;
  if (h > 0) $("lgPanes").style.setProperty("--h", h + "px");       // utan mått räcker rutnätet: kortet följer då den högsta panelen
}
addEventListener("resize", () => { if ($("login").classList.contains("on")) lgPane(LG.pane); });

// ---------- de sex rutorna
const lgBoxes = () => [...$("lgCode").children];
const lgValue = () => lgBoxes().map(b => b.value).join("");
function lgSync() { lgBoxes().forEach(b => b.classList.toggle("filled", b.value !== "")); }
function lgBuildBoxes() {
  const wrap = $("lgCode");
  wrap.className = "lg-code";
  wrap.replaceChildren();
  for (let i = 0; i < CODE_LEN; i++) {
    const inp = document.createElement("input");
    inp.type = "text"; inp.inputMode = "numeric"; inp.autocomplete = i === 0 ? "one-time-code" : "off"; inp.spellcheck = false;
    inp.setAttribute("aria-label", `Siffra ${i + 1} av ${CODE_LEN}`);
    inp.style.setProperty("--i", i);
    wrap.append(inp);
  }
}
function lgFill(start, digits) {
  const boxes = lgBoxes();
  let k = 0;
  for (; k < digits.length && start + k < CODE_LEN; k++) {
    const b = boxes[start + k];
    b.value = digits[k];
    b.classList.remove("pop"); void b.offsetWidth; b.classList.add("pop");
  }
  lgSync();
  const next = Math.min(start + k, CODE_LEN - 1);
  boxes[next].focus();
  if (lgValue().length === CODE_LEN) lgVerify();
}
$("lgCode").addEventListener("input", (e) => {
  const boxes = lgBoxes(), i = boxes.indexOf(e.target);
  const digits = e.target.value.replace(/\D/g, "");
  e.target.value = "";
  if (digits) lgFill(i, digits); else lgSync();
});
$("lgCode").addEventListener("keydown", (e) => {
  const boxes = lgBoxes(), i = boxes.indexOf(e.target);
  if (e.key === "Backspace") {
    e.preventDefault();
    if (boxes[i].value) boxes[i].value = "";
    else if (i > 0) { boxes[i - 1].value = ""; boxes[i - 1].focus(); }
    lgSync();
  } else if (e.key === "ArrowLeft" && i > 0) { e.preventDefault(); boxes[i - 1].focus(); }
  else if (e.key === "ArrowRight" && i < CODE_LEN - 1) { e.preventDefault(); boxes[i + 1].focus(); }
  else if (e.key === "Enter") { e.preventDefault(); if (lgValue().length === CODE_LEN) lgVerify(); }
});
$("lgCode").addEventListener("paste", (e) => {
  e.preventDefault();
  const digits = ((e.clipboardData || window.clipboardData).getData("text") || "").replace(/\D/g, "").slice(0, CODE_LEN);
  if (digits) lgFill(0, digits);
});
$("lgCode").addEventListener("focusin", (e) => { if (e.target.select) e.target.select(); });

// ---------- skicka kod
function lgError(msg, id = "lgErr") {
  const e = $(id);
  e.textContent = msg; e.classList.remove("shake"); void e.offsetWidth; e.classList.add("shake");
}
function lgCountdown(n) {
  clearInterval(LG.timer);
  const btn = $("lgResend");
  const paint = () => {
    btn.disabled = n > 0;
    btn.innerHTML = n > 0 ? `Skicka igen om <span id="lgCount">${n}</span> s` : "Skicka en ny kod";
  };
  paint();
  LG.timer = setInterval(() => { n -= 1; paint(); if (n <= 0) clearInterval(LG.timer); }, 1000);
}
async function lgSend(ev) {
  if (ev) ev.preventDefault();
  if (LG.busy) return;
  const email = $("lgEmail").value.trim();
  $("lgErr").textContent = "";
  if (!email) { lgError("Skriv in din e-post."); return; }
  LG.busy = true; $("lgGo").disabled = true;
  const r = await pywebview.api.account_send_code(email);
  LG.busy = false; $("lgGo").disabled = false;
  if (!r.ok) { lgError(r.error || "Något gick fel."); return; }
  LG.email = r.email || email;
  $("lgSentTo").textContent = LG.email;
  $("lgCodeErr").textContent = ""; $("lgCodeOk").textContent = "";
  lgBuildBoxes();
  lgPane("code");
  lgCountdown(60);
  setTimeout(() => lgBoxes()[0].focus(), 750);
}
async function lgResend() {
  if (LG.busy || $("lgResend").disabled) return;
  LG.busy = true; $("lgCodeErr").textContent = ""; $("lgCodeOk").textContent = "";
  const r = await pywebview.api.account_send_code(LG.email);
  LG.busy = false;
  if (!r.ok) { lgError(r.error || "Något gick fel.", "lgCodeErr"); return; }
  lgBuildBoxes(); lgBoxes()[0].focus();
  $("lgCodeOk").textContent = "Ny kod skickad.";
  lgCountdown(60);
}

// ---------- kontrollera koden
async function lgVerify() {
  if (LG.busy) return;
  const code = lgValue();
  if (code.length !== CODE_LEN) return;
  LG.busy = true;
  const box = $("lgCode");
  box.classList.add("busy");
  document.activeElement && document.activeElement.blur();
  $("lgCodeErr").textContent = ""; $("lgCodeOk").textContent = "";
  const t0 = performance.now();
  const r = await pywebview.api.account_verify_code(LG.email, code);
  await sleep(Math.max(0, 650 - (performance.now() - t0)));       // låt vågen hinna synas, så det inte blinkar
  box.classList.remove("busy");
  if (!r.ok) {
    box.classList.add("bad");
    lgError(r.error || "Koden stämmer inte.", "lgCodeErr");
    await sleep(800);
    box.classList.remove("bad");
    lgBoxes().forEach(b => { b.value = ""; });
    lgSync(); lgBoxes()[0].focus();
    LG.busy = false;
    return;
  }
  AC.email = r.email || LG.email;
  AC.onboarded = !!r.onboarded;
  clearInterval(LG.timer);
  await lgSuccess(r.onboarded ? "Välkommen *tillbaka.*" : "Konto *skapat.*", r.onboarded ? "Startar Hysch …" : "Nu gör vi Hysch till ditt.");
  LG.busy = false;
}

// Efter rätt kod: de sex rutorna glider ihop till en punkt, en mörk ring tar deras plats och ritar en bock,
// rubriken byts, och hela inloggningen tonar sedan bort och släpper in appen. Allt lugnt, inget som blinkar.
function lgSwapText(node, html) {
  node.classList.remove("lg-swap"); node.style.opacity = 0;
  setTimeout(() => { node.innerHTML = html.replace(/\*(.+?)\*/g, "<em>$1</em>"); node.style.opacity = ""; node.classList.add("lg-swap"); }, 260);
}
async function lgSuccess(heading, sub) {
  const wrap = $("lgCodeWrap"), pane = $("lgCodePane"), w = wrap.getBoundingClientRect(), cx = w.left + w.width / 2;
  lgBoxes().forEach(b => { const r = b.getBoundingClientRect(); b.style.setProperty("--dx", (cx - (r.left + r.width / 2)) + "px"); });
  $("lgCode").classList.remove("busy");
  pane.classList.add("finishing");
  wrap.classList.add("done");
  lgSwapText(pane.querySelector("h1"), heading);
  lgSwapText(pane.querySelector(".lg-sub"), sub);
  await sleep(2000);
  const login = $("login");
  login.classList.add("leaving");
  await sleep(560);
  const done = LG.resolve; LG.resolve = null;
  if (done) done(); else showView("setup");
  await sleep(120);                         // appen hinner byta vy innan inloggningen släpps, så ingenting blinkar till
  login.classList.remove("leaving");
  pane.classList.remove("finishing"); wrap.classList.remove("done");
}

// ---------- visa inloggningen
function showLogin(msg) {
  lgBars();
  clearInterval(LG.timer);
  LG.busy = false;
  lgBuildBoxes();
  showView("login");
  lgPane("email");                                       // inte beroende av requestAnimationFrame: fönstret kan vara dolt när det här körs
  setTimeout(() => lgPane(LG.pane), 150);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => lgPane(LG.pane));
  if (msg) setTimeout(() => lgError(msg), 100);
  setTimeout(() => $("lgEmail").focus(), 600);
}
function requireLogin(msg) {
  showLogin(msg);
  return new Promise(res => { LG.resolve = res; });
}

$("lgForm").addEventListener("submit", lgSend);
$("lgResend").onclick = lgResend;
$("lgChange").onclick = () => { clearInterval(LG.timer); lgPane("email"); setTimeout(() => $("lgEmail").focus(), 550); };

// ================= Mitt konto =================
const PLAN_NAME = { free: "Gratis", founding: "Grundare", pro: "Pro" };
const fmtDay = (iso) => new Date(iso).toLocaleDateString("sv-SE", { day: "numeric", month: "long" });

function acMeter(label, used, limit, hint) {
  const t = document.createElement("div");
  t.className = "tile ac-meter";
  const unlimited = limit === null || limit === undefined, none = limit === 0;
  const p = unlimited ? 0 : none ? 0 : Math.min(1, used / limit);
  t.innerHTML = `<span class="lbl"></span><div class="val"></div><div class="ac-bar"><i></i></div><div class="hint"></div>`;
  t.querySelector(".lbl").textContent = label;
  t.querySelector(".val").innerHTML = unlimited ? `Obegränsat` : none ? `Ingår inte` : `${used}<small>av ${limit}</small>`;
  const bar = t.querySelector(".ac-bar");
  bar.classList.toggle("full", !unlimited && !none && used >= limit);
  bar.classList.toggle("none", unlimited || none);
  t.querySelector(".hint").textContent = hint || "";
  requestAnimationFrame(() => requestAnimationFrame(() => bar.firstElementChild.style.setProperty("--p", p)));
  return t;
}

function acPlanCard(id, name, price, items, hot) {
  const c = document.createElement("div");
  c.className = "tile ac-plan" + (hot ? " hot" : "");
  c.innerHTML = `<span class="nm"></span><div class="pr"></div><ul></ul><button class="ac-btn" type="button"></button>`;
  c.querySelector(".nm").textContent = name;
  c.querySelector(".pr").innerHTML = `${price}<small>kr i månaden</small>`;
  items.forEach(t => { const li = document.createElement("li"); li.textContent = t; c.querySelector("ul").append(li); });
  const b = c.querySelector(".ac-btn");
  b.textContent = "Välj " + name;
  b.onclick = () => acUpgrade(id, b);
  return c;
}

function acRender(a, notice) {
  const plan = a.plan, paid = plan !== "free";
  $("acMail").textContent = AC.email;
  $("acPlanName").innerHTML = `${PLAN_NAME[plan] || plan}`;
  const chip = $("acChip");
  chip.className = "ac-chip"; chip.hidden = !paid;
  if (a.status === "past_due") { chip.textContent = "Betalning saknas"; chip.classList.add("warn"); }
  else if (paid) { chip.textContent = a.cancel_at_period_end ? "Avslutas" : "Aktiv"; chip.classList.add(a.cancel_at_period_end ? "warn" : "ok"); }
  $("acSub").textContent = paid
    ? (a.current_period_end ? (a.cancel_at_period_end ? `Din plan gäller till ${fmtDay(a.current_period_end)}.` : `Förnyas ${fmtDay(a.current_period_end)}.`) : "")
    : "Du kan prova lokalt läge gratis. Molnläget ingår i Grundare och Pro.";

  const m = $("acMeters"); m.replaceChildren();
  const resets = a.period_start ? "Räknas om " + fmtDay(new Date(new Date(a.period_start).setMonth(new Date(a.period_start).getMonth() + 1))) : "";
  m.append(
    acMeter("Molnsamtal den här månaden", Number(a.cloud_calls_used), a.cloud_calls_per_month, a.cloud_calls_per_month ? resets : ""),
    acMeter("Lokala samtal den här månaden", Number(a.local_calls_used), a.local_calls_per_month, a.local_calls_per_month ? resets : ""));

  const n = $("acNotice");
  n.hidden = !(notice || a.status === "past_due");
  n.className = "ac-notice" + (a.status === "past_due" ? " bad" : "");
  n.textContent = a.status === "past_due"
    ? "Din senaste betalning gick inte igenom. Uppdatera kortet under Hantera prenumeration så att du inte tappar din plan."
    : (notice || "");

  $("acUpgrade").hidden = paid;
  $("acManage").hidden = !paid;
  if (!paid && !$("acPlans").childElementCount) {
    $("acPlans").append(
      acPlanCard("founding", "Grundare", 100, ["10 molnsamtal i månaden", "Obegränsat lokalt läge", "Priset ligger fast för de första 50"], true),
      acPlanCard("pro", "Pro", 200, ["20 molnsamtal i månaden", "Obegränsat lokalt läge", "Hantera och säg upp när du vill"], false));
  }
}

async function openAccount(notice) {
  showView("account");
  $("acErr").textContent = "";
  $("account").classList.add("ac-loading");
  const r = await pywebview.api.account_overview();
  $("account").classList.remove("ac-loading");
  if (!r.ok) {
    if (r.code === "not_logged_in" || r.code === "expired") { await requireLogin(r.error); return openAccount(notice); }
    $("acErr").textContent = r.error || "Kunde inte hämta ditt konto.";
    return;
  }
  AC.data = r.account; AC.email = r.email || AC.email;
  acRender(r.account, notice);
}

async function acUpgrade(plan, btn) {
  if (AC.busy) return;
  AC.busy = true; btn.disabled = true; $("acErr").textContent = "";
  const r = await pywebview.api.account_upgrade(plan);
  AC.busy = false; btn.disabled = false;
  if (!r.ok) { $("acErr").textContent = r.error || "Det gick inte."; return; }
  $("acFine").textContent = "Betalsidan öppnades i din webbläsare. Kom tillbaka hit när du är klar.";
}
async function acPortal() {
  if (AC.busy) return;
  AC.busy = true; $("acManage").disabled = true; $("acErr").textContent = "";
  const r = await pywebview.api.account_portal();
  AC.busy = false; $("acManage").disabled = false;
  if (!r.ok) { $("acErr").textContent = r.error || "Det gick inte."; return; }
  $("acFine").textContent = "Sidan öppnades i din webbläsare. Kom tillbaka hit när du är klar.";
}
async function acLogout() {
  await pywebview.api.account_logout();
  AC.data = null; $("acPlans").replaceChildren();
  $("lgEmail").value = "";
  await requireLogin();
  showView("setup");
}

$("accBtn").onclick = () => openAccount();
$("acBack").onclick = () => showView("setup");
$("acManage").onclick = acPortal;
$("acLogout").onclick = acLogout;
// När man kommer tillbaka från betalsidan i webbläsaren uppdateras planen av sig själv.
addEventListener("focus", () => { if ($("account").classList.contains("on") && !AC.busy) openAccount(); });


// ================= Inställningar =================
// Egen vy, inte uppstartsguiden. Allt sparas av sig självt en kort stund efter varje ändring.
const ST = { p: null, info: null, plan: "free", timer: null, shown: 0 };

function stChips(box, items, isOn, onPick, multi) {
  box.replaceChildren();
  items.forEach(it => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "st-chip" + (isOn(it.id) ? " on" : ""); b.textContent = it.label;
    b.setAttribute("aria-pressed", isOn(it.id));
    b.onclick = () => { onPick(it.id); stChips(box, items, isOn, onPick, multi); stQuality(); stEngineBox(); stSave(); };
    box.append(b);
  });
}
function stQuality() {
  $("stQualityHint").textContent = (QUALITY[ST.p.quality] || {}).desc || "";
}
function stEngineBox() {
  const box = $("stEngineBox"), p = ST.p, info = ST.info;
  box.replaceChildren();
  const line = (txt, ok) => { const d = document.createElement("div"); d.className = "st-line " + (ok ? "ok" : "no"); d.innerHTML = `<i></i><span></span>`; d.lastChild.textContent = txt; box.append(d); };
  const note = (txt, label, fn) => {
    const n = document.createElement("div"); n.className = "st-note"; const t = document.createElement("span"); t.textContent = txt; n.append(t);
    if (label) { const b = document.createElement("button"); b.type = "button"; b.className = "pillbtn dark"; b.textContent = label; b.onclick = fn; n.append(b); }
    box.append(n);
  };
  if (p.engine === "cloud") {
    if (ST.plan === "free") note("Molnet ingår i Grundare och Pro. På Gratis kör du lokalt.", "Se planer", () => openAccount());
    else line("Molnet är på: bäst kvalitet och snabbast svar.", true);
    return;
  }
  const stt = (info.stt || []).find(x => x.id === p.stt) || (info.stt || [])[0];
  const names = new Set(((info.ollama || {}).models || []).map(m => m.name));
  const llmOk = p.llm && (names.has(p.llm) || names.has(p.llm + ":latest"));
  line(`Transkribering: ${stt ? stt.name : "Whisper"}${stt && stt.installed ? "" : " (saknas)"}`, !!(stt && stt.installed));
  line(`Text och analys: ${p.llm || "ingen vald"}${llmOk ? "" : " (saknas)"}`, !!llmOk);
  if (!(stt && stt.installed) || !llmOk) note("Modellerna behöver hämtas en gång.", "Ladda ner modeller", () => startOnboarding({ rerun: true, step: "models" }));
  else note("Allt ligger redan på din dator.", "Byt modeller", () => startOnboarding({ rerun: true, step: "models" }));
}
function stFlash(txt) {
  const e = $("stSaved"); e.textContent = txt; e.classList.add("on");
  clearTimeout(ST.shown); ST.shown = setTimeout(() => e.classList.remove("on"), 1600);
}
function stSave() {
  clearTimeout(ST.timer);
  ST.timer = setTimeout(async () => {
    const r = await pywebview.api.setup_save(ST.p, "");
    if (r) stFlash("Sparat");
    if (typeof obRefreshForm === "function") obRefreshForm();           // startformuläret följer med
  }, 450);
}

async function openSettings() {
  showView("settings");
  const [info, ov] = await Promise.all([pywebview.api.setup_info(), pywebview.api.account_overview()]);
  ST.info = info;
  ST.plan = ov.ok ? ov.account.plan : "free";
  const p = info.profile || {}, me = ((window.__init && window.__init.form && window.__init.form.me) || "").split(",");
  ST.p = { name: p.name || me[0] || "", company: p.company || me.slice(1).join(",").trim(), goals: p.goals || [],
           quality: p.quality || "balanced", engine: p.engine || "cloud", stt: p.stt || "", llm: p.llm || "" };
  $("stName").value = ST.p.name; $("stCompany").value = ST.p.company;
  stChips($("stGoals"), GOALS.map(g => ({ id: g.id, label: g.t })), id => ST.p.goals.includes(id),
    id => { ST.p.goals = ST.p.goals.includes(id) ? ST.p.goals.filter(v => v !== id) : [...ST.p.goals, id]; }, true);
  stChips($("stQuality"), Object.entries(QUALITY).map(([id, q]) => ({ id, label: q.label })), id => ST.p.quality === id, id => { ST.p.quality = id; });
  stChips($("stEngine"), [{ id: "cloud", label: "Hyschs moln" }, { id: "local", label: "På din dator" }], id => ST.p.engine === id, id => { ST.p.engine = id; });
  stQuality(); stEngineBox();
  AC.email = (ov.ok && ov.email) || AC.email;
  $("stMail").textContent = AC.email;
  $("stAcctSub").textContent = "Plan: " + (PLAN_NAME[ST.plan] || ST.plan);
}
$("stName").oninput = () => { ST.p.name = $("stName").value; stSave(); };
$("stCompany").oninput = () => { ST.p.company = $("stCompany").value; stSave(); };
$("stBack").onclick = () => showView("setup");
$("stPlans").onclick = () => openAccount();
$("stLogout").onclick = () => acLogout();
