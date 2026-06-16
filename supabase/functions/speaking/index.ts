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

async function callOpenRouter(messages: unknown[], model = "openai/gpt-4o", jsonMode = false): Promise<string> {
  const res = await fetch("https://openrouter.ai/api/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${Deno.env.get("OPENROUTER_API_KEY")}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model, messages, ...(jsonMode ? { response_format: { type: "json_object" } } : {}) }),
  });
  if (!res.ok) throw new Error(`OpenRouter ${res.status}: ${await res.text()}`);
  const d = await res.json();
  return d.choices[0].message.content;
}

async function generateTtsAudio(text: string, voice: string, lang = "en-GB"): Promise<string | null> {
  try {
    const token = Deno.env.get("REPLICATE_API_TOKEN");
    if (!token) return null;
    const styleMap: Record<string, string> = { Fenrir: "Speak authoritatively and professionally", Aoede: "Speak warmly and expressively" };
    const prompt = styleMap[voice] ?? "Speak naturally";
    const predRes = await fetch("https://api.replicate.com/v1/predictions", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: "google/gemini-3.1-flash-tts", input: { text, voice, prompt, language_code: lang } }),
    });
    let pred = await predRes.json();
    for (let i = 0; i < 30 && pred.status !== "succeeded" && pred.status !== "failed"; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const p = await fetch(`https://api.replicate.com/v1/predictions/${pred.id}`, { headers: { Authorization: `Bearer ${token}` } });
      pred = await p.json();
    }
    if (pred.status !== "succeeded" || !pred.output) return null;
    const audioRes = await fetch(pred.output);
    const buf = await audioRes.arrayBuffer();
    return `data:audio/mpeg;base64,${btoa(String.fromCharCode(...new Uint8Array(buf)))}`;
  } catch {
    return null;
  }
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 200, headers: corsHeaders });

  const url = new URL(req.url);
  const parts = url.pathname.split("/").filter(Boolean);
  const fnIdx = parts.indexOf("speaking");
  const sub = parts[fnIdx + 1] ?? "";
  const supabase = sb();

  try {
    // POST /speaking/converse
    if (req.method === "POST" && sub === "converse") {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const body = await req.json();
      const { part_num = 1, user_transcription = "", conversation_history = [], cue_card = "", action = "respond" } = body;

      const partInstructions: Record<number, string> = {
        1: "Part 1 (Introduction & Interview, 4-5 min): Ask about familiar topics like home, work, hobbies. Ask one question at a time. Keep follow-ups natural and brief.",
        2: `Part 2 (Long Turn): The candidate was given this cue card:\n${cue_card}\nAfter they finish speaking, ask 1-2 brief follow-up questions about what they said.`,
        3: "Part 3 (Discussion, 4-5 min): Ask abstract/analytical questions building on Part 2 topic. Probe deeper into the candidate's views. One question at a time.",
      };

      const systemPrompt = `You are a professional IELTS Speaking examiner named Daniel. You are conducting Part ${part_num} of the IELTS Speaking test.

Rules:
- Be professional, warm, and encouraging
- Ask ONE question at a time (never multiple)
- Keep your responses brief (1-2 sentences max for follow-ups)
- Sound natural, like a real conversation
- ${partInstructions[part_num] ?? ""}
- If action is "start", give an appropriate opening for this part
- Do NOT use any text formatting or special characters
- Respond in plain conversational English`;

      const messages: unknown[] = [{ role: "system", content: systemPrompt }];
      for (const h of conversation_history) {
        if (h?.text) messages.push({ role: h.role, content: h.text });
      }
      if (user_transcription) messages.push({ role: "user", content: user_transcription });
      if (action === "start" && !user_transcription) {
        messages.push({ role: "user", content: `[System: Begin Part ${part_num}. Greet the candidate and ask your first question.]` });
      }

      const examinerText = (await callOpenRouter(messages)).trim().replace(/^["']|["']$/g, "");
      const audiob64 = await generateTtsAudio(examinerText, "Fenrir");

      const newHistory = [...conversation_history];
      if (user_transcription) newHistory.push({ role: "user", text: user_transcription });
      newHistory.push({ role: "assistant", text: examinerText });

      return ok({ examiner_text: examinerText, audio_base64: audiob64, conversation_history: newHistory });
    }

    // POST /speaking/transcribe
    if (req.method === "POST" && sub === "transcribe") {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const formData = await req.formData();
      const audioFile = formData.get("audio_file") as File | null;
      if (!audioFile) return err("audio_file required");
      const audioBytes = new Uint8Array(await audioFile.arrayBuffer());
      if (audioBytes.length < 100) return err("Audio file too small");

      const token = Deno.env.get("REPLICATE_API_TOKEN");
      if (!token) return ok({ text: "", word_count: 0 });

      const predRes = await fetch("https://api.replicate.com/v1/predictions", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ model: "openai/whisper", input: { model: "large-v3" } }),
      });
      const pred = await predRes.json();

      // Upload audio file separately
      const uploadForm = new FormData();
      uploadForm.append("content", new Blob([audioBytes], { type: "audio/webm" }), "audio.webm");
      await fetch(`https://api.replicate.com/v1/predictions/${pred.id}/upload`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${token}` },
        body: uploadForm,
      });

      let result = pred;
      for (let i = 0; i < 30 && result.status !== "succeeded" && result.status !== "failed"; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const p = await fetch(`https://api.replicate.com/v1/predictions/${result.id}`, { headers: { Authorization: `Bearer ${token}` } });
        result = await p.json();
      }
      const text = result.output?.transcription ?? result.output?.text ?? "";
      return ok({ text, word_count: text ? text.split(" ").length : 0 });
    }

    return err("Not found", 404);
  } catch (e) {
    console.error(e);
    return err(String(e), 500);
  }
});
