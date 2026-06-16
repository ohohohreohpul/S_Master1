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

function sb() {
  return createClient(Deno.env.get("SUPABASE_URL") ?? "", Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "");
}
async function getUser(req: Request, supabase: ReturnType<typeof sb>) {
  const token = req.headers.get("Authorization")?.replace("Bearer ", "").trim();
  if (!token) return null;
  const { data } = await supabase
    .from("user_sessions")
    .select("users(*)")
    .eq("session_token", token)
    .gt("expires_at", new Date().toISOString())
    .maybeSingle();
  return data ? (data.users as Record<string, unknown>) : null;
}

async function requireAdmin(req: Request, supabase: ReturnType<typeof sb>) {
  const user = await getUser(req, supabase);
  if (!user) return null;
  const adminEmails = (Deno.env.get("ADMIN_EMAILS") ?? "").split(",").map((e) => e.trim());
  if (!adminEmails.includes(user.email as string)) return null;
  return user;
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 200, headers: corsHeaders });

  const url = new URL(req.url);
  const parts = url.pathname.split("/").filter(Boolean);
  const fnIdx = parts.indexOf("admin");
  const sub = parts[fnIdx + 1] ?? "";  // "exams" or "stats"
  const examId = parts[fnIdx + 2] ?? "";
  const action = parts[fnIdx + 3] ?? "";
  const supabase = sb();

  try {
    // GET /admin/exams
    if (req.method === "GET" && sub === "exams" && !examId) {
      const user = await requireAdmin(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const { data } = await supabase
        .from("exams")
        .select("exam_id, title, exam_type, telc_level, status, audio_progress, created_at, created_by")
        .order("created_at", { ascending: false });
      return ok(data ?? []);
    }

    // GET /admin/stats
    if (req.method === "GET" && sub === "stats") {
      const user = await requireAdmin(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const [{ count: totalExams }, { count: totalUsers }, { count: totalAttempts }, { count: totalAudio }, { count: proUsers }] = await Promise.all([
        supabase.from("exams").select("*", { count: "exact", head: true }),
        supabase.from("users").select("*", { count: "exact", head: true }),
        supabase.from("attempts").select("*", { count: "exact", head: true }),
        supabase.from("audio_files").select("*", { count: "exact", head: true }),
        supabase.from("users").select("*", { count: "exact", head: true }).neq("subscription->status", "null").eq("subscription->status", "active"),
      ]);
      return ok({ total_exams: totalExams, total_users: totalUsers, total_attempts: totalAttempts, total_audio_files: totalAudio, pro_users: proUsers });
    }

    // DELETE /admin/exams/{id}
    if (req.method === "DELETE" && sub === "exams" && examId) {
      const user = await requireAdmin(req, supabase);
      if (!user) return err("Unauthorized", 401);
      await supabase.from("exams").delete().eq("exam_id", examId);
      return ok({ deleted: true });
    }

    // POST /admin/exams/{id}/regenerate-audio
    if (req.method === "POST" && sub === "exams" && examId && action === "regenerate-audio") {
      const user = await requireAdmin(req, supabase);
      if (!user) return err("Unauthorized", 401);
      await supabase.from("exams").update({ status: "generating_audio", audio_progress: 0 }).eq("exam_id", examId);
      return ok({ status: "regenerating" });
    }

    return err("Not found", 404);
  } catch (e) {
    console.error(e);
    return err(String(e), 500);
  }
});
