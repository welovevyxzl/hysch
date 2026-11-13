"""Byter Hysch från Stripe-testläge till live. Körs i två steg:
   python go_live.py          (torrkörning: visar bara vad som skulle göras, ändrar inget)
   python go_live.py --apply  (skapar live-produkter, webhook och portal i Stripe och byter hemligheterna i Supabase)
Läser STRIPE_LIVE_SECRET_KEY ur .env. Skriver aldrig ut några nycklar. Testvärdena sparas i .env som STRIPE_TEST_* så att man kan gå tillbaka."""
import json, os, sys, urllib.parse, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLY = "--apply" in sys.argv
ENV_PATH = os.path.join(ROOT, ".env")
env = {}
for line in open(ENV_PATH, encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

live = env.get("STRIPE_LIVE_SECRET_KEY", "")
if not (live.startswith("sk_live_") or live.startswith("rk_live_")):
    sys.exit("STRIPE_LIVE_SECRET_KEY saknas i .env (eller är inte en live-nyckel).")
test = env["STRIPE_SECRET_KEY"]
if not test.startswith(("sk_test_", "rk_test_")):
    sys.exit("STRIPE_SECRET_KEY i .env är redan inte en testnyckel. Avbryter så att inget skrivs över av misstag.")
ref = env["SUPABASE_URL"].split("//")[1].split(".")[0]
SITE = env["SITE_URL"].rstrip("/")
HOOK = f"https://{ref}.supabase.co/functions/v1/stripe-webhook"
EVENTS = ["checkout.session.completed", "customer.subscription.created", "customer.subscription.deleted", "customer.subscription.updated"]


def stripe(key, method, path, form=None):
    data = urllib.parse.urlencode(form, doseq=True).encode() if form else None
    req = urllib.request.Request("https://api.stripe.com/v1" + path, data=data, method=method,
                                 headers={"authorization": "Bearer " + key, "user-agent": "hysch-go-live"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Stripe {method} {path}: {json.loads(e.read().decode() or '{}').get('error', {}).get('message')}")


def supabase_secrets(items):
    req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{ref}/secrets", data=json.dumps(items).encode(), method="POST",
                                 headers={"authorization": "Bearer " + env["SUPABASE_ACCESS_TOKEN"], "content-type": "application/json", "user-agent": "hysch-go-live"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


acct = stripe(live, "GET", "/account")
print("live-konto:", acct.get("id"), "| charges_enabled:", acct.get("charges_enabled"), "| payouts_enabled:", acct.get("payouts_enabled"))
if not acct.get("charges_enabled"):
    sys.exit("Live-kontot kan inte ta betalt än.")

# vad som finns i testläge, så att live blir likadant
t_price = {k: stripe(test, "GET", "/prices/" + env[k]) for k in ("STRIPE_PRICE_FOUNDING", "STRIPE_PRICE_PRO")}
t_cfg = stripe(test, "GET", "/billing_portal/configurations/" + env["STRIPE_PORTAL_CONFIG"])
names = {k: stripe(test, "GET", "/products/" + p["product"]).get("name") for k, p in t_price.items()}
print("planer:", {k: (names[k], p["unit_amount"] / 100, p["currency"]) for k, p in t_price.items()})
print("webhook:", HOOK, EVENTS)
print("portal speglas från testläget; return_url =", SITE + "/konto.html")
if not APPLY:
    sys.exit("Torrkörning klar. Kör med --apply för att byta till live.")

new = {}
for k, p in t_price.items():
    lookup = "hysch_" + k.split("_")[-1].lower() + "_monthly"
    found = stripe(live, "GET", "/prices?" + urllib.parse.urlencode({"lookup_keys[]": lookup, "active": "true"})).get("data", [])
    if found:
        new[k] = found[0]["id"]
        continue
    prod = stripe(live, "POST", "/products", {"name": names[k], "tax_code": "txcd_10103001"})
    new[k] = stripe(live, "POST", "/prices", {"product": prod["id"], "currency": p["currency"], "unit_amount": p["unit_amount"],
                                              "recurring[interval]": "month", "lookup_key": lookup})["id"]
print("live-priser:", new)

hooks = stripe(live, "GET", "/webhook_endpoints?limit=100")["data"]
mine = [h for h in hooks if h["url"] == HOOK]
if mine:
    sys.exit("Det finns redan en live-webhook för funktionen, men hemligheten visas bara när den skapas. Ta bort den i Stripe (Developers > Webhooks) och kör igen.")
wh = stripe(live, "POST", "/webhook_endpoints", {"url": HOOK, "enabled_events[]": EVENTS})
whsec = wh["secret"]

feat = {"business_profile[headline]": (t_cfg.get("business_profile") or {}).get("headline") or "Hantera din Hysch-prenumeration",
        "default_return_url": SITE + "/konto.html",
        "features[payment_method_update][enabled]": "true", "features[invoice_history][enabled]": "true",
        "features[customer_update][enabled]": "false",
        "features[subscription_cancel][enabled]": "true", "features[subscription_cancel][mode]": "at_period_end"}
cfg = stripe(live, "POST", "/billing_portal/configurations", feat)["id"]

# spara testvärdena, byt sedan hemligheterna i Supabase och i .env
backup = {"STRIPE_TEST_SECRET_KEY": test, "STRIPE_TEST_WEBHOOK_SECRET": env["STRIPE_WEBHOOK_SECRET"], "STRIPE_TEST_PRICE_FOUNDING": env["STRIPE_PRICE_FOUNDING"],
          "STRIPE_TEST_PRICE_PRO": env["STRIPE_PRICE_PRO"], "STRIPE_TEST_PORTAL_CONFIG": env["STRIPE_PORTAL_CONFIG"]}
secrets_new = {"STRIPE_SECRET_KEY": live, "STRIPE_WEBHOOK_SECRET": whsec, "STRIPE_PRICE_FOUNDING": new["STRIPE_PRICE_FOUNDING"],
               "STRIPE_PRICE_PRO": new["STRIPE_PRICE_PRO"], "STRIPE_PORTAL_CONFIG": cfg}
status = supabase_secrets([{"name": k, "value": v} for k, v in secrets_new.items()])
print("Supabase-hemligheter bytta:", status)

text = open(ENV_PATH, encoding="utf-8").read()
for k, v in secrets_new.items():
    lines = text.splitlines()
    text = "\n".join((f"{k}={v}" if l.startswith(k + "=") else l) for l in lines) + "\n"
text += "\n# testlägets värden, sparade när vi gick live\n" + "".join(f"{k}={v}\n" for k, v in backup.items())
open(ENV_PATH, "w", encoding="utf-8", newline="").write(text)
print("Klart: live. Testvärdena ligger kvar i .env som STRIPE_TEST_*.")
