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
  return createClient(Deno.env.get("SUPABASE_URL") ?? "", Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "");
}

async function getUser(req: Request, sb: ReturnType<typeof supabaseAdmin>) {
  const token = req.headers.get("Authorization")?.replace("Bearer ", "").trim();
  if (!token) return null;
  const { data } = await sb
    .from("user_sessions")
    .select("users(*)")
    .eq("session_token", token)
    .gt("expires_at", new Date().toISOString())
    .maybeSingle();
  return data ? (data.users as Record<string, unknown>) : null;
}

async function callOpenRouter(messages: unknown[], model = "google/gemini-3.5-flash", jsonMode = true): Promise<string> {
  const res = await fetch("https://openrouter.ai/api/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${Deno.env.get("OPENROUTER_API_KEY")}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model, messages, ...(jsonMode ? { response_format: { type: "json_object" } } : {}) }),
  });
  if (!res.ok) throw new Error(`OpenRouter ${res.status}: ${await res.text()}`);
  const d = await res.json();
  return d.choices[0].message.content;
}

async function generateExamAudio(examId: string, sb: ReturnType<typeof supabaseAdmin>) {
  const { data: exam } = await sb.from("exams").select("*").eq("exam_id", examId).maybeSingle();
  if (!exam) return;
  const examType = exam.exam_type ?? "ielts";
  if (examType === "telc") {
    await generateTelcAudio(examId, exam, sb);
  } else {
    await generateIeltsAudio(examId, exam, sb);
  }
}

async function generateTtsAudio(text: string, voice: string, lang = "en-GB"): Promise<Uint8Array | null> {
  try {
    const token = Deno.env.get("REPLICATE_API_TOKEN");
    if (!token) return null;
    const styleMap: Record<string, string> = {
      Fenrir: "Speak authoritatively with a clear, professional voice",
      Aoede: "Speak warmly and expressively with a natural British accent",
      Charon: "Speak steadily with a clear broadcaster tone",
      Kore: "Speak clearly like an educator",
      Orus: "Speak warmly with a storyteller quality",
      Puck: "Speak calmly with a British male accent",
      Rasalgethi: "Speak expressively like an academic",
    };
    const prompt = styleMap[voice] ?? "Speak naturally with a clear voice";

    const predRes = await fetch("https://api.replicate.com/v1/predictions", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: "google/gemini-3.1-flash-tts", input: { text, voice, prompt, language_code: lang } }),
    });
    let pred = await predRes.json();

    // Poll until done (max 60s)
    for (let i = 0; i < 30 && pred.status !== "succeeded" && pred.status !== "failed"; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const pollRes = await fetch(`https://api.replicate.com/v1/predictions/${pred.id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      pred = await pollRes.json();
    }
    if (pred.status !== "succeeded" || !pred.output) return null;
    const audioRes = await fetch(pred.output);
    return new Uint8Array(await audioRes.arrayBuffer());
  } catch {
    return null;
  }
}

async function saveAudio(
  sb: ReturnType<typeof supabaseAdmin>,
  audioId: string,
  examId: string,
  audioData: Uint8Array,
  audioType = "content",
) {
  const path = `${audioId}.mp3`;
  await sb.storage.from("audio-files").upload(path, audioData, { contentType: "audio/mpeg", upsert: true });
  await sb.from("audio_files").insert({ audio_id: audioId, exam_id: examId, storage_path: path, audio_type: audioType });
}

async function generateIeltsAudio(examId: string, exam: Record<string, unknown>, sb: ReturnType<typeof supabaseAdmin>) {
  const voices = ["Aoede", "Charon", "Kore", "Orus", "Puck"];
  let done = 0;
  const listening = (exam.listening as Record<string, unknown>) ?? {};
  const sections: unknown[] = (listening.sections as unknown[]) ?? [];
  const speaking = (exam.speaking as Record<string, unknown>) ?? {};
  const parts: unknown[] = (speaking.parts as unknown[]) ?? [];

  let total = 0;
  for (const sec of sections) {
    const s = sec as Record<string, unknown>;
    if (s.instruction) total++;
    total += ((s.script_segments as unknown[]) ?? []).length;
  }
  for (const part of parts) {
    total += ((part as Record<string, unknown>).questions as unknown[]).filter(
      (q) => (q as Record<string, unknown>).needs_audio,
    ).length;
  }

  const updateProgress = async (n: number) =>
    sb.from("exams").update({ audio_progress: Math.round((n / Math.max(total, 1)) * 100) }).eq("exam_id", examId);

  for (let si = 0; si < sections.length; si++) {
    const sec = sections[si] as Record<string, unknown>;
    const secNum = si + 1;

    if (sec.instruction && !sec.instruction_audio_id) {
      const audio = await generateTtsAudio(sec.instruction as string, "Fenrir");
      if (audio) {
        const aid = `audio_instr_${crypto.randomUUID().replace(/-/g, "").slice(0, 10)}`;
        await saveAudio(sb, aid, examId, audio, "instruction");
        const updated = JSON.parse(JSON.stringify(exam.listening));
        updated.sections[si].instruction_audio_id = aid;
        await sb.from("exams").update({ listening: updated }).eq("exam_id", examId);
        exam.listening = updated;
      }
      done++;
      await updateProgress(done);
    }

    const segs: unknown[] = (sec.script_segments as unknown[]) ?? [];
    const spkrs: unknown[] = (sec.speakers as unknown[]) ?? [];
    for (let segIdx = 0; segIdx < segs.length; segIdx++) {
      const seg = segs[segIdx] as Record<string, unknown>;
      if (seg.audio_id) { done++; continue; }
      const spkrName = seg.speaker as string;
      const spkrObj = spkrs.find((s) => (s as Record<string, unknown>).name === spkrName) as Record<string, unknown> | undefined;
      const voice = (spkrObj?.voice_id as string) ?? voices[segIdx % voices.length];
      const audio = await generateTtsAudio(seg.text as string, voice);
      if (audio) {
        const aid = `audio_seg_${examId}_${secNum}_${segIdx}_${crypto.randomUUID().replace(/-/g, "").slice(0, 6)}`;
        await saveAudio(sb, aid, examId, audio, "content");
        const updated = JSON.parse(JSON.stringify(exam.listening));
        updated.sections[si].script_segments[segIdx].audio_id = aid;
        await sb.from("exams").update({ listening: updated }).eq("exam_id", examId);
        exam.listening = updated;
      }
      done++;
      await updateProgress(done);
    }
  }

  for (let pi = 0; pi < parts.length; pi++) {
    const part = parts[pi] as Record<string, unknown>;
    const questions: unknown[] = (part.questions as unknown[]) ?? [];
    for (let qi = 0; qi < questions.length; qi++) {
      const q = questions[qi] as Record<string, unknown>;
      if (!q.needs_audio || q.audio_id) { if (q.needs_audio) { done++; } continue; }
      const audio = await generateTtsAudio(q.question_text as string, "Fenrir");
      if (audio) {
        const aid = `audio_spk_${examId}_${pi}_${qi}_${crypto.randomUUID().replace(/-/g, "").slice(0, 6)}`;
        await saveAudio(sb, aid, examId, audio, "speaking");
        const updated = JSON.parse(JSON.stringify(exam.speaking));
        updated.parts[pi].questions[qi].audio_id = aid;
        await sb.from("exams").update({ speaking: updated }).eq("exam_id", examId);
        exam.speaking = updated;
      }
      done++;
      await updateProgress(done);
    }
  }

  await sb.from("exams").update({ status: "ready", audio_progress: 100 }).eq("exam_id", examId);
}

async function generateTelcAudio(examId: string, exam: Record<string, unknown>, sb: ReturnType<typeof supabaseAdmin>) {
  const hoeren = (exam.hoeren as Record<string, unknown>) ?? {};
  const aufgaben: unknown[] = (hoeren.aufgaben as unknown[]) ?? [];
  let done = 0;
  let total = 0;
  for (const a of aufgaben) {
    total += ((a as Record<string, unknown>).ansagen as unknown[] ?? []).length;
    total += ((a as Record<string, unknown>).conversations as unknown[] ?? []).length;
  }

  for (let ai = 0; ai < aufgaben.length; ai++) {
    const aufgabe = aufgaben[ai] as Record<string, unknown>;
    const ansagen: unknown[] = (aufgabe.ansagen as unknown[]) ?? [];
    for (let idx = 0; idx < ansagen.length; idx++) {
      const ans = ansagen[idx] as Record<string, unknown>;
      if (ans.audio_id) { done++; continue; }
      const audio = await generateTtsAudio(ans.text as string, "Aoede", "de-DE");
      if (audio) {
        const aid = `audio_telc_${examId}_${ai}_${idx}_${crypto.randomUUID().replace(/-/g, "").slice(0, 6)}`;
        await saveAudio(sb, aid, examId, audio, "content");
        const updated = JSON.parse(JSON.stringify(exam.hoeren));
        updated.aufgaben[ai].ansagen[idx].audio_id = aid;
        await sb.from("exams").update({ hoeren: updated }).eq("exam_id", examId);
        exam.hoeren = updated;
      }
      done++;
      await sb.from("exams").update({ audio_progress: Math.round((done / Math.max(total, 1)) * 100) }).eq("exam_id", examId);
    }
    const convs: unknown[] = (aufgabe.conversations as unknown[]) ?? [];
    for (let cidx = 0; cidx < convs.length; cidx++) {
      const conv = convs[cidx] as Record<string, unknown>;
      if (conv.audio_id) { done++; continue; }
      const audio = await generateTtsAudio(conv.text as string, "Charon", "de-DE");
      if (audio) {
        const aid = `audio_telc_conv_${examId}_${ai}_${cidx}_${crypto.randomUUID().replace(/-/g, "").slice(0, 6)}`;
        await saveAudio(sb, aid, examId, audio, "content");
        const updated = JSON.parse(JSON.stringify(exam.hoeren));
        updated.aufgaben[ai].conversations[cidx].audio_id = aid;
        await sb.from("exams").update({ hoeren: updated }).eq("exam_id", examId);
        exam.hoeren = updated;
      }
      done++;
      await sb.from("exams").update({ audio_progress: Math.round((done / Math.max(total, 1)) * 100) }).eq("exam_id", examId);
    }
  }
  await sb.from("exams").update({ status: "ready", audio_progress: 100 }).eq("exam_id", examId);
}

function assignVoices(sections: unknown[]) {
  const femaleVoices = ["Aoede", "Kore"];
  const maleVoices = ["Charon", "Orus", "Puck"];
  let fi = 0; let mi = 0;
  for (const sec of sections) {
    const s = sec as Record<string, unknown>;
    for (const spk of (s.speakers as unknown[] ?? [])) {
      const sp = spk as Record<string, unknown>;
      if (!sp.voice_id) {
        const gender = (sp.gender as string ?? "").toLowerCase();
        sp.voice_id = gender === "female" ? femaleVoices[fi++ % femaleVoices.length] : maleVoices[mi++ % maleVoices.length];
      }
    }
  }
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 200, headers: corsHeaders });

  const url = new URL(req.url);
  const parts = url.pathname.split("/").filter(Boolean);
  const fnIdx = parts.indexOf("exams");
  const sub = parts[fnIdx + 1] ?? "";
  const sub2 = parts[fnIdx + 2] ?? "";
  const sb = supabaseAdmin();

  try {
    // GET /exams/progress
    if (req.method === "GET" && sub === "progress") {
      const user = await getUser(req, sb);
      if (!user) return err("Unauthorized", 401);
      const { data: attempts } = await sb
        .from("attempts")
        .select("module, scores, overall_band, status")
        .eq("user_id", user.user_id as string)
        .eq("status", "completed");
      const modules: Record<string, { attempts: number; latest_band: number | null }> = {
        listening: { attempts: 0, latest_band: null },
        reading: { attempts: 0, latest_band: null },
        writing: { attempts: 0, latest_band: null },
        speaking: { attempts: 0, latest_band: null },
      };
      for (const a of attempts ?? []) {
        const mod = (a.module as string)?.toLowerCase();
        if (mod in modules) {
          modules[mod].attempts++;
          const band = (a.scores as Record<string, unknown>)?.band_score ?? (a.scores as Record<string, unknown>)?.overall_band ?? a.overall_band;
          if (typeof band === "number") modules[mod].latest_band = band;
        }
      }
      const bands = Object.values(modules).map((m) => m.latest_band).filter((b): b is number => b !== null);
      return ok({ modules, overall_estimated_band: bands.length ? bands.reduce((a, b) => a + b, 0) / bands.length : null });
    }

    // GET /exams (list)
    if (req.method === "GET" && !sub) {
      const user = await getUser(req, sb);
      if (!user) return err("Unauthorized", 401);
      const { data } = await sb
        .from("exams")
        .select("exam_id, title, exam_type, telc_level, pathway, status, audio_progress, error_message, created_at")
        .order("created_at", { ascending: false });
      return ok(data ?? []);
    }

    // POST /exams/generate
    if (req.method === "POST" && sub === "generate" && !sub2) {
      const user = await getUser(req, sb);
      if (!user) return err("Unauthorized", 401);
      const examId = `exam_${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
      await sb.from("exams").insert({
        exam_id: examId, title: "Generating...", pathway: "academic", exam_type: "ielts",
        status: "generating_content", audio_progress: 0, created_by: user.user_id,
        listening: { sections: [], total_questions: 0, duration_minutes: 30 },
        reading: { passages: [], total_questions: 0, duration_minutes: 60 },
        writing: { tasks: [], total_time_minutes: 60 },
        speaking: { parts: [], total_time_minutes: 14 },
      });

      const generate = async () => {
        try {
          const listeningRaw = await callOpenRouter([
            { role: "system", content: "Generate realistic IELTS content. Return valid JSON only." },
            { role: "user", content: `Generate an IELTS Listening test with 4 sections, 10 questions each (40 total).
Each section needs:
1. "instruction": A brief instruction text like "You will hear a conversation between..."
2. "script_segments": Speaker turns with ElevenLabs V3 audio tags like [cheerful], [slowly], [pause]. For proper nouns, include spelling like "That's S-M-I-T-H".
3. "question_layout": A structured form/note layout matching real IELTS CBT format with inline {N} placeholders for blanks.
4. "questions": Array with question_num, question_type, correct_answer for scoring.

Section 1: Everyday conversation (2 speakers). Format as a FORM/NOTE with grouped headings and inline blanks {1} through {10}.
Section 2: Everyday monologue (1 speaker). Format as a structured NOTE with headings and inline blanks {11} through {20}.
Section 3: Educational discussion (2-3 speakers). Mix of matching and short_answer questions {21}-{30}.
Section 4: Academic lecture (1 speaker). Mix of sentence_completion and multiple_choice {31}-{40}.

Speakers use names: "Speaker A", "Speaker B", "Speaker C", "Lecturer"

Return JSON: {"sections": [{"section_num": 1, "title": "...", "context": "...", "instruction": "You will hear...",
"speakers": [{"name": "Speaker A", "role": "receptionist", "gender": "female"}],
"script_segments": [{"speaker": "Speaker A", "text": "[cheerful] Hello..."}],
"question_layout": {"title": "...", "instruction": "Complete the notes...", "groups": [{"heading": "...", "items": ["... {1} ..."]}]},
"questions": [{"question_num": 1, "question_type": "form_completion", "correct_answer": "Smith"}]}]}` },
          ]);
          const listeningData = JSON.parse(listeningRaw);
          assignVoices(listeningData.sections ?? []);

          const readingRaw = await callOpenRouter([
            { role: "system", content: "Generate realistic IELTS content. Return valid JSON only." },
            { role: "user", content: `Generate 3 IELTS Academic Reading passages (600-800 words each) with 13-14 questions each (40 total).
Topics: 1) Science/Technology 2) Social Science 3) Natural World
Question types: true_false_not_given, multiple_choice, matching_headings, sentence_completion, short_answer
Return JSON: {"passages": [{"passage_num": 1, "title": "...", "text": "...(full passage text)...",
"questions": [{"question_num": 1, "question_type": "true_false_not_given", "question_text": "...", "correct_answer": "True"}]}]}` },
          ]);
          const readingData = JSON.parse(readingRaw);
          let qNum = 1;
          for (const p of readingData.passages ?? []) {
            for (const q of p.questions ?? []) { q.question_num = qNum++; }
          }

          const writing = {
            total_time_minutes: 60,
            tasks: [
              { task_num: 1, task_type: "describe_visual", prompt: "The bar chart below shows the number of international students enrolled in three different faculties at a UK university from 2018 to 2023. Summarise the information by selecting and reporting the main features, and make comparisons where relevant. Write at least 150 words.", min_words: 150, time_minutes: 20 },
              { task_num: 2, task_type: "essay", prompt: "Some people believe that the best way to improve public health is by increasing the number of sports facilities. Others think this would have little effect and other measures are needed. Discuss both views and give your own opinion. Write at least 250 words.", min_words: 250, time_minutes: 40 },
            ],
          };

          const speaking = {
            total_time_minutes: 14,
            parts: [
              { part_num: 1, title: "Introduction and Interview", time_minutes: 5, instructions: "The examiner will ask you questions about familiar topics.", questions: [
                { question_num: 1, question_text: "Let's talk about where you live. Can you describe your neighbourhood?", needs_audio: true },
                { question_num: 2, question_text: "What do you like most about living there?", needs_audio: true },
                { question_num: 3, question_text: "Now let's talk about reading. How often do you read books?", needs_audio: true },
                { question_num: 4, question_text: "What kind of books do you enjoy reading?", needs_audio: true },
              ]},
              { part_num: 2, title: "Individual Long Turn", time_minutes: 4, preparation_time: 60, instructions: "You will have 1 minute to prepare, then speak for 1-2 minutes.", cue_card: "Describe a skill you learned that you found difficult at first.\nYou should say:\n- what the skill was\n- when you learned it\n- how you learned it\n- and explain why it was difficult at first", questions: [
                { question_num: 5, question_text: "Now I'd like you to talk about the following topic. You have one minute to prepare.", needs_audio: true },
              ]},
              { part_num: 3, title: "Two-way Discussion", time_minutes: 5, instructions: "The examiner will ask you more abstract questions related to Part 2.", questions: [
                { question_num: 6, question_text: "What skills do you think are most important for young people to learn today?", needs_audio: true },
                { question_num: 7, question_text: "Do you think schools should focus more on practical skills or academic knowledge?", needs_audio: true },
                { question_num: 8, question_text: "How has technology changed the way people learn new skills?", needs_audio: true },
              ]},
            ],
          };

          await sb.from("exams").update({
            title: `AI Practice Test — ${new Date().toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}`,
            listening: { ...listeningData, total_questions: 40, duration_minutes: 30 },
            reading: { ...readingData, total_questions: 40, duration_minutes: 60 },
            writing, speaking, status: "pending_audio",
          }).eq("exam_id", examId);

          await generateIeltsAudio(examId, { ...(await sb.from("exams").select("*").eq("exam_id", examId).maybeSingle()).data }, sb);
        } catch (e) {
          console.error("Exam generation error:", e);
          await sb.from("exams").update({ status: "error", error_message: String(e) }).eq("exam_id", examId);
        }
      };

      try { EdgeRuntime.waitUntil(generate()); } catch { generate(); }
      return ok({ exam_id: examId, status: "generating_content" });
    }

    // POST /exams/generate-telc
    if (req.method === "POST" && sub === "generate-telc") {
      const user = await getUser(req, sb);
      if (!user) return err("Unauthorized", 401);
      const body = await req.json();
      const level = body.level ?? "B1";
      const examId = `exam_telc_${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
      await sb.from("exams").insert({
        exam_id: examId, title: `TELC Deutsch ${level} — KI-generiert`, exam_type: "telc",
        telc_level: level, pathway: `telc_${level.toLowerCase()}`,
        status: "generating_content", audio_progress: 0, created_by: user.user_id,
        lesen: { aufgaben: [], total_questions: 15, duration_minutes: 90 },
        hoeren: { aufgaben: [], total_questions: 15, duration_minutes: 30 },
        schreiben: { aufgaben: [], total_time_minutes: 30 },
        sprechen: { teile: [], total_time_minutes: 15 },
        sprachbausteine: { aufgaben: [], total_questions: 10 },
      });

      const generate = async () => {
        try {
          const levelSpec = { A1: "very simple sentences, A1 vocabulary only", A2: "simple sentences, basic vocabulary", B1: "intermediate sentences, everyday topics", B2: "complex topics, formal register possible", C1: "advanced language, nuanced arguments" }[level] ?? "intermediate level";
          const sysPrompt = `You are a certified telc Deutsch ${level} exam author. Return valid JSON only — no markdown fences, no commentary. CRITICAL LEVEL ENFORCEMENT: ${levelSpec}`;

          const hoerenRaw = await callOpenRouter([
            { role: "system", content: sysPrompt },
            { role: "user", content: `Generate TELC Deutsch ${level} Hörverstehen (listening) with 3 Aufgaben:
Aufgabe 1: 5 short announcements (ansagen), each with a yes/no question (richtig/falsch). Each ansage has: text (spoken German), question_num (1-5), question_text, correct_answer ("Richtig" or "Falsch").
Aufgabe 2: One longer conversation (dialog) with 5 comprehension questions (question_num 6-10), multiple choice A/B/C.
Aufgabe 3: 5 short conversations (kurzgespräche), each with one yes/no question (question_num 11-15).
Return JSON: {"aufgaben": [{"aufgabe_num": 1, "type": "ansagen", "title": "...", "ansagen": [{"text": "...", "question_num": 1, "question_text": "...", "correct_answer": "Richtig"}]}, {"aufgabe_num": 2, "type": "dialog", "title": "...", "conversations": [{"speaker": "...", "text": "..."}], "questions": [{"question_num": 6, "question_text": "...", "options": {"A": "...", "B": "...", "C": "..."}, "correct_answer": "A"}]}, {"aufgabe_num": 3, "type": "kurzgespraeche", "title": "...", "conversations": [{"text": "...", "question_num": 11, "question_text": "...", "correct_answer": "Richtig"}]}]}` },
          ]);

          const lesenRaw = await callOpenRouter([
            { role: "system", content: sysPrompt },
            { role: "user", content: `Generate TELC Deutsch ${level} Leseverstehen (reading) with 3 Aufgaben:
Aufgabe 1: A text followed by 5 true/false/not mentioned questions (question_num 1-5, correct_answer: "Richtig"/"Falsch"/"Nicht im Text").
Aufgabe 2: 5 short texts, match each to a heading from a list (question_num 6-10, correct_answer is the heading letter A-F).
Aufgabe 3: A longer text with 5 fill-in-the-blank questions (question_num 11-15, multiple choice A/B/C).
Return JSON: {"aufgaben": [{"aufgabe_num": 1, "type": "richtig_falsch", "title": "...", "text": "...", "questions": [{"question_num": 1, "question_text": "...", "correct_answer": "Richtig"}]}, {"aufgabe_num": 2, "type": "zuordnung", "title": "...", "texts": [{"id": "A", "text": "..."}], "headings": {"A": "...", "B": "...", "C": "...", "D": "...", "E": "...", "F": "..."}, "questions": [{"question_num": 6, "text_id": "A", "correct_answer": "C"}]}, {"aufgabe_num": 3, "type": "lueckentext", "title": "...", "text": "...", "questions": [{"question_num": 11, "question_text": "...", "options": {"A": "...", "B": "...", "C": "..."}, "correct_answer": "A"}]}]}` },
          ]);

          const sprachbausteineRaw = await callOpenRouter([
            { role: "system", content: sysPrompt },
            { role: "user", content: `Generate TELC Deutsch ${level} Sprachbausteine with 1 Aufgabe:
A text with 10 gaps (question_num 1-10). For each gap, provide 3 options (A/B/C) and the correct answer.
Return JSON: {"aufgaben": [{"aufgabe_num": 1, "type": "sprachbausteine", "title": "...", "text": "...(with [1] markers)...", "options": [{"question_num": 1, "options": {"A": "...", "B": "...", "C": "..."}, "correct_answer": "A"}]}]}` },
          ]);

          const schreiben = {
            aufgaben: [{
              aufgabe_num: 1,
              type: "brief_email",
              title: `Schreiben — ${level} Brief/E-Mail`,
              aufgabe: level === "A1" || level === "A2"
                ? "Schreiben Sie eine kurze Nachricht (30-50 Wörter) an Ihren Freund/Ihre Freundin. Berichten Sie über Ihr Wochenende."
                : level === "B1"
                  ? "Schreiben Sie einen Brief (80-100 Wörter) an Ihre Nachbarin. Es gab Probleme mit dem Lärm. Beschreiben Sie das Problem und machen Sie einen Vorschlag."
                  : "Schreiben Sie eine formelle E-Mail (150-200 Wörter) an Ihren Arbeitgeber. Beantragen Sie Urlaub und begründen Sie Ihren Wunsch ausführlich.",
              min_words: level === "A1" ? 30 : level === "A2" ? 40 : level === "B1" ? 80 : 150,
            }],
          };

          const sprechen = {
            teile: [
              { teil_num: 1, title: "Sich vorstellen", instructions: `Stellen Sie sich kurz vor. Sagen Sie etwas über sich: Name, Herkunft, Beruf, Hobbys.`, time_minutes: 3 },
              { teil_num: 2, title: "Über ein Thema sprechen", instructions: `Sprechen Sie über das folgende Thema: "${level === "B1" ? "Freizeit und Hobbys in der heutigen Zeit" : level === "B2" ? "Die Rolle der Technologie im modernen Alltag" : "Umweltbewusstsein im Alltag"}"`, time_minutes: 4 },
              { teil_num: 3, title: "Gemeinsam etwas planen", instructions: "Planen Sie gemeinsam mit Ihrem Gesprächspartner ein Ereignis (z.B. ein Ausflug, ein Fest, ein Treffen).", time_minutes: 3 },
            ],
          };

          await sb.from("exams").update({
            hoeren: JSON.parse(hoerenRaw),
            lesen: JSON.parse(lesenRaw),
            sprachbausteine: JSON.parse(sprachbausteineRaw),
            schreiben, sprechen,
            status: "pending_audio",
          }).eq("exam_id", examId);

          const { data: freshExam } = await sb.from("exams").select("*").eq("exam_id", examId).maybeSingle();
          if (freshExam) await generateTelcAudio(examId, freshExam as Record<string, unknown>, sb);
        } catch (e) {
          console.error("TELC generation error:", e);
          await sb.from("exams").update({ status: "error", error_message: String(e) }).eq("exam_id", examId);
        }
      };

      try { EdgeRuntime.waitUntil(generate()); } catch { generate(); }
      return ok({ exam_id: examId, status: "generating_content" });
    }

    // Remaining routes need an exam ID: GET/DELETE /exams/{id}, GET /exams/{id}/status, POST /exams/{id}/prepare
    const examId = sub;
    if (!examId) return err("Not found", 404);

    if (req.method === "GET" && !sub2) {
      const user = await getUser(req, sb);
      if (!user) return err("Unauthorized", 401);
      const { data } = await sb.from("exams").select("*").eq("exam_id", examId).maybeSingle();
      if (!data) return err("Not found", 404);
      return ok(data);
    }

    if (req.method === "GET" && sub2 === "status") {
      const user = await getUser(req, sb);
      if (!user) return err("Unauthorized", 401);
      const { data } = await sb.from("exams").select("status, audio_progress, error_message").eq("exam_id", examId).maybeSingle();
      if (!data) return err("Not found", 404);
      return ok(data);
    }

    if (req.method === "POST" && sub2 === "prepare") {
      const user = await getUser(req, sb);
      if (!user) return err("Unauthorized", 401);
      const { data: exam } = await sb.from("exams").select("*").eq("exam_id", examId).maybeSingle();
      if (!exam) return err("Not found", 404);
      await sb.from("exams").update({ status: "generating_audio", audio_progress: 0 }).eq("exam_id", examId);
      const prepare = async () => { await generateExamAudio(examId, sb); };
      try { EdgeRuntime.waitUntil(prepare()); } catch { prepare(); }
      return ok({ status: "generating_audio" });
    }

    if (req.method === "DELETE") {
      const user = await getUser(req, sb);
      if (!user) return err("Unauthorized", 401);
      await sb.from("exams").delete().eq("exam_id", examId);
      return ok({ deleted: true });
    }

    return err("Not found", 404);
  } catch (e) {
    console.error(e);
    return err(String(e), 500);
  }
});
