// Delat av alla funktioner. Hemligheter läses bara härifrån (serverns miljö), aldrig från appen.
import { createClient } from "npm:@supabase/supabase-js@2";

export const admin = () =>
  createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

export const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "authorization, content-type, x-call-id",
  "access-control-allow-methods": "POST, OPTIONS",
};

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { ...CORS, "content-type": "application/json" } });

/** Inloggad användare från Authorization-headern, eller null. Verifieras av Supabase Auth, aldrig av oss. */
export async function userFromRequest(req: Request) {
  const token = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
  if (!token) return null;
  const { data, error } = await admin().auth.getUser(token);
  return error || !data.user ? null : data.user;
}

export const env = (name: string) => {
  const v = Deno.env.get(name);
  if (!v) throw new Error(`saknar hemlighet: ${name}`);
  return v;
};
