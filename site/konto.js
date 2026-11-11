// Inloggning och konto i webbläsaren. Samma konto som i appen: e-postkod mot Supabase Auth.
// Adressen och nyckeln är publika (som i appen); allt skyddas av inloggning och radskydd i databasen.
(() => {
  const URL_ = "https://jlzzcqbhpxxxwvwxaqnc.supabase.co";
  const KEY = "sb_publishable_CcTBuO8aDe3c7DzbRhdxcg_behZFjjS";
  const DOWNLOAD_URL = "https://github.com/welovevyxzl/hysch/releases/latest/download/Hysch.exe";
  const $ = (id) => document.getElementById(id);
  const PLAN = { free: "Gratis", founding: "Grundare", pro: "Pro" };
  const fmtDay = (d) => new Date(d).toLocaleDateString("sv-SE", { day: "numeric", month: "long" });
  let S = null, email = "";

  const load = () => { try { return JSON.parse(localStorage.getItem("hysch.session")); } catch { return null; } };
  const save = (s) => { try { s ? localStorage.setItem("hysch.session", JSON.stringify(s)) : localStorage.removeItem("hysch.session"); } catch {} };
  document.querySelectorAll("[data-download]").forEach((a) => { a.href = DOWNLOAD_URL; });

  const ERR = { otp_expired: "Koden stämmer inte eller har gått ut. Be om en ny.", validation_failed: "Kolla att e-posten är rätt skriven.",
    email_address_invalid: "Kolla att e-posten är rätt skriven.", over_request_rate_limit: "För många försök. Vänta en stund.",
    over_email_send_rate_limit: "Vänta en minut innan du ber om en ny kod." };

  async function api(path, opts = {}, token) {
    const r = await fetch(URL_ + path, { ...opts, headers: { apikey: KEY, authorization: "Bearer " + (token || KEY), "content-type": "application/json", ...(opts.headers || {}) } });
    let j = {}; try { j = await r.json(); } catch {}
    return { ok: r.ok, status: r.status, j };
  }
  const errText = (r, fb) => ERR[r.j.error_code || r.j.code] || fb;

  function show(which) {
    ["viewEmail", "viewCode", "viewAccount"].forEach((v) => { $(v).hidden = v !== which; });
    $("loading").hidden = true; $("logout").hidden = which !== "viewAccount";
  }

  async function token() {
    if (!S) return null;
    if (S.expires_at - Date.now() / 1000 > 60) return S.access_token;
    const r = await api("/auth/v1/token?grant_type=refresh_token", { method: "POST", body: JSON.stringify({ refresh_token: S.refresh_token }) });
    if (!r.ok) { if (r.status >= 400 && r.status < 500) { S = null; save(null); } return null; }
    S = { access_token: r.j.access_token, refresh_token: r.j.refresh_token, expires_at: Date.now() / 1000 + (r.j.expires_in || 3600), email: (r.j.user || {}).email || S.email };
    save(S); return S.access_token;
  }

  // ---------- inloggning
  $("formEmail").onsubmit = async (e) => {
    e.preventDefault(); $("errEmail").textContent = "";
    email = $("email").value.trim().toLowerCase();
    $("goEmail").disabled = true;
    const r = await api("/auth/v1/otp", { method: "POST", body: JSON.stringify({ email, create_user: true }) }).catch(() => null);
    $("goEmail").disabled = false;
    if (!r || !r.ok) { $("errEmail").textContent = r ? errText(r, "Kunde inte skicka koden. Försök igen.") : "Ingen kontakt med servern."; return; }
    $("sentTo").textContent = email; $("code").value = ""; show("viewCode"); $("code").focus();
  };
  $("back").onclick = () => show("viewEmail");
  $("formCode").onsubmit = async (e) => {
    e.preventDefault(); $("errCode").textContent = "";
    const code = $("code").value.replace(/\D/g, "");
    if (code.length !== 6) { $("errCode").textContent = "Koden har sex siffror."; return; }
    $("goCode").disabled = true;
    const r = await api("/auth/v1/verify", { method: "POST", body: JSON.stringify({ type: "email", email, token: code }) }).catch(() => null);
    $("goCode").disabled = false;
    if (!r || !r.ok) { $("errCode").textContent = r ? errText(r, "Koden stämmer inte eller har gått ut. Be om en ny.") : "Ingen kontakt med servern."; return; }
    S = { access_token: r.j.access_token, refresh_token: r.j.refresh_token, expires_at: Date.now() / 1000 + (r.j.expires_in || 3600), email: (r.j.user || {}).email || email };
    save(S); openAccount();
  };
  $("logout").onclick = async () => {
    const t = S && S.access_token; S = null; save(null);
    if (t) api("/auth/v1/logout", { method: "POST" }, t).catch(() => {});
    show("viewEmail");
  };

  // ---------- konto
  function meter(label, used, limit) {
    const unl = limit == null, none = limit === 0, p = unl || none ? 0 : Math.min(100, (used / limit) * 100);
    const d = document.createElement("div"); d.className = "m";
    d.innerHTML = `<div class="top"><span></span><span></span></div><div class="bar${!unl && !none && used >= limit ? " full" : ""}"><i></i></div>`;
    d.querySelectorAll(".top span")[0].textContent = label;
    d.querySelectorAll(".top span")[1].textContent = unl ? "Obegränsat" : none ? "Ingår inte" : `${used} av ${limit}`;
    d.querySelector("i").style.width = p + "%";
    return d;
  }
  async function openAccount() {
    const t = await token();
    if (!t) { show("viewEmail"); return; }
    const r = await api("/rest/v1/my_account?select=*", {}, t).catch(() => null);
    if (!r || !r.ok || !r.j.length) { show("viewAccount"); $("errAcct").textContent = "Kunde inte hämta ditt konto just nu."; return; }
    const a = r.j[0], paid = a.plan !== "free";
    show("viewAccount");
    $("mail").textContent = S.email;
    $("planName").textContent = PLAN[a.plan] || a.plan;
    const chip = $("chip"); chip.hidden = !paid; chip.className = "chip";
    if (a.status === "past_due") { chip.textContent = "Betalning saknas"; chip.classList.add("warn"); }
    else if (paid) { chip.textContent = a.cancel_at_period_end ? "Avslutas" : "Aktiv"; if (a.cancel_at_period_end) chip.classList.add("warn"); }
    $("planSub").textContent = paid
      ? (a.current_period_end ? (a.cancel_at_period_end ? `Gäller till ${fmtDay(a.current_period_end)}` : `Förnyas ${fmtDay(a.current_period_end)}`) : "")
      : "Lokalt läge är gratis. Molnläget ingår i Grundare och Pro.";
    const u = $("usage"); u.replaceChildren(meter("Molnsamtal", Number(a.cloud_calls_used), a.cloud_calls_per_month), meter("Lokala samtal", Number(a.local_calls_used), a.local_calls_per_month));
    $("upgrade").hidden = paid; $("manage").hidden = !paid;
    const q = new URLSearchParams(location.search).get("betalning");
    const b = $("banner");
    b.hidden = !q; b.className = "banner" + (q === "avbruten" ? " warn" : "");
    b.textContent = q === "klar" ? "Tack! Din plan uppdateras om en liten stund. Ladda om sidan om den inte syns direkt." : q === "avbruten" ? "Betalningen avbröts. Inget har dragits." : "";
  }

  async function fn(name, body, btn) {
    $("errAcct").textContent = ""; btn.disabled = true;
    const t = await token();
    const r = t && await api("/functions/v1/" + name, { method: "POST", body: JSON.stringify(body) }, t).catch(() => null);
    btn.disabled = false;
    if (r && r.ok && r.j.url) { location.href = r.j.url; return; }
    const msgs = { founding_sold_out: "Grundarplatserna är slut. Välj Pro.", already_subscribed: "Du har redan en prenumeration. Välj Hantera.", no_customer: "Du har ingen prenumeration att hantera än." };
    $("errAcct").textContent = !r ? "Ingen kontakt med servern." : msgs[r.j.error] || (r.status >= 500 ? "Betalningen är inte färdigkopplad på servern än." : "Kunde inte öppna betalsidan. Försök igen.");
  }
  document.querySelectorAll("[data-plan]").forEach((b) => { b.onclick = () => fn("create-checkout", { plan: b.dataset.plan }, b); });
  $("manage").onclick = () => fn("customer-portal", {}, $("manage"));

  S = load();
  if (S && S.refresh_token) openAccount(); else show("viewEmail");
})();
