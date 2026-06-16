import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Client-Info, Apikey",
};

const ok = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { ...corsHeaders, "Content-Type": "application/json" } });
const err = (msg: string, status = 400) =>
  new Response(JSON.stringify({ error: msg }), { status, headers: { ...corsHeaders, "Content-Type": "application/json" } });

function supabaseAdmin() {
  return createClient(
    Deno.env.get("SUPABASE_URL") ?? "",
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "",
  );
}

async function getSession(req: Request, sb: ReturnType<typeof supabaseAdmin>) {
  const token = req.headers.get("Authorization")?.replace("Bearer ", "").trim();
  if (!token) return null;
  const now = new Date().toISOString();
  const { data } = await sb
    .from("user_sessions")
    .select("user_id, users(*)")
    .eq("session_token", token)
    .gt("expires_at", now)
    .maybeSingle();
  return data ? (data.users as Record<string, unknown>) : null;
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 200, headers: corsHeaders });

  const url = new URL(req.url);
  const parts = url.pathname.split("/").filter(Boolean);
  const sub = parts[parts.indexOf("auth") + 1] ?? "";
  const sb = supabaseAdmin();

  try {
    // POST /auth/session – exchange Emergent session_id for our session token
    if (req.method === "POST" && sub === "session") {
      const { session_id } = await req.json();
      if (!session_id) return err("session_id required");

      const eRes = await fetch(
        "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
        { headers: { "X-Session-ID": session_id } },
      );
      if (!eRes.ok) return err("Invalid Emergent session", 401);
      const eData = await eRes.json();

      // Upsert user
      const { data: existing } = await sb
        .from("users")
        .select("user_id")
        .eq("email", eData.email)
        .maybeSingle();

      let userId: string;
      if (existing) {
        userId = existing.user_id as string;
        await sb.from("users").update({ name: eData.name, picture: eData.picture ?? "" }).eq("user_id", userId);
      } else {
        userId = `user_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
        await sb.from("users").insert({
          user_id: userId,
          email: eData.email,
          name: eData.name,
          picture: eData.picture ?? "",
          subscription: {},
        });
      }

      // Create session
      const sessionToken = `sess_${crypto.randomUUID().replace(/-/g, "")}`;
      const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
      await sb.from("user_sessions").insert({ user_id: userId, session_token: sessionToken, expires_at: expiresAt });

      const { data: user } = await sb.from("users").select("*").eq("user_id", userId).maybeSingle();
      const adminEmails = (Deno.env.get("ADMIN_EMAILS") ?? "").split(",").map((e) => e.trim());
      return ok({ ...user, session_token: sessionToken, is_admin: adminEmails.includes(eData.email) });
    }

    // GET /auth/me – return current user
    if (req.method === "GET" && sub === "me") {
      const user = await getSession(req, sb);
      if (!user) return err("Unauthorized", 401);
      const adminEmails = (Deno.env.get("ADMIN_EMAILS") ?? "").split(",").map((e) => e.trim());
      return ok({ ...user, is_admin: adminEmails.includes(user.email as string) });
    }

    // POST /auth/logout – delete session
    if (req.method === "POST" && sub === "logout") {
      const token = req.headers.get("Authorization")?.replace("Bearer ", "").trim();
      if (token) await sb.from("user_sessions").delete().eq("session_token", token);
      return ok({ message: "Logged out" });
    }

    return err("Not found", 404);
  } catch (e) {
    console.error(e);
    return err(String(e), 500);
  }
});
