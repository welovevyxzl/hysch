// Skapar en Stripe Checkout-session för Grundare eller Pro. Priset hämtas på servern, aldrig från appen.
import Stripe from "npm:stripe@17";
import { admin, CORS, env, json, userFromRequest } from "../_shared/common.ts";

const FOUNDER_LIMIT = 50;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  const user = await userFromRequest(req);
  if (!user) return json({ error: "not_logged_in" }, 401);

  let plan = "";
  try { plan = String((await req.json()).plan ?? ""); } catch { /* tom body */ }
  const price = plan === "founding" ? env("STRIPE_PRICE_FOUNDING") : plan === "pro" ? env("STRIPE_PRICE_PRO") : "";
  if (!price) return json({ error: "bad_plan" }, 400);

  const db = admin();
  const { data: sub } = await db.from("subscriptions").select("*").eq("user_id", user.id).maybeSingle();
  if (sub && sub.plan !== "free" && sub.status !== "canceled") return json({ error: "already_subscribed" }, 409);

  if (plan === "founding") {
    const { count } = await db.from("subscriptions").select("user_id", { count: "exact", head: true })
      .eq("plan", "founding").in("status", ["active", "trialing", "past_due"]);
    if ((count ?? 0) >= FOUNDER_LIMIT) return json({ error: "founding_sold_out" }, 409);
  }

  // Fel från Stripe skickas tillbaka som läsbar text (aldrig nycklar), så att appen och loggen visar vad som gick fel i stället för ett tomt 500.
  try {
    // Kontot har Managed Payments på, och det kräver API-version 2025-03-31.basil eller nyare. stripe@17 använder en äldre som standard,
    // så versionen sätts här uttryckligen (annars svarar Stripe 400 och funktionen 500).
    const stripe = new Stripe(env("STRIPE_SECRET_KEY"), {
      apiVersion: "2025-03-31.basil" as unknown as Stripe.LatestApiVersion,
      httpClient: Stripe.createFetchHttpClient(),
    });
    let customer = sub?.stripe_customer_id as string | null | undefined;
    if (!customer) {
      customer = (await stripe.customers.create({ email: user.email ?? undefined, metadata: { user_id: user.id } })).id;
      await db.from("subscriptions").update({ stripe_customer_id: customer }).eq("user_id", user.id);
    }

    const site = env("SITE_URL").replace(/\/$/, "");
    const session = await stripe.checkout.sessions.create({
      mode: "subscription",
      customer,
      client_reference_id: user.id,
      line_items: [{ price, quantity: 1 }],
      subscription_data: { metadata: { user_id: user.id, plan } },
      locale: "sv",
      success_url: `${site}/konto.html?betalning=klar`,
      cancel_url: `${site}/konto.html?betalning=avbruten`,
    });
    return json({ url: session.url });
  } catch (e) {
    const message = String((e as Error)?.message ?? e).slice(0, 300);
    console.error("create-checkout:", message);
    return json({ error: "checkout_failed", message }, 502);
  }
});
