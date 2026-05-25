"""
IELTS Mock Exam Platform - Backend Server
==========================================
Supabase + Replicate Gemini TTS + OpenRouter AI + Emergent Google Auth
"""
from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, BackgroundTasks, UploadFile, File
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
import os, logging, json, base64, uuid, httpx, asyncio, io
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import replicate
import database

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import stripe
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:3000')

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')

# Gemini TTS voices via Replicate (google/gemini-3.1-flash-tts)
VOICES = {
    "examiner":         "Fenrir",       # authoritative, deep
    "british_female_1": "Aoede",        # warm, expressive
    "british_male_1":   "Charon",       # steady broadcaster
    "british_female_2": "Kore",         # clear educator
    "british_male_2":   "Orus",         # warm storyteller
    "british_male_3":   "Puck",         # calm British male
    "professor":        "Rasalgethi",   # expressive academic
}

VOICES_DE = {
    "examiner_de":    "Fenrir",
    "german_female_1": "Aoede",
    "german_male_1":   "Charon",
    "german_female_2": "Kore",
    "german_male_2":   "Orus",
}

VOICE_STYLES = {
    "Fenrir":     "Speak as a calm, authoritative examiner with a clear British RP accent",
    "Aoede":      "Speak naturally with a warm, expressive British female voice",
    "Charon":     "Speak naturally with a steady, clear British male broadcaster voice",
    "Kore":       "Speak naturally with a clear, articulate British female educator voice",
    "Orus":       "Speak naturally with a warm British male storyteller voice",
    "Puck":       "Speak naturally with a calm, measured British male voice",
    "Rasalgethi": "Speak as an expressive, knowledgeable academic professor with a British accent",
}

# ── Gender-aware voice assignment ─────────────────────────────────────────────
_FEMALE_VOICES    = ["Aoede", "Kore"]
_MALE_VOICES      = ["Charon", "Orus", "Puck", "Fenrir", "Rasalgethi"]
_FEMALE_VOICES_DE = ["Aoede", "Kore"]
_MALE_VOICES_DE   = ["Charon", "Orus", "Fenrir"]

_FEMALE_INDICATORS = {"frau", "ms", "mrs", "miss", "dame", "moderatorin", "sprecherin", "ansagerin", "woman", "female", "she", "her"}
_MALE_INDICATORS   = {"herr", "mr", "sir", "moderator", "sprecher", "ansager", "man", "male", "he", "his"}

def _speaker_gender(name: str) -> str:
    """Infer gender from German/English speaker name prefix."""
    tokens = {t.lower().rstrip(".") for t in name.split()}
    if tokens & _FEMALE_INDICATORS:
        return "female"
    if tokens & _MALE_INDICATORS:
        return "male"
    return "male"  # default

def assign_voices_to_speakers(speakers: list, female_pool: list, male_pool: list) -> None:
    """Assign gender-appropriate voice IDs in-place; same name → same voice."""
    assigned: dict[str, str] = {}
    f_idx = m_idx = 0
    for sp in speakers:
        name = sp.get("name", "")
        if name in assigned:
            sp["voice_id"] = assigned[name]
            continue
        gender = _speaker_gender(name)
        if gender == "female":
            sp["voice_id"] = female_pool[f_idx % len(female_pool)]
            f_idx += 1
        else:
            sp["voice_id"] = male_pool[m_idx % len(male_pool)]
            m_idx += 1
        assigned[name] = sp["voice_id"]

db = database.DB()

ADMIN_EMAILS: set[str] = {
    e.strip() for e in os.environ.get("ADMIN_EMAILS", "jirananpanla@gmail.com").split(",") if e.strip()
}

def require_admin(user: dict):
    if user.get("email") not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required")

app = FastAPI()
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# PYDANTIC MODELS
# ==========================================
class SessionRequest(BaseModel):
    session_id: str

class AttemptCreate(BaseModel):
    exam_id: str
    module: str

class AnswerSubmit(BaseModel):
    answers: Dict[str, Any]

class WritingSubmit(BaseModel):
    task_1: str
    task_2: str

class SpeakingScoreRequest(BaseModel):
    transcriptions: Dict[str, str]

class FullTestModuleSubmit(BaseModel):
    module: str
    answers: Dict[str, Any]

class TelcWritingSubmit(BaseModel):
    aufgabe_1: str

class StripeCheckoutRequest(BaseModel):
    plan: str  # "monthly" or "annual"

# ==========================================
# AUTH
# ==========================================
async def get_current_user(request: Request) -> dict:
    session_token = request.cookies.get("session_token")
    if not session_token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            session_token = auth_header[7:]
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = await db.user_sessions.find_one({"session_token": session_token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    expires_at = session["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")

    user = await db.users.find_one({"user_id": session["user_id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return {**user, "is_admin": user.get("email") in ADMIN_EMAILS}

@api_router.post("/auth/session")
async def create_session(req: SessionRequest, response: Response):
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
                headers={"X-Session-ID": req.session_id}
            )
            if r.status_code != 200:
                raise HTTPException(400, "Invalid session")
            data = r.json()
    except httpx.RequestError as e:
        raise HTTPException(500, f"Auth service error: {str(e)}")

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    existing = await db.users.find_one({"email": data["email"]}, {"_id": 0})
    if existing:
        user_id = existing["user_id"]
        await db.users.update_one({"email": data["email"]}, {"$set": {"name": data["name"], "picture": data.get("picture", "")}})
    else:
        await db.users.insert_one({
            "user_id": user_id, "email": data["email"], "name": data["name"],
            "picture": data.get("picture", ""), "created_at": datetime.now(timezone.utc).isoformat()
        })

    session_token = f"sess_{uuid.uuid4().hex}"
    await db.user_sessions.insert_one({
        "user_id": user_id, "session_token": session_token,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()
    })

    is_https = os.environ.get("FRONTEND_URL", "").startswith("https")
    response.set_cookie(key="session_token", value=session_token, httponly=True, secure=is_https, samesite="none" if is_https else "lax", path="/", max_age=7*24*3600)
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    return {**user, "is_admin": user.get("email") in ADMIN_EMAILS}

@api_router.get("/auth/me")
async def get_me(request: Request):
    user = await get_current_user(request)
    return {**user, "is_admin": user.get("email") in ADMIN_EMAILS}

@api_router.post("/auth/logout")
async def logout(request: Request, response: Response):
    session_token = request.cookies.get("session_token")
    if session_token:
        await db.user_sessions.delete_one({"session_token": session_token})
    response.delete_cookie(key="session_token", path="/")
    return {"message": "Logged out"}

# ==========================================
# REPLICATE GEMINI TTS AUDIO PIPELINE
# ==========================================
def _generate_audio_sync(text: str, voice: str, lang: str = "en-GB") -> bytes:
    style = VOICE_STYLES.get(voice, "Speak naturally with a clear voice")
    output = replicate.run(
        "google/gemini-3.1-flash-tts",
        input={"text": text, "voice": voice, "prompt": style, "language_code": lang},
    )
    if hasattr(output, "read"):
        return output.read()
    url = str(output)
    resp = httpx.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content

async def generate_audio_for_text(text: str, voice: str, lang: str = "en-GB") -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _generate_audio_sync, text, voice, lang)

async def generate_exam_audio(exam_id: str):
    """Generate all audio for an exam (IELTS or TELC)"""
    try:
        exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
        if not exam:
            return
        exam_type = exam.get("exam_type", "ielts")
        if exam_type == "telc":
            await _generate_telc_audio(exam_id, exam)
        else:
            await _generate_ielts_audio(exam_id, exam)
    except Exception as e:
        logger.error(f"Audio pipeline error: {e}")
        await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "audio_error", "error_message": str(e)}})

async def _generate_ielts_audio(exam_id: str, exam: dict):
    """Generate all audio for an IELTS exam"""
    try:
        await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "generating_audio", "audio_progress": 0}})

        total_segments = 0
        generated = 0

        for section in exam.get("listening", {}).get("sections", []):
            total_segments += len(section.get("script_segments", []))
        for part in exam.get("speaking", {}).get("parts", []):
            for q in part.get("questions", []):
                if q.get("needs_audio"):
                    total_segments += 1

        # Guard: no sections means content generation failed silently
        if len(exam.get("listening", {}).get("sections", [])) == 0:
            await db.exams.update_one({"exam_id": exam_id}, {"$set": {
                "status": "error",
                "error_message": "Content generation produced no data. Please delete and regenerate."
            }})
            return

        if total_segments == 0:
            await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "ready", "audio_progress": 100}})
            return

        # Generate listening audio (including section instructions)
        for section in exam.get("listening", {}).get("sections", []):
            section_num = section["section_num"]

            # Generate instruction audio first if present
            instruction_text = section.get("instruction")
            if instruction_text and not section.get("instruction_audio_id"):
                try:
                    audio_bytes = await generate_audio_for_text(instruction_text, VOICES["examiner"])
                    audio_id = f"audio_instr_{uuid.uuid4().hex[:10]}"
                    await database.insert_audio_file(audio_id, exam_id, audio_bytes,
                        {"section_num": section_num, "audio_type": "instruction"})
                    await db.exams.update_one({"exam_id": exam_id},
                        {"$set": {f"listening.sections.{section_num-1}.instruction_audio_id": audio_id}})
                    logger.info(f"Generated instruction audio for section {section_num}")
                except Exception as e:
                    logger.error(f"Instruction audio error section {section_num}: {e}")

            for i, segment in enumerate(section.get("script_segments", [])):
                if segment.get("audio_id"):
                    generated += 1
                    continue
                try:
                    speaker_name = segment.get("speaker", "")
                    voice_id = (VOICES["british_female_1"] if _speaker_gender(speaker_name) == "female"
                                else VOICES["british_male_1"])
                    for sp in section.get("speakers", []):
                        if sp["name"] == speaker_name:
                            voice_id = sp.get("voice_id", voice_id)
                            break

                    audio_bytes = await generate_audio_for_text(segment["text"], voice_id)
                    audio_id = f"audio_{uuid.uuid4().hex[:12]}"
                    await database.insert_audio_file(audio_id, exam_id, audio_bytes,
                        {"section_num": section_num, "segment_index": i})

                    await db.exams.update_one({"exam_id": exam_id},
                        {"$set": {f"listening.sections.{section_num-1}.script_segments.{i}.audio_id": audio_id}})

                    generated += 1
                    progress = int((generated / total_segments) * 100)
                    await db.exams.update_one({"exam_id": exam_id}, {"$set": {"audio_progress": progress}})
                    logger.info(f"Audio {generated}/{total_segments} for exam {exam_id}")
                except Exception as e:
                    logger.error(f"Audio gen error section {section_num} seg {i}: {e}")

        # Generate speaking examiner audio
        for part_idx, part in enumerate(exam.get("speaking", {}).get("parts", [])):
            for q_idx, q in enumerate(part.get("questions", [])):
                if q.get("needs_audio") and not q.get("audio_id"):
                    try:
                        audio_bytes = await generate_audio_for_text(q["question_text"], VOICES["examiner"])
                        audio_id = f"audio_{uuid.uuid4().hex[:12]}"
                        await database.insert_audio_file(audio_id, exam_id, audio_bytes,
                            {"audio_type": "speaking"})
                        await db.exams.update_one({"exam_id": exam_id},
                            {"$set": {f"speaking.parts.{part_idx}.questions.{q_idx}.audio_id": audio_id}})
                        generated += 1
                        progress = int((generated / total_segments) * 100)
                        await db.exams.update_one({"exam_id": exam_id}, {"$set": {"audio_progress": progress}})
                    except Exception as e:
                        logger.error(f"Speaking audio gen error: {e}")

        await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "ready", "audio_progress": 100}})
        logger.info(f"All audio ready for exam {exam_id}")
    except Exception as e:
        logger.error(f"Audio pipeline error: {e}")
        await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "audio_error", "error_message": str(e)}})

async def _generate_telc_audio(exam_id: str, exam: dict):
    """Generate audio for TELC exam hoeren + sprechen"""
    try:
        await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "generating_audio", "audio_progress": 0}})
        total = 0
        generated = 0

        # Count segments in hoeren aufgaben
        for aufgabe in exam.get("hoeren", {}).get("aufgaben", []):
            total += len(aufgabe.get("script_segments", []))
            for conv in aufgabe.get("conversations", []):
                total += len(conv.get("script_segments", []))
            for ansage in aufgabe.get("ansagen", []):
                total += 1
        # Count sprechen teile
        for teil in exam.get("sprechen", {}).get("teile", []):
            for q in teil.get("fragen", []):
                if q.get("needs_audio"):
                    total += 1

        # Guard: if no content was generated, mark as error rather than silently ready
        hoeren_aufgaben = len(exam.get("hoeren", {}).get("aufgaben", []))
        lesen_aufgaben  = len(exam.get("lesen",  {}).get("aufgaben", []))
        if hoeren_aufgaben == 0 and lesen_aufgaben == 0:
            await db.exams.update_one({"exam_id": exam_id}, {"$set": {
                "status": "error",
                "error_message": "Content generation produced no data. Please delete and regenerate."
            }})
            return

        if total == 0:
            await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "ready", "audio_progress": 100}})
            return

        # Generate hoeren audio
        for a_idx, aufgabe in enumerate(exam.get("hoeren", {}).get("aufgaben", [])):
            # Handle direct script_segments (Aufgabe 2 style)
            for s_idx, seg in enumerate(aufgabe.get("script_segments", [])):
                if seg.get("audio_id"):
                    generated += 1
                    continue
                try:
                    sprecher_name = seg.get("sprecher", "")
                    voice_id = (_FEMALE_VOICES_DE[0] if _speaker_gender(sprecher_name) == "female"
                                else _MALE_VOICES_DE[0])
                    for sp in aufgabe.get("sprecher", []):
                        if sp["name"] == sprecher_name:
                            voice_id = sp.get("voice_id", voice_id)
                            break
                    audio_bytes = await generate_audio_for_text(seg["text"], voice_id)
                    audio_id = f"audio_{uuid.uuid4().hex[:12]}"
                    await database.insert_audio_file(audio_id, exam_id, audio_bytes,
                        {"audio_type": "telc_hoeren"})
                    await db.exams.update_one({"exam_id": exam_id},
                        {"$set": {f"hoeren.aufgaben.{a_idx}.script_segments.{s_idx}.audio_id": audio_id}})
                    generated += 1
                    await db.exams.update_one({"exam_id": exam_id},
                        {"$set": {"audio_progress": int(generated / total * 100)}})
                except Exception as e:
                    logger.error(f"TELC audio gen error: {e}")

            # Handle conversations (Aufgabe 1 style)
            for c_idx, conv in enumerate(aufgabe.get("conversations", [])):
                for s_idx, seg in enumerate(conv.get("script_segments", [])):
                    if seg.get("audio_id"):
                        generated += 1
                        continue
                    try:
                        sprecher_name_c = seg.get("sprecher", "")
                        voice_id = (_FEMALE_VOICES_DE[0] if _speaker_gender(sprecher_name_c) == "female"
                                    else _MALE_VOICES_DE[0])
                        sprecher_list = conv.get("sprecher", aufgabe.get("sprecher", []))
                        for sp in sprecher_list:
                            if sp["name"] == sprecher_name_c:
                                voice_id = sp.get("voice_id", voice_id)
                                break
                        audio_bytes = await generate_audio_for_text(seg["text"], voice_id)
                        audio_id = f"audio_{uuid.uuid4().hex[:12]}"
                        await database.insert_audio_file(audio_id, exam_id, audio_bytes,
                            {"audio_type": "telc_hoeren"})
                        await db.exams.update_one({"exam_id": exam_id},
                            {"$set": {f"hoeren.aufgaben.{a_idx}.conversations.{c_idx}.script_segments.{s_idx}.audio_id": audio_id}})
                        generated += 1
                        await db.exams.update_one({"exam_id": exam_id},
                            {"$set": {"audio_progress": int(generated / total * 100)}})
                    except Exception as e:
                        logger.error(f"TELC audio gen error: {e}")

            # Handle ansagen (Aufgabe 3 style)
            for ans_idx, ansage in enumerate(aufgabe.get("ansagen", [])):
                if ansage.get("audio_id"):
                    generated += 1
                    continue
                try:
                    voice_id = ansage.get("voice_id", VOICES_DE.get("examiner_de"))
                    audio_bytes = await generate_audio_for_text(ansage["text"], voice_id)
                    audio_id = f"audio_{uuid.uuid4().hex[:12]}"
                    await database.insert_audio_file(audio_id, exam_id, audio_bytes,
                        {"audio_type": "telc_hoeren"})
                    await db.exams.update_one({"exam_id": exam_id},
                        {"$set": {f"hoeren.aufgaben.{a_idx}.ansagen.{ans_idx}.audio_id": audio_id}})
                    generated += 1
                    await db.exams.update_one({"exam_id": exam_id},
                        {"$set": {"audio_progress": int(generated / total * 100)}})
                except Exception as e:
                    logger.error(f"TELC ansage audio error: {e}")

        # Generate sprechen audio
        for t_idx, teil in enumerate(exam.get("sprechen", {}).get("teile", [])):
            for q_idx, q in enumerate(teil.get("fragen", [])):
                if q.get("needs_audio") and not q.get("audio_id"):
                    try:
                        audio_bytes = await generate_audio_for_text(q["frage_text"], VOICES_DE["examiner_de"])
                        audio_id = f"audio_{uuid.uuid4().hex[:12]}"
                        await database.insert_audio_file(audio_id, exam_id, audio_bytes,
                            {"audio_type": "telc_sprechen"})
                        await db.exams.update_one({"exam_id": exam_id},
                            {"$set": {f"sprechen.teile.{t_idx}.fragen.{q_idx}.audio_id": audio_id}})
                        generated += 1
                        await db.exams.update_one({"exam_id": exam_id},
                            {"$set": {"audio_progress": int(generated / total * 100)}})
                    except Exception as e:
                        logger.error(f"TELC sprechen audio error: {e}")

        await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "ready", "audio_progress": 100}})
    except Exception as e:
        logger.error(f"TELC audio error: {e}")
        await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "audio_error", "error_message": str(e)}})

# ==========================================
# OPENROUTER AI
# ==========================================
async def call_openrouter(messages: list, model: str = "google/gemini-2.0-flash-001", json_mode: bool = True, retries: int = 2) -> str:
    """Call OpenRouter with automatic retry on 429/504/connection errors."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=150.0) as client:
                payload = {"model": model, "messages": messages}
                if json_mode:
                    payload["response_format"] = {"type": "json_object"}
                r = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                    json=payload
                )
                if r.status_code in (429, 502, 503, 504) and attempt < retries:
                    wait = 10 * (attempt + 1)
                    logger.warning(f"OpenRouter {r.status_code}, retrying in {wait}s (attempt {attempt+1})")
                    await asyncio.sleep(wait)
                    last_error = f"{r.status_code}"
                    continue
                if r.status_code != 200:
                    logger.error(f"OpenRouter error: {r.status_code} - {r.text}")
                    raise HTTPException(500, "AI service error")
                return r.json()["choices"][0]["message"]["content"]
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
            last_error = str(e)
            if attempt < retries:
                logger.warning(f"OpenRouter connection error, retrying in 10s: {e}")
                await asyncio.sleep(10)
            else:
                raise HTTPException(500, f"AI service error: {last_error}")
    raise HTTPException(500, f"AI service error after {retries+1} attempts: {last_error}")

async def score_writing_ai(task_1_text: str, task_2_text: str, task_1_prompt: str, task_2_prompt: str) -> dict:
    prompt = f"""You are an experienced IELTS Writing examiner. Score these responses using official IELTS Band Descriptors. Scores as multiples of 0.5.

TASK 1 PROMPT: {task_1_prompt}
TASK 1 ({len(task_1_text.split())} words): {task_1_text}

TASK 2 PROMPT: {task_2_prompt}
TASK 2 ({len(task_2_text.split())} words): {task_2_text}

Return JSON: {{"task_1":{{"task_achievement":{{"band":6.0,"feedback":"..."}},"coherence_cohesion":{{"band":6.0,"feedback":"..."}},"lexical_resource":{{"band":6.0,"feedback":"..."}},"grammatical_range":{{"band":6.0,"feedback":"..."}},"overall_band":6.0,"general_feedback":"..."}},"task_2":{{"task_achievement":{{"band":6.0,"feedback":"..."}},"coherence_cohesion":{{"band":6.0,"feedback":"..."}},"lexical_resource":{{"band":6.0,"feedback":"..."}},"grammatical_range":{{"band":6.0,"feedback":"..."}},"overall_band":6.0,"general_feedback":"..."}},"overall_writing_band":6.0}}"""
    result = await call_openrouter([
        {"role": "system", "content": "You are an IELTS examiner. Return only valid JSON."},
        {"role": "user", "content": prompt}
    ], model="openai/gpt-4o")
    return json.loads(result)

async def score_speaking_ai(transcriptions: dict, questions: dict) -> dict:
    prompt = f"""You are an IELTS Speaking examiner. Score these transcribed responses. Scores as multiples of 0.5.
Questions: {json.dumps(questions)}
Responses: {json.dumps(transcriptions)}
Return JSON: {{"fluency_coherence":{{"band":6.0,"feedback":"..."}},"lexical_resource":{{"band":6.0,"feedback":"..."}},"grammatical_range":{{"band":6.0,"feedback":"..."}},"pronunciation":{{"band":6.0,"feedback":"..."}},"overall_band":6.0,"general_feedback":"...","part_feedback":{{"part_1":"...","part_2":"...","part_3":"..."}}}}"""
    result = await call_openrouter([
        {"role": "system", "content": "You are an IELTS examiner. Return only valid JSON."},
        {"role": "user", "content": prompt}
    ], model="openai/gpt-4o")
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse speaking score JSON: {result[:200]}")
        raise HTTPException(500, "AI scoring returned invalid format")

async def score_telc_writing_ai(aufgabe_text: str, aufgabe_prompt: str, level: str) -> dict:
    prompt = f"""You are a certified TELC Deutsch {level} examiner. Score this writing response.

TASK: {aufgabe_prompt}
RESPONSE ({len(aufgabe_text.split())} words): {aufgabe_text}

TELC {level} writing criteria: Communicative achievement, Organization, Language range & accuracy.
Max 30 points total (10 per criterion).

Return JSON: {{"kommunikative_aufgabe":{{"punkte":8,"feedback":"..."}},"textaufbau":{{"punkte":8,"feedback":"..."}},"sprachliche_mittel":{{"punkte":8,"feedback":"..."}},"gesamt_punkte":24,"bestanden":true,"allgemeines_feedback":"..."}}"""
    result = await call_openrouter([
        {"role": "system", "content": "You are a TELC examiner. Return only valid JSON."},
        {"role": "user", "content": prompt}
    ], model="openai/gpt-4o")
    return json.loads(result)

async def score_telc_speaking_ai(transcriptions: dict, level: str) -> dict:
    prompt = f"""You are a certified TELC Deutsch {level} speaking examiner. Score these transcribed responses.
Responses: {json.dumps(transcriptions)}

Criteria: Communicative achievement, Fluency, Language accuracy & range. Max 30 points.
Return JSON: {{"kommunikative_kompetenz":{{"punkte":8,"feedback":"..."}},"fluessigkeit":{{"punkte":8,"feedback":"..."}},"sprachliche_korrektheit":{{"punkte":8,"feedback":"..."}},"gesamt_punkte":24,"bestanden":true,"allgemeines_feedback":"..."}}"""
    result = await call_openrouter([
        {"role": "system", "content": "You are a TELC examiner. Return only valid JSON."},
        {"role": "user", "content": prompt}
    ], model="openai/gpt-4o")
    return json.loads(result)

# ==========================================
# EXAM ENDPOINTS
# ==========================================
@api_router.get("/exams")
async def list_exams(exam_type: str = None):
    query = {}
    if exam_type:
        query["exam_type"] = exam_type
    exams = await db.exams.find(query, {"_id": 0, "exam_id": 1, "title": 1, "pathway": 1, "exam_type": 1, "telc_level": 1, "status": 1, "created_at": 1}).to_list(100)
    return exams

@api_router.get("/exams/{exam_id}")
async def get_exam(exam_id: str):
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise HTTPException(404, "Exam not found")
    exam_copy = json.loads(json.dumps(exam))
    for section in exam_copy.get("listening", {}).get("sections", []):
        for q in section.get("questions", []):
            q.pop("correct_answer", None)
    for passage in exam_copy.get("reading", {}).get("passages", []):
        for q in passage.get("questions", []):
            q.pop("correct_answer", None)
    return exam_copy

@api_router.get("/exams/{exam_id}/full")
async def get_exam_full(exam_id: str, request: Request):
    """Get exam with answers (for internal scoring only)"""
    await get_current_user(request)
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise HTTPException(404, "Exam not found")
    return exam

@api_router.post("/exams/{exam_id}/prepare")
async def prepare_exam(exam_id: str, background_tasks: BackgroundTasks):
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0, "status": 1, "audio_progress": 1})
    if not exam:
        raise HTTPException(404, "Exam not found")
    if exam["status"] == "ready":
        return {"status": "ready", "audio_progress": 100}
    if exam["status"] == "generating_audio":
        return {"status": "generating_audio", "audio_progress": exam.get("audio_progress", 0)}
    background_tasks.add_task(generate_exam_audio, exam_id)
    return {"status": "generating_audio", "audio_progress": 0}

@api_router.get("/exams/{exam_id}/status")
async def get_exam_status(exam_id: str):
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0, "status": 1, "audio_progress": 1, "error_message": 1})
    if not exam:
        raise HTTPException(404, "Exam not found")
    return exam

# ==========================================
# AUDIO ENDPOINT
# ==========================================
@api_router.get("/audio/{audio_id}")
async def get_audio(audio_id: str):
    storage_path = await database.get_audio_path(audio_id)
    if not storage_path:
        raise HTTPException(404, "Audio not found")
    public_url = database.get_audio_public_url(storage_path)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(public_url)
        r.raise_for_status()
    return Response(content=r.content, media_type="audio/mpeg",
        headers={"Content-Disposition": f"inline; filename={audio_id}.mp3",
                 "Cache-Control": "public, max-age=86400"})

# ==========================================
# SPEECH-TO-TEXT (Replicate openai/whisper)
# ==========================================
def _transcribe_audio_sync(audio_bytes: bytes) -> str:
    output = replicate.run(
        "openai/whisper",
        input={"audio": io.BytesIO(audio_bytes), "model": "large-v3"},
    )
    if isinstance(output, dict):
        return output.get("transcription") or output.get("text", "")
    return str(output)

async def transcribe_audio_async(audio_bytes: bytes) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _transcribe_audio_sync, audio_bytes)

@api_router.post("/speaking/transcribe")
async def transcribe_speaking(audio_file: UploadFile = File(...), request: Request = None):
    """Transcribe recorded speaking audio via Replicate Whisper"""
    if request:
        await get_current_user(request)
    audio_content = await audio_file.read()
    if len(audio_content) < 100:
        raise HTTPException(400, "Audio file too small")
    try:
        text = await transcribe_audio_async(audio_content)
        return {"text": text, "word_count": len(text.split()) if text else 0}
    except Exception as e:
        logger.error(f"STT error: {e}")
        raise HTTPException(500, f"Transcription failed: {str(e)}")

@api_router.post("/speaking/converse")
async def speaking_converse(request: Request):
    """Agentic speaking: AI examiner responds based on conversation context"""
    await get_current_user(request)
    body = await request.json()

    part_num = body.get("part_num", 1)
    user_text = body.get("user_transcription", "")
    history = body.get("conversation_history", [])
    cue_card = body.get("cue_card", "")
    action = body.get("action", "respond")  # respond, start, follow_up

    part_instructions = {
        1: "Part 1 (Introduction & Interview, 4-5 min): Ask about familiar topics like home, work, hobbies. Ask one question at a time. Keep follow-ups natural and brief. Generate variety.",
        2: f"Part 2 (Long Turn): The candidate was given this cue card:\n{cue_card}\nAfter they finish speaking, ask 1-2 brief follow-up questions about what they said.",
        3: "Part 3 (Discussion, 4-5 min): Ask abstract/analytical questions building on Part 2 topic. Probe deeper into the candidate's views. One question at a time."
    }

    system_prompt = f"""You are a professional IELTS Speaking examiner named Daniel. You are conducting Part {part_num} of the IELTS Speaking test.

Rules:
- Be professional, warm, and encouraging
- Ask ONE question at a time (never multiple)
- Keep your responses brief (1-2 sentences max for follow-ups)
- Sound natural, like a real conversation
- {part_instructions.get(part_num, '')}
- If action is "start", give an appropriate opening for this part
- Do NOT use any text formatting or special characters
- Respond in plain conversational English"""

    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        if h and h.get("text"):
            messages.append({"role": h["role"], "content": h["text"]})
    if user_text:
        messages.append({"role": "user", "content": user_text})
    if action == "start" and not user_text:
        messages.append({"role": "user", "content": f"[System: Begin Part {part_num}. Greet the candidate and ask your first question.]"})

    try:
        examiner_text = await call_openrouter(messages, model="openai/gpt-4o", json_mode=False)
        # Clean up any markdown or formatting
        examiner_text = examiner_text.strip().strip('"').strip("'")

        # Generate audio for examiner response
        audio_bytes = await generate_audio_for_text(examiner_text, VOICES["examiner"])
        audio_b64 = base64.b64encode(audio_bytes).decode()

        new_history = list(history)
        if user_text:
            new_history.append({"role": "user", "text": user_text})
        new_history.append({"role": "assistant", "text": examiner_text})

        return {
            "examiner_text": examiner_text,
            "audio_base64": audio_b64,
            "conversation_history": new_history
        }
    except Exception as e:
        logger.error(f"Speaking converse error: {e}")
        raise HTTPException(500, f"Examiner response failed: {str(e)}")

# ==========================================
# ATTEMPTS & SCORING
# ==========================================
@api_router.post("/attempts")
async def create_attempt(data: AttemptCreate, request: Request):
    user = await get_current_user(request)
    attempt_id = f"attempt_{uuid.uuid4().hex[:12]}"
    await db.attempts.insert_one({
        "attempt_id": attempt_id, "user_id": user["user_id"], "exam_id": data.exam_id,
        "module": data.module, "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None, "answers": {}, "scores": None, "status": "in_progress"
    })
    return {"attempt_id": attempt_id}

@api_router.put("/attempts/{attempt_id}/submit")
async def submit_answers(attempt_id: str, data: AnswerSubmit, request: Request):
    user = await get_current_user(request)
    attempt = await db.attempts.find_one({"attempt_id": attempt_id, "user_id": user["user_id"]}, {"_id": 0})
    if not attempt:
        raise HTTPException(404, "Attempt not found")

    exam = await db.exams.find_one({"exam_id": attempt["exam_id"]}, {"_id": 0})
    scores = None
    module = attempt["module"]
    exam_type = exam.get("exam_type", "ielts")

    if exam_type == "telc":
        telc_module_map = {"listening": "hoeren", "reading": "lesen", "sprachbausteine": "sprachbausteine"}
        telc_module = telc_module_map.get(module, module)
        if module in ["listening", "reading", "sprachbausteine"]:
            scores = score_telc_objective(exam, telc_module, data.answers)
    else:
        if module in ["listening", "reading"]:
            scores = score_objective(exam, module, data.answers)

    await db.attempts.update_one({"attempt_id": attempt_id}, {"$set": {
        "answers": data.answers, "scores": scores,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed" if scores else "submitted"
    }})
    return {"attempt_id": attempt_id, "scores": scores}

def normalize_answer(text):
    """Normalize answer for lenient comparison"""
    if not text:
        return ""
    t = str(text).strip().lower()
    t = ' '.join(t.split())
    for c in '.,;:!?$()[]{}':
        t = t.replace(c, '')
    return t

def answers_match(user_answer, correct_answer):
    """Check if answers match with tolerance for spelling/spacing variations"""
    user = normalize_answer(user_answer)
    if not user:
        return False
    for correct in str(correct_answer).split('|'):
        c = normalize_answer(correct)
        if not c:
            continue
        if user == c:
            return True
        if user.replace(' ', '') == c.replace(' ', ''):
            return True
        try:
            if float(user) == float(c):
                return True
        except ValueError:
            pass
    return False

def score_objective(exam: dict, module: str, answers: dict) -> dict:
    correct = 0
    total = 0
    details = []
    key = "sections" if module == "listening" else "passages"
    sections = exam.get(module, {}).get(key, [])

    for section in sections:
        for q in section.get("questions", []):
            q_num = str(q["question_num"])
            total += 1
            user_answer = str(answers.get(q_num, ""))
            correct_answer = str(q.get("correct_answer", ""))
            is_correct = answers_match(user_answer, correct_answer)
            if is_correct:
                correct += 1
            details.append({"question_num": int(q_num), "user_answer": user_answer,
                "correct_answer": correct_answer, "is_correct": is_correct})

    band = raw_to_band(correct, total, module)
    return {"correct": correct, "total": total, "band_score": band, "details": details}

def raw_to_band(correct: int, total: int, module: str) -> float:
    if total == 0:
        return 0.0
    bands_listening = [(39,9.0),(37,8.5),(35,8.0),(33,7.5),(30,7.0),(27,6.5),(23,6.0),(20,5.5),(16,5.0),(13,4.5),(10,4.0),(6,3.5),(4,3.0)]
    bands_reading = [(39,9.0),(37,8.5),(35,8.0),(33,7.5),(30,7.0),(27,6.5),(23,6.0),(19,5.5),(15,5.0),(13,4.5),(10,4.0),(8,3.5),(6,3.0)]
    bands = bands_listening if module == "listening" else bands_reading
    for threshold, band in bands:
        if correct >= threshold:
            return band
    return 2.0

def score_telc_objective(exam: dict, module: str, answers: dict) -> dict:
    """Score TELC objective modules (lesen, hoeren, sprachbausteine)."""
    correct = 0
    total = 0
    details = []
    aufgaben = exam.get(module, {}).get("aufgaben", [])

    for aufgabe in aufgaben:
        # Standard questions list (lesen, hoeren)
        for q in aufgabe.get("questions", []):
            q_num = str(q["question_num"])
            total += 1
            user_answer = str(answers.get(q_num, ""))
            correct_answer = str(q.get("correct_answer", ""))
            is_correct = answers_match(user_answer, correct_answer)
            if is_correct: correct += 1
            details.append({"question_num": int(q_num), "user_answer": user_answer,
                "correct_answer": correct_answer, "is_correct": is_correct})
        # Sprachbausteine: options list with per-gap correct_answer
        for opt in aufgabe.get("options", []):
            q_num = str(opt["question_num"])
            total += 1
            user_answer = str(answers.get(q_num, ""))
            correct_answer = str(opt.get("correct_answer", ""))
            is_correct = answers_match(user_answer, correct_answer)
            if is_correct: correct += 1
            details.append({"question_num": int(q_num), "user_answer": user_answer,
                "correct_answer": correct_answer, "is_correct": is_correct})
        # Ansagen: questions embedded in ansagen items
        for ansage in aufgabe.get("ansagen", []):
            if "question_num" in ansage:
                q_num = str(ansage["question_num"])
                total += 1
                user_answer = str(answers.get(q_num, ""))
                correct_answer = str(ansage.get("correct_answer", ""))
                is_correct = answers_match(user_answer, correct_answer)
                if is_correct: correct += 1
                details.append({"question_num": int(q_num), "user_answer": user_answer,
                    "correct_answer": correct_answer, "is_correct": is_correct})
        # Kurzgespräche: questions inside conversations
        for conv in aufgabe.get("conversations", []):
            for q in conv.get("questions", []):
                q_num = str(q["question_num"])
                total += 1
                user_answer = str(answers.get(q_num, ""))
                correct_answer = str(q.get("correct_answer", ""))
                is_correct = answers_match(user_answer, correct_answer)
                if is_correct: correct += 1
                details.append({"question_num": int(q_num), "user_answer": user_answer,
                    "correct_answer": correct_answer, "is_correct": is_correct})

    percentage = round(correct / total * 100, 1) if total > 0 else 0.0
    # TELC pass threshold: 60 % per objective section
    return {"correct": correct, "total": total, "percentage": percentage,
            "band_score": percentage / 100 * 9,  # rough IELTS-equivalent for display
            "passed": percentage >= 60.0, "details": details}

@api_router.post("/attempts/{attempt_id}/score-writing")
async def score_writing_attempt(attempt_id: str, data: WritingSubmit, request: Request):
    user = await get_current_user(request)
    attempt = await db.attempts.find_one({"attempt_id": attempt_id, "user_id": user["user_id"]}, {"_id": 0})
    if not attempt:
        raise HTTPException(404, "Attempt not found")
    exam = await db.exams.find_one({"exam_id": attempt["exam_id"]}, {"_id": 0})
    tasks = exam.get("writing", {}).get("tasks", [])
    t1p = tasks[0]["prompt"] if len(tasks) > 0 else ""
    t2p = tasks[1]["prompt"] if len(tasks) > 1 else ""
    scores = await score_writing_ai(data.task_1, data.task_2, t1p, t2p)
    await db.attempts.update_one({"attempt_id": attempt_id}, {"$set": {
        "answers": {"task_1": data.task_1, "task_2": data.task_2}, "scores": scores,
        "completed_at": datetime.now(timezone.utc).isoformat(), "status": "completed"
    }})
    return {"attempt_id": attempt_id, "scores": scores}

@api_router.post("/attempts/{attempt_id}/score-speaking")
async def score_speaking_attempt(attempt_id: str, data: SpeakingScoreRequest, request: Request):
    user = await get_current_user(request)
    attempt = await db.attempts.find_one({"attempt_id": attempt_id, "user_id": user["user_id"]}, {"_id": 0})
    if not attempt:
        raise HTTPException(404, "Attempt not found")
    exam = await db.exams.find_one({"exam_id": attempt["exam_id"]}, {"_id": 0})
    questions = {}
    for part in exam.get("speaking", {}).get("parts", []):
        for q in part.get("questions", []):
            questions[str(q.get("question_num", ""))] = q.get("question_text", "")
    scores = await score_speaking_ai(data.transcriptions, questions)
    await db.attempts.update_one({"attempt_id": attempt_id}, {"$set": {
        "answers": {"transcriptions": data.transcriptions}, "scores": scores,
        "completed_at": datetime.now(timezone.utc).isoformat(), "status": "completed"
    }})
    return {"attempt_id": attempt_id, "scores": scores}

@api_router.post("/attempts/{attempt_id}/score-telc-writing")
async def score_telc_writing_attempt(attempt_id: str, data: TelcWritingSubmit, request: Request):
    user = await get_current_user(request)
    attempt = await db.attempts.find_one({"attempt_id": attempt_id, "user_id": user["user_id"]}, {"_id": 0})
    if not attempt:
        raise HTTPException(404, "Attempt not found")
    exam = await db.exams.find_one({"exam_id": attempt["exam_id"]}, {"_id": 0})
    level = exam.get("telc_level", "B1")
    aufgaben = exam.get("schreiben", {}).get("aufgaben", [])
    prompt = aufgaben[0]["aufgabe"] if aufgaben else ""
    scores = await score_telc_writing_ai(data.aufgabe_1, prompt, level)
    await db.attempts.update_one({"attempt_id": attempt_id}, {"$set": {
        "answers": {"aufgabe_1": data.aufgabe_1}, "scores": scores,
        "completed_at": datetime.now(timezone.utc).isoformat(), "status": "completed"
    }})
    return {"attempt_id": attempt_id, "scores": scores}

@api_router.get("/attempts/{attempt_id}")
async def get_attempt(attempt_id: str, request: Request):
    user = await get_current_user(request)
    attempt = await db.attempts.find_one({"attempt_id": attempt_id, "user_id": user["user_id"]}, {"_id": 0})
    if not attempt:
        raise HTTPException(404, "Attempt not found")
    return attempt

@api_router.get("/attempts")
async def list_attempts(request: Request):
    user = await get_current_user(request)
    return await db.attempts.find({"user_id": user["user_id"]}, {"_id": 0}).sort("started_at", -1).to_list(100)

@api_router.post("/attempts/full-test")
async def create_full_test_attempt(request: Request):
    body = await request.json()
    exam_id = body.get("exam_id")
    user = await get_current_user(request)
    attempt_id = f"attempt_{uuid.uuid4().hex[:12]}"
    await db.attempts.insert_one({
        "attempt_id": attempt_id, "user_id": user["user_id"], "exam_id": exam_id,
        "module": "full_test", "mode": "full_test",
        "current_module": "listening",
        "modules_completed": [],
        "module_answers": {}, "module_scores": {},
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None, "status": "in_progress"
    })
    return {"attempt_id": attempt_id}

@api_router.put("/attempts/{attempt_id}/full-test/module")
async def submit_full_test_module(attempt_id: str, data: FullTestModuleSubmit, request: Request):
    user = await get_current_user(request)
    attempt = await db.attempts.find_one({"attempt_id": attempt_id, "user_id": user["user_id"]}, {"_id": 0})
    if not attempt:
        raise HTTPException(404, "Attempt not found")
    exam = await db.exams.find_one({"exam_id": attempt["exam_id"]}, {"_id": 0})
    module = data.module
    scores = None
    if module in ["listening", "reading"]:
        scores = score_objective(exam, module, data.answers)

    modules_completed = attempt.get("modules_completed", [])
    if module not in modules_completed:
        modules_completed.append(module)

    module_order = ["listening", "reading", "writing", "speaking"]
    current_idx = module_order.index(module)
    next_module = module_order[current_idx + 1] if current_idx + 1 < len(module_order) else None

    update = {
        f"module_answers.{module}": data.answers,
        "modules_completed": modules_completed,
        "current_module": next_module or "completed"
    }
    if scores:
        update[f"module_scores.{module}"] = scores

    all_done = set(modules_completed) >= {"listening", "reading", "writing", "speaking"}
    if all_done and next_module is None:
        update["status"] = "submitted"
        update["completed_at"] = datetime.now(timezone.utc).isoformat()

    await db.attempts.update_one({"attempt_id": attempt_id}, {"$set": update})
    return {"attempt_id": attempt_id, "next_module": next_module, "scores": scores}

@api_router.post("/attempts/{attempt_id}/full-test/score-writing")
async def score_full_test_writing(attempt_id: str, data: WritingSubmit, request: Request):
    user = await get_current_user(request)
    attempt = await db.attempts.find_one({"attempt_id": attempt_id, "user_id": user["user_id"]}, {"_id": 0})
    if not attempt:
        raise HTTPException(404, "Attempt not found")
    exam = await db.exams.find_one({"exam_id": attempt["exam_id"]}, {"_id": 0})
    tasks = exam.get("writing", {}).get("tasks", [])
    t1p = tasks[0]["prompt"] if tasks else ""
    t2p = tasks[1]["prompt"] if len(tasks) > 1 else ""
    scores = await score_writing_ai(data.task_1, data.task_2, t1p, t2p)
    await db.attempts.update_one({"attempt_id": attempt_id}, {"$set": {
        "module_answers.writing": {"task_1": data.task_1, "task_2": data.task_2},
        "module_scores.writing": scores
    }})
    return {"scores": scores}

@api_router.post("/attempts/{attempt_id}/full-test/score-speaking")
async def score_full_test_speaking(attempt_id: str, data: SpeakingScoreRequest, request: Request):
    user = await get_current_user(request)
    attempt = await db.attempts.find_one({"attempt_id": attempt_id, "user_id": user["user_id"]}, {"_id": 0})
    if not attempt:
        raise HTTPException(404, "Attempt not found")
    exam = await db.exams.find_one({"exam_id": attempt["exam_id"]}, {"_id": 0})
    questions = {}
    for part in exam.get("speaking", {}).get("parts", []):
        for q in part.get("questions", []):
            questions[str(q.get("question_num", ""))] = q.get("question_text", "")
    scores = await score_speaking_ai(data.transcriptions, questions)

    all_module_scores = attempt.get("module_scores", {})
    all_module_scores["speaking"] = scores

    bands = []
    for mod in ["listening", "reading"]:
        s = all_module_scores.get(mod, {})
        if s.get("band_score"):
            bands.append(s["band_score"])
    w = all_module_scores.get("writing", {})
    if w.get("overall_writing_band"):
        bands.append(w["overall_writing_band"])
    sp = all_module_scores.get("speaking", {})
    if sp.get("overall_band"):
        bands.append(sp["overall_band"])
    overall = round(sum(bands) / len(bands) * 2) / 2 if bands else 0.0

    await db.attempts.update_one({"attempt_id": attempt_id}, {"$set": {
        "module_scores.speaking": scores,
        "module_answers.speaking": {"transcriptions": data.transcriptions},
        "overall_band": overall,
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat()
    }})
    return {"scores": scores, "overall_band": overall}

# ==========================================
# PROGRESS
# ==========================================
@api_router.get("/progress")
async def get_progress(request: Request):
    user = await get_current_user(request)
    attempts = await db.attempts.find({"user_id": user["user_id"], "status": "completed"}, {"_id": 0}).sort("completed_at", -1).to_list(1000)

    module_stats = {}
    for module in ["listening", "reading", "writing", "speaking"]:
        ma = [a for a in attempts if a["module"] == module and a.get("scores")]
        if ma:
            bands = []
            for a in ma:
                s = a["scores"]
                if module in ["listening", "reading"]:
                    bands.append(s.get("band_score", 0))
                elif module == "writing":
                    bands.append(s.get("overall_writing_band", 0))
                else:
                    bands.append(s.get("overall_band", 0))
            module_stats[module] = {
                "attempts": len(ma), "latest_band": bands[0] if bands else 0,
                "average_band": round(sum(bands) / len(bands) * 2) / 2 if bands else 0,
                "highest_band": max(bands) if bands else 0,
                "history": [{"date": a.get("completed_at", ""), "band": bands[i]} for i, a in enumerate(ma[:10])]
            }
        else:
            module_stats[module] = {"attempts": 0, "latest_band": 0, "average_band": 0, "highest_band": 0, "history": []}

    latest_bands = [module_stats[m]["latest_band"] for m in ["listening", "reading", "writing", "speaking"] if module_stats[m]["latest_band"] > 0]
    overall = round(sum(latest_bands) / max(len(latest_bands), 1) * 2) / 2 if latest_bands else 0

    return {"modules": module_stats, "overall_estimated_band": overall, "total_attempts": len(attempts)}

# ==========================================
# AI EXAM GENERATION
# ==========================================
@api_router.post("/exams/generate")
async def generate_exam_endpoint(background_tasks: BackgroundTasks, request: Request):
    user = await get_current_user(request)
    exam_id = f"exam_{uuid.uuid4().hex[:8]}"
    await db.exams.insert_one({
        "exam_id": exam_id, "title": "Generating...", "pathway": "academic",
        "status": "generating_content", "audio_progress": 0,
        "created_at": datetime.now(timezone.utc).isoformat(), "created_by": user["user_id"],
        "listening": {"sections": [], "total_questions": 0, "duration_minutes": 30},
        "reading": {"passages": [], "total_questions": 0, "duration_minutes": 60},
        "writing": {"tasks": [], "total_time_minutes": 60},
        "speaking": {"parts": [], "total_time_minutes": 14}
    })
    background_tasks.add_task(ai_generate_exam, exam_id)
    return {"exam_id": exam_id, "status": "generating_content"}

async def ai_generate_exam(exam_id: str):
    """Full AI exam generation pipeline"""
    try:
        # Generate listening content with proper IELTS CBT layout
        listening_prompt = """Generate an IELTS Listening test with 4 sections, 10 questions each (40 total).
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

The question_layout format for each section:
{
  "title": "Phone call about booking a hotel",
  "instruction": "Complete the notes below. Write NO MORE THAN TWO WORDS AND/OR A NUMBER for each answer.",
  "groups": [
    {"heading": "Booking Details", "items": ["Room type: {1}", "Price: ${2} per night", "Check-in: {3} October"]}
  ]
}

For multiple_choice questions, use regular question format (no layout needed).

Return JSON: {"sections": [{"section_num": 1, "title": "...", "context": "...", "instruction": "You will hear...",
"speakers": [{"name": "Speaker A", "role": "receptionist"}],
"script_segments": [{"speaker": "Speaker A", "text": "[cheerful] Hello..."}],
"question_layout": {"title": "...", "instruction": "Complete the notes...", "groups": [{"heading": "...", "items": ["... {1} ..."]}]},
"questions": [{"question_num": 1, "question_type": "form_completion", "correct_answer": "Smith"}]}]}"""

        listening_raw = await call_openrouter([
            {"role": "system", "content": "Generate realistic IELTS content. Return valid JSON only."},
            {"role": "user", "content": listening_prompt}
        ])
        listening_data = json.loads(listening_raw)

        # Assign gender-appropriate voice IDs to IELTS speakers
        for section in listening_data.get("sections", []):
            assign_voices_to_speakers(
                section.get("speakers", []),
                female_pool=_FEMALE_VOICES,
                male_pool=_MALE_VOICES,
            )

        # Generate reading content
        reading_prompt = """Generate 3 IELTS Academic Reading passages (600-800 words each) with 13-14 questions each (40 total).
Topics: 1) Science/Technology 2) Social Science 3) Natural World

Question types: true_false_not_given, multiple_choice, matching_headings, sentence_completion, short_answer

Return JSON: {"passages": [{"passage_num": 1, "title": "...", "text": "...(full passage text)...",
"questions": [{"question_num": 1, "question_type": "true_false_not_given", "question_text": "...", "correct_answer": "True"}]}]}"""

        reading_raw = await call_openrouter([
            {"role": "system", "content": "Generate realistic IELTS content. Return valid JSON only."},
            {"role": "user", "content": reading_prompt}
        ])
        reading_data = json.loads(reading_raw)

        # Writing tasks (simple prompts)
        writing = {
            "total_time_minutes": 60,
            "tasks": [
                {"task_num": 1, "task_type": "describe_visual", "prompt": "The bar chart below shows the number of international students enrolled in three different faculties at a UK university from 2018 to 2023. Summarise the information by selecting and reporting the main features, and make comparisons where relevant. Write at least 150 words.", "min_words": 150, "time_minutes": 20},
                {"task_num": 2, "task_type": "essay", "prompt": "Some people believe that the best way to improve public health is by increasing the number of sports facilities. Others think this would have little effect and other measures are needed. Discuss both views and give your own opinion. Write at least 250 words.", "min_words": 250, "time_minutes": 40}
            ]
        }

        # Speaking parts
        speaking = {
            "total_time_minutes": 14,
            "parts": [
                {"part_num": 1, "title": "Introduction and Interview", "time_minutes": 5, "instructions": "The examiner will ask you questions about familiar topics.",
                 "questions": [
                     {"question_num": 1, "question_text": "Let's talk about where you live. Can you describe your neighbourhood?", "needs_audio": True},
                     {"question_num": 2, "question_text": "What do you like most about living there?", "needs_audio": True},
                     {"question_num": 3, "question_text": "Now let's talk about reading. How often do you read books?", "needs_audio": True},
                     {"question_num": 4, "question_text": "What kind of books do you enjoy reading?", "needs_audio": True}
                 ]},
                {"part_num": 2, "title": "Individual Long Turn", "time_minutes": 4, "preparation_time": 60,
                 "instructions": "You will have 1 minute to prepare, then speak for 1-2 minutes.",
                 "cue_card": "Describe a skill you learned that you found difficult at first.\nYou should say:\n- what the skill was\n- when you learned it\n- how you learned it\n- and explain why it was difficult at first",
                 "questions": [{"question_num": 5, "question_text": "Now I'd like you to talk about the following topic. You have one minute to prepare.", "needs_audio": True}]},
                {"part_num": 3, "title": "Two-way Discussion", "time_minutes": 5,
                 "instructions": "The examiner will ask you more abstract questions related to Part 2.",
                 "questions": [
                     {"question_num": 6, "question_text": "What skills do you think are most important for young people to learn today?", "needs_audio": True},
                     {"question_num": 7, "question_text": "Do you think schools should focus more on practical skills or academic knowledge?", "needs_audio": True},
                     {"question_num": 8, "question_text": "How has technology changed the way people learn new skills?", "needs_audio": True}
                 ]}
            ]
        }

        # Number reading questions sequentially
        q_num = 1
        for passage in reading_data.get("passages", []):
            for q in passage.get("questions", []):
                q["question_num"] = q_num
                q_num += 1

        await db.exams.update_one({"exam_id": exam_id}, {"$set": {
            "title": f"AI Practice Test - {datetime.now(timezone.utc).strftime('%b %d, %Y')}",
            "listening": {**listening_data, "total_questions": 40, "duration_minutes": 30},
            "reading": {**reading_data, "total_questions": 40, "duration_minutes": 60},
            "writing": writing, "speaking": speaking,
            "status": "pending_audio"
        }})

        await generate_exam_audio(exam_id)
    except Exception as e:
        logger.error(f"AI exam generation error: {e}")
        await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "error", "error_message": str(e)}})

@api_router.post("/exams/generate-telc")
async def generate_telc_endpoint(background_tasks: BackgroundTasks, request: Request):
    body = await request.json()
    level = body.get("level", "B1")
    user = await get_current_user(request)
    exam_id = f"exam_telc_{uuid.uuid4().hex[:8]}"
    await db.exams.insert_one({
        "exam_id": exam_id,
        "title": f"TELC Deutsch {level} - KI-generiert",
        "exam_type": "telc",
        "telc_level": level,
        "pathway": f"telc_{level.lower()}",
        "status": "generating_content",
        "audio_progress": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": user["user_id"],
        "lesen":     {"aufgaben": [], "total_questions": 15, "duration_minutes": 90},
        "hoeren":    {"aufgaben": [], "total_questions": 15, "duration_minutes": 30},
        "schreiben": {"aufgaben": [], "total_time_minutes": 30},
        "sprechen":  {"teile": [], "total_time_minutes": 15}
    })
    background_tasks.add_task(ai_generate_telc_exam, exam_id, level)
    return {"exam_id": exam_id, "status": "generating_content"}

def _telc_level_spec(level: str) -> str:
    """Return explicit CEFR level specification to inject into every generation prompt."""
    if level == "B1":
        return """CEFR B1 LEVEL REQUIREMENTS — apply strictly:
TOPICS: Everyday life, work, school, shopping, travel, leisure, family, health (familiar, concrete).
VOCABULARY: High-frequency everyday words only (~2,000–3,000 word range). NO academic, technical or low-frequency vocabulary.
GRAMMAR: Present, Perfekt, Präteritum (sein/haben/modals), basic subordinate clauses (weil, dass, wenn). NO Konjunktiv II, NO complex passive, NO participial constructions.
TEXT COMPLEXITY: Short, clear sentences. Simple paragraph structure. Explicit information — no inference required.
LISTENING SCRIPTS: Slow-to-normal speed, clear pronunciation, simple colloquial speech. Topics: booking appointments, everyday shopping, weather, transport, simple workplace talks.
QUESTIONS: Test explicit/literal understanding. Correct answer is directly stated in the text. Distractors are plausible but clearly wrong.
WRONG-LEVEL EXAMPLES TO AVOID: No academic articles, no professional jargon, no abstract social commentary, no complex argumentation."""
    else:  # B2
        return """CEFR B2 LEVEL REQUIREMENTS — apply strictly:
TOPICS: Abstract and professional: environment, technology, society, culture, health systems, professional development, globalisation, media, science. NOT everyday shopping or simple travel.
VOCABULARY: Wide range including abstract nouns, academic collocations, topic-specific terminology (e.g. "Nachhaltigkeit", "Globalisierung", "Fachkräftemangel"). Idiomatic expressions.
GRAMMAR: Konjunktiv II (hypotheticals/indirect speech), Passiv constructions (wird gebaut, wurde entlassen), complex subordinate clauses, participial constructions, nominalisations.
TEXT COMPLEXITY: Complex multi-paragraph texts with argumentation, counterarguments, implicit meaning. Requires inferencing beyond literal text.
LISTENING SCRIPTS: Normal/natural speed, complex sentence structures, speaker attitude/opinion must be inferred. Topics: expert interviews, radio discussions, workplace conflicts, academic lectures.
QUESTIONS: Test ability to infer, identify opinions, understand nuance. Correct answers often require combining two pieces of information or recognising implicit meaning. Distractors are very close to the correct answer."""


async def ai_generate_telc_exam(exam_id: str, level: str):
    """Generate a telc Deutsch B1/B2 exam using aufgaben format (matches audio pipeline + seed data)."""
    level_spec = _telc_level_spec(level)
    sys_prompt = f"You are a certified telc Deutsch {level} exam author with 15 years experience writing official exams. Return valid JSON only. CRITICAL: {level_spec}"

    try:
        # ── LESEVERSTEHEN ─────────────────────────────────────────────────────
        lesen_prompt = f"""Generate a telc Deutsch {level} Leseverstehen test with exactly 3 Aufgaben.

MANDATORY LEVEL: {level_spec}

Aufgabe 1 (q1-5): Zuordnung
5 short texts (A-E, 80-120 words each). {"Topics: notices, short articles on everyday topics." if level == "B1" else "Topics: newspaper extracts, opinion pieces, professional announcements."}
10 headings (a-j); only 5 match the texts.
correct_answer: heading letter

Aufgabe 2 (q6-10): Multiple Choice
One {"informational text (280-350 words) on a familiar everyday topic — must be clearly B1: simple language, concrete situation." if level == "B1" else "complex text (400-550 words) on an abstract/professional topic — argumentation, statistics, expert views. Language must be genuinely B2: complex grammar, academic vocabulary."}
5 questions with options a/b/c. {"Correct answer directly stated in text." if level == "B1" else "At least 2 questions require inferencing or understanding implied meaning."}

Aufgabe 3 (q11-20): Anzeigen-Zuordnung
10 situations + 12 short ads/notices (a-l). {"Situations and ads describe everyday needs: courses, jobs, services." if level == "B1" else "Situations and ads cover professional/specific needs: specialist training, services with conditions, technical requirements."}
2 ads have no matching situation (answer x).

All text and questions in German only.

Return JSON:
{{"aufgaben": [
  {{"aufgabe_num": 1, "typ": "zuordnung",
    "short_texts": [{{"id": "A", "text": "..."}}],
    "ueberschriften": [{{"id": "a", "text": "..."}}, ...10 total],
    "questions": [{{"question_num": 1, "question_text": "Text A - Welche Überschrift passt?", "correct_answer": "d"}}]}},
  {{"aufgabe_num": 2, "typ": "multiple_choice",
    "text": "...",
    "questions": [{{"question_num": 6, "question_text": "...", "options": ["a) ...", "b) ...", "c) ..."], "correct_answer": "a"}}]}},
  {{"aufgabe_num": 3, "typ": "anzeigen",
    "anzeigen": [{{"id": "a", "text": "..."}}],
    "questions": [{{"question_num": 11, "situation": "...", "question_text": "Welche Anzeige passt?", "correct_answer": "c"}}]}}
]}}

Return JSON:
{{"aufgaben": [
  {{"aufgabe_num": 1, "typ": "zuordnung",
    "short_texts": [{{"id": "A", "text": "..."}}],
    "ueberschriften": [{{"id": "a", "text": "..."}}, ...10 total],
    "questions": [{{"question_num": 1, "question_text": "Text A - Welche Überschrift passt?", "correct_answer": "d"}}]}},
  {{"aufgabe_num": 2, "typ": "multiple_choice",
    "text": "...",
    "questions": [{{"question_num": 6, "question_text": "...", "options": ["a) ...", "b) ...", "c) ..."], "correct_answer": "a"}}]}},
  {{"aufgabe_num": 3, "typ": "anzeigen",
    "anzeigen": [{{"id": "a", "text": "..."}}],
    "questions": [{{"question_num": 11, "situation": "...", "question_text": "...", "correct_answer": "c"}}]}}
]}}"""

        lesen_raw = await call_openrouter([
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": lesen_prompt}
        ])
        lesen_data = json.loads(lesen_raw)
        lesen_data["total_questions"] = 15
        lesen_data["duration_minutes"] = 90

        # ── HÖRVERSTEHEN ──────────────────────────────────────────────────────
        hoeren_prompt = f"""Generate a telc Deutsch {level} Hörverstehen test with 3 Aufgaben.
All questions are Richtig/Falsch only.

MANDATORY LEVEL: {{level_spec}}

Aufgabe 1 (q1-5): Kurzgespräche
5 short conversations (4-5 exchanges each).
Topics for B1: booking appointments, transport, everyday shopping, simple workplace.
Topics for B2: expert interviews, workplace conflicts, academic discussions, social debates.
Each: 1 Richtig/Falsch. Heard ONCE.
B2 requirement: questions should test inference of speaker attitude, not just literal facts.

Aufgabe 2 (q6-15): Gespräch
One longer conversation (14-16 exchanges).
B1: everyday dialogue — friends planning, customer service call, simple meeting.
B2: expert interview or discussion on abstract topic — environment, technology, social issues.
10 Richtig/Falsch (q6-15). Heard TWICE.
B2: at least 4 questions require understanding implied meaning or speaker viewpoint.

Aufgabe 3 (q16-20): Ansagen
5 short announcements. B1: station/shop/event info (2-3 simple sentences).
B2: official radio bulletin or company message with conditions/exceptions (3-4 sentences, complex structure).
1 Richtig/Falsch per announcement (q16-20). Heard TWICE.

Speech style B1: clear, standard, moderate pace. B2: natural speed, fillers, complex clauses.
Sprecher: "Frau Müller", "Herr Schmidt", "Moderatorin", "Ansager", etc.

Return JSON:
{{"aufgaben": [
  {{"aufgabe_num": 1, "typ": "kurzgespraeche",
    "sprecher": [{{"name": "...", "voice_id": ""}}],
    "conversations": [
      {{"conv_num": 1,
        "sprecher": [{{"name": "Frau Koch", "voice_id": ""}}, {{"name": "Herr Bauer", "voice_id": ""}}],
        "script_segments": [{{"sprecher": "Frau Koch", "text": "..."}}],
        "questions": [{{"question_num": 1, "question_text": "...", "correct_answer": "Richtig"}}]
      }}
    ]}},
  {{"aufgabe_num": 2, "typ": "gespraech",
    "sprecher": [{{"name": "Moderatorin", "voice_id": ""}}, {{"name": "Herr Bauer", "voice_id": ""}}],
    "script_segments": [{{"sprecher": "Moderatorin", "text": "..."}}],
    "questions": [{{"question_num": 6, "question_text": "...", "correct_answer": "Falsch"}}]}},
  {{"aufgabe_num": 3, "typ": "ansagen",
    "ansagen": [
      {{"ansage_num": 1, "text": "...", "question_num": 11, "question_text": "...", "correct_answer": "Richtig"}}
    ]}}
]}}"""

        hoeren_raw = await call_openrouter([
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": hoeren_prompt}
        ])
        hoeren_data = json.loads(hoeren_raw)
        hoeren_data["total_questions"] = 15
        hoeren_data["duration_minutes"] = 30

        # Assign gender-appropriate German voices to TELC sprecher
        for aufgabe in hoeren_data.get("aufgaben", []):
            assign_voices_to_speakers(
                aufgabe.get("sprecher", []),
                female_pool=_FEMALE_VOICES_DE,
                male_pool=_MALE_VOICES_DE,
            )
            for conv in aufgabe.get("conversations", []):
                assign_voices_to_speakers(
                    conv.get("sprecher", []),
                    female_pool=_FEMALE_VOICES_DE,
                    male_pool=_MALE_VOICES_DE,
                )

        # ── SPRACHBAUSTEINE ───────────────────────────────────────────────────
        sprachbausteine_prompt = f"""Generate a telc Deutsch {level} Sprachbausteine test with exactly 2 Aufgaben.

MANDATORY LEVEL: {level_spec}

Aufgabe 1 — Lückentext Multiple Choice (q21–30)
B1: A personal letter or informal email about everyday topic (200-240 words).
B2: A semi-formal letter, report excerpt, or article on a professional/abstract topic (220-260 words).
Exactly 10 numbered gaps as {{21}}, {{22}} ... {{30}}.
3 options per gap (a/b/c). B1: test common prepositions, articles, simple conjunctions, present/past verb forms.
B2: test Konjunktiv II, Passiv, complex conjunctions (obwohl, sodass, während), nominalisations, subjunctive indirect speech.
correct_answer: "a", "b", or "c"

Aufgabe 2 — Lückentext Wortschatz (q31–40)
B1: Everyday letter or notice (hotel, club, neighbourhood), 200-240 words. Words are common, concrete nouns/verbs.
B2: Professional or academic text (company announcement, research summary, news article), 220-260 words. Words include abstract nouns, collocations, technical terms.
Exactly 10 gaps as {{31}}, {{32}} ... {{40}}.
Word bank: 15 CAPITALISED words (a–o), 5 distractors. B2: distractors should be semantically close (same field, wrong collocate).
correct_answer: bank letter

All in German only.

Return JSON:
{{"aufgaben": [
  {{"aufgabe_num": 1, "typ": "lueckentext_mc",
    "text_with_gaps": "...full text with {21} {22} ... {30} markers...",
    "options": [
      {{"question_num": 21, "a": "aber", "b": "denn", "c": "sondern", "correct_answer": "b"}},
      ... 10 total
    ]}},
  {{"aufgabe_num": 2, "typ": "lueckentext_wortbank",
    "text_with_gaps": "...full text with {31} {32} ... {40} markers...",
    "wortbank": [{{"id": "a", "word": "BESONDERS"}}, ... 15 total],
    "options": [
      {{"question_num": 31, "correct_answer": "a"}},
      ... 10 total
    ]}}
]}}"""

        sprachbausteine_raw = await call_openrouter([
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": sprachbausteine_prompt}
        ])
        sprachbausteine_data = json.loads(sprachbausteine_raw)
        sprachbausteine_data["total_questions"] = 20
        sprachbausteine_data["duration_minutes"] = 90

        # ── SCHRIFTLICHER AUSDRUCK ────────────────────────────────────────────
        writing_topics = {"B1": "Umzug, Reise oder Freizeitaktivität", "B2": "Beruf, Weiterbildung oder gesellschaftliches Thema"}
        schreiben = {
            "total_time_minutes": 30,
            "aufgaben": [{
                "aufgabe_num": 1,
                "aufgabe_typ": "brief_email",
                "aufgabe": f"Sie haben eine E-Mail von Ihrer Freundin / Ihrem Freund bekommen. Sie/Er bittet Sie um Hilfe oder Rat zu einem alltäglichen Thema ({writing_topics.get(level, '')})."
                          f" Schreiben Sie eine Antwort-E-Mail (ca. 100 Wörter). Schreiben Sie zu allen drei Punkten:\n"
                          f"- ob und wie Sie helfen können\n- wann Sie Zeit haben\n- ein konkreter Vorschlag",
                "min_words": 80, "max_words": 130
            }]
        }

        # ── MÜNDLICHER AUSDRUCK ───────────────────────────────────────────────
        topic = "Homeoffice" if level == "B1" else "Nachhaltigkeit im Alltag"
        sprechen = {
            "total_time_minutes": 15,
            "vorbereitungszeit_minutes": 20,
            "teile": [
                {
                    "teil_num": 1, "titel": "Einander kennenlernen",
                    "instructions": "Stellen Sie sich vor und beantworten Sie die Fragen des Prüfers.",
                    "themen": ["Name", "Herkunft", "Wohnsituation", "Beruf/Studium", "Deutschlernen", "Hobbys"],
                    "fragen": [
                        {"frage_num": 1, "frage_text": "[warm] Guten Tag! Willkommen. Wie heißen Sie und woher kommen Sie?", "needs_audio": True},
                        {"frage_num": 2, "frage_text": "Was machen Sie beruflich oder studieren Sie gerade?", "needs_audio": True},
                        {"frage_num": 3, "frage_text": "Wie lange lernen Sie schon Deutsch und warum?", "needs_audio": True},
                        {"frage_num": 4, "frage_text": "Was machen Sie in Ihrer Freizeit?", "needs_audio": True},
                    ]
                },
                {
                    "teil_num": 2, "titel": "Uber ein Thema sprechen",
                    "instructions": f'Jede Person hat einen kurzen Text zum Thema "{topic}" gelesen. Stellen Sie Ihre Meinung vor und diskutieren Sie dann gemeinsam.',
                    "thema": topic,
                    "meinungen": [
                        {"person": "A", "zitat": f'"Ich finde {topic} sehr positiv, weil man flexibler und produktiver ist."'},
                        {"person": "B", "zitat": f'"Ich sehe {topic} kritisch - die Grenzen zwischen Arbeit und Freizeit verschwimmen."'}
                    ],
                    "fragen": [
                        {"frage_num": 5, "frage_text": f"Was haben Sie uber {topic} gelesen? Stellen Sie Ihre Meinung vor.", "needs_audio": True},
                        {"frage_num": 6, "frage_text": "Was denken Sie - welche Meinung uberzeugt mehr?", "needs_audio": True},
                        {"frage_num": 7, "frage_text": "Haben Sie eigene Erfahrungen mit diesem Thema?", "needs_audio": True},
                    ]
                },
                {
                    "teil_num": 3, "titel": "Gemeinsam etwas planen",
                    "instructions": "Planen Sie gemeinsam eine Veranstaltung fur Ihre Sprachschule.",
                    "szenario": "Ihre Sprachschule mochte ein Sommerfest fur alle Teilnehmer organisieren.",
                    "planungspunkte": ["Wann und wo?", "Programm und Aktivitaten", "Essen und Getranke", "Aufgabenverteilung"],
                    "fragen": [
                        {"frage_num": 8, "frage_text": "Wann und wo soll das Fest stattfinden? Was schlagen Sie vor?", "needs_audio": True},
                        {"frage_num": 9, "frage_text": "Was soll auf dem Programm stehen?", "needs_audio": True},
                        {"frage_num": 10, "frage_text": "Wer ubernimmt welche Aufgaben fur das Fest?", "needs_audio": True},
                    ]
                }
            ]
        }

        await db.exams.update_one({"exam_id": exam_id}, {"$set": {
            "title": f"TELC Deutsch {level} - Übungstest ({datetime.now(timezone.utc).strftime('%d.%m.%Y')})",
            "lesen": lesen_data,
            "hoeren": hoeren_data,
            "sprachbausteine": sprachbausteine_data,
            "schreiben": schreiben,
            "sprechen": sprechen,
            "status": "pending_audio"
        }})
        await generate_exam_audio(exam_id)
    except Exception as e:
        logger.error(f"TELC generation error: {e}")
        await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "error", "error_message": str(e)}})


# ==========================================
# SEED DATA & STARTUP
# ==========================================
def get_seed_exam():
    return {
        "exam_id": "exam_academic_001",
        "title": "IELTS Academic Practice Test 1",
        "pathway": "academic",
        "status": "pending_audio",
        "audio_progress": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "listening": {
            "total_questions": 40,
            "duration_minutes": 30,
            "sections": [
                {
                    "section_num": 1,
                    "title": "Hotel Reservation",
                    "context": "A conversation between a hotel receptionist and a guest making a booking",
                    "speakers": [
                        {"name": "Receptionist", "voice_id": "Aoede"},
                        {"name": "Guest", "voice_id": "Charon"}
                    ],
                    "instruction": "You will hear a conversation between a hotel receptionist and a guest making a booking. First, you have some time to look at questions 1 to 10. Now listen carefully and answer questions 1 to 10.",
                    "script_segments": [
                        {"speaker": "Receptionist", "text": "[cheerful] Good morning, Parkview Hotel. How may I help you today?"},
                        {"speaker": "Guest", "text": "Hi there. I'd like to book a room for next weekend, please."},
                        {"speaker": "Receptionist", "text": "Of course. And what dates would that be exactly?"},
                        {"speaker": "Guest", "text": "From Friday the fifteenth to Sunday the seventeenth. So that's two nights."},
                        {"speaker": "Receptionist", "text": "[helpful] Lovely. We have two room types available. Standard rooms are eighty-five pounds per night, and our deluxe rooms are one hundred and twenty pounds."},
                        {"speaker": "Guest", "text": "The standard room will be fine, thanks."},
                        {"speaker": "Receptionist", "text": "No problem. And that does include breakfast, which is served in the restaurant on the ground floor from seven until ten each morning."},
                        {"speaker": "Guest", "text": "Great, that's good to know. Is there parking available at the hotel?"},
                        {"speaker": "Receptionist", "text": "Yes, we have a car park at the back of the building. It's completely free for guests staying with us."},
                        {"speaker": "Guest", "text": "Perfect. [pause] One more thing, would it be possible to get a late checkout on the Sunday?"},
                        {"speaker": "Receptionist", "text": "We can arrange that. Late checkout is available until two p.m. for an additional fifteen pounds."},
                        {"speaker": "Guest", "text": "That works for me. Can I go ahead and book now?"},
                        {"speaker": "Receptionist", "text": "[professional] Certainly. I'll just need a few details. Could I have your full name please?"},
                        {"speaker": "Guest", "text": "Yes, it's David Thompson. That's T-H-O-M-P-S-O-N."},
                        {"speaker": "Receptionist", "text": "Thank you, Mr Thompson. And a contact telephone number?"},
                        {"speaker": "Guest", "text": "It's oh seven four five six, eight nine three two one four."},
                        {"speaker": "Receptionist", "text": "And could I take an email address as well?"},
                        {"speaker": "Guest", "text": "Sure, it's d dot thompson at mailbox dot com."},
                        {"speaker": "Receptionist", "text": "[cheerful] Wonderful. Your reservation is confirmed. The total comes to one hundred and eighty-five pounds, including the late checkout. Is there anything else I can help with?"},
                        {"speaker": "Guest", "text": "No, that's everything. Thank you very much."},
                        {"speaker": "Receptionist", "text": "You're welcome. We look forward to seeing you on the fifteenth. Have a lovely day."}
                    ],
                    "question_layout": {
                        "title": "Hotel Reservation Form",
                        "instruction": "Complete the form below. Write NO MORE THAN TWO WORDS AND/OR A NUMBER for each answer.",
                        "groups": [
                            {"heading": "Booking Details", "items": [
                                "Room type: {1}",
                                "Rate: ${2} per night",
                                "Breakfast served: {3} to 10 a.m.",
                                "Restaurant: on the {4} floor"
                            ]},
                            {"heading": "Facilities", "items": [
                                "Car park: at the {5} of building",
                                "Late checkout: until {6} p.m.",
                                "Additional charge: ${7}"
                            ]},
                            {"heading": "Guest Information", "items": [
                                "Surname: {8}",
                                "Contact number: 07456 {9}",
                                "Total booking cost: ${10}"
                            ]}
                        ]
                    },
                    "questions": [
                        {"question_num": 1, "question_type": "form_completion", "question_text": "Room type booked:", "correct_answer": "standard"},
                        {"question_num": 2, "question_type": "form_completion", "question_text": "Price per night: $ ________", "correct_answer": "85"},
                        {"question_num": 3, "question_type": "form_completion", "question_text": "Breakfast served from ________ to 10", "correct_answer": "7"},
                        {"question_num": 4, "question_type": "form_completion", "question_text": "Restaurant located on the ________ floor", "correct_answer": "ground"},
                        {"question_num": 5, "question_type": "form_completion", "question_text": "Car park is at the ________ of the building", "correct_answer": "back"},
                        {"question_num": 6, "question_type": "form_completion", "question_text": "Late checkout available until ________ p.m.", "correct_answer": "2"},
                        {"question_num": 7, "question_type": "form_completion", "question_text": "Additional charge for late checkout: $ ________", "correct_answer": "15"},
                        {"question_num": 8, "question_type": "form_completion", "question_text": "Guest surname:", "correct_answer": "Thompson|thomson"},
                        {"question_num": 9, "question_type": "form_completion", "question_text": "Contact number: 07456 ________", "correct_answer": "893214"},
                        {"question_num": 10, "question_type": "form_completion", "question_text": "Total booking cost: $ ________", "correct_answer": "185"}
                    ]
                },
                {
                    "section_num": 2,
                    "title": "City Transport Guide",
                    "context": "A transport officer giving information about public transport options in a city",
                    "speakers": [{"name": "Officer", "voice_id": "Kore"}],
                    "instruction": "You will hear a transport officer giving information about public transport options in a city. First, you have some time to look at questions 11 to 20. Now listen carefully and answer questions 11 to 20.",
                    "script_segments": [
                        {"speaker": "Officer", "text": "[warm] Good afternoon everyone, and welcome to the Riverside City orientation session. I'm going to give you an overview of the public transport options available to you here."},
                        {"speaker": "Officer", "text": "First, let me tell you about the bus network. The city operates twelve main bus routes that cover all major areas. Single tickets cost two pounds fifty, but if you're going to be here for a while, I'd recommend getting a weekly pass for just fourteen pounds."},
                        {"speaker": "Officer", "text": "[informative] The most useful route for visitors is the number seven bus, which runs between the train station and the harbour, passing through the main shopping district along Victoria Street."},
                        {"speaker": "Officer", "text": "Now, the buses run every fifteen minutes during peak hours, that's from seven a.m. to nine a.m. and four p.m. to six p.m. Outside those times, you can expect a bus every twenty-five minutes."},
                        {"speaker": "Officer", "text": "Moving on to the tram system. [pause] The tram was introduced in two thousand and eighteen and has become very popular. It runs on a single line from the university campus in the north down to the Riverside Market in the south."},
                        {"speaker": "Officer", "text": "Tram tickets are slightly cheaper at one pound eighty per journey. The journey from one end to the other takes approximately twenty-two minutes."},
                        {"speaker": "Officer", "text": "[helpful] For those of you who prefer cycling, the city has an excellent bike-sharing scheme called PedalGo. There are forty-three docking stations around the city. The first thirty minutes of each ride are free, and after that it's one pound per hour."},
                        {"speaker": "Officer", "text": "Finally, I should mention that the central area of the city, within the old city walls, is a pedestrian zone. No vehicles are allowed there between ten a.m. and four p.m. on weekdays. [slowly] This area includes Market Square, Cathedral Lane, and the Heritage Quarter."}
                    ],
                    "question_layout": {
                        "title": "Riverside City Public Transport",
                        "instruction": "Complete the notes below. Write NO MORE THAN TWO WORDS AND/OR A NUMBER for each answer.",
                        "groups": [
                            {"heading": "Bus Network", "items": [
                                "Main bus routes: {11}",
                                "Weekly pass: ${12}",
                                "Route 7: train station to the {13}",
                                "Peak frequency: every {14} minutes",
                                "Off-peak frequency: every {15} minutes"
                            ]},
                            {"heading": "Tram System", "items": [
                                "Year introduced: {16}",
                                "Full journey time: {17} minutes"
                            ]},
                            {"heading": "Cycling", "items": [
                                "Bike-sharing scheme name: {18}",
                                "Number of docking stations: {19}"
                            ]},
                            {"heading": "Pedestrian Zone", "items": [
                                "Vehicles banned from: {20} a.m."
                            ]}
                        ]
                    },
                    "questions": [
                        {"question_num": 11, "question_type": "form_completion", "question_text": "Number of main bus routes:", "correct_answer": "12"},
                        {"question_num": 12, "question_type": "form_completion", "question_text": "Cost of a weekly bus pass: $ ________", "correct_answer": "14"},
                        {"question_num": 13, "question_type": "form_completion", "question_text": "Route 7 runs between the train station and the ________", "correct_answer": "harbour"},
                        {"question_num": 14, "question_type": "form_completion", "question_text": "Peak hour bus frequency: every ________ minutes", "correct_answer": "15"},
                        {"question_num": 15, "question_type": "form_completion", "question_text": "Off-peak bus frequency: every ________ minutes", "correct_answer": "25"},
                        {"question_num": 16, "question_type": "form_completion", "question_text": "The tram was introduced in ________", "correct_answer": "2018"},
                        {"question_num": 17, "question_type": "form_completion", "question_text": "Tram journey time end to end: ________ minutes", "correct_answer": "22"},
                        {"question_num": 18, "question_type": "form_completion", "question_text": "Bike scheme name:", "correct_answer": "PedalGo|Pedal Go|pedalgo"},
                        {"question_num": 19, "question_type": "form_completion", "question_text": "Number of bike docking stations:", "correct_answer": "43"},
                        {"question_num": 20, "question_type": "form_completion", "question_text": "Pedestrian zone closes to vehicles at ________ a.m.", "correct_answer": "10"}
                    ]
                },
                {
                    "section_num": 3,
                    "title": "Research Project Discussion",
                    "context": "Three university students discussing their group research project",
                    "speakers": [
                        {"name": "Tutor", "voice_id": "Orus"},
                        {"name": "Sarah", "voice_id": "Aoede"},
                        {"name": "James", "voice_id": "Puck"}
                    ],
                    "instruction": "You will hear a discussion between a tutor and two students about their research project. First, you have some time to look at questions 21 to 30. Now listen carefully and answer questions 21 to 30.",
                    "script_segments": [
                        {"speaker": "Tutor", "text": "Right, Sarah and James, let's discuss how your research project on renewable energy adoption is progressing. Sarah, would you like to start?"},
                        {"speaker": "Sarah", "text": "Sure. Well, we've completed the literature review, which took us about three weeks. We found some really interesting studies on solar panel adoption in suburban areas."},
                        {"speaker": "James", "text": "Yes, and I've been working on the survey design. We're planning to distribute it to households in two different neighbourhoods, one affluent area and one mixed-income area."},
                        {"speaker": "Tutor", "text": "[thoughtful] That's a good approach for comparison. How many responses are you aiming for?"},
                        {"speaker": "Sarah", "text": "We're targeting two hundred responses in total, one hundred from each area. We think that should give us enough data for statistical significance."},
                        {"speaker": "James", "text": "The main challenge we've faced is getting ethical approval. It took longer than expected because we had to revise our consent forms twice."},
                        {"speaker": "Tutor", "text": "That's quite common actually. What about your methodology? Are you using qualitative or quantitative methods?"},
                        {"speaker": "Sarah", "text": "Both, actually. The survey provides the quantitative data, and we're also conducting twelve in-depth interviews with homeowners who've already installed solar panels."},
                        {"speaker": "James", "text": "[enthusiastic] One interesting finding from the pilot survey is that the biggest barrier isn't cost, as we initially assumed. It's actually a lack of information about the installation process."},
                        {"speaker": "Tutor", "text": "That is interesting. When do you expect to have all the data collected?"},
                        {"speaker": "Sarah", "text": "We're aiming to finish data collection by the end of March, which gives us six weeks for analysis and writing up before the May deadline."},
                        {"speaker": "James", "text": "Sarah is handling the statistical analysis using SPSS software, and I'll be doing the thematic analysis of the interview transcripts."}
                    ],
                    "question_layout": {
                        "title": "Research Project on Renewable Energy",
                        "instruction": "Complete the notes below. Write NO MORE THAN THREE WORDS AND/OR A NUMBER for each answer.",
                        "groups": [
                            {"heading": "Project Overview", "items": [
                                "Topic: {21}",
                                "Literature review took: {22} weeks",
                                "Comparison areas: affluent and {23}"
                            ]},
                            {"heading": "Survey Design", "items": [
                                "Target responses: {24}",
                                "Document revised for ethics: {25}",
                                "Number of interviews: {26}"
                            ]},
                            {"heading": "Key Findings", "items": [
                                "Biggest barrier to adoption: {27}",
                                "Data collection deadline: end of {28}",
                                "Statistical software: {29}",
                                "James will do {30} analysis"
                            ]}
                        ]
                    },
                    "questions": [
                        {"question_num": 21, "question_type": "short_answer", "question_text": "What topic is the students' research project about?", "correct_answer": "renewable energy adoption"},
                        {"question_num": 22, "question_type": "form_completion", "question_text": "The literature review took ________ weeks", "correct_answer": "3|three"},
                        {"question_num": 23, "question_type": "short_answer", "question_text": "The survey will compare an affluent area with a ________ area", "correct_answer": "mixed-income"},
                        {"question_num": 24, "question_type": "form_completion", "question_text": "Target number of total survey responses:", "correct_answer": "200"},
                        {"question_num": 25, "question_type": "short_answer", "question_text": "What document had to be revised twice for ethical approval?", "correct_answer": "consent forms"},
                        {"question_num": 26, "question_type": "form_completion", "question_text": "Number of in-depth interviews planned:", "correct_answer": "12"},
                        {"question_num": 27, "question_type": "short_answer", "question_text": "According to the pilot survey, the biggest barrier to solar panel adoption is:", "correct_answer": "lack of information"},
                        {"question_num": 28, "question_type": "form_completion", "question_text": "Data collection deadline: end of ________", "correct_answer": "March"},
                        {"question_num": 29, "question_type": "short_answer", "question_text": "What software will Sarah use for statistical analysis?", "correct_answer": "SPSS"},
                        {"question_num": 30, "question_type": "short_answer", "question_text": "James will perform ________ analysis of interviews", "correct_answer": "thematic"}
                    ]
                },
                {
                    "section_num": 4,
                    "title": "Marine Conservation Lecture",
                    "context": "A university lecture on marine conservation and coral reef restoration",
                    "speakers": [{"name": "Professor", "voice_id": "Rasalgethi"}],
                    "instruction": "You will hear a university lecture on marine conservation and coral reef restoration. First, you have some time to look at questions 31 to 40. Now listen carefully and answer questions 31 to 40.",
                    "script_segments": [
                        {"speaker": "Professor", "text": "[scholarly] Good morning. Today's lecture focuses on marine conservation, specifically the efforts being made to restore coral reef ecosystems around the world."},
                        {"speaker": "Professor", "text": "Coral reefs are often called the rainforests of the sea, and for good reason. Although they cover less than one percent of the ocean floor, they support approximately twenty-five percent of all known marine species."},
                        {"speaker": "Professor", "text": "[concerned] However, studies indicate that we have already lost around fifty percent of the world's coral reefs since nineteen fifty. The primary causes are rising ocean temperatures, ocean acidification, and destructive fishing practices."},
                        {"speaker": "Professor", "text": "One of the most promising restoration techniques is called coral gardening. This involves growing coral fragments in underwater nurseries before transplanting them onto degraded reefs. Research centres in the Caribbean have achieved survival rates of up to seventy-five percent using this method."},
                        {"speaker": "Professor", "text": "[pause] Another approach gaining attention is biorock technology, which uses low-voltage electrical currents passed through submerged steel structures to accelerate coral growth. Studies show this can increase growth rates by three to five times compared to natural conditions."},
                        {"speaker": "Professor", "text": "The Great Barrier Reef Marine Park Authority in Australia has been at the forefront of large-scale conservation. Their current programme monitors over two thousand individual reef sites and employs a team of three hundred and forty researchers."},
                        {"speaker": "Professor", "text": "[thoughtful] What's particularly interesting is the role of citizen science in reef monitoring. A programme called Reef Check has trained over thirty thousand volunteers in eighty-two countries to conduct standardised reef surveys."},
                        {"speaker": "Professor", "text": "Looking ahead, genetic research offers new possibilities. Scientists at the Australian Institute of Marine Science are developing heat-resistant coral strains that can withstand temperatures up to two degrees Celsius above current thresholds."},
                        {"speaker": "Professor", "text": "[slowly] The economic argument for conservation is also compelling. Coral reefs generate an estimated three hundred and seventy-five billion dollars annually through tourism, fisheries, and coastal protection. For every dollar invested in reef restoration, studies suggest a return of approximately twenty dollars."},
                        {"speaker": "Professor", "text": "To conclude, while the challenges facing our coral reefs are enormous, the combination of innovative restoration techniques, community engagement, and genetic research gives us genuine cause for optimism. [pause] Any questions?"}
                    ],
                    "question_layout": {
                        "title": "Marine Conservation and Coral Reefs",
                        "instruction": "Complete the notes below. Write NO MORE THAN TWO WORDS AND/OR A NUMBER for each answer. For multiple choice, select the correct letter.",
                        "groups": [
                            {"heading": "Coral Reef Facts", "items": [
                                "Cover less than {31}% of ocean floor",
                                "Support approximately {32}% of marine species",
                                "Lost around {33}% since 1950"
                            ]},
                            {"heading": "Restoration Techniques", "items": [
                                "Coral gardening survival rate: up to {35}%",
                                "Biorock increases growth by {36} to 5 times"
                            ]},
                            {"heading": "Monitoring", "items": [
                                "Great Barrier Reef monitors over {37} sites",
                                "Reef Check trained over {38} volunteers"
                            ]},
                            {"heading": "Future Research", "items": [
                                "Heat-resistant coral: withstands {39} degrees above threshold",
                                "Annual reef economic value: ${40} billion"
                            ]}
                        ]
                    },
                    "questions": [
                        {"question_num": 31, "question_type": "sentence_completion", "question_text": "Coral reefs cover less than ________ percent of the ocean floor", "correct_answer": "1|one"},
                        {"question_num": 32, "question_type": "sentence_completion", "question_text": "Reefs support approximately ________% of all known marine species", "correct_answer": "25"},
                        {"question_num": 33, "question_type": "sentence_completion", "question_text": "Around ________% of coral reefs have been lost since 1950", "correct_answer": "50"},
                        {"question_num": 34, "question_type": "multiple_choice", "question_text": "Coral gardening involves growing coral fragments in:", "options": ["A) Laboratories on land", "B) Underwater nurseries", "C) Floating platforms", "D) Shallow rock pools"], "correct_answer": "B"},
                        {"question_num": 35, "question_type": "sentence_completion", "question_text": "Coral gardening achieves survival rates of up to ________%", "correct_answer": "75"},
                        {"question_num": 36, "question_type": "sentence_completion", "question_text": "Biorock technology can increase coral growth rates by ________ to 5 times", "correct_answer": "3"},
                        {"question_num": 37, "question_type": "form_completion", "question_text": "The Great Barrier Reef programme monitors over ________ reef sites", "correct_answer": "2000"},
                        {"question_num": 38, "question_type": "form_completion", "question_text": "Reef Check has trained over ________ volunteers", "correct_answer": "30000|30,000"},
                        {"question_num": 39, "question_type": "sentence_completion", "question_text": "Heat-resistant coral can withstand temperatures up to ________ degrees above current thresholds", "correct_answer": "2"},
                        {"question_num": 40, "question_type": "sentence_completion", "question_text": "Coral reefs generate an estimated $ ________ billion annually", "correct_answer": "375"}
                    ]
                }
            ]
        },
        "reading": {
            "total_questions": 40,
            "duration_minutes": 60,
            "passages": [
                {
                    "passage_num": 1,
                    "title": "The Rise of Vertical Farming",
                    "text": """The concept of growing crops in vertically stacked layers within controlled indoor environments has moved from science fiction to commercial reality in the past two decades. Vertical farming, as the practice is known, represents a fundamental shift in agricultural thinking, one that could help address the food security challenges posed by a growing global population expected to reach 9.7 billion by 2050.

The origins of modern vertical farming can be traced to 1999, when Dickson Despommier, a professor of environmental health sciences at Columbia University, challenged his students to design a rooftop farm that could feed an entire Manhattan neighbourhood. When calculations showed a rooftop garden could feed only about two percent of the local population, Despommier proposed scaling the concept vertically, stacking growing floors inside a skyscraper. The idea captured public imagination and launched a new agricultural movement.

Today's vertical farms bear little resemblance to Despommier's original skyscraper vision. Most operate in converted warehouses or purpose-built facilities, typically between three and fourteen storeys tall. Plants grow in stacked trays under carefully calibrated LED lighting systems that provide specific light wavelengths optimised for photosynthesis. The most common growing method is hydroponics, where plant roots sit in nutrient-rich water rather than soil, though some facilities use aeroponics, which delivers nutrients through a fine mist sprayed directly onto roots.

The advantages of vertical farming are significant. Water usage is reduced by up to ninety-five percent compared to conventional agriculture, as water is continuously recycled within the closed system. Crops can be grown year-round regardless of weather conditions, and harvest cycles are dramatically shortened. Lettuce, for example, can be harvested every thirty-five days in a vertical farm compared to sixty to ninety days in a traditional field. The controlled environment eliminates the need for pesticides, and the proximity to urban centres reduces transportation costs and associated carbon emissions.

However, critics point to several challenges. Energy consumption remains the most significant concern. The artificial lighting required to replace sunlight consumes enormous amounts of electricity. A study by Cornell University found that the energy cost of producing one kilogram of lettuce in a vertical farm was approximately twenty-five times higher than growing it in a greenhouse. The initial capital investment is also substantial, with a medium-scale vertical farm requiring between two and five million dollars to establish.

Despite these challenges, the industry has attracted considerable investment. In 2021 alone, vertical farming companies worldwide raised over five billion dollars in funding. Major players include AeroFarms in New Jersey, which operates one of the world's largest indoor farms at approximately six thousand five hundred square metres, and Plenty, a San Francisco-based company backed by over nine hundred million dollars in investment from figures including Jeff Bezos.

The crops best suited to vertical farming are currently limited to leafy greens, herbs, and some small fruits like strawberries. Staple crops such as wheat, rice, and corn remain impractical due to their size and the energy requirements for producing them indoors. Research into expanding the range of viable crops continues, with some companies experimenting with tomatoes, peppers, and even root vegetables.

Looking ahead, advocates argue that advances in LED efficiency and renewable energy will eventually address the cost and sustainability concerns. Some researchers predict that by 2040, vertical farms could produce up to ten percent of the world's leafy greens. Whether vertical farming can truly scale to make a meaningful contribution to global food security remains an open question, but the trajectory of investment and technological development suggests it will play an increasingly important role in the future of agriculture.""",
                    "questions": [
                        {"question_num": 1, "question_type": "true_false_not_given", "question_text": "Vertical farming was first proposed as a concept in the 21st century.", "correct_answer": "False"},
                        {"question_num": 2, "question_type": "true_false_not_given", "question_text": "Despommier's rooftop garden could have fed the entire neighbourhood.", "correct_answer": "False"},
                        {"question_num": 3, "question_type": "true_false_not_given", "question_text": "Most current vertical farms are built inside converted skyscrapers.", "correct_answer": "False"},
                        {"question_num": 4, "question_type": "true_false_not_given", "question_text": "Aeroponics involves delivering nutrients through a fine mist.", "correct_answer": "True"},
                        {"question_num": 5, "question_type": "true_false_not_given", "question_text": "Vertical farms use more water than conventional farming methods.", "correct_answer": "False"},
                        {"question_num": 6, "question_type": "form_completion", "question_text": "In vertical farms, lettuce can be harvested every ________ days", "correct_answer": "35"},
                        {"question_num": 7, "question_type": "form_completion", "question_text": "Energy cost of vertical farm lettuce is ________ times higher than greenhouse", "correct_answer": "25"},
                        {"question_num": 8, "question_type": "form_completion", "question_text": "In 2021, the industry raised over $ ________ billion", "correct_answer": "5"},
                        {"question_num": 9, "question_type": "form_completion", "question_text": "AeroFarms operates a facility of approximately ________ square metres", "correct_answer": "6500|6,500"},
                        {"question_num": 10, "question_type": "multiple_choice", "question_text": "Which crop is NOT mentioned as suitable for vertical farming?", "options": ["A) Lettuce", "B) Strawberries", "C) Wheat", "D) Herbs"], "correct_answer": "C"},
                        {"question_num": 11, "question_type": "multiple_choice", "question_text": "The main criticism of vertical farming relates to:", "options": ["A) Water usage", "B) Energy consumption", "C) Crop quality", "D) Labour costs"], "correct_answer": "B"},
                        {"question_num": 12, "question_type": "true_false_not_given", "question_text": "Plenty has received investment from Jeff Bezos.", "correct_answer": "True"},
                        {"question_num": 13, "question_type": "true_false_not_given", "question_text": "By 2040, vertical farms are expected to produce 10% of all food globally.", "correct_answer": "False"}
                    ]
                },
                {
                    "passage_num": 2,
                    "title": "The Science of Forgetting",
                    "text": """Why do we forget? This seemingly simple question has occupied psychologists, neuroscientists, and philosophers for over a century, and the answers that have emerged reveal forgetting to be not a failure of memory but an essential cognitive function that allows the brain to operate efficiently.

The scientific study of forgetting began in earnest with Hermann Ebbinghaus, a German psychologist who in 1885 published his groundbreaking work on memory. Through meticulous self-experimentation involving memorising lists of nonsense syllables, Ebbinghaus discovered what he termed the 'forgetting curve', a mathematical relationship showing that memory retention declines exponentially after learning. His research revealed that approximately fifty-six percent of newly learned information is forgotten within one hour, sixty-six percent within one day, and seventy-five percent within six days, assuming no review takes place.

For decades, the dominant explanation for forgetting was decay theory, which proposed that memories simply fade over time like ink exposed to sunlight. While intuitively appealing, this theory has largely fallen out of favour among researchers. The problem with decay theory is that it cannot explain why some very old memories remain vivid while recent ones disappear, or why memories that seem lost can suddenly resurface when triggered by a particular smell, sound, or context.

The theory that has gained most support is interference theory, which suggests we forget not because memories decay but because other memories compete with and disrupt them. There are two types of interference. Proactive interference occurs when old information makes it harder to learn new information, for example, when your memory of an old phone number interferes with learning a new one. Retroactive interference works in the opposite direction: new information disrupts the recall of older memories.

In 2007, a team led by neuroscientist Michael Anderson at the University of Oregon proposed a more active model of forgetting. Anderson's research demonstrated that the brain has a mechanism for deliberately suppressing unwanted memories, a process he termed 'retrieval-induced forgetting'. In experiments, participants who were asked to repeatedly retrieve certain items from a studied list showed impaired memory for related but non-retrieved items. The act of remembering some things, Anderson argued, causes the active forgetting of others.

More recently, research has focused on the biological mechanisms underlying forgetting. A study published in the journal Science in 2019 by Paul Bhatt and colleagues at the Scripps Research Institute in Florida identified specific molecules called 'forgetting cells' that actively remove memories from the brain. Working with fruit flies, the team showed that the neurotransmitter dopamine triggers a process that destabilises memory-storing proteins in brain cells. This suggests that forgetting is not passive erosion but an active biological process.

Perhaps the most revolutionary perspective on forgetting comes from computational neuroscience. Blake Richards and Paul Bhatt argued in a 2017 paper in the journal Neuron that forgetting is not a bug but a feature of memory. They proposed that the purpose of memory is not to store information with perfect fidelity but to optimise decision-making. By forgetting irrelevant details and retaining general patterns, the brain creates flexible mental models that allow us to respond adaptively to new situations. In this view, a person who remembers every detail of every day would actually be at a disadvantage, unable to see the forest for the trees.

The implications of this research extend beyond pure science. Understanding the mechanisms of forgetting has practical applications in education, where spaced repetition techniques exploit the forgetting curve to strengthen long-term retention. In clinical psychology, insights into deliberate forgetting mechanisms offer potential treatments for conditions like post-traumatic stress disorder, where the inability to forget is the core problem.""",
                    "questions": [
                        {"question_num": 14, "question_type": "true_false_not_given", "question_text": "Ebbinghaus used other participants in his memory experiments.", "correct_answer": "False"},
                        {"question_num": 15, "question_type": "form_completion", "question_text": "According to Ebbinghaus, ________% of information is forgotten within one hour", "correct_answer": "56"},
                        {"question_num": 16, "question_type": "form_completion", "question_text": "________% is forgotten within six days without review", "correct_answer": "75"},
                        {"question_num": 17, "question_type": "multiple_choice", "question_text": "Decay theory compares memory fading to:", "options": ["A) Water evaporation", "B) Ink fading in sunlight", "C) Plants wilting", "D) Batteries losing charge"], "correct_answer": "B"},
                        {"question_num": 18, "question_type": "short_answer", "question_text": "What type of interference occurs when old information disrupts learning new information?", "correct_answer": "proactive|proactive interference"},
                        {"question_num": 19, "question_type": "short_answer", "question_text": "What term did Michael Anderson use for deliberate memory suppression?", "correct_answer": "retrieval-induced forgetting"},
                        {"question_num": 20, "question_type": "form_completion", "question_text": "Anderson's research was conducted at the University of ________", "correct_answer": "Oregon"},
                        {"question_num": 21, "question_type": "short_answer", "question_text": "Which neurotransmitter triggers the forgetting process in fruit flies?", "correct_answer": "dopamine"},
                        {"question_num": 22, "question_type": "true_false_not_given", "question_text": "Richards and Bhatt believe perfect memory would be disadvantageous.", "correct_answer": "True"},
                        {"question_num": 23, "question_type": "multiple_choice", "question_text": "According to Richards and Bhatt, the purpose of memory is to:", "options": ["A) Store all information accurately", "B) Record emotional experiences", "C) Optimise decision-making", "D) Preserve personal identity"], "correct_answer": "C"},
                        {"question_num": 24, "question_type": "true_false_not_given", "question_text": "Spaced repetition techniques are based on understanding the forgetting curve.", "correct_answer": "True"},
                        {"question_num": 25, "question_type": "true_false_not_given", "question_text": "PTSD patients typically forget traumatic memories too quickly.", "correct_answer": "False"},
                        {"question_num": 26, "question_type": "short_answer", "question_text": "What educational technique exploits the forgetting curve?", "correct_answer": "spaced repetition"}
                    ]
                },
                {
                    "passage_num": 3,
                    "title": "The Architecture of Trust in Digital Economies",
                    "text": """The rapid expansion of digital economies has fundamentally altered the mechanisms through which trust operates in commercial transactions. Where once trust depended on personal relationships, physical presence, and established reputation within a community, today it increasingly relies on digital systems, algorithmic assessments, and platform-mediated interactions between strangers.

Trust has always been the invisible infrastructure of commerce. The sociologist Georg Simmel, writing in 1900, described trust as 'one of the most important synthetic forces within society', a kind of social glue that makes complex exchanges possible. In pre-industrial economies, trust was built through repeated face-to-face interactions within relatively stable communities. A merchant's reputation was a locally verifiable asset, sustained through direct experience and community gossip.

The industrial revolution began to stretch these trust relationships across greater distances, introducing intermediary institutions, banks, insurance companies, trade associations, that served as trust brokers. These institutions worked by concentrating trust: rather than evaluating every individual counterparty, people learned to trust the institution itself, and by extension anyone validated by that institution. This institutional model of trust dominated commercial life for two centuries and remains influential today.

The digital revolution has introduced a third trust paradigm: distributed trust. In this model, trust is no longer concentrated in institutions but spread across networks of peers, algorithms, and data. The pioneering example is eBay, which launched in 1995 and faced an immediate trust problem: how could strangers be persuaded to send money to or ship goods to people they had never met? The solution was the feedback system, a mutual rating mechanism that allowed buyers and sellers to build visible reputations over time. This seemingly simple innovation proved transformative, enabling billions of dollars in transactions between complete strangers.

The platform economy has refined and extended this model considerably. Companies like Airbnb, Uber, and TaskRabbit have developed sophisticated trust architectures that combine multiple signals: verified identities, behavioural ratings, social media connections, algorithmic matching, and insurance guarantees. Research by Arun Sundararajan at New York University has shown that these layered trust mechanisms can generate levels of confidence comparable to personal recommendation, even between people who have never met and may be separated by thousands of miles.

However, the shift to algorithmic trust raises significant concerns. First, there is the problem of manipulation. Rating systems can be gamed through fake reviews, coordinated rating inflation, or strategic timing of transactions. A 2018 study found that approximately thirty percent of online reviews across major platforms were either fraudulent or significantly misleading. Platforms have responded with increasingly sophisticated detection algorithms, but the arms race between authentic and manufactured trust continues.

Second, algorithmic trust tends to encode and amplify existing social biases. Research has consistently demonstrated that platform ratings are influenced by factors such as race, gender, and socioeconomic markers. A study by Benjamin Edelman at Harvard Business School found that Airbnb hosts with distinctively African-American names received sixteen percent fewer booking requests than identical listings with white-sounding names, despite equivalent ratings. The algorithm doesn't create the bias, but the trust system's reliance on subjective ratings gives it a mechanism for expression.

Third, there is the concentration risk. While distributed trust was supposed to democratise commerce, in practice the platforms that mediate trust have become enormously powerful intermediaries themselves. Amazon, Google, and Facebook collectively control much of the trust infrastructure of the digital economy. A seller banned from Amazon or a business invisible to Google search effectively loses access to the trust network on which their livelihood depends.

Looking forward, blockchain technology and decentralised autonomous organisations represent an attempt to address the concentration problem by creating trust systems that operate without central intermediaries. Whether these technologies can deliver on their promise of 'trustless trust', systems so transparent and mathematically verifiable that trust in any particular party becomes unnecessary, remains one of the defining questions of digital commerce.""",
                    "questions": [
                        {"question_num": 27, "question_type": "matching_headings", "question_text": "Which paragraph discusses the problem of fake reviews and rating manipulation?", "options": ["A) Paragraph 2", "B) Paragraph 4", "C) Paragraph 6", "D) Paragraph 8"], "correct_answer": "C"},
                        {"question_num": 28, "question_type": "matching_headings", "question_text": "Which paragraph explains how trust operated in pre-industrial societies?", "options": ["A) Paragraph 1", "B) Paragraph 2", "C) Paragraph 3", "D) Paragraph 5"], "correct_answer": "B"},
                        {"question_num": 29, "question_type": "short_answer", "question_text": "What did Georg Simmel describe trust as?", "correct_answer": "one of the most important synthetic forces within society"},
                        {"question_num": 30, "question_type": "form_completion", "question_text": "eBay was launched in ________", "correct_answer": "1995"},
                        {"question_num": 31, "question_type": "short_answer", "question_text": "What innovation did eBay introduce to solve its trust problem?", "correct_answer": "feedback system"},
                        {"question_num": 32, "question_type": "form_completion", "question_text": "Approximately ________% of online reviews were found to be fraudulent or misleading", "correct_answer": "30"},
                        {"question_num": 33, "question_type": "form_completion", "question_text": "Airbnb hosts with African-American names received ________% fewer bookings", "correct_answer": "16"},
                        {"question_num": 34, "question_type": "short_answer", "question_text": "Who conducted the Airbnb discrimination study?", "correct_answer": "Benjamin Edelman"},
                        {"question_num": 35, "question_type": "true_false_not_given", "question_text": "Blockchain technology has already solved the trust concentration problem.", "correct_answer": "False"},
                        {"question_num": 36, "question_type": "true_false_not_given", "question_text": "Sundararajan's research found that platform trust can match personal recommendations.", "correct_answer": "True"},
                        {"question_num": 37, "question_type": "multiple_choice", "question_text": "The 'institutional model of trust' mainly relies on:", "options": ["A) Personal relationships", "B) Digital algorithms", "C) Intermediary organisations", "D) Peer ratings"], "correct_answer": "C"},
                        {"question_num": 38, "question_type": "true_false_not_given", "question_text": "The algorithmic trust system creates social biases.", "correct_answer": "False"},
                        {"question_num": 39, "question_type": "multiple_choice", "question_text": "The term 'trustless trust' refers to systems that:", "options": ["A) Don't require any trust", "B) Are transparent and mathematically verifiable", "C) Only work between known parties", "D) Are controlled by governments"], "correct_answer": "B"},
                        {"question_num": 40, "question_type": "true_false_not_given", "question_text": "The author is optimistic that blockchain will solve all digital trust issues.", "correct_answer": "Not Given"}
                    ]
                }
            ]
        },
        "writing": {
            "total_time_minutes": 60,
            "tasks": [
                {"task_num": 1, "task_type": "describe_visual", "prompt": "The bar chart below shows the number of international students enrolled in three different faculties (Engineering, Business, and Arts) at a British university between 2018 and 2023.\n\nSummarise the information by selecting and reporting the main features, and make comparisons where relevant.\n\nWrite at least 150 words.\n\n[Data: Engineering grew from 450 to 820 students; Business remained relatively stable at around 600-650; Arts declined from 380 to 290]", "min_words": 150, "time_minutes": 20},
                {"task_num": 2, "task_type": "essay", "prompt": "In many countries, the gap between the rich and the poor is increasing. What problems does this cause, and what solutions can you suggest?\n\nGive reasons for your answer and include any relevant examples from your own knowledge or experience.\n\nWrite at least 250 words.", "min_words": 250, "time_minutes": 40}
            ]
        },
        "speaking": {
            "total_time_minutes": 14,
            "parts": [
                {"part_num": 1, "title": "Introduction and Interview", "time_minutes": 5,
                 "instructions": "The examiner will ask you questions about familiar topics such as home, work, studies, and interests.",
                 "questions": [
                     {"question_num": 1, "question_text": "[warm] Good afternoon. My name is Daniel, and I'll be your examiner today. Can you tell me your full name please?", "needs_audio": True},
                     {"question_num": 2, "question_text": "Thank you. Now, let's talk about where you live. Can you describe your neighbourhood for me?", "needs_audio": True},
                     {"question_num": 3, "question_text": "What do you like most about living in that area?", "needs_audio": True},
                     {"question_num": 4, "question_text": "[curious] Now let's move on to talk about cooking. Do you enjoy cooking? Why or why not?", "needs_audio": True},
                     {"question_num": 5, "question_text": "What is a typical meal that you like to prepare?", "needs_audio": True}
                 ]},
                {"part_num": 2, "title": "Individual Long Turn", "time_minutes": 4, "preparation_time": 60,
                 "instructions": "You will be given a topic card. You have 1 minute to prepare, then you should speak for 1-2 minutes.",
                 "cue_card": "Describe a time when you helped someone.\nYou should say:\n- who you helped\n- how you helped them\n- why they needed help\n- and explain how you felt about helping them",
                 "questions": [
                     {"question_num": 6, "question_text": "[professional] Now, I'm going to give you a topic. You'll have one minute to prepare, and then I'd like you to speak for one to two minutes. Here is your topic card.", "needs_audio": True},
                     {"question_num": 7, "question_text": "Thank you. Can you tell me anything else about that experience?", "needs_audio": True}
                 ]},
                {"part_num": 3, "title": "Two-way Discussion", "time_minutes": 5,
                 "instructions": "The examiner will ask abstract questions related to the Part 2 topic.",
                 "questions": [
                     {"question_num": 8, "question_text": "[thoughtful] Let's talk more generally about helping others. Do you think people today are less willing to help strangers than in the past?", "needs_audio": True},
                     {"question_num": 9, "question_text": "Some people argue that governments should be responsible for helping those in need, rather than individuals. What's your view on this?", "needs_audio": True},
                     {"question_num": 10, "question_text": "How do you think volunteering benefits the person who volunteers, not just the people they help?", "needs_audio": True}
                 ]}
            ]
        }
    }

def get_telc_b1_seed():
    return {
        "exam_id": "exam_telc_b1_001",
        "title": "TELC Deutsch B1 - Übungstest 1",
        "exam_type": "telc",
        "telc_level": "B1",
        "pathway": "telc_b1",
        "status": "pending_audio",
        "audio_progress": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "lesen": {
            "total_questions": 15,
            "duration_minutes": 90,
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "zuordnung",
                    "title": "Aufgabe 1 - Texte zuordnen",
                    "instruction": "Welche Überschrift passt zu welchem Text? Ordnen Sie zu. Eine Überschrift passt nicht.",
                    "short_texts": [
                        {"id": "A", "text": "Das Stadtbad Mitte ist täglich von 7 bis 22 Uhr geöffnet. Wir bieten Schwimmkurse für Kinder ab 5 Jahren und Aquafitness für Erwachsene an. Familienkarte erhältlich. Sauna im Obergeschoss (nur für Erwachsene)."},
                        {"id": "B", "text": "Stadtbücherei am Marktplatz: Bücher, Zeitschriften und DVDs kostenlos ausleihen. Montag bis Freitag 10–19 Uhr, Samstag 10–14 Uhr. Für die Anmeldung bringen Sie bitte Ihren Personalausweis mit."},
                        {"id": "C", "text": "Frischer Obst- und Gemüsemarkt jeden Dienstag und Freitag von 8 bis 13 Uhr auf dem Rathausplatz. Regionale Produkte direkt vom Bauernhof. Auch Käse, Eier und Honig aus der Umgebung."},
                        {"id": "D", "text": "Volkshochschule – Anmeldung Herbstkurse ab sofort möglich. Deutsch als Fremdsprache (alle Niveaus), Englisch, Spanisch, Kochen, Fotografie und vieles mehr. Infos und Anmeldung online oder persönlich."},
                        {"id": "E", "text": "Fahrradverleih am Bahnhof: Cityräder, E-Bikes und Kindersitze verfügbar. Stundenpreise ab 3 Euro, Tageskarte 15 Euro. Personalausweis als Pfand erforderlich. Geöffnet täglich 8–20 Uhr."}
                    ],
                    "questions": [
                        {"question_num": 1, "question_text": "Hier kann man Bücher kostenlos mitnehmen.", "correct_answer": "B"},
                        {"question_num": 2, "question_text": "Hier kann man Fahrräder mieten.", "correct_answer": "E"},
                        {"question_num": 3, "question_text": "Hier kann man frische Lebensmittel kaufen.", "correct_answer": "C"},
                        {"question_num": 4, "question_text": "Hier kann man eine neue Sprache lernen.", "correct_answer": "D"},
                        {"question_num": 5, "question_text": "Hier kann man schwimmen gehen.", "correct_answer": "A"}
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "richtig_falsch",
                    "title": "Aufgabe 2 - Richtig, Falsch oder Nicht im Text?",
                    "instruction": "Lesen Sie den Text und entscheiden Sie: Ist die Aussage richtig, falsch oder steht das nicht im Text?",
                    "text": """Homeoffice in Deutschland – Fluch oder Segen?

Seit der Corona-Pandemie arbeiten immer mehr Deutsche von zu Hause aus. Laut einer aktuellen Studie des Instituts für Arbeitsmarkt- und Berufsforschung arbeiten heute etwa 24 Prozent aller Beschäftigten regelmäßig im Homeoffice – vor der Pandemie waren es nur 12 Prozent.

Die Meinungen über das Homeoffice sind geteilt. Viele Arbeitnehmer schätzen die Flexibilität: Sie können sich ihre Arbeitszeit besser einteilen und sparen täglich Zeit und Geld für den Weg zur Arbeit. Für Eltern ist es oft leichter, Familie und Beruf zu vereinbaren.

Allerdings gibt es auch Nachteile. Viele Menschen vermissen den Kontakt zu den Kollegen. Die Grenze zwischen Arbeit und Freizeit verschwimmt oft, und manche Mitarbeiter arbeiten im Homeoffice sogar länger als im Büro. Außerdem haben nicht alle zu Hause einen ruhigen Arbeitsplatz.

Experten empfehlen deshalb ein hybrides Modell: ein bis zwei Tage Homeoffice pro Woche und den Rest der Zeit im Büro. So profitiert man von den Vorteilen beider Welten.""",
                    "questions": [
                        {"question_num": 6, "question_text": "Vor der Pandemie arbeiteten 24 Prozent der Deutschen im Homeoffice.", "correct_answer": "Falsch"},
                        {"question_num": 7, "question_text": "Im Homeoffice sparen viele Arbeitnehmer Zeit und Geld für den Weg zur Arbeit.", "correct_answer": "Richtig"},
                        {"question_num": 8, "question_text": "Alle Mitarbeiter im Homeoffice haben einen ruhigen Arbeitsplatz.", "correct_answer": "Falsch"},
                        {"question_num": 9, "question_text": "Manche Mitarbeiter im Homeoffice verdienen mehr Geld als im Büro.", "correct_answer": "Nicht im Text"},
                        {"question_num": 10, "question_text": "Experten empfehlen, manchmal im Büro und manchmal zu Hause zu arbeiten.", "correct_answer": "Richtig"}
                    ]
                },
                {
                    "aufgabe_num": 3,
                    "typ": "multiple_choice",
                    "title": "Aufgabe 3 - Multiple Choice",
                    "instruction": "Lesen Sie den Text. Welche Antwort (A, B oder C) passt?",
                    "text": """Stadtführungen mal anders

Die Tourismusbehörde der Stadt Freiburg bietet ab diesem Sommer ein neues Programm an: Stadtführungen mit dem Fahrrad. Jeden Samstag um 10 Uhr starten geführte Radtouren vom Hauptbahnhof. Die Tour dauert etwa zwei Stunden und führt zu den wichtigsten Sehenswürdigkeiten der Altstadt, zum Münster und entlang der Dreisam.

Die Führung kostet 15 Euro pro Person, Fahrräder können für 8 Euro gemietet werden. Für Gruppen ab 10 Personen gibt es einen Rabatt von 20 Prozent. Kinder unter 12 Jahren nehmen kostenlos teil, wenn sie von einem Erwachsenen begleitet werden.

Die Stadtführerin Claudia Berger, die die Tour leitet, ist begeistert: "Mit dem Fahrrad kann man die Stadt viel intensiver erleben als zu Fuß. Wir machen auch Stopps an weniger bekannten Orten, die normale Touristengruppen nicht besuchen."

Anmeldung ist nicht erforderlich – man kann einfach am Startpunkt erscheinen. Bei schlechtem Wetter findet die Tour trotzdem statt, außer bei starkem Regen oder Sturm.""",
                    "questions": [
                        {"question_num": 11, "question_text": "Wo beginnt die Radtour?", "options": ["A) Am Münster", "B) Am Hauptbahnhof", "C) Am Rathaus"], "correct_answer": "B"},
                        {"question_num": 12, "question_text": "Was kostet die Führung für ein Kind von 10 Jahren mit einem Erwachsenen?", "options": ["A) 15 Euro", "B) 8 Euro", "C) Nichts"], "correct_answer": "C"},
                        {"question_num": 13, "question_text": "Was ist ein Vorteil der Radtour laut Claudia Berger?", "options": ["A) Sie ist billiger als eine Fußtour.", "B) Man sieht auch weniger bekannte Orte.", "C) Man kann die Tour online buchen."], "correct_answer": "B"},
                        {"question_num": 14, "question_text": "Wann findet die Tour nicht statt?", "options": ["A) Bei leichtem Regen", "B) Bei starkem Regen oder Sturm", "C) Im Winter"], "correct_answer": "B"},
                        {"question_num": 15, "question_text": "Wie viel zahlt eine Gruppe von 10 Personen für die Führung insgesamt?", "options": ["A) 150 Euro", "B) 120 Euro", "C) 130 Euro"], "correct_answer": "B"}
                    ]
                }
            ]
        },
        "hoeren": {
            "total_questions": 15,
            "duration_minutes": 40,
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "kurzgespraeche",
                    "title": "Aufgabe 1 - Kurze Gespräche",
                    "instruction": "Sie hören fünf kurze Gespräche. Sind die Aussagen richtig oder falsch?",
                    "conversations": [
                        {
                            "conv_num": 1,
                            "sprecher": [
                                {"name": "Frau Koch", "voice_id": "Aoede"},
                                {"name": "Herr Braun", "voice_id": "Charon"}
                            ],
                            "script_segments": [
                                {"sprecher": "Frau Koch", "text": "[freundlich] Guten Morgen! Haben Sie schon gehört? Das neue Restaurant am Marktplatz macht heute Abend auf."},
                                {"sprecher": "Herr Braun", "text": "Ja, ich habe eine Einladung zur Eröffnung bekommen. Es soll italienisches Essen anbieten."},
                                {"sprecher": "Frau Koch", "text": "Wirklich? Ich dachte, es wird ein asiatisches Restaurant."},
                                {"sprecher": "Herr Braun", "text": "Nein, nein – Pizza und Pasta, alles frisch gemacht."}
                            ],
                            "questions": [{"question_num": 1, "question_text": "Das neue Restaurant bietet asiatisches Essen an.", "correct_answer": "Falsch"}]
                        },
                        {
                            "conv_num": 2,
                            "sprecher": [
                                {"name": "Studentin", "voice_id": "Kore"},
                                {"name": "Bibliothekarin", "voice_id": "Aoede"}
                            ],
                            "script_segments": [
                                {"sprecher": "Studentin", "text": "Entschuldigung, ich suche ein Buch über deutsche Geschichte. Haben Sie etwas über die Weimarer Republik?"},
                                {"sprecher": "Bibliothekarin", "text": "Ja, wir haben mehrere Bücher dazu. Aber die meisten sind gerade ausgeliehen. Eines ist noch da – im Regal Nummer sieben."},
                                {"sprecher": "Studentin", "text": "Kann ich es für drei Wochen ausleihen?"},
                                {"sprecher": "Bibliothekarin", "text": "Normalerweise sind es vier Wochen, aber dieses Buch ist sehr gefragt, daher nur zwei Wochen."}
                            ],
                            "questions": [{"question_num": 2, "question_text": "Die Studentin kann das Buch für vier Wochen ausleihen.", "correct_answer": "Falsch"}]
                        },
                        {
                            "conv_num": 3,
                            "sprecher": [
                                {"name": "Kunde", "voice_id": "Charon"},
                                {"name": "Verkäuferin", "voice_id": "Kore"}
                            ],
                            "script_segments": [
                                {"sprecher": "Kunde", "text": "[pausiert] Ich hätte gerne diese Jacke in Größe L, aber in Blau, wenn möglich."},
                                {"sprecher": "Verkäuferin", "text": "In Blau haben wir nur noch Größe M und XL. L ist leider ausverkauft."},
                                {"sprecher": "Kunde", "text": "Schade. Und in Grün?"},
                                {"sprecher": "Verkäuferin", "text": "In Grün haben wir alle Größen vorrätig, auch L."}
                            ],
                            "questions": [{"question_num": 3, "question_text": "Der Kunde kann die Jacke in Blau und Größe L kaufen.", "correct_answer": "Falsch"}]
                        },
                        {
                            "conv_num": 4,
                            "sprecher": [
                                {"name": "Lars", "voice_id": "Orus"},
                                {"name": "Mia", "voice_id": "Aoede"}
                            ],
                            "script_segments": [
                                {"sprecher": "Lars", "text": "Mia, ich wollte dich fragen – machst du morgen Abend mit beim Stadtlauf?"},
                                {"sprecher": "Mia", "text": "Stadtlauf? Wann fängt der an?"},
                                {"sprecher": "Lars", "text": "Um halb sieben am Stadtpark. Es sind fünf Kilometer – nicht zu schwer."},
                                {"sprecher": "Mia", "text": "[überlegt] Das klingt gut. Ich bin dabei!"}
                            ],
                            "questions": [{"question_num": 4, "question_text": "Der Stadtlauf findet am Stadtpark statt.", "correct_answer": "Richtig"}]
                        },
                        {
                            "conv_num": 5,
                            "sprecher": [
                                {"name": "Frau Weber", "voice_id": "Aoede"},
                                {"name": "Herr Fischer", "voice_id": "Puck"}
                            ],
                            "script_segments": [
                                {"sprecher": "Frau Weber", "text": "Herr Fischer, ich habe gehört, Sie ziehen nächsten Monat um?"},
                                {"sprecher": "Herr Fischer", "text": "Ja, genau. Ich habe eine neue Wohnung im Norden der Stadt gefunden – größer und mit Balkon."},
                                {"sprecher": "Frau Weber", "text": "Das klingt wunderbar! Brauchen Sie Hilfe beim Umzug?"},
                                {"sprecher": "Herr Fischer", "text": "Das ist sehr nett, danke! Ich habe schon einen Umzugswagen gemietet."}
                            ],
                            "questions": [{"question_num": 5, "question_text": "Herr Fischer zieht in eine Wohnung ohne Balkon.", "correct_answer": "Falsch"}]
                        }
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "gespraech",
                    "title": "Aufgabe 2 - Ein Gespräch",
                    "instruction": "Sie hören ein Gespräch zwischen zwei Personen. Wählen Sie die richtige Antwort.",
                    "sprecher": [
                        {"name": "Sandra", "voice_id": "Aoede"},
                        {"name": "Thomas", "voice_id": "Charon"}
                    ],
                    "script_segments": [
                        {"sprecher": "Sandra", "text": "[entspannt] Thomas, hast du schon Pläne für den Urlaub nächsten Sommer?"},
                        {"sprecher": "Thomas", "text": "Ja! Ich möchte gerne nach Spanien fahren – an die Costa Brava. Ich war noch nie dort."},
                        {"sprecher": "Sandra", "text": "Oh, das klingt toll! Ich war letztes Jahr da. Das Wasser ist wirklich wunderschön. Wie lange möchtest du fahren?"},
                        {"sprecher": "Thomas", "text": "Zwei Wochen wären ideal. Aber ich weiß noch nicht genau wann – entweder Juli oder August."},
                        {"sprecher": "Sandra", "text": "[nachdenklich] Im August ist es sehr heiß und es gibt viele Touristen. Ich würde Juli empfehlen – angenehmer und nicht so überfüllt."},
                        {"sprecher": "Thomas", "text": "Guter Tipp, danke! Fliegst du mit dem Flugzeug oder fährst du lieber mit dem Zug?"},
                        {"sprecher": "Sandra", "text": "Ich bin letztes Mal geflogen – nur zwei Stunden. Mit dem Zug wäre es viel länger, aber vielleicht interessanter."},
                        {"sprecher": "Thomas", "text": "Ich denke, ich nehme das Flugzeug. Es ist schneller und oft auch günstiger, wenn man früh bucht."},
                        {"sprecher": "Sandra", "text": "Hast du schon ein Hotel gesucht? In Juli sollte man früh buchen, sonst sind die guten Hotels ausgebucht."},
                        {"sprecher": "Thomas", "text": "[enthusiastisch] Nein, noch nicht. Ich schaue heute Abend online nach. Hast du eine Empfehlung?"},
                        {"sprecher": "Sandra", "text": "Ja, ich war im Hotel Mediterrán – direkt am Strand, frühstück inklusive, und das Personal war sehr freundlich."}
                    ],
                    "questions": [
                        {"question_num": 6, "question_text": "Wohin möchte Thomas im Urlaub fahren?", "options": ["A) Nach Italien", "B) Nach Spanien", "C) Nach Portugal"], "correct_answer": "B"},
                        {"question_num": 7, "question_text": "Wann empfiehlt Sandra den Urlaub zu machen?", "options": ["A) Im Juni", "B) Im August", "C) Im Juli"], "correct_answer": "C"},
                        {"question_num": 8, "question_text": "Wie ist Sandra letztes Jahr gereist?", "options": ["A) Mit dem Zug", "B) Mit dem Flugzeug", "C) Mit dem Auto"], "correct_answer": "B"},
                        {"question_num": 9, "question_text": "Was war gut an Sandras Hotel?", "options": ["A) Es war sehr günstig.", "B) Es hatte einen Pool.", "C) Das Personal war freundlich."], "correct_answer": "C"},
                        {"question_num": 10, "question_text": "Was will Thomas heute Abend machen?", "options": ["A) Flüge buchen", "B) Online ein Hotel suchen", "C) Sandra besuchen"], "correct_answer": "B"}
                    ]
                },
                {
                    "aufgabe_num": 3,
                    "typ": "ansagen",
                    "title": "Aufgabe 3 - Ansagen und Mitteilungen",
                    "instruction": "Sie hören fünf kurze Ansagen. Sind die Aussagen richtig oder falsch?",
                    "ansagen": [
                        {"ansage_num": 1, "sprecher": "Ansager", "voice_id": "Fenrir",
                         "text": "[klar] Achtung, eine Durchsage für Reisende nach München: Der ICE 1234 ab Gleis 7 hat heute eine Verspätung von etwa 20 Minuten. Wir bitten um Ihr Verständnis.",
                         "question_num": 11, "question_text": "Der Zug nach München fährt pünktlich ab.", "correct_answer": "Falsch"},
                        {"ansage_num": 2, "sprecher": "Moderatorin", "voice_id": "Kore",
                         "text": "[professionell] Das Stadtmuseum ist ab nächster Woche jeden Mittwoch bis 21 Uhr geöffnet. Der Eintritt am Mittwochabend ist für alle Besucher kostenlos.",
                         "question_num": 12, "question_text": "Das Stadtmuseum bietet mittwochabends kostenlosen Eintritt an.", "correct_answer": "Richtig"},
                        {"ansage_num": 3, "sprecher": "Ansager", "voice_id": "Fenrir",
                         "text": "Liebe Kunden, unser Supermarkt ist am kommenden Sonntag wegen Inventur geschlossen. Wir sind am Montag wieder ab 8 Uhr für Sie da.",
                         "question_num": 13, "question_text": "Der Supermarkt ist am Sonntag geöffnet.", "correct_answer": "Falsch"},
                        {"ansage_num": 4, "sprecher": "Moderatorin", "voice_id": "Kore",
                         "text": "[warm] Das Wetter morgen: In der Nordhälfte gibt es Regen und Temperaturen um 12 Grad. Im Süden bleibt es trocken und sonnig, bis zu 18 Grad.",
                         "question_num": 14, "question_text": "Im Süden wird es morgen regnen.", "correct_answer": "Falsch"},
                        {"ansage_num": 5, "sprecher": "Ansager", "voice_id": "Fenrir",
                         "text": "Die Volkshochschule lädt ein: Deutschkurse für Anfänger starten am ersten September. Anmeldung bis zum 20. August online oder persönlich im Sekretariat.",
                         "question_num": 15, "question_text": "Die Anmeldung für den Deutschkurs ist ab dem ersten September möglich.", "correct_answer": "Falsch"}
                    ]
                }
            ]
        },
        "schreiben": {
            "total_time_minutes": 30,
            "aufgaben": [{
                "aufgabe_num": 1,
                "aufgabe_typ": "brief_email",
                "aufgabe": "Sie haben diese E-Mail von Ihrem Freund Jonas bekommen:\n\n'Hallo! Ich ziehe nächsten Monat in eine neue Wohnung um und brauche Hilfe. Hast du am Samstag, den 15., Zeit? Wir könnten danach zusammen essen gehen. Liebe Grüße, Jonas'\n\nSchreiben Sie eine Antwort-E-Mail. Schreiben Sie:\n• ob Sie helfen können und warum (nicht)\n• wann Sie Zeit haben\n• einen Vorschlag für das Abendessen\n\nSchreiben Sie ca. 100 Wörter.",
                "min_words": 80,
                "max_words": 130
            }]
        },
        "sprechen": {
            "total_time_minutes": 15,
            "teile": [
                {
                    "teil_num": 1,
                    "titel": "Kontaktaufnahme",
                    "instructions": "Der Prüfer stellt Ihnen einige Fragen zu Ihrer Person. Antworten Sie auf Deutsch.",
                    "fragen": [
                        {"frage_num": 1, "frage_text": "[warm] Guten Tag! Willkommen zur Prüfung. Wie heißen Sie und woher kommen Sie ursprünglich?", "needs_audio": True},
                        {"frage_num": 2, "frage_text": "Was machen Sie zurzeit – arbeiten Sie oder studieren Sie?", "needs_audio": True},
                        {"frage_num": 3, "frage_text": "Was sind Ihre Hobbys? Was machen Sie in Ihrer Freizeit gerne?", "needs_audio": True}
                    ]
                },
                {
                    "teil_num": 2,
                    "titel": "Über ein Thema sprechen",
                    "instructions": "Sie sehen ein Bild. Beschreiben Sie das Bild und beantworten Sie die Fragen.",
                    "bild_beschreibung": "Ein belebter Wochenmarkt in einer deutschen Stadt: Stände mit frischem Gemüse, Obst und Blumen. Verkäufer und Kunden im Gespräch. Menschen schlendern zwischen den Ständen.",
                    "fragen": [
                        {"frage_num": 4, "frage_text": "Beschreiben Sie dieses Bild. Was sehen Sie?", "needs_audio": True},
                        {"frage_num": 5, "frage_text": "Gehen Sie manchmal auf den Markt? Was kaufen Sie dort gerne?", "needs_audio": True},
                        {"frage_num": 6, "frage_text": "Was finden Sie besser – im Supermarkt einkaufen oder auf dem Wochenmarkt? Warum?", "needs_audio": True}
                    ]
                },
                {
                    "teil_num": 3,
                    "titel": "Gemeinsam etwas planen",
                    "instructions": "Planen Sie gemeinsam mit dem Prüfer eine Veranstaltung.",
                    "aufgabe": "Sie und ein Freund oder eine Freundin möchten den Geburtstag eines gemeinsamen Freundes feiern. Diskutieren Sie, wie Sie die Party organisieren möchten.",
                    "fragen": [
                        {"frage_num": 7, "frage_text": "Also, für Peters Geburtstag – haben Sie schon Ideen? Machen wir eine Party bei jemandem zu Hause oder gehen wir lieber aus?", "needs_audio": True},
                        {"frage_num": 8, "frage_text": "Wie viele Personen sollen eingeladen werden? Und was essen wir – kochen wir selbst oder bestellen wir?", "needs_audio": True}
                    ]
                }
            ]
        }
    }

def get_telc_b2_seed():
    return {
        "exam_id": "exam_telc_b2_001",
        "title": "TELC Deutsch B2 - Übungstest 1",
        "exam_type": "telc",
        "telc_level": "B2",
        "pathway": "telc_b2",
        "status": "pending_audio",
        "audio_progress": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "lesen": {
            "total_questions": 15,
            "duration_minutes": 90,
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "zuordnung",
                    "title": "Aufgabe 1 - Texte zuordnen",
                    "instruction": "Lesen Sie die Texte A–E und die Beschreibungen 1–5. Welcher Text passt zu welcher Beschreibung? Eine Beschreibung passt nicht.",
                    "short_texts": [
                        {"id": "A", "text": "Die Digitalisierung verändert den Arbeitsmarkt grundlegend. Laut einer Studie des Instituts für Arbeitsmarktforschung werden bis 2030 rund 35 Prozent aller Berufe durch Automatisierung und künstliche Intelligenz stark verändert oder ersetzt. Besonders betroffen sind Routinetätigkeiten in der Produktion und Verwaltung, während kreative und soziale Berufe weniger gefährdet sind."},
                        {"id": "B", "text": "Nachhaltiger Tourismus gewinnt an Bedeutung: Immer mehr Reisende achten auf den ökologischen Fußabdruck ihrer Urlaubsreisen. Flüge werden zunehmend kritisch bewertet, und Alternativen wie Bahnreisen oder Fahrradtouren erfreuen sich wachsender Beliebtheit. Reiseveranstalter reagieren mit umweltfreundlichen Angeboten und CO₂-Kompensationsprogrammen."},
                        {"id": "C", "text": "Ehrenamtliches Engagement in Deutschland: Über 28 Millionen Menschen engagieren sich freiwillig in Vereinen, sozialen Einrichtungen oder politischen Organisationen. Die häufigsten Bereiche sind Sport, Kultur und soziale Dienste. Studien zeigen, dass Ehrenamtliche nicht nur der Gesellschaft, sondern auch ihrer eigenen Gesundheit und Lebenszufriedenheit nutzen."},
                        {"id": "D", "text": "Urbane Gärten – Grün in der Stadt: In vielen deutschen Großstädten entstehen Gemeinschaftsgärten, in denen Stadtbewohner gemeinsam Obst, Gemüse und Kräuter anbauen. Diese sogenannten Urban-Farming-Projekte fördern nicht nur die Artenvielfalt, sondern stärken auch den sozialen Zusammenhalt in den Quartieren und bieten Erholung vom Stadtleben."},
                        {"id": "E", "text": "Medienkompetenz wird zur Schlüsselqualifikation des 21. Jahrhunderts erklärt. Der kritische Umgang mit Informationen, das Erkennen von Falschmeldungen und das Verstehen algorithmischer Empfehlungssysteme gelten als unverzichtbare Fähigkeiten. Schulen und Hochschulen integrieren zunehmend Medienkompetenz-Module in ihre Lehrpläne."}
                    ],
                    "questions": [
                        {"question_num": 1, "question_text": "Dieser Text beschreibt, wie sich Technologie auf Beschäftigung auswirkt.", "correct_answer": "A"},
                        {"question_num": 2, "question_text": "Dieser Text befasst sich mit umweltbewusstem Reisen.", "correct_answer": "B"},
                        {"question_num": 3, "question_text": "Dieser Text handelt vom freiwilligen Engagement der Bevölkerung.", "correct_answer": "C"},
                        {"question_num": 4, "question_text": "Dieser Text thematisiert den Anbau von Pflanzen in städtischen Gebieten.", "correct_answer": "D"},
                        {"question_num": 5, "question_text": "Dieser Text erläutert die Bedeutung kompetenter Informationsverarbeitung.", "correct_answer": "E"}
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "richtig_falsch",
                    "title": "Aufgabe 2 - Richtig, Falsch oder Nicht im Text?",
                    "instruction": "Lesen Sie den Text und entscheiden Sie: Ist die Aussage richtig, falsch oder steht das nicht im Text?",
                    "text": """Künstliche Intelligenz in der Medizin: Chancen und Risiken

Die Integration künstlicher Intelligenz in die medizinische Diagnostik schreitet mit beachtlicher Geschwindigkeit voran. KI-Systeme sind heute in der Lage, Röntgenbilder und MRT-Aufnahmen mit einer Präzision zu analysieren, die in bestimmten Bereichen mit der erfahrener Radiologen vergleichbar oder sogar überlegen ist. Eine Studie der Stanford University zeigte, dass ein KI-Algorithmus bestimmte Hautkrebs-Arten mit einer Genauigkeit von 91 Prozent diagnostizieren konnte, während erfahrene Dermatologen auf 86 Prozent kamen.

Dennoch stehen dem breiten Einsatz von KI im Gesundheitswesen erhebliche Hindernisse entgegen. Datenschutzrechtliche Bedenken sind besonders relevant, da medizinische KI-Systeme auf enormen Mengen sensibler Patientendaten trainiert werden müssen. Die Europäische Datenschutz-Grundverordnung setzt hier enge Grenzen, die eine internationale Zusammenarbeit erschweren.

Ein weiteres Problem ist die sogenannte algorithmische Verzerrung: KI-Systeme, die überwiegend auf Daten westlicher, wohlhabender Patientengruppen trainiert wurden, erzielen bei anderen demographischen Gruppen deutlich schlechtere Ergebnisse. Forscher der Universität Harvard wiesen nach, dass ein weit verbreiteter KI-Algorithmus zur Risikoeinschätzung von Patienten systematisch afroamerikanische Patienten benachteiligte.

Trotz dieser Herausforderungen investieren Gesundheitssysteme weltweit massiv in KI-gestützte Lösungen. Deutschland hat im Rahmen seiner KI-Strategie zwei Milliarden Euro für medizinische KI-Forschung bereitgestellt. Experten sind sich einig, dass KI die Medizin nicht ersetzen, aber als mächtiges Werkzeug ergänzen wird – vorausgesetzt, ethische und rechtliche Rahmenbedingungen werden sorgfältig entwickelt.""",
                    "questions": [
                        {"question_num": 6, "question_text": "KI-Systeme übertreffen Radiologen in allen Bereichen der Diagnostik.", "correct_answer": "Falsch"},
                        {"question_num": 7, "question_text": "Der KI-Algorithmus zur Hautkrebsdiagnose erzielte eine höhere Genauigkeit als erfahrene Dermatologen.", "correct_answer": "Richtig"},
                        {"question_num": 8, "question_text": "Die DSGVO erleichtert die internationale Zusammenarbeit bei medizinischen KI-Projekten.", "correct_answer": "Falsch"},
                        {"question_num": 9, "question_text": "Der beschriebene Harvard-Algorithmus bevorzugte systematisch bestimmte Patientengruppen.", "correct_answer": "Richtig"},
                        {"question_num": 10, "question_text": "Deutschland plant, KI vollständig in alle Krankenhäuser einzuführen.", "correct_answer": "Nicht im Text"}
                    ]
                },
                {
                    "aufgabe_num": 3,
                    "typ": "multiple_choice",
                    "title": "Aufgabe 3 - Multiple Choice",
                    "instruction": "Lesen Sie den Text. Welche Antwort (A, B oder C) passt am besten?",
                    "text": """Homeoffice und Produktivität: Was sagt die Forschung?

Die Covid-19-Pandemie hat das weltweit größte Experiment zur Telearbeit erzwungen und dabei wertvolle Erkenntnisse geliefert, die Arbeitspsychologen und Ökonomen seither intensiv auswerten. Die Ergebnisse sind differenzierter, als viele erwartet hatten.

Eine groß angelegte Studie der Universität Stanford, die 16.000 Mitarbeiter über neun Monate begleitete, zeigte eine Produktivitätssteigerung von 13 Prozent bei Homeoffice-Beschäftigten. Als Ursachen wurden weniger Ablenkungen durch Kollegen, kürzere Pausen und eine reduzierte Krankenquote identifiziert. Gleichzeitig berichteten die Mitarbeiter von einer höheren Arbeitszufriedenheit und einem geringeren Stressniveau.

Allerdings zeichnen neuere Studien ein komplexeres Bild. Für kreative und kollaborative Aufgaben, die intensive Zusammenarbeit erfordern, scheint die Präsenz im Büro weiterhin einen Vorteil zu bieten. Eine Untersuchung von Microsoft Research ergab, dass Teams im Homeoffice zwar effizienter innerhalb ihres engeren Netzwerks kommunizierten, aber deutlich weniger Verbindungen zu anderen Abteilungen und Kollegen aufbauten – ein Phänomen, das als "Silobildung" bezeichnet wird.

Führungskräfte stehen vor der Herausforderung, ein Gleichgewicht zu finden. Hybride Modelle, bei denen Mitarbeiter zwei bis drei Tage im Büro und den Rest von zu Hause arbeiten, gelten derzeit als vielversprechendster Ansatz. Sie kombinieren die Produktivitätsvorteile des Homeoffice mit den sozialen und kreativen Impulsen der Büropräsenz.

Die langfristigen gesellschaftlichen Auswirkungen sind noch nicht absehbar. Stadtplaner beobachten eine Verlagerung von Arbeitskräften aus teuren Großstädten in kleinere Städte und ländliche Regionen, was sowohl Immobilienmärkte als auch kommunale Infrastrukturen grundlegend verändern könnte.""",
                    "questions": [
                        {"question_num": 11, "question_text": "Was ergab die Stanford-Studie über Homeoffice-Mitarbeiter?", "options": ["A) Sie arbeiteten weniger Stunden.", "B) Ihre Produktivität stieg um 13 Prozent.", "C) Sie hatten mehr Kontakt zu Kollegen."], "correct_answer": "B"},
                        {"question_num": 12, "question_text": "Was versteht man laut Text unter 'Silobildung'?", "options": ["A) Mitarbeiter arbeiten effizienter.", "B) Teams kommunizieren weniger mit anderen Abteilungen.", "C) Führungskräfte verlieren den Überblick."], "correct_answer": "B"},
                        {"question_num": 13, "question_text": "Welches Arbeitsmodell wird derzeit als am vielversprechendsten angesehen?", "options": ["A) Vollständiges Homeoffice", "B) Vollständige Büropräsenz", "C) Ein hybrides Modell"], "correct_answer": "C"},
                        {"question_num": 14, "question_text": "Was beobachten Stadtplaner als Folge des Homeoffice-Trends?", "options": ["A) Mehr Menschen ziehen in Großstädte.", "B) Arbeitnehmer verlassen teure Großstädte.", "C) Immobilienpreise sinken überall."], "correct_answer": "B"},
                        {"question_num": 15, "question_text": "Welchen Vorteil bietet laut Text die Büropräsenz gegenüber dem Homeoffice?", "options": ["A) Höhere Produktivität bei Routineaufgaben", "B) Förderung kreativer und kollaborativer Prozesse", "C) Niedrigere Betriebskosten"], "correct_answer": "B"}
                    ]
                }
            ]
        },
        "hoeren": {
            "total_questions": 15,
            "duration_minutes": 40,
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "kurzgespraeche",
                    "title": "Aufgabe 1 - Kurze Gespräche",
                    "instruction": "Sie hören fünf kurze Gespräche. Sind die Aussagen richtig oder falsch?",
                    "conversations": [
                        {
                            "conv_num": 1,
                            "sprecher": [
                                {"name": "Dr. Hoffmann", "voice_id": "Orus"},
                                {"name": "Kollegin", "voice_id": "Aoede"}
                            ],
                            "script_segments": [
                                {"sprecher": "Dr. Hoffmann", "text": "[nachdenklich] Ich habe gerade den neuen Forschungsbericht gelesen. Die Ergebnisse sind interessant, aber die Methodik erscheint mir fragwürdig."},
                                {"sprecher": "Kollegin", "text": "Inwiefern? Die Stichprobengröße war doch mit über 500 Teilnehmern recht solide."},
                                {"sprecher": "Dr. Hoffmann", "text": "Das stimmt, aber die Kontrollgruppe wurde nicht ordentlich isoliert. Das beeinträchtigt die Validität der Schlussfolgerungen erheblich."},
                                {"sprecher": "Kollegin", "text": "Da haben Sie recht. Das sollten wir in unserem Review ansprechen."}
                            ],
                            "questions": [{"question_num": 1, "question_text": "Dr. Hoffmann ist mit der Stichprobengröße der Studie nicht zufrieden.", "correct_answer": "Falsch"}]
                        },
                        {
                            "conv_num": 2,
                            "sprecher": [
                                {"name": "Moderator", "voice_id": "Fenrir"},
                                {"name": "Expertin", "voice_id": "Kore"}
                            ],
                            "script_segments": [
                                {"sprecher": "Moderator", "text": "Frau Professor, wie bewerten Sie die aktuellen Maßnahmen zur Reduzierung von Plastikmüll in deutschen Städten?"},
                                {"sprecher": "Expertin", "text": "[bestimmt] Die bisherigen Schritte sind ein Anfang, reichen aber bei weitem nicht aus. Wir brauchen verbindliche Reduktionsziele und konsequente Herstellerverantwortung, nicht nur freiwillige Initiativen."},
                                {"sprecher": "Moderator", "text": "Glauben Sie, dass die EU-Plastikrichtlinie einen Unterschied macht?"},
                                {"sprecher": "Expertin", "text": "Sie ist ein Fortschritt, aber die Umsetzung variiert stark zwischen den Mitgliedsstaaten. Deutschland könnte hier eine Vorreiterrolle übernehmen."}
                            ],
                            "questions": [{"question_num": 2, "question_text": "Die Expertin hält die bisherigen Plastikmaßnahmen für ausreichend.", "correct_answer": "Falsch"}]
                        },
                        {
                            "conv_num": 3,
                            "sprecher": [
                                {"name": "Lena", "voice_id": "Aoede"},
                                {"name": "Felix", "voice_id": "Charon"}
                            ],
                            "script_segments": [
                                {"sprecher": "Lena", "text": "Felix, ich habe überlegt, ob wir unser Startup nicht lieber in Berlin gründen sollten statt in München."},
                                {"sprecher": "Felix", "text": "[überlegend] Berlin hat zweifellos ein lebhafteres Start-up-Ökosystem und günstigere Mieten. Aber München bietet bessere Vernetzung zu etablierten Unternehmen und Investoren."},
                                {"sprecher": "Lena", "text": "Stimmt. Und die Lebenshaltungskosten in München sind enorm."},
                                {"sprecher": "Felix", "text": "Lass uns beide Optionen gründlich analysieren, bevor wir entscheiden."}
                            ],
                            "questions": [{"question_num": 3, "question_text": "Felix empfiehlt, das Startup definitiv in Berlin zu gründen.", "correct_answer": "Falsch"}]
                        },
                        {
                            "conv_num": 4,
                            "sprecher": [
                                {"name": "Studentin", "voice_id": "Kore"},
                                {"name": "Professor", "voice_id": "Orus"}
                            ],
                            "script_segments": [
                                {"sprecher": "Studentin", "text": "Professor Weber, ich wollte fragen, ob ich meine Bachelorarbeit auch auf Englisch verfassen darf."},
                                {"sprecher": "Professor", "text": "Grundsätzlich ist das möglich, sofern Sie einen formellen Antrag beim Prüfungsamt stellen. Beachten Sie aber, dass die Beurteilung dann auch sprachliche Korrektheit im Englischen einschließt."},
                                {"sprecher": "Studentin", "text": "Verstanden. Gibt es eine Frist für den Antrag?"},
                                {"sprecher": "Professor", "text": "Der Antrag muss spätestens vier Wochen vor Beginn der Bearbeitungszeit eingereicht werden."}
                            ],
                            "questions": [{"question_num": 4, "question_text": "Eine Bachelorarbeit auf Englisch ist ohne besonderen Antrag möglich.", "correct_answer": "Falsch"}]
                        },
                        {
                            "conv_num": 5,
                            "sprecher": [
                                {"name": "Journalist", "voice_id": "Charon"},
                                {"name": "Bürgermeisterin", "voice_id": "Aoede"}
                            ],
                            "script_segments": [
                                {"sprecher": "Journalist", "text": "Frau Bürgermeisterin, wie planen Sie, das Wohnungsproblem in unserer Stadt anzugehen?"},
                                {"sprecher": "Bürgermeisterin", "text": "[entschlossen] Wir haben ein dreistufiges Programm entwickelt: kurzfristig mehr Sozialwohnungen, mittelfristig Anreize für private Investoren und langfristig eine neue Stadtentwicklungsplanung mit dichterem, aber lebenswertserem Wohnen."},
                                {"sprecher": "Journalist", "text": "Und wie wird das finanziert?"},
                                {"sprecher": "Bürgermeisterin", "text": "Durch eine Kombination aus kommunalen Mitteln, Landesförderung und EU-Strukturfonds."}
                            ],
                            "questions": [{"question_num": 5, "question_text": "Die Bürgermeisterin hat einen dreistufigen Plan zur Lösung des Wohnungsproblems.", "correct_answer": "Richtig"}]
                        }
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "gespraech",
                    "title": "Aufgabe 2 - Ein Gespräch",
                    "instruction": "Sie hören ein Gespräch. Wählen Sie die richtige Antwort (A, B oder C).",
                    "sprecher": [
                        {"name": "Moderatorin", "voice_id": "Kore"},
                        {"name": "Experte", "voice_id": "Orus"}
                    ],
                    "script_segments": [
                        {"sprecher": "Moderatorin", "text": "[professionell] Herzlich willkommen zu unserem Podcast über Zukunftstechnologien. Heute sprechen wir über Quantencomputing. Herr Dr. Maier, können Sie erklären, was Quantencomputer von klassischen Computern unterscheidet?"},
                        {"sprecher": "Experte", "text": "Gerne. Klassische Computer arbeiten mit Bits, die entweder 0 oder 1 sein können. Quantencomputer verwenden sogenannte Qubits, die dank eines Phänomens namens Superposition gleichzeitig 0 und 1 sein können – das ermöglicht eine exponentiell höhere Rechenkapazität."},
                        {"sprecher": "Moderatorin", "text": "Und wann werden wir Quantencomputer im Alltag sehen?"},
                        {"sprecher": "Experte", "text": "[nachdenklich] Für hochspezialisierte Anwendungen in Pharmakologie und Kryptographie werden sie in fünf bis zehn Jahren relevant sein. Für den Massenmarkt rechne ich mit mindestens zwanzig Jahren, da die Technologie noch sehr fragil ist und bei extrem niedrigen Temperaturen betrieben werden muss."},
                        {"sprecher": "Moderatorin", "text": "Welche konkreten Anwendungen versprechen die größten Durchbrüche?"},
                        {"sprecher": "Experte", "text": "Die Simulation molekularer Strukturen für die Medikamentenentwicklung ist besonders vielversprechend. Was heute Milliarden kostet und Jahre dauert, könnte mit Quantencomputern in Wochen und zu einem Bruchteil der Kosten möglich werden."},
                        {"sprecher": "Moderatorin", "text": "Gibt es auch Risiken dieser Technologie?"},
                        {"sprecher": "Experte", "text": "[ernst] Absolut. Das größte Risiko ist die Bedrohung aktueller Verschlüsselungsstandards. Quantencomputer könnten die gängigen RSA-Verschlüsselungen innerhalb von Minuten brechen – was bedeutet, dass wir jetzt schon an quantensicherer Kryptographie arbeiten müssen."},
                        {"sprecher": "Moderatorin", "text": "Wie ist Deutschland im internationalen Vergleich aufgestellt?"},
                        {"sprecher": "Experte", "text": "Deutschland hat zwei Milliarden Euro in Quantentechnologien investiert und ist europäischer Vorreiter. Im globalen Vergleich liegen aber die USA und China deutlich vorne, insbesondere bei der Anzahl der einsatzfähigen Qubits."}
                    ],
                    "questions": [
                        {"question_num": 6, "question_text": "Was ist der Hauptunterschied zwischen klassischen Computern und Quantencomputern?", "options": ["A) Quantencomputer sind kleiner.", "B) Quantencomputer verwenden Qubits statt Bits.", "C) Quantencomputer brauchen keinen Strom."], "correct_answer": "B"},
                        {"question_num": 7, "question_text": "Wann erwartet der Experte Quantencomputer im Massenmarkt?", "options": ["A) In fünf Jahren", "B) In zehn Jahren", "C) In mindestens zwanzig Jahren"], "correct_answer": "C"},
                        {"question_num": 8, "question_text": "Welchen Bereich nennt der Experte als vielversprechendste Anwendung?", "options": ["A) Unterhaltungselektronik", "B) Medikamentenentwicklung", "C) Soziale Netzwerke"], "correct_answer": "B"},
                        {"question_num": 9, "question_text": "Welches Risiko nennt der Experte im Zusammenhang mit Quantencomputern?", "options": ["A) Überhitzung von Rechenzentren", "B) Gefährdung aktueller Verschlüsselungsstandards", "C) Hoher Energieverbrauch"], "correct_answer": "B"},
                        {"question_num": 10, "question_text": "Wie ist Deutschlands Position im globalen Quantencomputing?", "options": ["A) Deutschland ist weltweit führend.", "B) Deutschland ist europäischer Vorreiter, aber hinter USA und China.", "C) Deutschland hat noch nicht in Quantencomputing investiert."], "correct_answer": "B"}
                    ]
                },
                {
                    "aufgabe_num": 3,
                    "typ": "ansagen",
                    "title": "Aufgabe 3 - Kurze Texte aus dem Radio",
                    "instruction": "Sie hören fünf kurze Radiotexte. Sind die Aussagen richtig oder falsch?",
                    "ansagen": [
                        {"ansage_num": 1, "sprecher": "Moderator", "voice_id": "Fenrir",
                         "text": "[professionell] Meldung aus der Wissenschaft: Forscher der Technischen Universität München haben einen neuen Batterietyp entwickelt, der sich innerhalb von fünf Minuten vollständig aufladen lässt und eine dreifach höhere Energiedichte als herkömmliche Lithium-Ionen-Akkus aufweist. Die Serienreife wird für 2028 erwartet.",
                         "question_num": 11, "question_text": "Die neue Batterie hat eine niedrigere Energiedichte als herkömmliche Akkus.", "correct_answer": "Falsch"},
                        {"ansage_num": 2, "sprecher": "Moderatorin", "voice_id": "Kore",
                         "text": "[sachlich] Wirtschaftsnachrichten: Der Deutsche Aktienindex DAX hat heute seinen höchsten Stand seit drei Jahren erreicht. Analysten führen dies auf positive Quartalszahlen aus dem Technologiesektor und sinkende Inflationserwartungen zurück.",
                         "question_num": 12, "question_text": "Der DAX hat heute einen Rekordstand erreicht.", "correct_answer": "Falsch"},
                        {"ansage_num": 3, "sprecher": "Moderator", "voice_id": "Fenrir",
                         "text": "Kulturmeldung: Die Staatsgalerie Stuttgart präsentiert ab dem 1. März eine umfangreiche Retrospektive der Künstlerin Käthe Kollwitz. Die Ausstellung umfasst über 200 Werke aus allen Schaffensphasen und ist bis Ende Juni geöffnet.",
                         "question_num": 13, "question_text": "Die Ausstellung der Staatsgalerie Stuttgart zeigt weniger als 200 Werke.", "correct_answer": "Falsch"},
                        {"ansage_num": 4, "sprecher": "Moderatorin", "voice_id": "Kore",
                         "text": "[informativ] Verkehrsmeldung: Auf der Autobahn A9 zwischen München und Nürnberg kommt es wegen Bauarbeiten noch bis Ende des Monats zu erheblichen Verzögerungen. Reisende werden gebeten, Alternativrouten zu nutzen oder die Reise auf die Abendstunden zu verlegen.",
                         "question_num": 14, "question_text": "Die Bauarbeiten auf der A9 sind bereits abgeschlossen.", "correct_answer": "Falsch"},
                        {"ansage_num": 5, "sprecher": "Moderator", "voice_id": "Fenrir",
                         "text": "Gesundheitsnachricht: Das Bundesgesundheitsministerium empfiehlt angesichts steigender Grippezahlen die Impfung für alle Personen über 60 Jahre sowie chronisch Kranke. Die Impfung ist in allen Arztpraxen und Apotheken mit Impfservice erhältlich.",
                         "question_num": 15, "question_text": "Die Grippeimpfung wird für alle Personen über 60 empfohlen.", "correct_answer": "Richtig"}
                    ]
                }
            ]
        },
        "schreiben": {
            "total_time_minutes": 45,
            "aufgaben": [{
                "aufgabe_num": 1,
                "aufgabe_typ": "erörterung",
                "aufgabe": "In vielen Ländern wird diskutiert, ob soziale Medien für Jugendliche unter 16 Jahren verboten werden sollten.\n\nSchreiben Sie einen argumentativen Aufsatz zu diesem Thema. Gehen Sie dabei auf folgende Punkte ein:\n• Vorteile sozialer Medien für Jugendliche\n• Nachteile und Risiken sozialer Medien\n• Ihre eigene Meinung mit Begründung\n\nSchreiben Sie ca. 200 Wörter.",
                "min_words": 180,
                "max_words": 250
            }]
        },
        "sprechen": {
            "total_time_minutes": 15,
            "teile": [
                {
                    "teil_num": 1,
                    "titel": "Präsentation eines Themas",
                    "instructions": "Sie stellen ein Thema vor und diskutieren es mit dem Prüfer.",
                    "fragen": [
                        {"frage_num": 1, "frage_text": "[professionell] Guten Tag und willkommen zur Prüfung. Stellen Sie sich bitte kurz vor: Wer sind Sie, was machen Sie beruflich oder akademisch, und was interessiert Sie besonders?", "needs_audio": True},
                        {"frage_num": 2, "frage_text": "Sie haben das Thema 'Digitalisierung im Bildungswesen' vorbereitet. Bitte präsentieren Sie Ihre Überlegungen dazu. Sie haben etwa zwei Minuten Zeit.", "needs_audio": True},
                        {"frage_num": 3, "frage_text": "Sie haben einige interessante Punkte angesprochen. Wie stehen Sie persönlich dazu: Verbessert Technologie das Lernen, oder birgt sie mehr Risiken als Chancen?", "needs_audio": True}
                    ]
                },
                {
                    "teil_num": 2,
                    "titel": "Diskussion eines gesellschaftlichen Themas",
                    "instructions": "Sie diskutieren ein aktuelles gesellschaftliches Thema mit dem Prüfer.",
                    "fragen": [
                        {"frage_num": 4, "frage_text": "Lassen Sie uns über das Thema Klimawandel sprechen. Viele Menschen sind der Meinung, dass individuelle Maßnahmen keinen Unterschied machen. Was denken Sie darüber?", "needs_audio": True},
                        {"frage_num": 5, "frage_text": "Welche Verantwortung tragen Ihrer Meinung nach Regierungen, Unternehmen und Einzelpersonen beim Klimaschutz jeweils?", "needs_audio": True},
                        {"frage_num": 6, "frage_text": "Wie würden Sie reagieren, wenn die Regierung einschneidende Maßnahmen wie ein Verbot von Inlandsflügen oder stark erhöhte Benzinsteuern einführen würde?", "needs_audio": True}
                    ]
                },
                {
                    "teil_num": 3,
                    "titel": "Problemlösung und Argumentation",
                    "instructions": "Sie lösen gemeinsam mit dem Prüfer ein Problem und vertreten Ihren Standpunkt.",
                    "aufgabe": "Eine Stadt überlegt, ob sie die Innenstadt vollständig autofrei machen soll. Diskutieren Sie die Vor- und Nachteile und entwickeln Sie gemeinsam einen Lösungsvorschlag.",
                    "fragen": [
                        {"frage_num": 7, "frage_text": "Stellen Sie sich vor, Sie sind Stadtrat. Die Stadt möchte die Innenstadt autofrei machen. Welche Argumente würden Sie für und gegen diesen Vorschlag vorbringen?", "needs_audio": True},
                        {"frage_num": 8, "frage_text": "Wie könnte man die Interessen von Geschäftsleuten, Pendlern und Umweltschützern ausgewogen berücksichtigen? Haben Sie einen konkreten Kompromissvorschlag?", "needs_audio": True}
                    ]
                }
            ]
        }
    }

@app.on_event("startup")
async def startup():
    existing = await db.exams.find_one({"exam_id": "exam_academic_001"}, {"_id": 0})
    if not existing:
        seed = get_seed_exam()
        await db.exams.insert_one(seed)
        logger.info("Seeded exam_academic_001")
    else:
        # Migration: Add question_layout if missing
        sections = existing.get("listening", {}).get("sections", [])
        if sections and not sections[0].get("question_layout"):
            seed = get_seed_exam()
            seed_sections = seed.get("listening", {}).get("sections", [])
            for i, ss in enumerate(seed_sections):
                if ss.get("question_layout"):
                    await db.exams.update_one(
                        {"exam_id": "exam_academic_001"},
                        {"$set": {f"listening.sections.{i}.question_layout": ss["question_layout"]}}
                    )
                if ss.get("instruction") and not sections[i].get("instruction"):
                    await db.exams.update_one(
                        {"exam_id": "exam_academic_001"},
                        {"$set": {f"listening.sections.{i}.instruction": ss["instruction"]}}
                    )
            logger.info("Migrated exam_academic_001 with question layouts")

    # Seed TELC B1
    if not await db.exams.find_one({"exam_id": "exam_telc_b1_001"}):
        await db.exams.insert_one(get_telc_b1_seed())
        logger.info("Seeded exam_telc_b1_001")

    # Seed TELC B2
    if not await db.exams.find_one({"exam_id": "exam_telc_b2_001"}):
        await db.exams.insert_one(get_telc_b2_seed())
        logger.info("Seeded exam_telc_b2_001")

# ==========================================
# STRIPE SUBSCRIPTIONS
# ==========================================
STRIPE_PRICE_MONTHLY = os.environ.get('STRIPE_PRICE_MONTHLY', '')
STRIPE_PRICE_ANNUAL = os.environ.get('STRIPE_PRICE_ANNUAL', '')

@api_router.post("/stripe/checkout")
async def create_checkout_session(data: StripeCheckoutRequest, request: Request):
    user = await get_current_user(request)
    price_id = STRIPE_PRICE_MONTHLY if data.plan == "monthly" else STRIPE_PRICE_ANNUAL
    if not price_id:
        raise HTTPException(400, "Stripe not configured")
    try:
        customer_id = user.get("stripe_customer_id")
        if not customer_id:
            customer = stripe.Customer.create(email=user["email"], name=user["name"])
            customer_id = customer.id
            await db.users.update_one({"user_id": user["user_id"]}, {"$set": {"stripe_customer_id": customer_id}})

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=f"{FRONTEND_URL}/subscription/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_URL}/pricing",
        )
        return {"url": session.url}
    except stripe.error.StripeError as e:
        raise HTTPException(400, str(e))

@api_router.post("/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(400, "Invalid signature")

    if event["type"] == "customer.subscription.created" or event["type"] == "customer.subscription.updated":
        sub = event["data"]["object"]
        customer_id = sub["customer"]
        status = sub["status"]
        tier = "pro" if status == "active" else "free"
        expires_at = datetime.fromtimestamp(sub["current_period_end"], tz=timezone.utc).isoformat()
        await db.users.update_one({"stripe_customer_id": customer_id}, {"$set": {
            "subscription": {"tier": tier, "stripe_subscription_id": sub["id"],
                             "stripe_customer_id": customer_id, "expires_at": expires_at, "status": status}
        }})
    elif event["type"] == "customer.subscription.deleted":
        sub = event["data"]["object"]
        customer_id = sub["customer"]
        await db.users.update_one({"stripe_customer_id": customer_id}, {"$set": {
            "subscription": {"tier": "free", "status": "canceled"}
        }})
    return {"received": True}

@api_router.get("/stripe/portal")
async def customer_portal(request: Request):
    user = await get_current_user(request)
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(400, "No subscription found")
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{FRONTEND_URL}/dashboard"
        )
        return {"url": session.url}
    except stripe.error.StripeError as e:
        raise HTTPException(400, str(e))

@api_router.get("/subscription/status")
async def get_subscription_status(request: Request):
    user = await get_current_user(request)
    sub = user.get("subscription", {})
    tier = "free"
    if sub:
        tier = sub.get("tier", "free")
        if tier != "free":
            expires_at = sub.get("expires_at")
            if expires_at:
                try:
                    exp = datetime.fromisoformat(expires_at)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp < datetime.now(timezone.utc):
                        tier = "free"
                except:
                    tier = "free"
    return {"tier": tier, "subscription": sub}

# ==========================================
# ADMIN ENDPOINTS
# ==========================================
@api_router.get("/admin/exams")
async def admin_list_exams(request: Request):
    """Admin view - all exams with full details"""
    require_admin(await get_current_user(request))
    exams = await db.exams.find({}, {"_id": 0, "exam_id": 1, "title": 1, "pathway": 1, "exam_type": 1, "telc_level": 1, "status": 1,
        "audio_progress": 1, "created_at": 1, "error_message": 1,
        "listening.total_questions": 1, "reading.total_questions": 1,
        "writing.tasks": 1, "speaking.parts": 1}).to_list(100)
    # Add audio count
    for exam in exams:
        count = await db.audio_files.count_documents({"exam_id": exam["exam_id"]})
        exam["audio_files_count"] = count
    return exams

@api_router.delete("/admin/exams/{exam_id}")
async def admin_delete_exam(exam_id: str, request: Request):
    """Delete an exam, its attempts, and its audio files."""
    require_admin(await get_current_user(request))
    # Delete attempts first (FK constraint)
    await db.attempts.delete_many({"exam_id": exam_id})
    audio_result = await db.audio_files.delete_many({"exam_id": exam_id})
    await db.exams.delete_one({"exam_id": exam_id})
    return {"deleted": True, "audio_files_removed": audio_result.deleted_count}

@api_router.post("/admin/exams/{exam_id}/regenerate-audio")
async def admin_regenerate_audio(exam_id: str, background_tasks: BackgroundTasks, request: Request):
    """Force regenerate all audio for an exam"""
    require_admin(await get_current_user(request))
    # Clear existing audio
    await db.audio_files.delete_many({"exam_id": exam_id})
    # Reset audio IDs in exam
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise HTTPException(404, "Exam not found")
    for si, section in enumerate(exam.get("listening", {}).get("sections", [])):
        for sgi, seg in enumerate(section.get("script_segments", [])):
            await db.exams.update_one({"exam_id": exam_id},
                {"$unset": {f"listening.sections.{si}.script_segments.{sgi}.audio_id": ""}})
        await db.exams.update_one({"exam_id": exam_id},
            {"$unset": {f"listening.sections.{si}.instruction_audio_id": ""}})
    for pi, part in enumerate(exam.get("speaking", {}).get("parts", [])):
        for qi, q in enumerate(part.get("questions", [])):
            await db.exams.update_one({"exam_id": exam_id},
                {"$unset": {f"speaking.parts.{pi}.questions.{qi}.audio_id": ""}})
    await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "pending_audio", "audio_progress": 0}})
    background_tasks.add_task(generate_exam_audio, exam_id)
    return {"status": "regenerating"}

@api_router.get("/admin/stats")
async def admin_stats(request: Request):
    """Platform statistics"""
    require_admin(await get_current_user(request))
    return {
        "total_exams": await db.exams.count_documents({}),
        "ready_exams": await db.exams.count_documents({"status": "ready"}),
        "total_audio_files": await db.audio_files.count_documents({}),
        "total_users": await db.users.count_documents({}),
        "total_attempts": await db.attempts.count_documents({}),
        "completed_attempts": await db.attempts.count_documents({"status": "completed"}),
        "ielts_exams": await db.exams.count_documents({"exam_type": {"$in": ["ielts", None]}}),
        "telc_exams": await db.exams.count_documents({"exam_type": "telc"}),
        "pro_users": await db.users.count_documents({"subscription.tier": "pro"}),
    }

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown():
    pass  # Supabase client uses connection pooling; nothing to close
