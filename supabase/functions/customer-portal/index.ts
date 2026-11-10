// Skickar användaren till Stripes kundportal: se faktura, byta kort, säga upp.
import Stripe from "npm:stripe@17";
import { admin, CORS, env, json, userFromRequest } from "../_shared/common.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  const user = await userFromRequest(req);
  if (!user) return json({ error: "not_logged_in" }, 401);

  const { data: sub } = await admin().from("subscriptions").select("stripe_customer_id").eq("user_id", user.id).maybeSingle();
  if (!sub?.stripe_customer_id) return json({ error: "no_customer" }, 404);

  const stripe = new Stripe(env("STRIPE_SECRET_KEY"), { httpClient: Stripe.createFetchHttpClient() });
  const configuration = Deno.env.get("STRIPE_PORTAL_CONFIG");    // Hysch egen portalinställning
  const session = await stripe.billingPortal.sessions.create({
    customer: sub.stripe_customer_id,
    return_url: env("SITE_URL"),
    ...(configuration ? { configuration } : {}),
  });
  return json({ url: session.url });
});
