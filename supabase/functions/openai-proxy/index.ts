// OpenAI-proxy. Appen har aldrig en OpenAI-nyckel: den skickar sitt inloggningstoken och ett samtals-ID hit,
// och bara vi pratar med OpenAI. Kräver ett molnsamtal som användaren själv startat med start_call().
// Pågående samtal: alla godkända modeller, inom planens längdgräns. Avslutat samtal: bara analysmodellen, i 24 timmar.
//
// Appen pekar sin OpenAI-klient hit:
//   OpenAI(base_url="https://<projekt>.supabase.co/functions/v1/openai-proxy/v1", api_key=<inloggningstoken>,
//          default_headers={"x-call-id": <call_id från start_call>})
import { admin, CORS, env, json, userFromRequest } from "../_shared/common.ts";

const ALLOWED: Record<string, Set<string>> = {
  "/v1/audio/transcriptions": new Set(["gpt-transcribe", "gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"]),
  "/v1/responses": new Set(["gpt-6-luna", "gpt-6.1-terra"]),
};
const MAX_BYTES = { "/v1/audio/transcriptions": 25 * 1024 * 1024, "/v1/responses": 1024 * 1024 } as const;
const GRACE_MINUTES = 2;
const SUMMARY_MODEL = "gpt-6.1-terra";
const SUMMARY_WINDOW_MS = 24 * 60 * 60 * 1000;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  const path = new URL(req.url).pathname.replace(/^.*?(\/v1\/)/, "/v1/");
  const allowedModels = ALLOWED[path];
  if (!allowedModels) return json({ error: "endpoint_not_allowed" }, 404);

  const user = await userFromRequest(req);
  if (!user) return json({ error: "not_logged_in" }, 401);

  // Samtalet måste finnas, tillhöra användaren, vara ett molnsamtal och fortfarande vara öppet.
  const callId = req.headers.get("x-call-id") ?? "";
  if (!/^[0-9a-f-]{36}$/i.test(callId)) return json({ error: "missing_call_id" }, 400);
  const db = admin();
  const { data: call } = await db.from("call_usage").select("started_at, mode, ended_at")
    .eq("id", callId).eq("user_id", user.id).maybeSingle();
  if (!call || call.mode !== "cloud") return json({ error: "call_not_open" }, 403);

  // Storlek och modell: bara de modeller vi godkänt, så ingen kan köra dyra modeller på vår nyckel.
  const body = await req.arrayBuffer();
  if (body.byteLength > MAX_BYTES[path as keyof typeof MAX_BYTES]) return json({ error: "too_large" }, 413);
  const contentType = req.headers.get("content-type") ?? "";
  let model = "";
  try {
    if (contentType.includes("multipart/form-data")) {
      const form = await new Response(body, { headers: { "content-type": contentType } }).formData();
      model = String(form.get("model") ?? "");
    } else {
      model = String(JSON.parse(new TextDecoder().decode(body)).model ?? "");
    }
  } catch { return json({ error: "bad_request" }, 400); }
  if (!allowedModels.has(model)) return json({ error: "model_not_allowed" }, 403);

  if (call.ended_at) {
    // Samtalet är slut: bara analysen (den stora modellen) får gå igenom, och bara en dag framåt.
    if (model !== SUMMARY_MODEL || Date.now() - new Date(call.ended_at).getTime() > SUMMARY_WINDOW_MS) {
      return json({ error: "call_not_open" }, 403);
    }
  } else {
    // Pågående samtal: planens längdgräns, plus lite marginal.
    const { data: sub } = await db.from("subscriptions").select("plan").eq("user_id", user.id).maybeSingle();
    const { data: lim } = await db.from("plan_limits").select("max_call_minutes").eq("plan", sub?.plan ?? "free").maybeSingle();
    const maxMs = ((lim?.max_call_minutes ?? 30) + GRACE_MINUTES) * 60_000;
    if (Date.now() - new Date(call.started_at).getTime() > maxMs) return json({ error: "call_too_long" }, 403);
  }

  const upstream = await fetch(`https://api.openai.com${path}`, {
    method: "POST",
    headers: { authorization: `Bearer ${env("OPENAI_API_KEY")}`, "content-type": contentType },
    body,
  });
  const headers = new Headers(CORS);
  for (const h of ["content-type", "cache-control"]) { const v = upstream.headers.get(h); if (v) headers.set(h, v); }
  return new Response(upstream.body, { status: upstream.status, headers });
});
