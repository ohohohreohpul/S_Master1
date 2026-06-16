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

async function callOpenRouter(messages: unknown[], model = "openai/gpt-4o", jsonMode = true): Promise<string> {
  const res = await fetch("https://openrouter.ai/api/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${Deno.env.get("OPENROUTER_API_KEY")}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model, messages, ...(jsonMode ? { response_format: { type: "json_object" } } : {}) }),
  });
  if (!res.ok) throw new Error(`OpenRouter ${res.status}: ${await res.text()}`);
  const d = await res.json();
  return d.choices[0].message.content;
}

function normalizeAnswer(text: string): string {
  if (!text) return "";
  let t = String(text).trim().toLowerCase().replace(/\s+/g, " ");
  for (const c of ".,;:!?$()[]{}") t = t.split(c).join("");
  return t;
}
function answersMatch(user: string, correct: string): boolean {
  const u = normalizeAnswer(user);
  if (!u) return false;
  for (const c of String(correct).split("|")) {
    const cn = normalizeAnswer(c);
    if (!cn) continue;
    if (u === cn) return true;
    if (u.replace(/\s/g, "") === cn.replace(/\s/g, "")) return true;
    try { if (parseFloat(u) === parseFloat(cn)) return true; } catch { /* ok */ }
  }
  return false;
}

function rawToBand(correct: number, total: number, module: string): number {
  if (total === 0) return 0;
  const bl = [[39, 9.0],[37, 8.5],[35, 8.0],[33, 7.5],[30, 7.0],[27, 6.5],[23, 6.0],[20, 5.5],[16, 5.0],[13, 4.5],[10, 4.0],[6, 3.5],[4, 3.0]] as [number, number][];
  const br = [[39, 9.0],[37, 8.5],[35, 8.0],[33, 7.5],[30, 7.0],[27, 6.5],[23, 6.0],[19, 5.5],[15, 5.0],[13, 4.5],[10, 4.0],[8, 3.5],[6, 3.0]] as [number, number][];
  const bands = module === "listening" ? bl : br;
  for (const [threshold, band] of bands) if (correct >= threshold) return band;
  return 2.0;
}

function scoreObjective(exam: Record<string, unknown>, module: string, answers: Record<string, string>) {
  let correct = 0; let total = 0;
  const details: unknown[] = [];
  const key = module === "listening" ? "sections" : "passages";
  const sections: unknown[] = ((exam[module] as Record<string, unknown>)?.[key] as unknown[]) ?? [];
  for (const sec of sections) {
    for (const q of ((sec as Record<string, unknown>).questions as unknown[] ?? [])) {
      const qq = q as Record<string, unknown>;
      total++;
      const userAns = String(answers[String(qq.question_num)] ?? "");
      const isCorrect = answersMatch(userAns, String(qq.correct_answer ?? ""));
      if (isCorrect) correct++;
      details.push({ question_num: Number(qq.question_num), user_answer: userAns, correct_answer: qq.correct_answer, is_correct: isCorrect });
    }
  }
  return { correct, total, band_score: rawToBand(correct, total, module), details };
}

function scoreTelcObjective(exam: Record<string, unknown>, module: string, answers: Record<string, string>) {
  let correct = 0; let total = 0;
  const details: unknown[] = [];
  const aufgaben: unknown[] = ((exam[module] as Record<string, unknown>)?.aufgaben as unknown[]) ?? [];
  for (const aufgabe of aufgaben) {
    const a = aufgabe as Record<string, unknown>;
    for (const q of (a.questions as unknown[] ?? [])) {
      const qq = q as Record<string, unknown>;
      total++;
      const userAns = String(answers[String(qq.question_num)] ?? "");
      const isCorrect = answersMatch(userAns, String(qq.correct_answer ?? ""));
      if (isCorrect) correct++;
      details.push({ question_num: Number(qq.question_num), user_answer: userAns, correct_answer: qq.correct_answer, is_correct: isCorrect });
    }
    for (const opt of (a.options as unknown[] ?? [])) {
      const oo = opt as Record<string, unknown>;
      total++;
      const userAns = String(answers[String(oo.question_num)] ?? "");
      const isCorrect = answersMatch(userAns, String(oo.correct_answer ?? ""));
      if (isCorrect) correct++;
      details.push({ question_num: Number(oo.question_num), user_answer: userAns, correct_answer: oo.correct_answer, is_correct: isCorrect });
    }
    for (const ansage of (a.ansagen as unknown[] ?? [])) {
      const ans = ansage as Record<string, unknown>;
      if ("question_num" in ans) {
        total++;
        const userAns = String(answers[String(ans.question_num)] ?? "");
        const isCorrect = answersMatch(userAns, String(ans.correct_answer ?? ""));
        if (isCorrect) correct++;
        details.push({ question_num: Number(ans.question_num), user_answer: userAns, correct_answer: ans.correct_answer, is_correct: isCorrect });
      }
    }
    for (const conv of (a.conversations as unknown[] ?? [])) {
      const cv = conv as Record<string, unknown>;
      for (const q of (cv.questions as unknown[] ?? [])) {
        const qq = q as Record<string, unknown>;
        total++;
        const userAns = String(answers[String(qq.question_num)] ?? "");
        const isCorrect = answersMatch(userAns, String(qq.correct_answer ?? ""));
        if (isCorrect) correct++;
        details.push({ question_num: Number(qq.question_num), user_answer: userAns, correct_answer: qq.correct_answer, is_correct: isCorrect });
      }
    }
  }
  const percentage = total > 0 ? Math.round((correct / total) * 1000) / 10 : 0;
  return { correct, total, percentage, band_score: (percentage / 100) * 9, passed: percentage >= 60, details };
}

const IELTS_FULL_TEST_ORDER = ["listening", "reading", "writing", "speaking"];
const TELC_FULL_TEST_ORDER = ["listening", "reading", "sprachbausteine", "writing", "speaking"];

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 200, headers: corsHeaders });

  const url = new URL(req.url);
  const parts = url.pathname.split("/").filter(Boolean);
  const fnIdx = parts.indexOf("attempts");
  const sub = parts[fnIdx + 1] ?? "";      // attempt_id OR "full-test"
  const sub2 = parts[fnIdx + 2] ?? "";     // "submit", "score-writing", etc.
  const sub3 = parts[fnIdx + 3] ?? "";     // for full-test sub-routes

  const supabase = sb();

  try {
    // POST /attempts/full-test  — start full test
    if (req.method === "POST" && sub === "full-test" && !sub2) {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const { exam_id } = await req.json();
      const attemptId = `attempt_${crypto.randomUUID().replace(/-/g, "").slice(0, 10)}`;
      await supabase.from("attempts").insert({
        attempt_id: attemptId, user_id: user.user_id, exam_id,
        module: "full_test", mode: "full_test", status: "in_progress",
        answers: {}, module_answers: {}, module_scores: {}, modules_completed: [],
        current_module: "listening",
      });
      return ok({ attempt_id: attemptId });
    }

    // POST /attempts  — start single module attempt
    if (req.method === "POST" && !sub) {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const { exam_id, module } = await req.json();
      const attemptId = `attempt_${crypto.randomUUID().replace(/-/g, "").slice(0, 10)}`;
      await supabase.from("attempts").insert({
        attempt_id: attemptId, user_id: user.user_id, exam_id, module, status: "in_progress", answers: {}, module_scores: {}, module_answers: {}, modules_completed: [],
      });
      return ok({ attempt_id: attemptId });
    }

    // GET /attempts/{id}
    if (req.method === "GET" && sub && !sub2) {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const { data } = await supabase.from("attempts").select("*, exams(*)").eq("attempt_id", sub).maybeSingle();
      if (!data) return err("Not found", 404);
      return ok(data);
    }

    const attemptId = sub;
    if (!attemptId) return err("Not found", 404);

    // PUT /attempts/{id}/submit  — submit objective (listening/reading)
    if (req.method === "PUT" && sub2 === "submit") {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const { answers } = await req.json();
      const { data: attempt } = await supabase.from("attempts").select("*").eq("attempt_id", attemptId).eq("user_id", user.user_id).maybeSingle();
      if (!attempt) return err("Attempt not found", 404);
      const { data: exam } = await supabase.from("exams").select("*").eq("exam_id", attempt.exam_id).maybeSingle();
      const module = attempt.module as string;
      const examType = (exam?.exam_type as string) ?? "ielts";
      let scores: unknown = null;
      if (examType === "telc") {
        const telcMap: Record<string, string> = { listening: "hoeren", reading: "lesen", sprachbausteine: "sprachbausteine" };
        const telcModule = telcMap[module] ?? module;
        if (["listening", "reading", "sprachbausteine"].includes(module)) {
          scores = scoreTelcObjective(exam as Record<string, unknown>, telcModule, answers);
        }
      } else if (["listening", "reading"].includes(module)) {
        scores = scoreObjective(exam as Record<string, unknown>, module, answers);
      }
      await supabase.from("attempts").update({ answers, scores, completed_at: new Date().toISOString(), status: "completed" }).eq("attempt_id", attemptId);
      return ok({ attempt_id: attemptId, scores });
    }

    // POST /attempts/{id}/score-writing
    if (req.method === "POST" && sub2 === "score-writing") {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const { task_1, task_2 } = await req.json();
      const { data: attempt } = await supabase.from("attempts").select("*").eq("attempt_id", attemptId).eq("user_id", user.user_id).maybeSingle();
      if (!attempt) return err("Attempt not found", 404);
      const { data: exam } = await supabase.from("exams").select("writing").eq("exam_id", attempt.exam_id).maybeSingle();
      const tasks = (exam?.writing as Record<string, unknown>)?.tasks as unknown[] ?? [];
      const t1p = (tasks[0] as Record<string, unknown>)?.prompt as string ?? "";
      const t2p = (tasks[1] as Record<string, unknown>)?.prompt as string ?? "";
      const prompt = `You are an experienced IELTS Writing examiner. Score these responses using official IELTS Band Descriptors. Scores as multiples of 0.5.

TASK 1 PROMPT: ${t1p}
TASK 1 (${task_1?.split(" ").length ?? 0} words): ${task_1}

TASK 2 PROMPT: ${t2p}
TASK 2 (${task_2?.split(" ").length ?? 0} words): ${task_2}

Return JSON: {"task_1":{"task_achievement":{"band":6.0,"feedback":"..."},"coherence_cohesion":{"band":6.0,"feedback":"..."},"lexical_resource":{"band":6.0,"feedback":"..."},"grammatical_range":{"band":6.0,"feedback":"..."},"overall_band":6.0,"general_feedback":"..."},"task_2":{"task_achievement":{"band":6.0,"feedback":"..."},"coherence_cohesion":{"band":6.0,"feedback":"..."},"lexical_resource":{"band":6.0,"feedback":"..."},"grammatical_range":{"band":6.0,"feedback":"..."},"overall_band":6.0,"general_feedback":"..."},"overall_writing_band":6.0}`;
      const scores = JSON.parse(await callOpenRouter([{ role: "system", content: "You are an IELTS examiner. Return only valid JSON." }, { role: "user", content: prompt }]));
      await supabase.from("attempts").update({ answers: { task_1, task_2 }, scores, completed_at: new Date().toISOString(), status: "completed" }).eq("attempt_id", attemptId);
      return ok({ attempt_id: attemptId, scores });
    }

    // POST /attempts/{id}/score-telc-writing
    if (req.method === "POST" && sub2 === "score-telc-writing") {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const { aufgabe_1 } = await req.json();
      const { data: attempt } = await supabase.from("attempts").select("*").eq("attempt_id", attemptId).eq("user_id", user.user_id).maybeSingle();
      if (!attempt) return err("Attempt not found", 404);
      const { data: exam } = await supabase.from("exams").select("schreiben, telc_level").eq("exam_id", attempt.exam_id).maybeSingle();
      const level = (exam?.telc_level as string) ?? "B1";
      const aufgaben = (exam?.schreiben as Record<string, unknown>)?.aufgaben as unknown[] ?? [];
      const taskPrompt = (aufgaben[0] as Record<string, unknown>)?.aufgabe as string ?? "";
      const prompt = `You are a certified TELC Deutsch ${level} examiner. Score this writing response.

TASK: ${taskPrompt}
RESPONSE (${aufgabe_1?.split(" ").length ?? 0} words): ${aufgabe_1}

TELC ${level} writing criteria: Communicative achievement, Organization, Language range & accuracy. Max 30 points total (10 per criterion).

Return JSON: {"kommunikative_aufgabe":{"punkte":8,"feedback":"..."},"textaufbau":{"punkte":8,"feedback":"..."},"sprachliche_mittel":{"punkte":8,"feedback":"..."},"gesamt_punkte":24,"bestanden":true,"allgemeines_feedback":"..."}`;
      const scores = JSON.parse(await callOpenRouter([{ role: "system", content: "You are a TELC examiner. Return only valid JSON." }, { role: "user", content: prompt }]));
      await supabase.from("attempts").update({ answers: { aufgabe_1 }, scores, completed_at: new Date().toISOString(), status: "completed" }).eq("attempt_id", attemptId);
      return ok({ attempt_id: attemptId, scores });
    }

    // POST /attempts/{id}/score-speaking
    if (req.method === "POST" && sub2 === "score-speaking") {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const { transcriptions } = await req.json();
      const { data: attempt } = await supabase.from("attempts").select("*").eq("attempt_id", attemptId).eq("user_id", user.user_id).maybeSingle();
      if (!attempt) return err("Attempt not found", 404);
      const { data: exam } = await supabase.from("exams").select("speaking").eq("exam_id", attempt.exam_id).maybeSingle();
      const questions: Record<string, string> = {};
      for (const part of ((exam?.speaking as Record<string, unknown>)?.parts as unknown[] ?? [])) {
        for (const q of ((part as Record<string, unknown>).questions as unknown[] ?? [])) {
          const qq = q as Record<string, unknown>;
          questions[String(qq.question_num)] = String(qq.question_text ?? "");
        }
      }
      const prompt = `You are an IELTS Speaking examiner. Score these transcribed responses. Scores as multiples of 0.5.
Questions: ${JSON.stringify(questions)}
Responses: ${JSON.stringify(transcriptions)}
Return JSON: {"fluency_coherence":{"band":6.0,"feedback":"..."},"lexical_resource":{"band":6.0,"feedback":"..."},"grammatical_range":{"band":6.0,"feedback":"..."},"pronunciation":{"band":6.0,"feedback":"..."},"overall_band":6.0,"general_feedback":"...","part_feedback":{"part_1":"...","part_2":"...","part_3":"..."}}`;
      const scores = JSON.parse(await callOpenRouter([{ role: "system", content: "You are an IELTS examiner. Return only valid JSON." }, { role: "user", content: prompt }]));
      await supabase.from("attempts").update({ answers: { transcriptions }, scores, completed_at: new Date().toISOString(), status: "completed" }).eq("attempt_id", attemptId);
      return ok({ attempt_id: attemptId, scores });
    }

    // ── Full-test sub-routes ──────────────────────────────────────────────────

    // PUT /attempts/{id}/full-test/module
    if (req.method === "PUT" && sub2 === "full-test" && sub3 === "module") {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const { module, answers } = await req.json();
      const { data: attempt } = await supabase.from("attempts").select("*").eq("attempt_id", attemptId).eq("user_id", user.user_id).maybeSingle();
      if (!attempt) return err("Attempt not found", 404);
      const { data: exam } = await supabase.from("exams").select("*").eq("exam_id", attempt.exam_id).maybeSingle();
      const examType = (exam?.exam_type as string) ?? "ielts";
      const order = examType === "telc" ? TELC_FULL_TEST_ORDER : IELTS_FULL_TEST_ORDER;

      let scores: unknown = null;
      if (["listening", "reading"].includes(module)) {
        scores = examType === "telc"
          ? scoreTelcObjective(exam as Record<string, unknown>, module === "listening" ? "hoeren" : "lesen", answers)
          : scoreObjective(exam as Record<string, unknown>, module, answers);
      } else if (module === "sprachbausteine") {
        scores = scoreTelcObjective(exam as Record<string, unknown>, "sprachbausteine", answers);
      }

      const completed: string[] = [...((attempt.modules_completed as string[]) ?? []), module];
      const currentIdx = order.indexOf(module);
      const nextModule = order[currentIdx + 1] ?? null;

      const moduleScores = { ...((attempt.module_scores as Record<string, unknown>) ?? {}), ...(scores ? { [module]: scores } : {}) };
      const moduleAnswers = { ...((attempt.module_answers as Record<string, unknown>) ?? {}), [module]: answers };

      if (!nextModule) {
        // Compute overall band
        const bands = Object.values(moduleScores).map((s) => (s as Record<string, unknown>).band_score as number).filter((b) => typeof b === "number");
        const overallBand = bands.length ? bands.reduce((a, b) => a + b, 0) / bands.length : null;
        await supabase.from("attempts").update({ module_scores: moduleScores, module_answers: moduleAnswers, modules_completed: completed, status: "completed", completed_at: new Date().toISOString(), overall_band: overallBand, current_module: null }).eq("attempt_id", attemptId);
      } else {
        await supabase.from("attempts").update({ module_scores: moduleScores, module_answers: moduleAnswers, modules_completed: completed, current_module: nextModule }).eq("attempt_id", attemptId);
      }
      return ok({ next_module: nextModule, scores });
    }

    // POST /attempts/{id}/full-test/score-writing
    if (req.method === "POST" && sub2 === "full-test" && sub3 === "score-writing") {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const { task_1, task_2 } = await req.json();
      const { data: attempt } = await supabase.from("attempts").select("*").eq("attempt_id", attemptId).eq("user_id", user.user_id).maybeSingle();
      if (!attempt) return err("Attempt not found", 404);
      const { data: exam } = await supabase.from("exams").select("writing").eq("exam_id", attempt.exam_id).maybeSingle();
      const tasks = (exam?.writing as Record<string, unknown>)?.tasks as unknown[] ?? [];
      const t1p = (tasks[0] as Record<string, unknown>)?.prompt as string ?? "";
      const t2p = (tasks[1] as Record<string, unknown>)?.prompt as string ?? "";
      const prompt = `You are an experienced IELTS Writing examiner. Score these responses using official IELTS Band Descriptors. Scores as multiples of 0.5.
TASK 1 PROMPT: ${t1p}
TASK 1: ${task_1}
TASK 2 PROMPT: ${t2p}
TASK 2: ${task_2}
Return JSON: {"task_1":{"task_achievement":{"band":6.0,"feedback":"..."},"coherence_cohesion":{"band":6.0,"feedback":"..."},"lexical_resource":{"band":6.0,"feedback":"..."},"grammatical_range":{"band":6.0,"feedback":"..."},"overall_band":6.0,"general_feedback":"..."},"task_2":{"task_achievement":{"band":6.0,"feedback":"..."},"coherence_cohesion":{"band":6.0,"feedback":"..."},"lexical_resource":{"band":6.0,"feedback":"..."},"grammatical_range":{"band":6.0,"feedback":"..."},"overall_band":6.0,"general_feedback":"..."},"overall_writing_band":6.0}`;
      const scores = JSON.parse(await callOpenRouter([{ role: "system", content: "You are an IELTS examiner. Return only valid JSON." }, { role: "user", content: prompt }]));
      const moduleScores = { ...((attempt.module_scores as Record<string, unknown>) ?? {}), writing: scores };
      const moduleAnswers = { ...((attempt.module_answers as Record<string, unknown>) ?? {}), writing: { task_1, task_2 } };
      const completed = [...((attempt.modules_completed as string[]) ?? []), "writing"];
      await supabase.from("attempts").update({ module_scores: moduleScores, module_answers: moduleAnswers, modules_completed: completed, current_module: "speaking" }).eq("attempt_id", attemptId);
      return ok({ scores });
    }

    // POST /attempts/{id}/full-test/score-speaking
    if (req.method === "POST" && sub2 === "full-test" && sub3 === "score-speaking") {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const { transcriptions } = await req.json();
      const { data: attempt } = await supabase.from("attempts").select("*").eq("attempt_id", attemptId).eq("user_id", user.user_id).maybeSingle();
      if (!attempt) return err("Attempt not found", 404);
      const { data: exam } = await supabase.from("exams").select("speaking").eq("exam_id", attempt.exam_id).maybeSingle();
      const questions: Record<string, string> = {};
      for (const part of ((exam?.speaking as Record<string, unknown>)?.parts as unknown[] ?? [])) {
        for (const q of ((part as Record<string, unknown>).questions as unknown[] ?? [])) {
          const qq = q as Record<string, unknown>;
          questions[String(qq.question_num)] = String(qq.question_text ?? "");
        }
      }
      const prompt = `You are an IELTS Speaking examiner. Score these transcribed responses. Scores as multiples of 0.5.
Questions: ${JSON.stringify(questions)}
Responses: ${JSON.stringify(transcriptions)}
Return JSON: {"fluency_coherence":{"band":6.0,"feedback":"..."},"lexical_resource":{"band":6.0,"feedback":"..."},"grammatical_range":{"band":6.0,"feedback":"..."},"pronunciation":{"band":6.0,"feedback":"..."},"overall_band":6.0,"general_feedback":"...","part_feedback":{"part_1":"...","part_2":"...","part_3":"..."}}`;
      const scores = JSON.parse(await callOpenRouter([{ role: "system", content: "You are an IELTS examiner. Return only valid JSON." }, { role: "user", content: prompt }]));
      const moduleScores = { ...((attempt.module_scores as Record<string, unknown>) ?? {}), speaking: scores };
      const moduleAnswers = { ...((attempt.module_answers as Record<string, unknown>) ?? {}), speaking: { transcriptions } };
      const completed = [...((attempt.modules_completed as string[]) ?? []), "speaking"];
      const bands = Object.values(moduleScores).map((s) => (s as Record<string, unknown>).band_score ?? (s as Record<string, unknown>).overall_band).filter((b): b is number => typeof b === "number");
      const overallBand = bands.length ? bands.reduce((a, b) => a + b, 0) / bands.length : null;
      await supabase.from("attempts").update({ module_scores: moduleScores, module_answers: moduleAnswers, modules_completed: completed, status: "completed", completed_at: new Date().toISOString(), overall_band: overallBand, current_module: null }).eq("attempt_id", attemptId);
      return ok({ scores, overall_band: overallBand });
    }

    return err("Not found", 404);
  } catch (e) {
    console.error(e);
    return err(String(e), 500);
  }
});
