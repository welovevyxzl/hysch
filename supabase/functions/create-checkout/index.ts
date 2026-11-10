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

  const stripe = new Stripe(env("STRIPE_SECRET_KEY"), { httpClient: Stripe.createFetchHttpClient() });
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
    success_url: `${site}/?betalning=klar`,
    cancel_url: `${site}/?betalning=avbruten#pricing`,
  });
  return json({ url: session.url });
});
