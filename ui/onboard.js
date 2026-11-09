"use strict";
// Uppstartsguiden. Laddas efter huvudskriptet i index.html och använder dess hjälpfunktioner ($, el, showView).

const OB_ICONS = {
  sell: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
  meetings: '<rect x="3" y="4.5" width="18" height="16" rx="3"/><path d="M3 9.5h18M8 3v3M16 3v3"/><path d="M8.5 14.5l2.5 2.5 4.5-4.5"/>',
  objections: '<path d="M12 21s8-3.5 8-10V5.5L12 3 4 5.5V11c0 6.5 8 10 8 10Z"/>',
  learn: '<path d="M12 3l1.8 4.7 4.7 1.8-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8L12 3Z"/><path d="M19 16l.8 2.2 2.2.8-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z"/>',
  cloud: '<path d="M7 18a4.5 4.5 0 1 1 .9-8.9A6 6 0 0 1 19.5 11 3.5 3.5 0 0 1 19 18H7Z"/>',
  chip: '<rect x="6" y="6" width="12" height="12" rx="2.5"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  mic: '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
};
function obSvg(name, size = 18, sw = 2) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round">${OB_ICONS[name]}</svg>`;
}

const OB = {
  on: false, rerun: false, nav: false, step: null, cur: null, hist: [], info: null, poll: null,
  d: { name: "", company: "", goals: [], quality: "balanced", engine: "", key: "", keyOk: false, keyChecked: null, skipKey: false, stt: "", llm: "" },
  ollama: { installed: false, running: false, models: [], starting: false },
  dl: {}, dlSpeed: { t: 0, bytes: 0, bps: 0 }, test: { state: "idle" },
};
const obSleep = (ms) => new Promise(r => setTimeout(r, ms));
const obGB = (mb) => mb >= 1024 ? (mb / 1024).toFixed(1).replace(".", ",") + " GB" : Math.round(mb) + " MB";
const QUALITY = {
  fast: { label: "Snabb", fart: 3, prec: 1, desc: "Svarar nästan direkt. Räcker långt när samtalet är tydligt och ljudet bra." },
  balanced: { label: "Balanserad", fart: 2, prec: 2, desc: "Rätt balans mellan fart och precision. Passar de flesta samtal." },
  best: { label: "Bäst", fart: 1, prec: 3, desc: "Hör även svåra ord, namn och otydligt tal. Kan ta lite längre tid." },
};
const GOALS = [
  { id: "sell", icon: "sell", ic: "", t: "Sälja mer", d: "Hitta rätt ord för att stänga affären." },
  { id: "meetings", icon: "meetings", ic: "m", t: "Boka fler möten", d: "Driv varje samtal mot ett tydligt nästa steg." },
  { id: "objections", icon: "objections", ic: "c", t: "Hantera invändningar", d: "Svara lugnt och bra när det blir tufft." },
  { id: "learn", icon: "learn", ic: "a", t: "Lära mig av varje samtal", d: "Få feedback och en plan efter samtalet." },
];

// ---------- byggstenar
function obHeading(markup, cls = "") {
  const h = el("h1", "ob-h " + cls);
  let i = 0;
  markup.split("*").forEach((seg, k) => {
    seg.split(/(\s+)/).forEach(tok => {
      if (!tok) return;
      if (/^\s+$/.test(tok)) { h.append(" "); return; }
      const w = el("span", "w");
      w.style.setProperty("--i", i++);
      if (k % 2 === 1) w.append(el("em", "", tok)); else w.textContent = tok;
      h.append(w);
    });
  });
  return h;
}
function obStep(cls = "") { return el("div", "ob-step " + cls); }
function obCard({ cls = "", icon, icCls = "", title, desc, on, i = 0, onClick, badge, extra }) {
  const c = el("button", "ob-card " + cls + (on ? " on" : ""));
  c.type = "button";
  c.style.setProperty("--i", i);
  if (icon) { const ic = el("span", "ic " + icCls); ic.innerHTML = obSvg(icon, cls.includes("big") ? 24 : 20); c.append(ic); }
  const t = el("div", "t", title);
  if (badge) t.append(el("span", "badge " + (badge.cls || ""), badge.text));
  c.append(t);
  if (desc) c.append(el("div", "d", desc));
  if (extra) c.append(extra);
  const tick = el("span", "tick"); tick.innerHTML = obSvg("check", 12, 3.4); c.append(tick);
  c.onclick = onClick;
  return c;
}
function obMeter(label, n) {
  const m = el("div", "meter");
  const p = el("span", "pips");
  for (let k = 0; k < 3; k++) p.append(el("i", k < n ? "fill" : ""));
  m.append(label, p);
  return m;
}

// ---------- flöde
function obFlow() {
  const f = ["welcome", "name", "goals", "quality", "engine"];
  if (OB.d.engine === "local") f.push("models", "download");
  else if (OB.d.engine === "cloud") f.push("key");
  f.push("test", "done");
  return f;
}
function obNeeds() {          // vad som måste laddas ner (lokalt läge)
  const info = OB.info || { stt: [], llm: [] };
  const s = info.stt.find(p => p.id === OB.d.stt);
  const sttMB = s && !s.installed ? s.mb : 0;
  const have = OB.ollama.models.some(m => m.name === OB.d.llm || m.name === OB.d.llm + ":latest");
  const p = info.llm.find(x => x.tag === OB.d.llm);
  const llmMB = !have && OB.d.llm ? (p ? p.mb : 2000) : 0;
  return { sttMB, llmMB, total: sttMB + llmMB, haveLlm: have };
}
function obNextId() {
  const f = obFlow(), i = f.indexOf(OB.step);
  let n = f[i + 1];
  if (OB.step === "models" && obNeeds().total === 0) n = "test";     // inget att ladda ner
  if (n === "test" && OB.d.engine === "cloud" && !OB.d.keyOk) n = "done";   // inget att testa med utan nyckel
  return n;
}

// ---------- steg
const OB_STEPS = {
  welcome() {
    const s = obStep("ob-center");
    const m = el("div", "ob-mark");
    m.innerHTML = '<div class="bigmark"><svg viewBox="0 0 100 100" width="100%" height="100%" aria-hidden="true"><rect width="100" height="100" rx="24" fill="#16181D"/><g fill="none" stroke="#fff" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"><path d="M30 22V76"/><path d="M30 54C30 44 36 38 45 38C55 38 60 44 60 54V76"/></g><circle cx="77" cy="75" r="6.5" fill="#FF5A36"/></svg></div>';
    s.append(m, obHeading("Välkommen till *Hysch.*", "big"),
      el("p", "ob-sub", "Din säljcoach i örat. Fyra snabba frågor, så att allt passar just dig."));
    return { el: s, next: () => ({ label: "Kom igång", hint: "Det tar ungefär en minut." }) };
  },

  name() {
    const s = obStep();
    s.append(el("div", "ob-kicker", "Fråga 1 av 4"), obHeading("Vad *heter* du?"),
      el("p", "ob-sub", "Så att coachen kan presentera dig när motparten undrar vem som ringer."));
    const f1 = el("div", "ob-field"), n = el("input", "ob-input xl");
    n.placeholder = "Ditt namn"; n.value = OB.d.name; n.autocomplete = "off"; n.spellcheck = false;
    f1.append(n);
    const f2 = el("div", "ob-field"), c = el("input", "ob-input md");
    c.placeholder = "Företag (valfritt)"; c.value = OB.d.company; c.autocomplete = "off"; c.spellcheck = false;
    f2.append(c);
    const greet = el("div", "ob-greet");
    const after = el("div", "ob-after"); after.append(f1, greet, f2);
    f2.style.marginTop = "0";
    s.append(after);
    const ok = () => OB.d.name.trim().length >= 2;
    let lastFirst = "";
    const paintGreet = () => {          // en hälsning som följer med medan du skriver
      const first = OB.d.name.trim().split(/\s+/)[0] || "";
      if (first === lastFirst) return;
      lastFirst = first;
      greet.replaceChildren();
      if (first.length >= 2) { const sp = el("span"); sp.append("Trevligt att träffas, ", el("b", "", first), "."); greet.append(sp); }
    };
    n.oninput = () => { OB.d.name = n.value; paintGreet(); obUpdateNext(); };
    setTimeout(paintGreet, 50);
    c.oninput = () => { OB.d.company = c.value; };
    n.onkeydown = (e) => { if (e.key === "Enter") { e.stopPropagation(); ok() ? c.focus() : null; } };
    c.onkeydown = (e) => { if (e.key === "Enter") { e.stopPropagation(); if (ok()) obNext(); } };
    return { el: s, onEnter: () => setTimeout(() => { n.focus(); n.setSelectionRange(n.value.length, n.value.length); }, 900), next: () => ({ disabled: !ok() }) };
  },

  goals() {
    const s = obStep();
    s.append(el("div", "ob-kicker", "Fråga 2 av 4"), obHeading("Vad vill du få ut av *Hysch?*"),
      el("p", "ob-sub", "Välj ett eller flera. Coachen riktar sina förslag efter det."));
    const g = el("div", "ob-grid c2");
    GOALS.forEach((x, i) => {
      const on = () => OB.d.goals.includes(x.id);
      const c = obCard({ icon: x.icon, icCls: x.ic, title: x.t, desc: x.d, on: on(), i,
        onClick: () => { OB.d.goals = on() ? OB.d.goals.filter(v => v !== x.id) : [...OB.d.goals, x.id]; c.classList.toggle("on", on()); obUpdateNext(); } });
      g.append(c);
    });
    s.append(g);
    return { el: s, next: () => ({ disabled: !OB.d.goals.length }) };
  },

  quality() {
    const s = obStep();
    s.append(el("div", "ob-kicker", "Fråga 3 av 4"), obHeading("Hur *bra* ska det vara?"),
      el("p", "ob-sub", "Högre kvalitet tar lite längre tid. Du kan ändra det när som helst."));
    // Appens egen segmentkontroll: en vit markör glider mellan valen, och förklaringen byts under.
    const seg = el("div", "ob-seg"), detail = el("div", "ob-detail");
    seg.append(el("span", "thumb"));
    const paint = (swap) => {
      seg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.q === OB.d.quality));
      moveThumb(seg);
      const q = QUALITY[OB.d.quality];
      detail.replaceChildren();
      const txt = el("div", "txt" + (swap ? " swap" : ""));
      if (OB.d.quality === "balanced") txt.append(el("span", "badge", "Rekommenderas"));
      txt.append(q.desc);
      const meters = el("div", "meters");
      meters.append(obMeter("Fart", q.fart), obMeter("Precision", q.prec));
      detail.append(txt, meters);
    };
    Object.entries(QUALITY).forEach(([id, q]) => {
      const b = el("button", "", q.label);
      b.type = "button"; b.dataset.q = id;
      b.onclick = () => { OB.d.quality = id; paint(true); obUpdateNext(); };
      seg.append(b);
    });
    s.append(seg, detail);
    return { el: s, onEnter: () => { paint(false); setTimeout(() => moveThumb(seg), 60); }, next: () => ({}) };
  },

  engine() {
    const s = obStep();
    const sys = (OB.info && OB.info.system) || { ram_gb: 0, cores: 0, os: "windows" };
    const ram = sys.ram_gb;
    s.append(el("div", "ob-kicker", "Fråga 4 av 4"), obHeading("Var ska Hysch *tänka?*"),
      el("p", "ob-sub", "Du kan använda OpenAI:s moln, eller köra allt privat på din egen dator."));
    const need = (() => {
      const st = (OB.info ? OB.info.stt : []).find(p => p.tier === OB.d.quality), ll = (OB.info ? OB.info.llm : []).find(p => p.tier === OB.d.quality);
      return st && ll ? obGB(st.mb + ll.mb) : "";
    })();
    const localBadge = ram >= 16 ? { text: "Passar din dator", cls: "good" } : ram >= 8 ? { text: "Går, välj gärna Snabb", cls: "warn" } : { text: "Lite minne", cls: "bad" };
    const mk = (id, icon, title, items, badge, i) => {
      const ul = el("ul"); items.forEach(t => ul.append(el("li", "", t)));
      return obCard({ cls: "ob-big", icon, icCls: id === "local" ? "m" : "", title, on: OB.d.engine === id, i, badge, extra: ul,
        onClick: () => { OB.d.engine = id; g.querySelectorAll(".ob-card").forEach((n, k) => n.classList.toggle("on", ["cloud", "local"][k] === id)); obUpdateNext(); } });
    };
    const g = el("div", "ob-grid c2");
    g.append(
      mk("cloud", "cloud", "OpenAI-molnet", ["Bäst kvalitet och snabbast svar", "Kräver en egen OpenAI-nyckel (du betalar per användning)", "Ljud och text skickas till OpenAI"], { text: "Enklast", cls: "" }, 0),
      mk("local", "chip", "På din dator", ["Privat: inget lämnar datorn", `Gratis efter nedladdningen${need ? " (ca " + need + ")" : ""}`, "Kräver mycket minne och kan vara långsammare"], localBadge, 1));
    s.append(g);
    const chip = el("div", "ob-chip");
    chip.innerHTML = `<span>Din dator: <b>${sys.os === "mac" ? "Mac" : sys.os === "windows" ? "Windows" : "Linux"}</b> · <b>${ram ? ram.toFixed(0) : "?"} GB</b> minne · <b>${sys.cores}</b> kärnor${sys.cuda ? " · NVIDIA-grafikkort som fungerar" : ""}</span>`;
    s.append(chip);
    return { el: s, next: () => ({ disabled: !OB.d.engine }) };
  },

  key() {
    const s = obStep();
    s.append(el("div", "ob-kicker", "Molnet"), obHeading("Din *OpenAI-nyckel.*"),
      el("p", "ob-sub", "Den sparas bara på din dator och används för att prata med OpenAI. Du betalar OpenAI för det du använder."));
    const row = el("div", "ob-keyrow ob-after"), f = el("div", "ob-field"), inp = el("input", "ob-input md");
    inp.type = "password"; inp.placeholder = (OB.info && OB.info.hasKey && !OB.d.key) ? "Du har redan en nyckel sparad" : "sk-..."; inp.autocomplete = "off"; inp.spellcheck = false;
    inp.value = OB.d.key;
    f.append(inp);
    const btn = el("button", "ob-btn pri", "Kontrollera");
    btn.type = "button";
    row.append(f, btn);
    const status = el("div", "ob-status");
    const links = el("div"); links.style.cssText = "margin-top:6px;display:flex;gap:6px;flex-wrap:wrap";
    const how = el("button", "ob-link", "Hur får jag en nyckel?"); how.type = "button"; how.onclick = () => pywebview.api.setup_open("openai_keys");
    const skip = el("button", "ob-link", "Jag lägger in den senare"); skip.type = "button";
    skip.onclick = () => { OB.d.skipKey = true; OB.d.keyOk = false; obNext(); };
    links.append(how, skip);
    s.append(row, status, links);

    const paint = (r) => {
      status.replaceChildren();
      const line = (cls, txt, spin) => { const d = el("div", "ob-st " + cls); const dot = el("span", spin ? "spin" : "dot"); if (!spin) dot.innerHTML = obSvg(cls === "ok" ? "check" : "x", 12, 3.4); d.append(dot, txt); status.append(d); return d; };
      if (!r) return;
      if (r.pending) { line("", "Kontrollerar …", true); return; }
      if (!r.ok) { line("no", r.error || "Det gick inte."); return; }
      line("ok", "Nyckeln fungerar");
      const m = r.models || {};
      line(m.transcribe ? "ok" : "no", "Transkribering");
      line(m.coach ? "ok" : "no", "Förslag under samtalet");
      line(m.summary ? "ok" : "no", "Analys efter samtalet");
      if (!m.transcribe || !m.coach || !m.summary) {
        const n = el("div", "ob-note warn", "Kontot saknar någon av modellerna. Du kan gå vidare, men den delen kan misslyckas.");
        status.append(n);
      }
    };
    const check = async () => {
      const val = inp.value.trim();
      if (!val && !(OB.info && OB.info.hasKey)) { paint({ ok: false, error: "Klistra in din nyckel först." }); return; }
      OB.d.key = val; btn.disabled = true; paint({ pending: true });
      const r = await pywebview.api.setup_check_key(val);
      btn.disabled = false; paint(r);
      OB.d.keyOk = !!r.ok; OB.d.skipKey = false; OB.d.keyChecked = r;
      obUpdateNext();
    };
    btn.onclick = check;
    inp.onkeydown = (e) => { if (e.key === "Enter") { e.stopPropagation(); check(); } };
    inp.oninput = () => { OB.d.key = inp.value; if (OB.d.keyOk && inp.value.trim()) { OB.d.keyOk = false; paint(null); obUpdateNext(); } };
    return {
      el: s,
      onEnter: () => {
        if (OB.d.keyChecked && OB.d.keyChecked.ok) paint(OB.d.keyChecked);
        else if (OB.info && OB.info.hasKey && !OB.d.key) check();      // kontrollera den sparade nyckeln
        else setTimeout(() => inp.focus(), 900);
      },
      next: () => ({ disabled: !OB.d.keyOk }),
    };
  },

  models() {
    const s = obStep("tight");
    const info = OB.info, ram = info.system.ram_gb;
    s.append(el("div", "ob-kicker", "Lokal motor"), obHeading("Välj dina *modeller.*"));
    const wrap = el("div", "ob-scroll"); wrap.style.marginTop = "4px";
    const secA = el("div", "ob-sec"), secB = el("div", "ob-sec");
    secA.append(el("div", "ob-label", "Lyssnar · Whisper, tränad på svenska"));
    secB.append(el("div", "ob-label", "Tänker och föreslår · via Ollama"));
    const rowsA = el("div", "ob-rows"), boxB = el("div");
    secA.append(rowsA); secB.append(boxB);
    wrap.append(secA, secB);
    s.append(wrap);

    const cuda = !!info.system.cuda;
    // Uppmätt på en 12-kärnig processor: Small ca 2 s för 9 s tal. Medium är ungefär 3 gånger och Large ungefär 6 gånger långsammare.
    const fit = (p) => ram && p.min_ram > ram ? { text: "Tungt för din dator", cls: "warn" }
      : !cuda && p.tier === "best" ? { text: "Långsam utan NVIDIA-kort", cls: "warn" }
      : !cuda && p.tier === "balanced" ? { text: "Märkbart långsammare", cls: "warn" }
      : p.tier === OB.d.quality || (!cuda && p.tier === "fast") ? { text: !cuda && p.tier === "fast" ? "Snabbast, rekommenderas" : "Passar ditt val", cls: "" } : null;
    const paintA = () => {
      rowsA.replaceChildren();
      info.stt.forEach((p, i) => {
        const c = obCard({ cls: "ob-row sm", title: p.name, desc: p.installed ? "Redan nedladdad" : `Hämtas en gång, ${obGB(p.mb)}`, on: OB.d.stt === p.id, i, badge: fit(p),
          onClick: () => { OB.d.stt = p.id; paintA(); obUpdateNext(); } });
        const body = el("div", "grow");
        [...c.childNodes].filter(n => !n.classList || !n.classList.contains("tick")).forEach(n => body.append(n));
        c.insertBefore(body, c.firstChild);
        c.insertBefore(el("span", "sz", obGB(p.mb)), c.querySelector(".tick"));
        rowsA.append(c);
      });
    };
    const paintB = () => {
      boxB.replaceChildren();
      const o = OB.ollama;
      if (!o.installed) {
        const n = el("div", "ob-note");
        const g = el("div", "grow"); g.innerHTML = "<b>Ollama behövs.</b> Det är ett gratis program som kör textmodellen på din dator. Installera det och kom tillbaka hit, så hittar Hysch det av sig själv.";
        const get = el("button", "ob-btn pri", "Hämta Ollama"); get.type = "button"; get.onclick = () => pywebview.api.setup_open("ollama");
        const re = el("button", "ob-btn", "Jag har installerat det"); re.type = "button"; re.onclick = () => obRefreshOllama(true);
        n.append(g, get, re); boxB.append(n); return;
      }
      if (!o.running) {
        const n = el("div", "ob-note"); const sp = el("span", "ob-st"); sp.innerHTML = '<span class="spin"></span>';
        const g = el("div", "grow", o.starting ? "Startar Ollama …" : "Ollama är installerat men inte igång."); n.append(sp, g);
        if (!o.starting) { const b = el("button", "ob-btn pri", "Starta Ollama"); b.type = "button"; b.onclick = () => obRefreshOllama(true); n.append(b); }
        boxB.append(n); return;
      }
      const rows = el("div", "ob-rows"); boxB.append(rows);
      const names = new Set(o.models.map(m => m.name));
      info.llm.forEach((p, i) => {
        const has = names.has(p.tag) || names.has(p.tag + ":latest");
        const c = obCard({ cls: "ob-row sm", title: p.name, desc: has ? "Redan på din dator" : `Hämtas en gång, ${obGB(p.mb)}`, on: OB.d.llm === p.tag, i, badge: ram && p.min_ram > ram ? { text: "Tungt för din dator", cls: "warn" } : p.tier === OB.d.quality ? { text: "Passar ditt val", cls: "" } : null,
          onClick: () => { OB.d.llm = p.tag; paintB(); obUpdateNext(); } });
        const body = el("div", "grow");
        [...c.childNodes].filter(n => !n.classList || !n.classList.contains("tick")).forEach(n => body.append(n));
        c.insertBefore(body, c.firstChild);
        c.insertBefore(el("span", "sz", obGB(p.mb)), c.querySelector(".tick"));
        rows.append(c);
      });
      const presetTags = new Set(info.llm.map(p => p.tag));
      const own = o.models.filter(m => !presetTags.has(m.name));
      if (own.length) {
        const sel = el("select", "ob-select"); sel.style.marginTop = "10px";
        const first = el("option", "", `Eller välj en av dina ${own.length} installerade modeller …`); first.value = ""; sel.append(first);
        own.forEach(m => { const op = el("option", "", `${m.name} · ${obGB(m.mb)}`); op.value = m.name; sel.append(op); });
        if (own.some(m => m.name === OB.d.llm)) sel.value = OB.d.llm;
        sel.onchange = () => { if (sel.value) { OB.d.llm = sel.value; rows.querySelectorAll(".ob-card").forEach(n => n.classList.remove("on")); obUpdateNext(); } };
        boxB.append(sel);
      }
    };
    const defaults = () => {
      // Utan ett fungerande NVIDIA-kort är den lilla modellen den enda som hinner med i realtid.
      const st = (info.system.cuda ? info.stt.find(p => p.tier === OB.d.quality) : null) || (info.system.cuda ? info.stt[1] : info.stt[0]);
      if (!OB.d.stt || !info.stt.some(p => p.id === OB.d.stt)) OB.d.stt = st.id;
      if (!OB.d.llm) OB.d.llm = (info.llm.find(p => p.tier === OB.d.quality) || info.llm[1]).tag;
    };
    OB.repaintModels = () => { paintA(); paintB(); obUpdateNext(); };
    return {
      el: s,
      onEnter: () => {
        defaults(); paintA(); paintB();
        obRefreshOllama(false);
        OB.poll = setInterval(() => { if (!OB.ollama.running) obRefreshOllama(false); }, 3000);
      },
      onLeave: () => { clearInterval(OB.poll); OB.repaintModels = null; },
      next: () => {
        const n = obNeeds(), free = info.system.free_gb;
        if (!OB.ollama.running) return { disabled: true, hint: "Starta Ollama för att gå vidare." };
        if (n.total && free && n.total / 1024 * 1.15 > free) return { disabled: true, err: true, hint: `Det är för lite diskutrymme (${free.toFixed(0)} GB ledigt).` };
        return { label: n.total ? `Ladda ner ${obGB(n.total)}` : "Fortsätt", hint: n.total ? `Ledigt på disken: ${free ? free.toFixed(0) + " GB" : "okänt"}` : "Allt är redan på din dator." };
      },
    };
  },

  download() {
    const s = obStep("tight");
    s.append(el("div", "ob-kicker", "Lokal motor"), obHeading("Hämtar *hjärnan.*"));
    const wrap = el("div", "ob-dl");
    const big = el("div", "ob-bignum");
    big.innerHTML = '<span id="obPct">0</span><small>%</small>';
    const eta = el("div", "ob-eta"); eta.id = "obEta"; eta.textContent = "Förbereder …";
    const parts = el("div", "ob-parts");
    const mkPart = (id, title) => {
      const p = el("div", "ob-part"); p.id = "obp-" + id;
      p.innerHTML = `<div class="top">${title}<span data-k="amt"></span></div><div class="bar"><i></i></div><div class="st" data-k="st">Väntar …</div>`;
      return p;
    };
    const info = OB.info, st = info.stt.find(p => p.id === OB.d.stt);
    parts.append(mkPart("stt", `Transkribering · ${st ? st.name : "Whisper"}`), mkPart("llm", `Text och analys · ${OB.d.llm}`));
    wrap.append(big, eta, parts);
    const tip = el("div", "ob-tip", "");
    const err = el("div"); err.id = "obDlErr";
    s.append(wrap, tip, err);

    const tips = ["Modellerna ligger kvar på din dator och används helt privat.", "Går något fel återupptas nedladdningen där den slutade.",
      "Whisper är tränad på tusentals timmar svenskt tal.", "Ingen lyssnar på dina samtal. Allt körs lokalt."];
    let ti = 0;
    const tipTimer = setInterval(() => { tip.style.opacity = 0; setTimeout(() => { tip.textContent = tips[++ti % tips.length]; tip.style.opacity = 1; }, 400); }, 5200);
    tip.textContent = tips[0];

    return {
      el: s,
      onEnter: () => {
        document.body.classList.add("ob-busy");
        const n = obNeeds();
        OB.dl = { stt: { state: "wait", done: 0, total: (st ? st.mb : 0) * 1048576 }, llm: { state: n.haveLlm ? "ready" : "wait", done: 0, total: (n.llmMB || 0) * 1048576 } };
        if (st && st.installed) OB.dl.stt.state = "ready";
        OB.dlSpeed = { t: performance.now(), bytes: 0, bps: 0 };
        obPaintDl();
        $("obDlErr").replaceChildren();
        pywebview.api.setup_download(OB.d.stt, OB.d.llm);
      },
      onLeave: () => { clearInterval(tipTimer); document.body.classList.remove("ob-busy"); },
      next: () => ({ hidden: true }),
    };
  },

  test() {
    const s = obStep("ob-center");
    s.append(el("div", "ob-kicker", "Sista kollen"), obHeading("Säg *något.*"),
      el("p", "ob-sub", "Läs en mening högt, så testar vi att Hysch hör dig och skriver rätt."));
    const t = el("div", "ob-test ob-after");
    const orb = el("button", "ob-orb"); orb.type = "button"; orb.id = "obOrb";
    const lv = el("div", "lv"); for (let k = 0; k < 7; k++) lv.append(el("b")); lv.style.display = "none";
    orb.innerHTML = obSvg("mic", 54, 1.8);
    orb.append(lv, el("span", "cd"));
    const txt = el("div", "ob-testtxt"); txt.id = "obTestTxt";
    const again = el("button", "ob-link", "Prova igen"); again.type = "button"; again.hidden = true; again.id = "obAgain";
    t.append(orb, txt, again);
    s.append(t);
    orb.onclick = obRunTest; again.onclick = obRunTest;
    OB.test = OB.test.state === "done" ? OB.test : { state: "idle" };
    return {
      el: s,
      onEnter: () => obPaintTest(),
      next: () => ({ label: OB.test.state === "done" && OB.test.text ? "Fortsätt" : "Hoppa över", hint: OB.test.state === "done" && OB.test.text ? "" : "Du kan alltid testa senare." }),
    };
  },

  done() {
    const s = obStep("ob-center");
    const nm = OB.d.name.trim().split(/\s+/)[0] || "";
    s.append(obHeading(`Allt klart${nm ? ", *" + nm + ".*" : ".*"}`, "big"),
      el("p", "ob-sub", "Hysch är redo för ditt första samtal. Du hittar inställningarna uppe till höger på startsidan."));
    const sum = el("div", "ob-summary");
    const chips = [];
    chips.push(OB.d.engine === "local" ? "Lokalt på din dator" : "OpenAI-molnet");
    chips.push(QUALITY[OB.d.quality].label);
    if (OB.d.engine === "local") { const st = (OB.info.stt || []).find(p => p.id === OB.d.stt); if (st) chips.push(st.name); chips.push(OB.d.llm); }
    chips.forEach(c => { const d = el("div", "ob-chip"); d.textContent = c; sum.append(d); });
    s.append(sum);
    return { el: s, next: () => ({ label: OB.rerun ? "Spara" : "Starta Hysch" }) };
  },
};

// ---------- navigering
function obUpdateNext() {
  const btn = $("obNext"), cur = OB.cur;
  if (!btn || !cur) return;
  const st = (cur.next && cur.next()) || {};
  btn.hidden = !!st.hidden;
  btn.disabled = !!st.disabled;
  $("obNextTxt").textContent = st.label || "Fortsätt";
  btn.classList.toggle("ready", !st.disabled);
  const h = $("obHint");
  h.textContent = st.hint || "";
  h.classList.toggle("err", !!st.err);
  obEq();
  $("obBack").hidden = OB.step === "welcome";
}
// Loggans fem staplar är framstegsmätaren: de fylls i violett steg för steg och studsar vid varje scenbyte.
function obEq() {
  const eq = $("obEq"), f = obFlow(), p = Math.max(0, f.indexOf(OB.step)) / Math.max(1, f.length - 1);
  const filled = OB.step === "welcome" ? 0 : Math.max(1, Math.ceil(p * 5));
  [...eq.children].forEach((b, k) => b.classList.toggle("on", k < filled));
  if (eq.dataset.step !== OB.step) { eq.dataset.step = OB.step; eq.classList.remove("bump"); void eq.offsetWidth; eq.classList.add("bump"); }
}
async function obGo(id, back = false) {
  if (OB.nav) return;
  OB.nav = true;
  try {
    const prev = OB.cur;
    if (prev) {
      if (prev.onLeave) prev.onLeave();
      prev.el.classList.add("leave");
      await obSleep(300);
      prev.el.remove();
    }
    if (!back && OB.step) OB.hist.push(OB.step);
    OB.step = id;
    document.body.dataset.scene = id;     // auran flyttar sig till den här scenens ljussättning
    const s = OB_STEPS[id]();
    OB.cur = s;
    s.el.classList.add("enter");
    $("obBody").append(s.el);
    obUpdateNext();
    if (s.onEnter) s.onEnter();
  } finally { OB.nav = false; }
}
function obNext() {
  const st = (OB.cur && OB.cur.next && OB.cur.next()) || {};
  if (st.disabled || OB.nav) return;
  if (OB.step === "done") { obFinish(); return; }
  obGo(obNextId());
}
function obBack() {
  if (OB.nav) return;
  if (OB.step === "download") pywebview.api.setup_cancel();
  const prev = OB.hist.pop();
  if (prev) obGo(prev, true);
}

async function obRefreshOllama(start) {
  try {
    if (start && OB.ollama.installed) { OB.ollama.starting = true; OB.repaintModels && OB.repaintModels(); OB.ollama = Object.assign(await pywebview.api.setup_ollama_start(), { starting: false }); }
    else OB.ollama = Object.assign(await pywebview.api.setup_ollama(), { starting: false });
    if (OB.ollama.installed && !OB.ollama.running && !OB.ollama.starting && !OB._autostarted) {
      OB._autostarted = true;
      OB.ollama.starting = true; OB.repaintModels && OB.repaintModels();
      OB.ollama = Object.assign(await pywebview.api.setup_ollama_start(), { starting: false });
    }
  } catch (e) { /* ignorera: nästa avläsning försöker igen */ }
  if (OB.repaintModels) OB.repaintModels();
}

// ---------- nedladdning (händelser från Python)
function obPaintDl() {
  let done = 0, total = 0;
  ["stt", "llm"].forEach(k => {
    const p = OB.dl[k], box = $("obp-" + k);
    if (!p || !box) return;
    const pct = p.state === "ready" ? 100 : p.total ? Math.min(100, p.done / p.total * 100) : 0;
    box.classList.toggle("ready", p.state === "ready");
    box.querySelector(".bar i").style.width = pct + "%";
    box.querySelector('[data-k="amt"]').textContent = p.state === "ready" ? "Klar" : p.total ? `${obGB(p.done / 1048576)} av ${obGB(p.total / 1048576)}` : "";
    box.querySelector('[data-k="st"]').textContent = { wait: "Väntar …", starting: "Startar …", running: p.status && !/pulling|manifest/i.test(p.status) ? p.status : "Hämtar …", ready: p.state === "ready" && p.total <= 1 ? "Redan på din dator" : "Nedladdad" }[p.state] || "";
    const t = Math.max(p.total, p.done, 1);
    done += p.state === "ready" ? t : p.done; total += t;
  });
  const frac = total ? Math.min(1, done / total) : 0;
  if ($("obPct")) $("obPct").textContent = Math.floor(frac * 100);
  const sp = OB.dlSpeed;
  const now = performance.now();
  if (now - sp.t > 700) { const inst = (done - sp.bytes) / ((now - sp.t) / 1000); sp.bps = sp.bps ? sp.bps * 0.7 + inst * 0.3 : inst; sp.t = now; sp.bytes = done; }
  const eta = sp.bps > 1e5 ? (total - done) / sp.bps : 0;
  if ($("obEta")) $("obEta").textContent = sp.bps > 1e5 && frac < 1 ? `${(sp.bps / 1048576).toFixed(1).replace(".", ",")} MB/s · ${eta > 90 ? "ca " + Math.round(eta / 60) + " min" : Math.max(1, Math.round(eta)) + " s"}` : frac >= 1 ? "Klart" : "Förbereder …";
}
function obEvent(d) {
  if (!OB.on || !d) return;
  if (d.stage === "dl") {
    OB.dl[d.part] = Object.assign(OB.dl[d.part] || {}, { state: d.state, done: d.done || 0, total: d.total || OB.dl[d.part]?.total || 0, status: d.status, file: d.file });
    obPaintDl();
  } else if (d.stage === "dl_done") {
    ["stt", "llm"].forEach(k => { if (OB.dl[k]) OB.dl[k].state = "ready"; });
    obPaintDl();
    setTimeout(() => {
      if (OB.step !== "download") return;
      OB.hist.pop(); OB.step = "models";      // "Tillbaka" från testet ska gå till modellvalet, inte starta om nedladdningen
      obGo("test");
    }, 1100);
  } else if (d.stage === "dl_error") {
    const e = $("obDlErr");
    if (!e) return;
    e.replaceChildren();
    const n = el("div", "ob-note warn");
    const g = el("div", "grow"); g.innerHTML = "<b>Nedladdningen avbröts.</b> " + (d.error || "");
    const b = el("button", "ob-btn pri", "Försök igen"); b.type = "button";
    b.onclick = () => { e.replaceChildren(); pywebview.api.setup_download(OB.d.stt, OB.d.llm); };
    n.append(g, b); e.append(n);
  } else if (d.stage === "dl_cancelled") {
    /* användaren gick tillbaka: inget att visa */
  } else if (d.stage && d.stage.startsWith("test") || d.stage === "level") {
    obTestEvent(d);
  }
}

// ---------- mikrofontest
function obPaintTest() {
  const orb = $("obOrb"), txt = $("obTestTxt"), again = $("obAgain");
  if (!orb) return;
  const T = OB.test, lv = orb.querySelector(".lv"), cd = orb.querySelector(".cd"), mic = orb.querySelector("svg");
  const rec = T.state === "rec";
  orb.classList.toggle("rec", rec);
  orb.classList.toggle("busy", ["loading", "rec", "trans"].includes(T.state));
  lv.style.display = rec ? "flex" : "none";
  mic.style.display = rec ? "none" : "";
  cd.textContent = "";
  again.hidden = !["done", "error"].includes(T.state);
  txt.replaceChildren();
  const lead = (t) => txt.append(el("div", "lead", t));
  if (T.state === "idle") lead("Tryck på cirkeln och prata i fem sekunder.");
  else if (T.state === "loading") lead("Laddar modellen …");
  else if (T.state === "rec") lead("Jag lyssnar …");
  else if (T.state === "trans") lead("Skriver ut …");
  else if (T.state === "error") { lead("Testet gick inte: " + (T.error || "okänt fel")); }
  else if (T.state === "done") {
    if (T.text) {
      txt.append(el("div", "quote", "”" + T.text + "”"));
      const m = el("div", "meta"); m.innerHTML = `Skrevs ut på <b>${T.secs.toFixed(1).replace(".", ",")} s</b> · ${OB.d.engine === "local" ? "lokalt på din dator" : "OpenAI"}`;
      if (OB.d.engine === "local" && T.secs > 4) m.append(` · Det är långsamt. Välj en mindre modell i Inställningar för snabbare svar.`);
      txt.append(m);
    } else lead(T.peak < 150 ? "Jag hörde ingenting. Är mikrofonen på och inte mutad?" : "Jag hörde ljud men inget tal. Prova igen, lite närmare mikrofonen.");
  }
  obUpdateNext();
}
function obRunTest() {
  if (["loading", "rec", "trans"].includes(OB.test.state)) return;
  OB.test = { state: "loading" };
  obPaintTest();
  pywebview.api.setup_test(obProfile(), OB.d.key);
}
function obTestEvent(d) {
  const T = OB.test;
  if (d.stage === "level") {
    const bars = document.querySelectorAll("#obOrb .lv b");
    bars.forEach((b, k) => { const w = [.5, .8, 1, .9, 1, .75, .5][k]; b.style.transform = `scaleY(${.18 + Math.min(1, d.v * w * 1.8) * .82})`; });
    return;
  }
  if (d.stage === "test_loading") OB.test = { state: "loading" };
  else if (d.stage === "test_rec") {
    OB.test = { state: "rec", t0: performance.now() };
    const tick = setInterval(() => {
      if (OB.test.state !== "rec") { clearInterval(tick); return; }
      const left = Math.max(0, 5 - (performance.now() - OB.test.t0) / 1000);
      const cd = document.querySelector("#obOrb .cd");
      if (cd) cd.textContent = Math.ceil(left) || "";
    }, 120);
  } else if (d.stage === "test_trans") OB.test = { state: "trans" };
  else if (d.stage === "test_done") OB.test = { state: "done", text: d.text, secs: d.secs, peak: d.peak };
  else if (d.stage === "test_error") OB.test = { state: "error", error: d.error };
  obPaintTest();
}

// ---------- spara och avsluta
function obProfile() {
  const d = OB.d;
  return { name: d.name, company: d.company, goals: d.goals, quality: d.quality, engine: d.engine, stt: d.stt, llm: d.llm };
}
async function obFinish() {
  if (OB.nav) return;
  OB.nav = true;
  try {
    await pywebview.api.setup_save(obProfile(), OB.d.skipKey ? "" : OB.d.key);
  } catch (e) { OB.nav = false; return; }
  const ob = $("onboard");
  OB.on = false;
  document.body.classList.remove("ob-busy");
  // appen visas under guiden medan guiden zoomar bort
  $("setup").classList.add("on");
  document.body.classList.remove("ob-on");
  ob.classList.add("leaving");
  await obRefreshForm();
  await obSleep(1000);
  ob.classList.remove("on", "leaving");
  if (OB.cur) { OB.cur.el.remove(); OB.cur = null; }
  delete document.body.dataset.scene;
  OB.nav = false;
}
async function obRefreshForm() {       // fyll i formuläret med namnet och målet från guiden
  try {
    const s = await pywebview.api.init();
    if (s.form.me != null && $("me")) $("me").value = s.form.me;
    if (s.form.goal && typeof setGoal === "function") setGoal(s.form.goal);
    if (typeof setMode === "function") setMode(s.mode);
    if (s.needKey) { $("keyField").hidden = false; $("audio").classList.add("k3"); }
    else { $("keyField").hidden = true; $("audio").classList.remove("k3"); }
  } catch (e) { /* formuläret fylls i nästa gång appen startar */ }
}
function obClose() {                    // stäng guiden utan att spara (bara vid ändring av inställningar)
  if (!OB.rerun || OB.nav) return;
  if (OB.step === "download") pywebview.api.setup_cancel();
  OB.on = false; OB.nav = true;
  document.body.classList.remove("ob-on", "ob-busy");
  clearInterval(OB.poll);
  const ob = $("onboard");
  $("setup").classList.add("on");
  ob.classList.add("leaving");
  setTimeout(() => { ob.classList.remove("on", "leaving"); if (OB.cur) { OB.cur.el.remove(); OB.cur = null; } OB.nav = false; }, 800);
}

// ---------- start
async function startOnboarding(opts = {}) {
  if (OB.on) return;
  OB.on = true; OB.rerun = !!opts.rerun; OB.hist = []; OB.step = null; OB.cur = null; OB._autostarted = false;
  OB.test = { state: "idle" };
  let info;
  try { info = await pywebview.api.setup_info(); } catch (e) { OB.on = false; return; }
  OB.info = info; OB.ollama = Object.assign({ starting: false }, info.ollama);
  const p = info.profile || {};
  const me = ((window.__init && window.__init.form && window.__init.form.me) || "").split(",");
  OB.d = { name: p.name || me[0] || "", company: p.company || (me.slice(1).join(",").trim()), goals: p.goals || [], quality: p.quality || "balanced",
    engine: p.engine || "", key: "", keyOk: !!(info.hasKey && p.engine === "cloud"), keyChecked: null, skipKey: false, stt: p.stt || "", llm: p.llm || "" };
  const body = document.body;
  body.classList.add("ob-on");
  const ob = $("onboard");
  if (!ob.querySelector(".ob-horizon i")) {
    const hz = ob.querySelector(".ob-horizon");
    for (let k = 0; k < 64; k++) {
      const i = document.createElement("i");
      const x = k / 63, env = 0.35 + 0.65 * Math.sin(Math.PI * x) ** 0.8;
      i.style.cssText = `--h:${(env * (0.45 + Math.random() * 0.55)).toFixed(2)};--d:${(-Math.random() * 3).toFixed(2)}s;--s:${(1.8 + Math.random() * 2).toFixed(2)}s`;
      hz.append(i);
    }
  }
  $("obClose").hidden = !OB.rerun;
  showView("onboard");
  if (!OB.rerun) {         // vänta tills öppningssekvensen är klar, så att välkomsten får sin stora entré
    for (let k = 0; k < 80 && document.body.classList.contains("intro-on"); k++) await obSleep(100);
    await obSleep(250);
  }
  obGo(OB.rerun ? "name" : "welcome");
}
// Kameran följer med: auran förskjuts lite åt motsatt håll mot musen (parallax).
document.addEventListener("pointermove", (e) => {
  if (!OB.on) return;
  document.body.style.setProperty("--px", ((e.clientX / innerWidth) * 2 - 1).toFixed(3));
  document.body.style.setProperty("--py", ((e.clientY / innerHeight) * 2 - 1).toFixed(3));
});
document.addEventListener("keydown", (e) => {
  if (!OB.on) return;
  if (e.key === "Enter" && !(e.target.matches && e.target.matches("button, select, textarea"))) { e.preventDefault(); obNext(); }
  else if (e.key === "Escape" && OB.rerun) obClose();
});
document.addEventListener("DOMContentLoaded", () => {
  $("obNext").onclick = obNext;
  $("obBack").onclick = obBack;
  $("obClose").onclick = obClose;
});
