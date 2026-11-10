// Stripe säger till här när någon betalat, ändrat eller sagt upp. Bara den här funktionen får ändra plan.
// Signaturen kontrolleras, och vi hämtar alltid prenumerationen på nytt från Stripe i stället för att lita på
// innehållet i händelsen, så en gammal eller förfalskad händelse kan inte ge fel plan.
import Stripe from "npm:stripe@17";
import { admin, env } from "../_shared/common.ts";

const stripe = new Stripe(env("STRIPE_SECRET_KEY"), { httpClient: Stripe.createFetchHttpClient() });
const cryptoProvider = Stripe.createSubtleCryptoProvider();

async function sync(subscriptionId: string) {
  const sub = await stripe.subscriptions.retrieve(subscriptionId);
  const db = admin();
  const customerId = typeof sub.customer === "string" ? sub.customer : sub.customer.id;

  let userId = sub.metadata?.user_id as string | undefined;
  if (!userId) {
    const { data } = await db.from("subscriptions").select("user_id").eq("stripe_customer_id", customerId).maybeSingle();
    userId = data?.user_id;
  }
  if (!userId) { console.error("ingen användare för kund", customerId); return; }

  const priceId = sub.items.data[0]?.price.id;
  const plan = priceId === env("STRIPE_PRICE_FOUNDING") ? "founding" : priceId === env("STRIPE_PRICE_PRO") ? "pro" : null;
  if (!plan) { console.error("okänt pris", priceId); return; }

  const ended = ["canceled", "unpaid", "incomplete_expired"].includes(sub.status);
  const waiting = sub.status === "incomplete";                       // betalningen är inte klar än: ge inget ännu
  const item = sub.items.data[0] as unknown as { current_period_start?: number; current_period_end?: number };
  const start = sub.current_period_start ?? item.current_period_start;
  const end = sub.current_period_end ?? item.current_period_end;

  const row = (ended || waiting)
    ? { plan: "free", status: "active", stripe_subscription_id: null, current_period_start: null, current_period_end: null, cancel_at_period_end: false }
    : {
      plan,
      status: sub.status === "past_due" ? "past_due" : sub.status === "trialing" ? "trialing" : "active",
      stripe_subscription_id: sub.id,
      current_period_start: start ? new Date(start * 1000).toISOString() : null,
      current_period_end: end ? new Date(end * 1000).toISOString() : null,
      cancel_at_period_end: sub.cancel_at_period_end || !!sub.cancel_at,
    };
  const { error } = await db.from("subscriptions")
    .update({ ...row, stripe_customer_id: customerId, updated_at: new Date().toISOString() }).eq("user_id", userId);
  if (error) throw error;
}

Deno.serve(async (req) => {
  const signature = req.headers.get("stripe-signature");
  if (!signature) return new Response("no signature", { status: 400 });
  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(await req.text(), signature, env("STRIPE_WEBHOOK_SECRET"), undefined, cryptoProvider);
  } catch (e) {
    console.error("fel signatur", (e as Error).message);
    return new Response("bad signature", { status: 400 });
  }

  try {
    switch (event.type) {
      case "checkout.session.completed": {
        const s = event.data.object as Stripe.Checkout.Session;
        if (s.mode === "subscription" && s.subscription) await sync(typeof s.subscription === "string" ? s.subscription : s.subscription.id);
        break;
      }
      case "customer.subscription.created":
      case "customer.subscription.updated":
      case "customer.subscription.deleted":
        await sync((event.data.object as Stripe.Subscription).id);
        break;
    }
  } catch (e) {
    console.error("webhook misslyckades", event.type, (e as Error).message);
    return new Response("error", { status: 500 });          // Stripe försöker igen
  }
  return new Response("ok");
});
