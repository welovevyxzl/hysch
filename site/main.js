/* Hysch: hemsida. Inga bibliotek. Rörelse styrs av scroll och en enda tidslinje i demofönstret. */
(() => {
  // Byt till den riktiga nedladdningsadressen när du har lagt ut Hysch.exe publikt.
  const DOWNLOAD_URL = "https://github.com/welovevyxzl/hysch/releases/latest/download/Hysch.exe";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const smooth = (t) => { t = clamp(t); return t * t * (3 - 2 * t); };
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const staticMode = /[?&]static/.test(location.search);
  if (staticMode) document.documentElement.classList.add("static");
  const still = reduce || staticMode;

  $$("[data-download]").forEach((a) => { a.href = DOWNLOAD_URL; });

  /* ---------- meny ---------- */
  const nav = $("#nav");
  const onScrollNav = () => nav.classList.toggle("scrolled", scrollY > 8);
  onScrollNav();

  /* ---------- avslöja vid scroll ---------- */
  const rv = $$(".rv");
  if (still || !("IntersectionObserver" in window)) rv.forEach((e) => e.classList.add("in"));
  else {
    const io = new IntersectionObserver((es) => es.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }), { threshold: 0.14, rootMargin: "0px 0px -6% 0px" });
    rv.forEach((e) => io.observe(e));
  }

  /* ---------- demofönstret i hero ---------- */
  const CUSTOMER = "Vi har faktiskt ingen budget för sånt här just nu.";
  const SUGGESTION = "Okej, då testar vi det gratis med en klass först. Skulle det vara intressant?";
  const dSay = $("#dSay"), dAlts = $("#dAlts"), dHeard = $("#dHeard"), dCaret = $("#dCaret"), dLat = $("#dLat"),
        dLbl = $("#dLbl"), dStatus = $("#dStatus"), dThem = $("#dThem"), dClock = $("#dClock"), win = $("#win");
  const words = SUGGESTION.split(" ").map((w, i, a) => (i < a.length - 1 ? w + " " : w));
  dSay.innerHTML = words.map((w) => `<span class="wd">${w}</span>`).join("");
  const wd = $$(".wd", dSay);

  const fmtClock = (s) => `00:${String(s).padStart(2, "0")}`;
  const setLat = () => { dLat.innerHTML = "Svar på <b>1,3 s</b>"; };

  if (still) {
    dHeard.textContent = CUSTOMER; wd.forEach((w) => w.classList.add("on")); dAlts.classList.add("on");
    setLat(); dLat.classList.add("on"); dThem.classList.add("on");
  } else {
    let visible = true, wake = null;
    new IntersectionObserver((es) => {
      visible = es[0].isIntersecting;
      if (visible && wake) { wake(); wake = null; }
    }, { threshold: 0.2 }).observe(win);
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const whenVisible = () => (visible ? Promise.resolve() : new Promise((r) => { wake = r; }));

    let first = true;
    (async function loop() {
      for (;;) {
        await whenVisible();
        // nollställ
        wd.forEach((w) => w.classList.remove("on")); dAlts.classList.remove("on"); dLat.classList.remove("on");
        dHeard.textContent = ""; dLbl.classList.remove("think"); dStatus.textContent = "Lyssnar"; dThem.classList.remove("on");
        let sec = 5; dClock.textContent = fmtClock(sec);
        await wait(first ? 1800 : 1400); first = false;
        // kunden pratar
        dThem.classList.add("on"); dCaret.classList.add("on");
        for (let i = 1; i <= CUSTOMER.length; i++) { dHeard.textContent = CUSTOMER.slice(0, i); await wait(34); }
        dCaret.classList.remove("on"); dThem.classList.remove("on");
        dClock.textContent = fmtClock(sec + 2);
        // tystnaden: Hysch tänker
        dLbl.classList.add("think"); await wait(1300); dLbl.classList.remove("think");
        // svaret
        for (const w of wd) { w.classList.add("on"); await wait(85); }
        dAlts.classList.add("on"); setLat(); dLat.classList.add("on");
        dClock.textContent = fmtClock(sec + 4);
        await wait(6200);
      }
    })();
  }

  /* ---------- betygsringen ---------- */
  const ring = $("#ring");
  if (ring) {
    const val = $("#ringVal"), n = $("#ringNum"), TARGET = 78, C = 314.16;
    const go = () => {
      val.style.strokeDashoffset = String(C * (1 - TARGET / 100));
      const t0 = performance.now();
      const tick = (now) => { const t = clamp((now - t0 - 200) / 1700); n.textContent = Math.round(smooth(t) * TARGET); if (t < 1) requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
    };
    if (still) { val.style.transition = "none"; val.style.strokeDashoffset = String(C * (1 - TARGET / 100)); n.textContent = TARGET; }
    else new IntersectionObserver((es, o) => { if (es[0].isIntersecting) { go(); o.disconnect(); } }, { threshold: 0.5 }).observe(ring);
  }

  /* ---------- att-göra-listan: gå att bocka av ---------- */
  $$("#todos button").forEach((b) => b.addEventListener("click", () => {
    const on = !b.classList.contains("done");
    b.classList.toggle("done", on); b.setAttribute("aria-pressed", String(on));
  }));

  addEventListener("scroll", onScrollNav, { passive: true });

})();

/* muspekarens ljus i glaskorten */
document.querySelectorAll(".glass").forEach((g) => g.addEventListener("pointermove", (e) => {
  const r = g.getBoundingClientRect();
  g.style.setProperty("--mx", e.clientX - r.left + "px"); g.style.setProperty("--my", e.clientY - r.top + "px");
}, { passive: true }));
