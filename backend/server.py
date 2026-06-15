"""
TELC Deutsch Mock Exam Platform - Backend Server
=================================================
InsForge (DB + Storage) + Replicate Gemini TTS + OpenRouter AI + Emergent Google Auth
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
# AUTH  (open-access mode — no login required)
# ==========================================
_GUEST_USER = {
    "user_id": "guest",
    "email": "guest@example.com",
    "name": "Guest",
    "picture": "",
    "subscription": {},
    "is_admin": False,
}

async def get_current_user(request: Request) -> dict:
    session_token = request.cookies.get("session_token")
    if not session_token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            session_token = auth_header[7:]
    # No session → return guest user (open access)
    if not session_token:
        return _GUEST_USER

    session = await db.user_sessions.find_one({"session_token": session_token}, {"_id": 0})
    if not session:
        return _GUEST_USER

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

def _strip_correct_answers(exam_copy: dict) -> dict:
    """Remove correct_answer from all question structures before sending to client.
    Uses (field or {}) to safely handle NULL/None values stored in the DB.
    """
    # IELTS: listening sections + reading passages
    for section in (exam_copy.get("listening") or {}).get("sections", []):
        for q in section.get("questions", []):
            q.pop("correct_answer", None)
    for passage in (exam_copy.get("reading") or {}).get("passages", []):
        for q in passage.get("questions", []):
            q.pop("correct_answer", None)
    # TELC: hoeren (conversations + direct questions + ansagen)
    for aufgabe in (exam_copy.get("hoeren") or {}).get("aufgaben", []):
        for q in aufgabe.get("questions", []):
            q.pop("correct_answer", None)
        for conv in aufgabe.get("conversations", []):
            for q in conv.get("questions", []):
                q.pop("correct_answer", None)
        for ansage in aufgabe.get("ansagen", []):
            ansage.pop("correct_answer", None)
    # TELC: lesen
    for aufgabe in (exam_copy.get("lesen") or {}).get("aufgaben", []):
        for q in aufgabe.get("questions", []):
            q.pop("correct_answer", None)
    # TELC: sprachbausteine (MC options + wortbank options)
    for aufgabe in (exam_copy.get("sprachbausteine") or {}).get("aufgaben", []):
        for opt in aufgabe.get("options", []):
            opt.pop("correct_answer", None)
    return exam_copy


@api_router.get("/exams/{exam_id}")
async def get_exam(exam_id: str):
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise HTTPException(404, "Exam not found")
    return _strip_correct_answers(json.loads(json.dumps(exam)))


@api_router.get("/exams/{exam_id}/full")
async def get_exam_full(exam_id: str, request: Request):
    """Get exam with answers — admin only."""
    user = await get_current_user(request)
    require_admin(user)
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
    from starlette.responses import RedirectResponse as _Redirect
    storage_path = await database.get_audio_path(audio_id)
    if not storage_path:
        raise HTTPException(404, "Audio not found")
    public_url = database.get_audio_public_url(storage_path)
    # InsForge storage returns a 302 → CDN. Redirect the client directly;
    # browsers + fetch(credentials:"include") follow redirects transparently.
    return _Redirect(public_url, status_code=302)

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

    is_telc = exam.get("exam_type") == "telc"
    module_order = (
        ["listening", "reading", "sprachbausteine", "writing", "speaking"]
        if is_telc
        else ["listening", "reading", "writing", "speaking"]
    )
    current_idx = module_order.index(module) if module in module_order else -1
    next_module = module_order[current_idx + 1] if 0 <= current_idx + 1 < len(module_order) else None

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
    if level == "A1":
        return """CEFR A1 LEVEL REQUIREMENTS — apply with absolute strictness:
REFERENCE STANDARD: telc Deutsch A1 / Goethe-Zertifikat A1 / Start Deutsch 1.
TOPICS: ONLY the most concrete everyday situations: greetings, numbers, dates, times, family members, colours, simple food/drink, basic shopping (price, size), one's own name/address/country, very simple weather, basic body parts. NO workplaces, NO abstract concepts whatsoever.
VOCABULARY: Only the 800 most common German words. Calibration examples of ALLOWED words: kommen, heißen, wohnen, Brot, kaufen, Uhr, Montag, rot, groß, Familie, Hund, Haus, ja, nein, bitte, danke. FORBIDDEN words (too advanced): trotzdem, obwohl, eigentlich, wahrscheinlich, Veranstaltung, Umgebung.
GRAMMAR: ONLY Präsens of sein/haben/regular verbs/basic modals (können, müssen, möchten). Single main clause only. NO subordinate clauses, NO Perfekt in questions, NO Dativ plurals with adjective endings, NO Konjunktiv of any kind.
SENTENCE STYLE: Maximum 8 words per sentence. "Ich heiße Maria. Ich komme aus Spanien. Ich wohne in Berlin." — this is the target complexity. WRONG LEVEL: "Obwohl ich müde bin, gehe ich zum Supermarkt."
LISTENING SCRIPTS: Very slow, very clear, pauses between sentences. Speakers introduce themselves, ask for prices, say times, spell their name.
QUESTIONS: Only test whether an explicitly stated fact was heard/read. One unambiguous correct answer. Distractors must use the same vocabulary but wrong values (wrong number, wrong day, wrong name).
ANTI-PATTERNS — if your output contains any of these it is WRONG: subordinate clauses, Konjunktiv II, abstract nouns, sentences longer than 12 words, topics like "society" or "technology" or "environment"."""
    elif level == "A2":
        return """CEFR A2 LEVEL REQUIREMENTS — apply with absolute strictness:
REFERENCE STANDARD: telc Deutsch A2 / Goethe-Zertifikat A2 / Start Deutsch 2.
TOPICS: Extended everyday contexts — neighbourhood, simple workplace routines, public transport, leisure activities (sport, cinema, café), simple health (doctor visit, pharmacy), simple letters/messages. Still concrete and familiar. NO abstract debates.
VOCABULARY: ~1,500 most common German words. ALLOWED: Termin, Arzt, Bushaltestelle, Einkaufszentrum, Öffnungszeiten, Freizeit, Kollege, meistens, manchmal. FORBIDDEN (too advanced): gesellschaftlich, Nachhaltigkeit, Fachkräftemangel, analysieren, Konjunktiv.
GRAMMAR: Präsens, Perfekt (regular + haben/sein), simple Präteritum (war, hatte), basic modal verbs, basic dative case, simple subordinate clauses with weil/dass/wenn. NO Konjunktiv II, NO complex passive, NO participial constructions, NO Genitiv case.
SENTENCE STYLE: Short sentences, max 15 words. "Ich war gestern beim Arzt, weil ich Halsschmerzen hatte." — this is the target complexity. WRONG LEVEL: "Die zunehmende Digitalisierung stellt viele Unternehmen vor große Herausforderungen."
LISTENING SCRIPTS: Slow-to-normal speed. Short conversations in everyday settings: at the doctor, making an appointment, at the supermarket, on the phone with a friend. No expert opinions.
QUESTIONS: Test explicitly stated facts. At most 1 question per text requires a very simple inference ("She is tired → she did not sleep well"). Distractors are clearly wrong on close reading.
ANTI-PATTERNS — if your output contains any of these it is WRONG: Konjunktiv II, Genitiv constructions, abstract social topics, expert interviews, academic vocabulary, sentences with 3+ clauses."""
    elif level == "B1":
        return """CEFR B1 LEVEL REQUIREMENTS — apply strictly:
REFERENCE STANDARD: telc Deutsch B1 / Goethe-Zertifikat B1 / Zertifikat Deutsch.
TOPICS: Familiar and concrete: work routines, school/study life, travel planning, housing/neighbours, health and appointments, hobbies, local events, simple media (newspaper summaries). NOT abstract societal analysis.
VOCABULARY: ~3,000 most common words. ALLOWED: Erfahrung, Möglichkeit, erledigen, beschreiben, Veranstaltung, Unterschied, Vorschlag. FORBIDDEN (too advanced): Globalisierung, Nachhaltigkeit, Digitalisierung (as abstract topic), Konjunktiv II in tests, nominalisations like "das Gelingen".
GRAMMAR: Full use of Präsens/Perfekt/Präteritum, modals, subordinate clauses (weil, dass, wenn, obwohl, damit), Komparativ/Superlativ, basic Passiv (wird gemacht). NO Konjunktiv II except in set phrases (könnte/würde), NO complex participial phrases, NO Genitiv attributes.
SENTENCE STYLE: Clear multi-clause sentences but not complex. "Sie suchen eine Wohnung, weil sie in eine neue Stadt gezogen ist." WRONG LEVEL: "Die zunehmende Urbanisierung führt zu erhöhtem Wohnungsdruck in Ballungsräumen."
LISTENING SCRIPTS: Normal spoken speed, clear articulation, natural but not fast. Conversations about everyday topics with direct statements. No implicit meaning required.
QUESTIONS: Primarily test explicit understanding. Max 1–2 questions per section require very simple inference. Correct answer is stated or very directly implied in the text. Distractors are plausible on surface but factually wrong.
CALIBRATION CHECK: Before finalising, ask yourself — "Could a person who passed the A2 exam and studied German for 1 year understand every sentence in this text?" If yes, level is correct."""
    elif level == "B2":
        return """CEFR B2 LEVEL REQUIREMENTS — apply strictly:
REFERENCE STANDARD: telc Deutsch B2 / Goethe-Zertifikat B2.
TOPICS: Abstract and professional — digital transformation, environmental policy, workplace culture shifts, intercultural communication, public health debates, media literacy, economic inequality, education reform, urban development. NOT everyday shopping or simple personal routines.
VOCABULARY: ~6,000 word range. REQUIRED vocabulary types: abstract nouns (Nachhaltigkeit, Digitalisierung, Fachkräftemangel, Herausforderung, Maßnahme), academic collocations (einen Beitrag leisten, im Mittelpunkt stehen, auf dem Vormarsch sein), verbal idioms. AVOID: words a complete beginner knows.
GRAMMAR: MUST USE: Konjunktiv II (for hypotheticals and reported speech: "Er sagte, er würde gerne..."), Passiv (wird/wurde + Partizip II), complex subordinate clauses (sodass, sofern, während, indem, wobei), participial constructions ("der bereits erschienene Bericht..."), nominalisations (das Scheitern, die Veränderung).
SENTENCE STYLE: Long, complex, multi-clause. "Obwohl der technologische Fortschritt zweifellos Vorteile mit sich bringt, warnen Experten vor den gesellschaftlichen Risiken der zunehmenden Automatisierung." — this is the target sentence complexity. WRONG LEVEL: "Ich finde Homeoffice gut, weil man flexibler ist."
LISTENING SCRIPTS: Natural conversational speed, speaker attitudes must be INFERRED not directly stated. Expert opinions, hedged claims, disagreement. At least 40% of content is implicit.
QUESTIONS: MUST include questions where the answer requires: (a) inferring speaker attitude/opinion, (b) combining two pieces of information, (c) recognising what is NOT said. Distractors must be very close — often containing a correct word from the text but applying it wrongly.
CALIBRATION CHECK: Before finalising, ask yourself — "Would a B1 student struggle with this text?" If they would not struggle, the text is NOT B2 level. Raise the vocabulary, lengthen the sentences, add complexity."""
    else:  # C1
        return """CEFR C1 LEVEL REQUIREMENTS — apply strictly:
REFERENCE STANDARD: telc Deutsch C1 Hochschule / Goethe-Zertifikat C1.
TOPICS: Specialist and academic — constitutional law debates, cognitive science findings, geopolitical analysis, literary criticism, advanced ethics (Bioethik, KI-Regulierung), complex social phenomena (Polarisierung, Identitätspolitik), economics (Konjunktur, Fiskalstabilität), philosophy of language.
VOCABULARY: Near-native range. REQUIRED: Nominalisierungen (das Scheitern, die Inanspruchnahme), discipline-specific terms (Regressionsanalyse, Verfassungswidrigkeit, kognitive Dissonanz, Fiskalstabilität), figurative language, hedging expressions (es bleibt fraglich ob, sofern man davon ausgeht dass), irony markers.
GRAMMAR: Extended participial constructions ("die im Zuge der Reform neu eingeführten Bestimmungen"), elaborate concessive/conditional structures (wenngleich, insofern als, nicht nur... sondern auch), indirect speech with tense shifts across multiple clauses, rhetorical devices (Anapher, Litotes, Ellipse).
SENTENCE STYLE: Very long, highly subordinated. "Insofern als die jüngsten Studienergebnisse darauf hindeuten, dass kognitive Verzerrungen nicht nur individuelle, sondern auch systemische Entscheidungsfehler begünstigen, stellt sich die Frage, inwieweit institutionelle Strukturen reformiert werden müssten." — this is the target complexity.
LISTENING SCRIPTS: Near-native speed with authentic hesitations, self-corrections, complex subordination, irony, hedged claims. Speakers take nuanced or seemingly contradictory positions.
QUESTIONS: Test ability to evaluate arguments, detect implicit rhetorical stance, distinguish main thesis from subsidiary points, recognise irony or presupposition. All distractors are partially correct — wrong only at a subtle nuanced level."""


def _lesen_level_hints(level: str) -> dict:
    """Per-level text/question specs for the Leseverstehen prompt."""
    if level in ("A1", "A2"):
        return {
            "aufgabe1_topics": "A1/A2: Very short everyday notices, opening hours, simple signs, short messages from family/friends.",
            "aufgabe1_length": "40–70 words each",
            "aufgabe2_text": "short informational text (150–220 words) on a very concrete everyday topic (buying a train ticket, a library card, a gym membership) — extremely simple sentences, present tense, common vocabulary only.",
            "aufgabe2_questions": "All 5 correct answers are DIRECTLY stated word-for-word in the text. Zero inference.",
            "aufgabe3_topics": "A1/A2: Everyday needs like finding a swimming pool, buying food, getting a bus pass, booking a room. Ads are short notices (2–3 sentences).",
        }
    elif level == "B1":
        return {
            "aufgabe1_topics": "B1: Short articles, notices, brief opinion pieces on familiar topics (travel tips, neighbourhood events, hobby clubs, local job ads).",
            "aufgabe1_length": "80–120 words each",
            "aufgabe2_text": "informational text (280–350 words) on a familiar everyday topic — simple paragraphs, concrete situation, clear cause-and-effect. B1 calibration: every sentence should be understandable after 1 year of German study.",
            "aufgabe2_questions": "Correct answer is directly stated in text. Max 1 question requires a basic inference ('She arrived late → she missed the beginning').",
            "aufgabe3_topics": "B1: Situations and ads cover everyday needs: language courses, part-time jobs, sports clubs, rental flats, simple services.",
        }
    else:  # B2, C1
        return {
            "aufgabe1_topics": "B2/C1: Newspaper extracts, opinion pieces, professional announcements, editorial summaries on abstract topics.",
            "aufgabe1_length": "100–150 words each",
            "aufgabe2_text": "complex text (430–570 words) on an abstract/professional topic — argumentation, statistics, expert views, counterarguments. Grammar MUST include: Konjunktiv II, Passiv, participial constructions, nominalisations. B2 calibration: a B1 student would NOT understand this text.",
            "aufgabe2_questions": "At least 3 of 5 questions require inferencing, identifying opinion, or understanding implicit meaning. Distractors contain correct words used incorrectly.",
            "aufgabe3_topics": "B2/C1: Situations and ads cover professional/specific needs: specialist training with entry conditions, services requiring qualifications, technical requirements, complex scheduling.",
        }


async def ai_generate_telc_exam(exam_id: str, level: str):
    """Generate a telc Deutsch exam at the specified CEFR level."""
    level_spec = _telc_level_spec(level)
    hints = _lesen_level_hints(level)
    sys_prompt = (
        f"You are a certified telc Deutsch {level} exam author with 15 years experience "
        f"writing official exams. Return valid JSON only — no markdown fences, no commentary. "
        f"CRITICAL LEVEL ENFORCEMENT: {level_spec}"
    )

    try:
        # ── LESEVERSTEHEN ─────────────────────────────────────────────────────
        lesen_prompt = f"""Generate a telc Deutsch {level} Leseverstehen test with exactly 3 Aufgaben.

MANDATORY CEFR LEVEL: {level_spec}

Aufgabe 1 (q1-5): Zuordnung
5 short texts (A-E, {hints['aufgabe1_length']}). {hints['aufgabe1_topics']}
10 headings (a-j); only 5 match the texts.
correct_answer: heading letter

Aufgabe 2 (q6-10): Multiple Choice
One {hints['aufgabe2_text']}
5 questions with options a/b/c. {hints['aufgabe2_questions']}

Aufgabe 3 (q11-20): Anzeigen-Zuordnung
10 situations + 12 short ads/notices (a-l). {hints['aufgabe3_topics']}
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
        hoeren_topic_ideas = {
            "A1": "A1 topic ideas: sich vorstellen, Lieblingsessen, Tiere, Wetter, Wochentage — ONLY the simplest personal facts, max 3–4 sentences per monologue, present tense only, common words only",
            "A2": "A2 topic ideas: Einkaufen im Supermarkt, ein Arzttermin, ein Ausflug am Wochenende, Sport in der Freizeit — short everyday experiences, 4–6 sentences, Perfekt allowed",
            "B1": "B1 topic ideas: Hausarbeit im Alltag, Haustiere halten, Urlaub planen, Sport und Freizeit — familiar topics, 5–8 sentences, direct personal opinions",
            "B2": "B2 topic ideas: Homeoffice-Erfahrungen, Klimaschutz im Alltag, soziale Medien und Gesellschaft, Fachkräftemangel — abstract angles, 6–9 sentences, implied attitudes",
            "C1": "C1 topic ideas: KI und die Zukunft der Arbeit, Bildungsreform aus verschiedenen Perspektiven, Regulierung sozialer Medien — nuanced academic views, hedged claims, irony",
        }
        hoeren_gespraech_hints = {
            "A1": "A1: very simple exchange between 2 people (e.g. at a market, asking for directions) — 6–8 exchanges, present tense, max 1 clause per turn, no opinions",
            "A2": "A2: simple dialogue (booking an appointment, asking about bus times) — 8–10 exchanges, mostly present tense, simple questions and answers, no abstract content",
            "B1": "B1: local community topic or everyday theme — 10–12 exchanges, simple language, direct facts, max 1 opinion per speaker",
            "B2": "B2: expert interview on abstract/professional topic (Umwelt, Technologie, Gesellschaft) — 12–14 exchanges, speaker attitude must be INFERRED, complex sentences",
            "C1": "C1: specialist debate with complex reasoning, hedged claims, apparent contradictions — 14–16 exchanges, near-native speed implied",
        }
        hoeren_ansagen_hints = {
            "A1": "A1: 2 short sentences max, only explicit facts (time, place, price, name) — train/bus departure, shop opening time, simple phone message",
            "A2": "A2: 2–3 simple sentences with one specific piece of information to extract — cinema schedule, pharmacy hours, simple event announcement",
            "B1": "B1: 2–3 sentences, everyday info (departure times, opening hours, event details) — clear and direct",
            "B2": "B2: 3–4 sentences with conditions or exceptions, semi-official register",
            "C1": "C1: 4–5 sentences with complex official/legal language, conditions, exceptions",
        }
        hoeren_q_hints = {
            "A1": "A1: question tests ONE explicitly stated fact (a number, a name, a day, a simple yes/no). Correct answer is a word said in the text. Zero inference.",
            "A2": "A2: question tests an explicitly stated fact. At most 1 question per Aufgabe requires a very simple inference.",
            "B1": "B1: mostly explicit facts, max 2 questions require basic inference per Aufgabe.",
            "B2": "B2: at least 4 of 10 Aufgabe-2 questions require inferring speaker attitude or implicit meaning. Distractors use words from the text incorrectly.",
            "C1": "C1: at least 6 of 10 Aufgabe-2 questions require evaluating implicit stance or detecting nuanced position.",
        }
        t1_hint = hoeren_topic_ideas.get(level, hoeren_topic_ideas["B2"])
        t2_hint = hoeren_gespraech_hints.get(level, hoeren_gespraech_hints["B2"])
        t3_hint = hoeren_ansagen_hints.get(level, hoeren_ansagen_hints["B2"])
        q_hint = hoeren_q_hints.get(level, hoeren_q_hints["B2"])

        hoeren_prompt = f"""Generate a telc Deutsch {level} Hörverstehen (Listening Comprehension) test with exactly 3 Aufgaben.
ALL questions use ONLY Richtig/Falsch format — absolutely no multiple-choice options.

MANDATORY CEFR LEVEL: {level_spec}

═══════════════════════════════════════════════════════════════
AUFGABE 1 — Kurztexte (5 Monologe zum gleichen Thema)
═══════════════════════════════════════════════════════════════
• 5 SHORT MONOLOGUES — each spoken by a DIFFERENT single speaker
• All 5 texts share ONE common topic — each speaker gives their personal view/experience
• {t1_hint}
• QUESTION DIFFICULTY: {q_hint}
• 1 Richtig/Falsch question per monologue (question_num 1–5)
• heard_times: 1 (heard ONCE only — keine Wiederholung)
• preparation_seconds: 30
• Use `conversations` array; each entry has exactly ONE sprecher giving a monologue

═══════════════════════════════════════════════════════════════
AUFGABE 2 — Ein Gespräch / Interview
═══════════════════════════════════════════════════════════════
• 1 radio interview or discussion between 2 speakers (e.g. Moderatorin + Experte/Gast)
• {t2_hint}
• 10 Richtig/Falsch questions (question_num 6–15) — NO options array
• QUESTION DIFFICULTY: {q_hint}
• heard_times: 2 (heard TWICE — zweimal)
• preparation_seconds: 60

═══════════════════════════════════════════════════════════════
AUFGABE 3 — Kurze Ansagen
═══════════════════════════════════════════════════════════════
• 5 independent short announcements (radio, Bahnhof, telephone recording, store intercom)
• {t3_hint}
• 1 Richtig/Falsch per announcement (question_num 16–20)
• QUESTION DIFFICULTY: {q_hint}
• heard_times: 2 (heard TWICE each)
• preparation_seconds: 30
• Single announcer voice per ansage; use "Ansagerin"/"Ansager" or specific role names

Return valid JSON only — no markdown, no commentary:
{{"aufgaben": [
  {{
    "aufgabe_num": 1,
    "typ": "kurzgespraeche",
    "title": "Aufgabe 1",
    "instruction": "Sie hören fünf kurze Texte. Kreuzen Sie an: Sind die Aussagen richtig oder falsch? Sie hören jeden Text einmal.",
    "heard_times": 1,
    "preparation_seconds": 30,
    "topic": "...[the shared topic, e.g. 'Hausarbeit im Alltag']",
    "conversations": [
      {{
        "conv_num": 1,
        "sprecher": [{{"name": "Frau Berger", "voice_id": ""}}],
        "script_segments": [
          {{"sprecher": "Frau Berger", "text": "...sentence 1... sentence 2... sentence 3... sentence 4... sentence 5..."}},
          {{"sprecher": "Frau Berger", "text": "...sentence 6... sentence 7..."}}
        ],
        "questions": [{{"question_num": 1, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Richtig"}}]
      }},
      {{
        "conv_num": 2,
        "sprecher": [{{"name": "Herr Koch", "voice_id": ""}}],
        "script_segments": [{{"sprecher": "Herr Koch", "text": "...monologue..."}}],
        "questions": [{{"question_num": 2, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Falsch"}}]
      }},
      {{
        "conv_num": 3,
        "sprecher": [{{"name": "eine Studentin", "voice_id": ""}}],
        "script_segments": [{{"sprecher": "eine Studentin", "text": "..."}}],
        "questions": [{{"question_num": 3, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Richtig"}}]
      }},
      {{
        "conv_num": 4,
        "sprecher": [{{"name": "Herr Müller", "voice_id": ""}}],
        "script_segments": [{{"sprecher": "Herr Müller", "text": "..."}}],
        "questions": [{{"question_num": 4, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Falsch"}}]
      }},
      {{
        "conv_num": 5,
        "sprecher": [{{"name": "Frau Schmidt", "voice_id": ""}}],
        "script_segments": [{{"sprecher": "Frau Schmidt", "text": "..."}}],
        "questions": [{{"question_num": 5, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Richtig"}}]
      }}
    ]
  }},
  {{
    "aufgabe_num": 2,
    "typ": "gespraech",
    "title": "Aufgabe 2",
    "instruction": "Sie hören jetzt ein Gespräch. Kreuzen Sie an: Sind die Aussagen richtig oder falsch? Sie hören das Gespräch zweimal.",
    "heard_times": 2,
    "preparation_seconds": 60,
    "sprecher": [
      {{"name": "Moderatorin", "voice_id": ""}},
      {{"name": "Herr Dr. Weber", "voice_id": ""}}
    ],
    "script_segments": [
      {{"sprecher": "Moderatorin", "text": "...opening question..."}},
      {{"sprecher": "Herr Dr. Weber", "text": "...answer..."}},
      {{"sprecher": "Moderatorin", "text": "...follow-up..."}},
      {{"sprecher": "Herr Dr. Weber", "text": "..."}},
      {{"sprecher": "Moderatorin", "text": "..."}},
      {{"sprecher": "Herr Dr. Weber", "text": "..."}},
      {{"sprecher": "Moderatorin", "text": "..."}},
      {{"sprecher": "Herr Dr. Weber", "text": "..."}},
      {{"sprecher": "Moderatorin", "text": "..."}},
      {{"sprecher": "Herr Dr. Weber", "text": "..."}},
      {{"sprecher": "Moderatorin", "text": "..."}},
      {{"sprecher": "Herr Dr. Weber", "text": "...closing remark..."}}
    ],
    "questions": [
      {{"question_num": 6, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Richtig"}},
      {{"question_num": 7, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Falsch"}},
      {{"question_num": 8, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Richtig"}},
      {{"question_num": 9, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Falsch"}},
      {{"question_num": 10, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Richtig"}},
      {{"question_num": 11, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Falsch"}},
      {{"question_num": 12, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Richtig"}},
      {{"question_num": 13, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Falsch"}},
      {{"question_num": 14, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Richtig"}},
      {{"question_num": 15, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Falsch"}}
    ]
  }},
  {{
    "aufgabe_num": 3,
    "typ": "ansagen",
    "title": "Aufgabe 3",
    "instruction": "Sie hören fünf kurze Texte aus dem Radio und anderen Medien. Kreuzen Sie an: Sind die Aussagen richtig oder falsch? Sie hören jeden Text zweimal.",
    "heard_times": 2,
    "preparation_seconds": 30,
    "ansagen": [
      {{"ansage_num": 1, "sprecher": "Ansagerin", "voice_id": "", "text": "...", "question_num": 16, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Richtig"}},
      {{"ansage_num": 2, "sprecher": "Ansager", "voice_id": "", "text": "...", "question_num": 17, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Falsch"}},
      {{"ansage_num": 3, "sprecher": "Ansagerin", "voice_id": "", "text": "...", "question_num": 18, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Richtig"}},
      {{"ansage_num": 4, "sprecher": "Ansager", "voice_id": "", "text": "...", "question_num": 19, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Falsch"}},
      {{"ansage_num": 5, "sprecher": "Ansagerin", "voice_id": "", "text": "...", "question_num": 20, "question_type": "richtig_falsch", "question_text": "...", "correct_answer": "Richtig"}}
    ]
  }}
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
            # Top-level sprecher (Aufgabe 2 gespraech)
            assign_voices_to_speakers(
                aufgabe.get("sprecher", []),
                female_pool=_FEMALE_VOICES_DE,
                male_pool=_MALE_VOICES_DE,
            )
            # Per-conversation sprecher (Aufgabe 1 monologues / kurzgespraeche)
            for conv in aufgabe.get("conversations", []):
                assign_voices_to_speakers(
                    conv.get("sprecher", []),
                    female_pool=_FEMALE_VOICES_DE,
                    male_pool=_MALE_VOICES_DE,
                )
            # Ansagen voices (Aufgabe 3)
            for ansage in aufgabe.get("ansagen", []):
                if not ansage.get("voice_id"):
                    sprecher_name = ansage.get("sprecher", "Ansager")
                    gender = _speaker_gender(sprecher_name)
                    ansage["voice_id"] = (
                        _FEMALE_VOICES_DE[0] if gender == "female" else _MALE_VOICES_DE[0]
                    )

        # ── SPRACHBAUSTEINE ───────────────────────────────────────────────────
        sprach_mc_hints = {
            "A1": "A very short, simple personal message (80–120 words) — greetings, simple present tense facts, common everyday words. Gaps test: articles (der/die/das/ein/eine), common prepositions (in, auf, mit, zu), basic verb forms (bin, habe, komme).",
            "A2": "A short personal letter or message (140–180 words) about an everyday topic (a meeting, a visit). Gaps test: common prepositions, Perfekt verb forms (habe ... gemacht), simple conjunctions (und, aber, weil), basic modal verbs.",
            "B1": "A personal letter or informal email about everyday topic (200-240 words). Gaps test: common prepositions, articles, simple conjunctions, present/past verb forms.",
            "B2": "A semi-formal letter, report excerpt, or article on a professional/abstract topic (220-260 words). Gaps test: Konjunktiv II, Passiv, complex conjunctions (obwohl, sodass, während), nominalisations, subjunctive indirect speech.",
            "C1": "A formal academic or professional text (240-280 words). Gaps test: extended participial phrases, complex connectors (wenngleich, insofern als), Genitiv constructions, academic collocations.",
        }
        sprach_wb_hints = {
            "A1": "A very short notice or message (80–120 words). Words are the most common concrete nouns and verbs (Haus, kaufen, groß, kommen, Montag). Distractors are other common words from a completely different semantic field.",
            "A2": "A short everyday text (140–180 words) — hotel notice, simple letter from a friend. Words are common concrete nouns/verbs. Distractors are plausible-looking but semantically wrong.",
            "B1": "Everyday letter or notice (hotel, club, neighbourhood), 200-240 words. Words are common, concrete nouns/verbs. Distractors are words from same topic area but wrong meaning.",
            "B2": "Professional or academic text (company announcement, research summary, news article), 220-260 words. Words include abstract nouns, collocations, technical terms. Distractors semantically close (same field, wrong collocate).",
            "C1": "Academic or specialist text (scientific summary, legal notice), 240-280 words. Words include nominalisations, rare collocations, academic verbs. Distractors are near-synonyms wrong in context.",
        }
        mc_hint = sprach_mc_hints.get(level, sprach_mc_hints["B2"])
        wb_hint = sprach_wb_hints.get(level, sprach_wb_hints["B2"])

        sprachbausteine_prompt = f"""Generate a telc Deutsch {level} Sprachbausteine test with exactly 2 Aufgaben.

MANDATORY CEFR LEVEL: {level_spec}

Aufgabe 1 — Lückentext Multiple Choice (q21–30)
{mc_hint}
Exactly 10 numbered gaps as {{21}}, {{22}} ... {{30}}.
3 options per gap (a/b/c).
correct_answer: "a", "b", or "c"

Aufgabe 2 — Lückentext Wortschatz (q31–40)
{wb_hint}
Exactly 10 gaps as {{31}}, {{32}} ... {{40}}.
Word bank: 15 CAPITALISED words (a–o), 5 distractors.
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
        sprachbausteine_data["duration_minutes"] = 30

        # ── SCHRIFTLICHER AUSDRUCK ────────────────────────────────────────────
        writing_formats = {
            "A1": ("formular_kurzmitteilung", "ca. 30 Wörter", 20, 45,
                   "Füllen Sie das Formular aus oder schreiben Sie eine sehr kurze Mitteilung.\n"
                   "Schreiben Sie: Ihr Name, Ihr Land, Ihre Telefonnummer, und einen einfachen Satz.\n"
                   "Benutzen Sie nur einfache, bekannte Wörter."),
            "A2": ("kurze_mitteilung", "ca. 50 Wörter", 40, 65,
                   "Sie möchten Ihrer Freundin / Ihrem Freund eine kurze Nachricht schreiben.\n"
                   "Schreiben Sie zu diesen drei Punkten:\n"
                   "- Wann treffen Sie sich?\n- Wo treffen Sie sich?\n- Was machen Sie zusammen?"),
            "B1": ("brief_email", "ca. 100 Wörter", 80, 130,
                   "Sie haben eine E-Mail von Ihrer Freundin / Ihrem Freund bekommen. "
                   "Sie/Er bittet Sie um Hilfe oder Rat zu einem alltäglichen Thema "
                   "(Umzug, Reise oder Freizeitaktivität). Schreiben Sie eine Antwort-E-Mail.\n"
                   "Schreiben Sie zu allen drei Punkten:\n"
                   "- ob und wie Sie helfen können\n- wann Sie Zeit haben\n- ein konkreter Vorschlag"),
            "B2": ("erörterung", "ca. 200 Wörter", 180, 250,
                   "Schreiben Sie einen argumentativen Aufsatz zum Thema 'Beruf, Weiterbildung oder gesellschaftliches Thema'.\n"
                   "Gehen Sie dabei auf folgende Punkte ein:\n"
                   "- Vorteile und Chancen\n- Nachteile und Risiken\n- Ihre eigene Position mit Begründung"),
            "C1": ("erörterung", "ca. 250 Wörter", 220, 300,
                   "Schreiben Sie einen differenzierten Aufsatz zum Thema 'Wissenschaft, Ethik oder gesellschaftliche Entwicklung'.\n"
                   "Analysieren Sie verschiedene Perspektiven, beziehen Sie sich auf gesellschaftliche "
                   "Zusammenhänge und vertreten Sie eine begründete eigene Position."),
        }
        w_typ, w_hint, w_min, w_max, w_prompt = writing_formats.get(
            level, writing_formats["B2"]
        )
        schreiben = {
            "total_time_minutes": 30,
            "aufgaben": [{
                "aufgabe_num": 1,
                "aufgabe_typ": w_typ,
                "aufgabe": w_prompt,
                "min_words": w_min, "max_words": w_max,
            }]
        }

        # ── MÜNDLICHER AUSDRUCK ───────────────────────────────────────────────
        speaking_topics = {
            "A1": "Meine Familie",
            "A2": "Freizeit und Hobbys",
            "B1": "Homeoffice",
            "B2": "Nachhaltigkeit im Alltag",
            "C1": "KI und die Zukunft der Arbeit",
        }
        topic = speaking_topics.get(level, "Nachhaltigkeit im Alltag")
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


def get_telc_b2_seed_002():
    return {
        "exam_id": "exam_telc_b2_002",
        "title": "TELC Deutsch B2 - Übungstest 2",
        "exam_type": "telc",
        "telc_level": "B2",
        "pathway": "telc_b2",
        "status": "pending_audio",
        "audio_progress": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "lesen": {
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "zuordnung",
                    "instructions": "Lesen Sie die folgenden Texte A–E und die Überschriften a–j. Ordnen Sie jedem Text die passende Überschrift zu. Fünf Überschriften passen nicht.",
                    "texts": [
                        {
                            "id": "A",
                            "text": "Immer mehr Unternehmen stellen fest, dass ihre Mitarbeiter im Homeoffice produktiver arbeiten als im Büro. Eine aktuelle Studie des Fraunhofer-Instituts zeigt, dass Beschäftigte zu Hause durchschnittlich 15 Prozent mehr Aufgaben erledigen. Experten führen dies auf die ruhigere Arbeitsumgebung und die wegfallenden Pendelzeiten zurück. Allerdings betonen Arbeitspsychologen, dass dies stark von der individuellen Persönlichkeit und den häuslichen Verhältnissen abhänge.",
                            "word_count": 78
                        },
                        {
                            "id": "B",
                            "text": "Die Digitalisierung verändert nicht nur die Art, wie wir arbeiten, sondern auch, welche Qualifikationen Arbeitgeber suchen. Kenntnisse in der Datenanalyse, im Umgang mit künstlicher Intelligenz und in der Cybersicherheit sind heute in nahezu jeder Branche gefragt. Bildungseinrichtungen reagieren auf diesen Wandel mit neuen Studiengängen und berufsbegleitenden Zertifikatsprogrammen, die auf die Anforderungen der digitalen Wirtschaft zugeschnitten sind.",
                            "word_count": 70
                        },
                        {
                            "id": "C",
                            "text": "Das sogenannte Recht auf Nichterreichbarkeit wird in Deutschland zunehmend diskutiert. Gewerkschaften fordern gesetzliche Regelungen, die Arbeitnehmer davor schützen, außerhalb der regulären Arbeitszeiten per E-Mail oder Telefon kontaktiert zu werden. In Frankreich und Portugal existieren solche Regelungen bereits. Kritiker hingegen argumentieren, dass flexible Arbeitszeiten und moderne Kommunikationsmittel eine starre Trennung von Arbeits- und Freizeit ohnehin unmöglich machen.",
                            "word_count": 73
                        },
                        {
                            "id": "D",
                            "text": "Co-Working-Spaces erleben seit der Pandemie einen neuen Aufschwung. Viele Selbstständige und Remote-Mitarbeiter schätzen diese geteilten Arbeitsflächen als Kompromiss zwischen Homeoffice und klassischem Büro. Sie bieten nicht nur Schreibtischplätze und schnelles Internet, sondern auch die Möglichkeit, spontan Kontakte zu knüpfen und sich auszutauschen. In Großstädten wie Berlin, Hamburg und München wächst das Angebot an solchen Räumen stetig.",
                            "word_count": 72
                        },
                        {
                            "id": "E",
                            "text": "Videokonferenzen sind aus dem modernen Arbeitsleben nicht mehr wegzudenken. Doch viele Beschäftigte leiden unter der sogenannten Zoom-Fatigue — einer Erschöpfung, die durch stundenlange virtuelle Besprechungen entsteht. Forschungen zeigen, dass der intensive Blickkontakt auf dem Bildschirm, die ständige Selbstwahrnehmung durch das eigene Kamerabild und das Fehlen nonverbaler Kommunikation das Gehirn stärker beanspruchen als Gespräche in Präsenz.",
                            "word_count": 70
                        }
                    ],
                    "headings": [
                        {"id": "a", "text": "Neue Kompetenzen für die digitale Arbeitswelt"},
                        {"id": "b", "text": "Warum virtuelle Meetings so ermüdend sind"},
                        {"id": "c", "text": "Höhere Leistung außerhalb des Büros"},
                        {"id": "d", "text": "Gemeinsam arbeiten — aber nicht zusammen"},
                        {"id": "e", "text": "Gesetzlicher Schutz vor ständiger Erreichbarkeit"},
                        {"id": "f", "text": "Homeoffice als Ursache sozialer Isolation"},
                        {"id": "g", "text": "Technologische Sicherheitsrisiken im Heimnetzwerk"},
                        {"id": "h", "text": "Nachhaltigkeit durch digitale Prozesse"},
                        {"id": "i", "text": "Führungskräfte im Wandel der digitalen Ära"},
                        {"id": "j", "text": "Flexibles Arbeiten als Karrierehemmnis"}
                    ],
                    "questions": [
                        {"question_num": 1, "question_text": "Text A", "correct_answer": "c"},
                        {"question_num": 2, "question_text": "Text B", "correct_answer": "a"},
                        {"question_num": 3, "question_text": "Text C", "correct_answer": "e"},
                        {"question_num": 4, "question_text": "Text D", "correct_answer": "d"},
                        {"question_num": 5, "question_text": "Text E", "correct_answer": "b"}
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "multiple_choice",
                    "instructions": "Lesen Sie den folgenden Text und beantworten Sie die Fragen. Wählen Sie bei jeder Frage die richtige Antwort a, b oder c.",
                    "text_title": "Fachkräftemangel in Deutschland: Herausforderungen und Lösungsansätze",
                    "text": "Der Fachkräftemangel ist eine der drängendsten wirtschaftlichen Herausforderungen Deutschlands. Laut einer Studie des Instituts für Arbeitsmarkt- und Berufsforschung (IAB) fehlen derzeit in Deutschland über 1,7 Millionen qualifizierte Arbeitskräfte. Besonders betroffen sind die Bereiche Pflege, Handwerk, Ingenieurwesen und Informationstechnologie. Die Folgen sind gravierend: Unternehmen können Aufträge nicht annehmen, Projekte verzögern sich, und die Wettbewerbsfähigkeit des Standortes Deutschland leidet.\n\nDie Ursachen des Mangels sind vielfältig. Einerseits scheiden geburtenstarke Jahrgänge der sogenannten Babyboomer-Generation aus dem Arbeitsleben aus, ohne dass ausreichend jüngere Arbeitskräfte nachfolgen. Andererseits hat die Digitalisierung in vielen Branchen neue Berufsbilder entstehen lassen, für die bislang kaum ausgebildete Fachleute zur Verfügung stehen.\n\nPolitik und Wirtschaft reagieren mit verschiedenen Strategien. Das Fachkräfteeinwanderungsgesetz, das 2020 in Kraft trat und 2023 weiter reformiert wurde, soll die Zuwanderung qualifizierter Arbeitskräfte aus Nicht-EU-Ländern erleichtern. Anerkennungsverfahren für ausländische Berufsabschlüsse wurden vereinfacht, und die Möglichkeiten zur Jobsuche vor der eigentlichen Einwanderung wurden ausgeweitet. Erste Erfolge sind sichtbar: Die Zahl der zugewanderten Fachkräfte ist gestiegen.\n\nGleichzeitig setzen Unternehmen verstärkt auf die Qualifizierung ihrer bestehenden Belegschaft. Weiterbildungsprogramme, die gezielt digitale Kompetenzen vermitteln, werden staatlich gefördert. Das Konzept des lebenslangen Lernens gewinnt an Bedeutung, wobei Online-Plattformen und berufsbegleitende Kurse eine zentrale Rolle spielen.\n\nExperten warnen jedoch, dass kurzfristige Maßnahmen allein nicht ausreichen werden. Eine nachhaltige Lösung erfordere strukturelle Veränderungen im Bildungssystem, attraktivere Rahmenbedingungen für Familien und eine konsequente Förderung von Frauen in technischen Berufen. Nur ein umfassender Ansatz könne den Fachkräftemangel mittelfristig beheben.",
                    "questions": [
                        {
                            "question_num": 6,
                            "question_text": "Wie viele Fachkräfte fehlen laut IAB-Studie in Deutschland?",
                            "options": ["a) Über 1,7 Millionen", "b) Rund 500.000", "c) Mehr als 3 Millionen"],
                            "correct_answer": "a"
                        },
                        {
                            "question_num": 7,
                            "question_text": "Was ist eine der Hauptursachen des Fachkräftemangels?",
                            "options": ["a) Zu viele Studienabgänger in technischen Fächern", "b) Das Ausscheiden der Babyboomer-Generation aus dem Berufsleben", "c) Rückgang der Digitalisierung in deutschen Unternehmen"],
                            "correct_answer": "b"
                        },
                        {
                            "question_num": 8,
                            "question_text": "Was hat das reformierte Fachkräfteeinwanderungsgesetz zum Ziel?",
                            "options": ["a) Die Einwanderung aus EU-Ländern zu begrenzen", "b) Qualifizierten Fachkräften aus Nicht-EU-Ländern die Zuwanderung zu erleichtern", "c) Deutsche Arbeitnehmer vor ausländischer Konkurrenz zu schützen"],
                            "correct_answer": "b"
                        },
                        {
                            "question_num": 9,
                            "question_text": "Wie reagieren Unternehmen auf den Fachkräftemangel?",
                            "options": ["a) Sie reduzieren ihr Angebot an Stellen", "b) Sie setzen auf Weiterbildung der bestehenden Belegschaft", "c) Sie verlagern ihre Produktionsstätten ins Ausland"],
                            "correct_answer": "b"
                        },
                        {
                            "question_num": 10,
                            "question_text": "Was fordern Experten für eine nachhaltige Lösung des Problems?",
                            "options": ["a) Ausschließlich mehr Einwanderung", "b) Kürzere Ausbildungszeiten in allen Bereichen", "c) Strukturelle Veränderungen im Bildungssystem und bessere Bedingungen für Familien"],
                            "correct_answer": "c"
                        }
                    ]
                },
                {
                    "aufgabe_num": 3,
                    "typ": "anzeigen",
                    "instructions": "Lesen Sie die Anzeigen a–l und die Situationen 11–20. Welche Anzeige passt zu welcher Situation? Für zwei Situationen gibt es keine passende Anzeige. Schreiben Sie dann x.",
                    "ads": [
                        {
                            "id": "a",
                            "title": "IT-Projektmanager (m/w/d) – Remote möglich",
                            "text": "Wir suchen erfahrene IT-Projektmanager für die Leitung internationaler Softwareprojekte. Mindestens 5 Jahre Berufserfahrung, PMP-Zertifizierung erwünscht. 60 % Homeoffice möglich. Gehalt: 75.000–90.000 € jährlich. Bewerbung an: jobs@digitalworks.de"
                        },
                        {
                            "id": "b",
                            "title": "Webinar: Effektiv im Homeoffice arbeiten",
                            "text": "Zweitägiges Online-Seminar für Berufstätige, die ihre Produktivität im Homeoffice steigern möchten. Themen: Zeitmanagement, digitale Tools, Kommunikation mit Kollegen. Termin: 14.–15. März. Kosten: 120 €. Anmeldung: www.digitalseminar.de"
                        },
                        {
                            "id": "c",
                            "title": "Zertifikatskurs: Datenschutz und DSGVO",
                            "text": "Berufsbegleitender Kurs für Fach- und Führungskräfte. Lernen Sie die wichtigsten Grundlagen der Datenschutz-Grundverordnung. 8 Abendkurse à 90 Minuten. Teilnahmegebühr: 380 €. Zertifikat der IHK. Anmeldung: www.ihk-weiterbildung.de"
                        },
                        {
                            "id": "d",
                            "title": "Bürokaufmann/-frau gesucht – Vollzeit",
                            "text": "Kleines Familienunternehmen sucht zuverlässige Bürokraft für administrative Aufgaben. Kenntnisse in MS Office erforderlich. Keine Homeoffice-Möglichkeit. Gehalt nach Tarif. Bitte Bewerbung mit Lebenslauf senden an: info@familienbetrieb-berlin.de"
                        },
                        {
                            "id": "e",
                            "title": "Technischer Redakteur (m/w/d) – 100 % remote",
                            "text": "Für unser wachsendes Softwareunternehmen suchen wir einen erfahrenen technischen Redakteur zur Erstellung von Handbüchern und Online-Hilfen. Sehr gute Deutschkenntnisse (C1), Englischkenntnisse von Vorteil. Vollständig remote. Bewerbung: karriere@softwaredoc.com"
                        },
                        {
                            "id": "f",
                            "title": "Online-Kurs: Grundlagen der künstlichen Intelligenz",
                            "text": "Selbstlernkurs für Einsteiger ohne Vorkenntnisse. Lernen Sie, wie KI funktioniert und wie Sie sie in Ihrem Berufsalltag einsetzen können. Kursdauer: 6 Wochen à 3 Stunden/Woche. Preis: 199 €. Start jederzeit möglich. www.ki-kurs-online.de"
                        },
                        {
                            "id": "g",
                            "title": "Stellenangebot: Customer Success Manager – hybrid",
                            "text": "Sie betreuen unsere B2B-Kunden und sichern deren langfristige Zufriedenheit. Erfahrung im Kundenservice, gutes Englisch, CRM-Kenntnisse erwünscht. 2 Tage/Woche Büropräsenz erforderlich, Rest Homeoffice. Bewerbung: hr@businesssolutions.de"
                        },
                        {
                            "id": "h",
                            "title": "Intensivkurs Spanisch für Berufstätige",
                            "text": "Kleine Gruppen (max. 8 Personen), erfahrene Muttersprachler als Lehrer. Niveau A1 bis B2. Kurse montags und mittwochs, 18:30–20:00 Uhr. Monatliche Kursgebühr: 85 €. Erstes Schnuppertreffen kostenlos! www.sprachschule-zentrum.de"
                        },
                        {
                            "id": "i",
                            "title": "Buchhalter/in (m/w/d) – Teilzeit, remote",
                            "text": "Steuerberatungskanzlei sucht erfahrene Buchhalter/in für 20–25 Stunden/Woche. Kenntnisse in DATEV erforderlich. Vollständig von zu Hause aus möglich. Flexibel einteilbare Arbeitszeiten. Vergütung: 28 €/Stunde. jobs@steuerkanzlei-nord.de"
                        },
                        {
                            "id": "j",
                            "title": "Seminar: Führen auf Distanz",
                            "text": "Eintägiges Präsenzseminar für Führungskräfte, die verteilte Teams leiten. Inhalte: virtuelle Kommunikation, Vertrauensaufbau, Mitarbeitermotivation im Homeoffice. Termin: 22. April. Ort: Frankfurt am Main. Preis: 450 €. www.fuehrungsakademie.de"
                        },
                        {
                            "id": "k",
                            "title": "Grafikdesigner (m/w/d) – Festanstellung oder Freelance",
                            "text": "Kreativagentur sucht talentierte Grafikdesigner für Print- und Digitalprojekte. Adobe Creative Suite erforderlich. Festanstellung oder Freelance-Basis möglich. Überwiegend remote. Portfolio bitte einreichen unter: design@kreaturagentur.de"
                        },
                        {
                            "id": "l",
                            "title": "Online-Workshop: Stressbewältigung im Homeoffice",
                            "text": "Interaktiver Workshop für alle, die im Homeoffice unter hohem Druck stehen. Psychologin Dr. Müller vermittelt praktische Entspannungstechniken und Strategien zur Work-Life-Balance. Samstag, 9:00–13:00 Uhr. Teilnahme: 75 €. www.mental-balance-online.de"
                        }
                    ],
                    "questions": [
                        {
                            "question_num": 11,
                            "question_text": "Martina ist ausgebildete Buchhalterin und möchte wegen ihrer Kinder nur halbtags arbeiten. Sie sucht eine Stelle, bei der sie von zu Hause aus arbeiten kann.",
                            "correct_answer": "i"
                        },
                        {
                            "question_num": 12,
                            "question_text": "Klaus ist Abteilungsleiter und hat seit der Pandemie ein Team von zwölf Personen, die alle von verschiedenen Standorten aus arbeiten. Er sucht eine Möglichkeit, seine Führungskompetenzen in dieser Situation zu verbessern.",
                            "correct_answer": "j"
                        },
                        {
                            "question_num": 13,
                            "question_text": "Sandra arbeitet als freiberufliche Designerin und sucht eine Festanstellung oder Freelance-Projekte, bei denen sie hauptsächlich von zu Hause aus arbeiten kann.",
                            "correct_answer": "k"
                        },
                        {
                            "question_num": 14,
                            "question_text": "Tobias fühlt sich im Homeoffice oft gestresst und hat Schwierigkeiten, Beruf und Privatleben zu trennen. Er sucht einen Kurs, der ihm dabei helfen kann.",
                            "correct_answer": "l"
                        },
                        {
                            "question_num": 15,
                            "question_text": "Anna hat einen kaufmännischen Abschluss und möchte sich im Bereich Datenschutz weiterbilden, um für ihren Arbeitgeber den DSGVO-Datenschutzbeauftragten zu entlasten.",
                            "correct_answer": "c"
                        },
                        {
                            "question_num": 16,
                            "question_text": "Peter ist erfahrener Projektleiter in der IT-Branche und sucht eine gut bezahlte Stelle, bei der er größtenteils von zu Hause aus arbeiten kann.",
                            "correct_answer": "a"
                        },
                        {
                            "question_num": 17,
                            "question_text": "Elena möchte lernen, wie sie KI-Tools in ihrem Büroalltag einsetzen kann. Sie hat keine Vorkenntnisse und möchte flexibel lernen.",
                            "correct_answer": "f"
                        },
                        {
                            "question_num": 18,
                            "question_text": "Georg ist Ingenieur und sucht eine vollständig remote Stelle, bei der er technische Dokumentationen auf Deutsch verfasst.",
                            "correct_answer": "e"
                        },
                        {
                            "question_num": 19,
                            "question_text": "Monika arbeitet in der Personalabteilung und sucht eine Stelle als Bürokauffrau in Vollzeit. Sie möchte jedoch nicht von zu Hause aus arbeiten.",
                            "correct_answer": "d"
                        },
                        {
                            "question_num": 20,
                            "question_text": "Lukas möchte ein Musikinstrument erlernen und sucht einen geeigneten Abendkurs in seiner Stadt.",
                            "correct_answer": "x"
                        }
                    ]
                }
            ]
        },
        "hoeren": {
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "kurzgespraeche",
                    "heard_times": 1,
                    "preparation_seconds": 30,
                    "topic": "Homeoffice — persönliche Erfahrungen",
                    "instructions": "Sie hören fünf kurze Aussagen zum Thema Homeoffice. Sie hören jeden Text einmal. Entscheiden Sie bei jeder Aussage, ob die Aussage richtig oder falsch ist.",
                    "conversations": [
                        {
                            "id": 1,
                            "sprecher": "Sprecher 1",
                            "voice_id": "Fenrir",
                            "script": "Ich arbeite jetzt seit zwei Jahren vollständig von zu Hause aus und würde niemals mehr ins Büro zurückwechseln wollen. Der größte Vorteil ist für mich die eingesparte Pendelzeit. Früher habe ich täglich fast zwei Stunden im Zug gesessen. Diese Zeit nutze ich jetzt für Sport und Kochen. Meine Chefin war anfangs skeptisch, aber meine Leistungen haben sich sogar verbessert. Ich denke, dass Homeoffice nicht für jeden geeignet ist, aber für mich persönlich ist es die ideale Arbeitsform.",
                            "questions": [
                                {
                                    "question_num": 1,
                                    "question_text": "Der Sprecher möchte wieder ins Büro zurückwechseln.",
                                    "question_type": "richtig_falsch",
                                    "correct_answer": "falsch"
                                }
                            ]
                        },
                        {
                            "id": 2,
                            "sprecher": "Sprecher 2",
                            "voice_id": "Kore",
                            "script": "Ich habe das Homeoffice zunächst sehr genossen, aber nach einigen Monaten merkte ich, dass mir der soziale Kontakt zu meinen Kollegen fehlte. Die kurzen Gespräche in der Küche, das gemeinsame Mittagessen — das alles fehlt mir jetzt sehr. Außerdem habe ich zu Hause keine klare Trennung zwischen Arbeitszeit und Freizeit. Ich sitze manchmal bis spät abends am Computer, weil ich das Gefühl habe, nicht genug geleistet zu haben. Deswegen gehe ich inzwischen wieder drei Tage pro Woche ins Büro.",
                            "questions": [
                                {
                                    "question_num": 2,
                                    "question_text": "Die Sprecherin geht gar nicht mehr ins Büro.",
                                    "question_type": "richtig_falsch",
                                    "correct_answer": "falsch"
                                }
                            ]
                        },
                        {
                            "id": 3,
                            "sprecher": "Sprecher 3",
                            "voice_id": "Fenrir",
                            "script": "Als Vater von zwei kleinen Kindern ist das Homeoffice für mich eine riesige Herausforderung. Die Kinder verstehen nicht, dass Papa arbeitet, auch wenn er zu Hause ist. Ich habe mir daher ein kleines Arbeitszimmer eingerichtet, das ich während der Arbeitszeit abschließe. Meine Frau übernimmt in dieser Zeit die Kinderbetreuung. Dieses Modell funktioniert bei uns gut, aber es erfordert eine klare Absprache und gegenseitigen Respekt. Ohne diese Struktur wäre Homeoffice mit Kindern für mich unmöglich.",
                            "questions": [
                                {
                                    "question_num": 3,
                                    "question_text": "Der Sprecher hat ein eigenes Arbeitszimmer eingerichtet.",
                                    "question_type": "richtig_falsch",
                                    "correct_answer": "richtig"
                                }
                            ]
                        },
                        {
                            "id": 4,
                            "sprecher": "Sprecher 4",
                            "voice_id": "Kore",
                            "script": "Ich leite ein kleines Team von acht Personen, und das komplett digital ist eine echte Herausforderung. Am Anfang hatten wir viele Missverständnisse, weil die nonverbale Kommunikation per Video einfach nicht funktioniert wie im echten Leben. Wir haben dann regelmäßige virtuelle Kaffeepausen eingeführt und ein klares Kommunikationsprotokoll vereinbart. Seitdem läuft es deutlich besser. Ich glaube, dass gute Führung auf Distanz vor allem Vertrauen und klare Strukturen erfordert.",
                            "questions": [
                                {
                                    "question_num": 4,
                                    "question_text": "Die Sprecherin findet, dass virtuelle Kaffeepausen die Kommunikation verbessert haben.",
                                    "question_type": "richtig_falsch",
                                    "correct_answer": "richtig"
                                }
                            ]
                        },
                        {
                            "id": 5,
                            "sprecher": "Sprecher 5",
                            "voice_id": "Fenrir",
                            "script": "Ich bin Softwareentwickler und arbeite seit der Pandemie fast ausschließlich remote. Technisch gesehen ist das für meinen Beruf kein Problem — ich brauche nur meinen Laptop und eine stabile Internetverbindung. Was ich allerdings vermisse, ist das spontane Brainstorming mit Kollegen. Wenn man ein Problem hat, kann man nicht einfach kurz zum Nachbartisch gehen. Deshalb haben wir in unserem Team feste Zeiten für gemeinsame digitale Arbeitssessions eingeführt, was gut funktioniert.",
                            "questions": [
                                {
                                    "question_num": 5,
                                    "question_text": "Der Sprecher hat technische Probleme mit dem Homeoffice.",
                                    "question_type": "richtig_falsch",
                                    "correct_answer": "falsch"
                                }
                            ]
                        }
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "gespraech",
                    "heard_times": 2,
                    "preparation_seconds": 60,
                    "topic": "Homeoffice und Arbeitswelt der Zukunft",
                    "instructions": "Sie hören ein Interview zum Thema Homeoffice und Arbeitswelt der Zukunft. Sie hören das Gespräch zweimal. Entscheiden Sie bei jeder Aussage, ob sie richtig oder falsch ist.",
                    "sprecher": [
                        {"id": "sprecher_1", "name": "Aoede", "rolle": "Moderatorin", "voice_id": "Aoede"},
                        {"id": "sprecher_2", "name": "Dr. Petra Lange", "rolle": "Arbeitspsychologin", "voice_id": "Kore"}
                    ],
                    "script_segments": [
                        {
                            "sprecher_id": "sprecher_1",
                            "text": "Herzlich willkommen zu unserem heutigen Gespräch. Ich begrüße Dr. Petra Lange, Arbeitspsychologin an der Universität Mannheim. Frau Dr. Lange, Homeoffice hat in den letzten Jahren massiv zugenommen. Ist das eine positive Entwicklung?"
                        },
                        {
                            "sprecher_id": "sprecher_2",
                            "text": "Guten Tag. Das ist in der Tat eine komplexe Frage. Aus psychologischer Sicht hat das Homeoffice sowohl Vor- als auch Nachteile. Studien zeigen, dass viele Beschäftigte im Homeoffice konzentrierter arbeiten können, weil sie weniger durch Lärm und Kollegen abgelenkt werden. Gleichzeitig beobachten wir aber auch eine Zunahme von Einsamkeitsgefühlen und eine Verwischung der Grenzen zwischen Arbeit und Privatleben."
                        },
                        {
                            "sprecher_id": "sprecher_1",
                            "text": "Welche Berufsgruppen profitieren besonders vom Homeoffice, und für wen ist es eher ungeeignet?"
                        },
                        {
                            "sprecher_id": "sprecher_2",
                            "text": "Wissensarbeiter — also Programmierer, Analysten, Autoren oder Berater — können im Homeoffice oft sehr effizient arbeiten. Für Berufe, die direkte physische Präsenz erfordern, wie Handwerker, Ärzte oder Lehrkräfte, ist es natürlich keine Option. Interessant ist aber, dass auch viele Führungskräfte Schwierigkeiten mit dem Remote-Führen haben, weil sie gelernt haben, Anwesenheit mit Leistung gleichzusetzen."
                        },
                        {
                            "sprecher_id": "sprecher_1",
                            "text": "Sie erwähnten die Verwischung von Arbeit und Freizeit. Wie können Arbeitnehmer damit umgehen?"
                        },
                        {
                            "sprecher_id": "sprecher_2",
                            "text": "Das wichtigste ist die Schaffung klarer Rituale und Strukturen. Feste Arbeitszeiten, ein separater Arbeitsbereich zu Hause und bewusste Abendrituale, die den Übergang in die Freizeit markieren, sind sehr hilfreich. Außerdem empfehle ich, das Arbeitsmaterial am Ende des Tages wegzuräumen oder zumindest den Bildschirm zu deaktivieren, sodass man nicht ständig daran erinnert wird, dass noch Arbeit wartet."
                        },
                        {
                            "sprecher_id": "sprecher_1",
                            "text": "Was sind Ihre Beobachtungen zur sozialen Isolation im Homeoffice?"
                        },
                        {
                            "sprecher_id": "sprecher_2",
                            "text": "Das ist tatsächlich eines der größten Probleme. Menschen sind soziale Wesen, und der informelle Austausch am Arbeitsplatz ist wichtiger, als viele denken. Er fördert nicht nur das Wohlbefinden, sondern auch die Kreativität und die Identifikation mit dem Unternehmen. Unternehmen, die komplett auf Homeoffice setzen, sollten daher regelmäßige Präsenztreffen organisieren, auch wenn sie nicht zwingend arbeitsbezogen sein müssen."
                        },
                        {
                            "sprecher_id": "sprecher_1",
                            "text": "Wie sehen Sie die Zukunft des Arbeitens? Wird das Büro überflüssig?"
                        },
                        {
                            "sprecher_id": "sprecher_2",
                            "text": "Nein, das glaube ich nicht. Ich prognostiziere, dass sich hybride Arbeitsmodelle langfristig durchsetzen werden. Also Modelle, bei denen Mitarbeiter einen Teil ihrer Zeit im Büro und einen Teil zu Hause oder anderswo arbeiten. Das Büro wird sich dabei verändern — es wird weniger ein Ort für konzentriertes Einzelarbeiten sein, sondern stärker ein Ort für Kollaboration, Kreativität und Gemeinschaft."
                        },
                        {
                            "sprecher_id": "sprecher_1",
                            "text": "Haben Unternehmen eine Verpflichtung, ihre Mitarbeiter beim Homeoffice zu unterstützen?"
                        },
                        {
                            "sprecher_id": "sprecher_2",
                            "text": "Auf jeden Fall. Arbeitgeber haben eine Fürsorgepflicht gegenüber ihren Mitarbeitern, die auch im Homeoffice gilt. Das bedeutet, dass sie für eine angemessene technische Ausstattung sorgen, Weiterbildungsangebote bereitstellen und aktiv gegen soziale Isolation vorgehen sollten. Unternehmen, die das ignorieren, riskieren langfristig einen Rückgang des Engagements und eine höhere Fluktuation."
                        },
                        {
                            "sprecher_id": "sprecher_1",
                            "text": "Frau Dr. Lange, vielen Dank für das aufschlussreiche Gespräch."
                        },
                        {
                            "sprecher_id": "sprecher_2",
                            "text": "Danke für die Einladung. Ich hoffe, dass meine Ausführungen hilfreich waren."
                        }
                    ],
                    "questions": [
                        {
                            "question_num": 6,
                            "question_text": "Laut Dr. Lange können Mitarbeiter im Homeoffice konzentrierter arbeiten.",
                            "question_type": "richtig_falsch",
                            "correct_answer": "richtig"
                        },
                        {
                            "question_num": 7,
                            "question_text": "Dr. Lange sagt, dass Homeoffice für Ärzte besonders gut geeignet ist.",
                            "question_type": "richtig_falsch",
                            "correct_answer": "falsch"
                        },
                        {
                            "question_num": 8,
                            "question_text": "Viele Führungskräfte haben laut Dr. Lange Schwierigkeiten mit dem Remote-Führen.",
                            "question_type": "richtig_falsch",
                            "correct_answer": "richtig"
                        },
                        {
                            "question_num": 9,
                            "question_text": "Dr. Lange empfiehlt, am Abend den Bildschirm zu deaktivieren.",
                            "question_type": "richtig_falsch",
                            "correct_answer": "richtig"
                        },
                        {
                            "question_num": 10,
                            "question_text": "Laut Dr. Lange schadet informeller Austausch der Kreativität.",
                            "question_type": "richtig_falsch",
                            "correct_answer": "falsch"
                        },
                        {
                            "question_num": 11,
                            "question_text": "Dr. Lange glaubt, dass das Büro in Zukunft überflüssig werden wird.",
                            "question_type": "richtig_falsch",
                            "correct_answer": "falsch"
                        },
                        {
                            "question_num": 12,
                            "question_text": "Dr. Lange prognostiziert, dass hybride Arbeitsmodelle sich langfristig durchsetzen werden.",
                            "question_type": "richtig_falsch",
                            "correct_answer": "richtig"
                        },
                        {
                            "question_num": 13,
                            "question_text": "Das Büro der Zukunft soll laut Dr. Lange hauptsächlich für konzentriertes Einzelarbeiten genutzt werden.",
                            "question_type": "richtig_falsch",
                            "correct_answer": "falsch"
                        },
                        {
                            "question_num": 14,
                            "question_text": "Unternehmen haben laut Dr. Lange eine Fürsorgepflicht gegenüber Homeoffice-Mitarbeitern.",
                            "question_type": "richtig_falsch",
                            "correct_answer": "richtig"
                        },
                        {
                            "question_num": 15,
                            "question_text": "Dr. Lange sagt, dass Unternehmen ohne Homeoffice-Unterstützung eine höhere Mitarbeiterfluktuation riskieren.",
                            "question_type": "richtig_falsch",
                            "correct_answer": "richtig"
                        }
                    ]
                },
                {
                    "aufgabe_num": 3,
                    "typ": "ansagen",
                    "heard_times": 2,
                    "preparation_seconds": 30,
                    "instructions": "Sie hören fünf kurze Ansagen aus dem Radio oder aus einem Unternehmen. Sie hören jede Ansage zweimal. Entscheiden Sie, ob die Aussage richtig oder falsch ist.",
                    "ansagen": [
                        {
                            "id": 1,
                            "voice_id": "Fenrir",
                            "register": "radio",
                            "script": "Hier ist eine Mitteilung der Bundesagentur für Arbeit: Ab dem ersten Januar des nächsten Jahres erhalten Arbeitnehmer, die im Homeoffice arbeiten, einen steuerlichen Freibetrag von bis zu sechshundert Euro jährlich für die Einrichtung ihres Heimarbeitsplatzes. Voraussetzung ist, dass der Heimarbeitsplatz ausschließlich beruflich genutzt wird und mindestens zwanzig Stunden pro Woche dort gearbeitet wird. Weitere Informationen finden Sie auf der Website der Bundesagentur für Arbeit.",
                            "questions": [
                                {
                                    "question_num": 16,
                                    "question_text": "Der steuerliche Freibetrag gilt auch für Heimarbeitsplätze, die privat genutzt werden.",
                                    "question_type": "richtig_falsch",
                                    "correct_answer": "falsch"
                                }
                            ]
                        },
                        {
                            "id": 2,
                            "voice_id": "Kore",
                            "register": "company",
                            "script": "Sehr geehrte Mitarbeiterinnen und Mitarbeiter, wir möchten Sie darüber informieren, dass ab nächstem Monat alle Abteilungen auf das neue digitale Zeiterfassungssystem umgestellt werden. Bitte melden Sie sich bis Freitag bei Ihrer direkten Führungskraft an, um Ihre Zugangsdaten zu erhalten. Das alte System wird zum Ende des Monats abgeschaltet. Bei technischen Fragen steht Ihnen unser IT-Helpdesk unter der internen Rufnummer 4499 zur Verfügung.",
                            "questions": [
                                {
                                    "question_num": 17,
                                    "question_text": "Mitarbeiter sollen sich bei der IT-Abteilung anmelden, um Zugangsdaten zu erhalten.",
                                    "question_type": "richtig_falsch",
                                    "correct_answer": "falsch"
                                }
                            ]
                        },
                        {
                            "id": 3,
                            "voice_id": "Fenrir",
                            "register": "radio",
                            "script": "Radio Wirtschaft berichtet: Eine neue Studie der Universität Köln zeigt, dass Unternehmen, die flexible Arbeitsmodelle anbieten, im Durchschnitt dreißig Prozent weniger Personalfluktuation verzeichnen als Unternehmen mit starren Büropflichten. Besonders bei Fachkräften zwischen dreißig und fünfzig Jahren sei die Möglichkeit zum Homeoffice ein entscheidendes Kriterium bei der Jobwahl. Die Studie befragte über zweitausend Arbeitnehmer in ganz Deutschland.",
                            "questions": [
                                {
                                    "question_num": 18,
                                    "question_text": "Laut der Studie haben Unternehmen mit flexiblen Arbeitsmodellen weniger Personalfluktuation.",
                                    "question_type": "richtig_falsch",
                                    "correct_answer": "richtig"
                                }
                            ]
                        },
                        {
                            "id": 4,
                            "voice_id": "Kore",
                            "register": "company",
                            "script": "Liebe Kolleginnen und Kollegen, wir laden Sie herzlich zu unserem nächsten digitalen Team-Event ein. Am Donnerstagabend, dem zwölften Mai, findet ab neunzehn Uhr unser virtuelles Sommer-Quiz statt. Die Teilnahme ist freiwillig, aber wir freuen uns über möglichst viele Mitmacher. Den Zugangslink erhalten Sie am Mittwoch per E-Mail. Wir freuen uns auf einen spaßigen Abend mit Ihnen!",
                            "questions": [
                                {
                                    "question_num": 19,
                                    "question_text": "Die Teilnahme am virtuellen Team-Event ist für alle Mitarbeiter verpflichtend.",
                                    "question_type": "richtig_falsch",
                                    "correct_answer": "falsch"
                                }
                            ]
                        },
                        {
                            "id": 5,
                            "voice_id": "Fenrir",
                            "register": "radio",
                            "script": "Meldung aus der Wirtschaft: Der Deutsche Gewerkschaftsbund hat heute einen Gesetzentwurf vorgestellt, der Arbeitnehmern in Deutschland ein Recht auf mindestens zwei Homeoffice-Tage pro Woche einräumen soll, sofern die Tätigkeit dies erlaubt. Arbeitgeberverbände reagierten mit Kritik und betonten, dass ein solches Gesetz die unternehmerische Flexibilität einschränken würde. Das Bundesarbeitsministerium erklärte, den Vorschlag prüfen zu wollen.",
                            "questions": [
                                {
                                    "question_num": 20,
                                    "question_text": "Der DGB-Gesetzentwurf sieht mindestens zwei Homeoffice-Tage pro Woche für alle Arbeitnehmer vor.",
                                    "question_type": "richtig_falsch",
                                    "correct_answer": "richtig"
                                }
                            ]
                        }
                    ]
                }
            ]
        },
        "sprachbausteine": {
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "lueckentext_mc",
                    "instructions": "Lesen Sie den folgenden Brief und wählen Sie für jede Lücke die richtige Antwort a, b oder c.",
                    "text_title": "Antrag auf Homeoffice-Regelung",
                    "text_parts": [
                        "Sehr geehrte Frau Becker,",
                        "ich {21} mich an Sie wenden, um eine Homeoffice-Regelung für meine Tätigkeit zu beantragen. Wie Sie wissen, {22} ich meine Aufgaben seit Beginn der Pandemie größtenteils digital {23}. In dieser Zeit habe ich festgestellt, dass ich zu Hause konzentrierter arbeiten {24}, da ich weniger durch Bürolärm abgelenkt {25}.",
                        "Ich {26} vorschlagen, dass ich künftig an drei Tagen pro Woche von zu Hause aus arbeite. {27} würden mir Pendelzeiten von insgesamt zwei Stunden täglich erspart bleiben, die ich stattdessen in zusätzliche Arbeitsleistung investieren {28}. Die {29} Kommunikation mit dem Team könnte weiterhin über die bestehenden digitalen Kanäle {30}.",
                        "Ich stehe gerne für ein persönliches Gespräch zur Verfügung und freue mich auf Ihre positive Rückmeldung.",
                        "Mit freundlichen Grüßen,\nMarkus Hoffmann"
                    ],
                    "gaps": [
                        {
                            "gap_num": 21,
                            "options": ["a) möchte", "b) müsste", "c) dürfte"],
                            "correct_answer": "a"
                        },
                        {
                            "gap_num": 22,
                            "options": ["a) erledige", "b) habe", "c) bin"],
                            "correct_answer": "a"
                        },
                        {
                            "gap_num": 23,
                            "options": ["a) ausgeführt", "b) erledigt", "c) gemacht"],
                            "correct_answer": "b"
                        },
                        {
                            "gap_num": 24,
                            "options": ["a) kann", "b) könnte", "c) soll"],
                            "correct_answer": "b"
                        },
                        {
                            "gap_num": 25,
                            "options": ["a) werde", "b) bin", "c) wurde"],
                            "correct_answer": "a"
                        },
                        {
                            "gap_num": 26,
                            "options": ["a) will", "b) würde", "c) sollte"],
                            "correct_answer": "b"
                        },
                        {
                            "gap_num": 27,
                            "options": ["a) Daher", "b) Damit", "c) Dadurch"],
                            "correct_answer": "c"
                        },
                        {
                            "gap_num": 28,
                            "options": ["a) könnte", "b) kann", "c) werde"],
                            "correct_answer": "a"
                        },
                        {
                            "gap_num": 29,
                            "options": ["a) erforderliche", "b) nötigste", "c) tägliche"],
                            "correct_answer": "a"
                        },
                        {
                            "gap_num": 30,
                            "options": ["a) stattfinden", "b) erfolgen", "c) ablaufen"],
                            "correct_answer": "b"
                        }
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "lueckentext_wortbank",
                    "instructions": "Lesen Sie den folgenden Text und füllen Sie die Lücken mit den passenden Wörtern aus der Wortbank. Fünf Wörter passen nicht.",
                    "text_title": "Digitalisierung im Unternehmen — Unser Weg in die Zukunft",
                    "text_parts": [
                        "Liebe Mitarbeiterinnen und Mitarbeiter,",
                        "in den vergangenen Jahren hat die Digitalisierung unsere Arbeitswelt {31} verändert. Als Unternehmen {32} wir auf diese Entwicklung reagieren, um wettbewerbsfähig zu bleiben. Daher haben wir in den letzten Monaten verschiedene Maßnahmen {33}, die unseren Arbeitsalltag {34} und gleichzeitig unsere {35} steigern sollen.",
                        "Ein zentrales Projekt ist die {36} eines neuen digitalen Kommunikationssystems, das alle Abteilungen miteinander {37}. Dieses System ermöglicht es, Informationen in {38} zu teilen und gemeinsam an Dokumenten zu {39}. Zudem werden wir in den nächsten Monaten Schulungen {40}, damit alle Mitarbeiter die neuen Tools optimal nutzen können.",
                        "Wir sind überzeugt, dass diese Investitionen in die Digitalisierung langfristig allen zugutekommen werden.",
                        "Mit freundlichen Grüßen,\nDie Geschäftsführung"
                    ],
                    "wortbank": [
                        {"id": "a", "word": "GRUNDLEGEND"},
                        {"id": "b", "word": "MÜSSEN"},
                        {"id": "c", "word": "EINGEFÜHRT"},
                        {"id": "d", "word": "VEREINFACHEN"},
                        {"id": "e", "word": "EFFIZIENZ"},
                        {"id": "f", "word": "EINFÜHRUNG"},
                        {"id": "g", "word": "VERBINDET"},
                        {"id": "h", "word": "ECHTZEIT"},
                        {"id": "i", "word": "ARBEITEN"},
                        {"id": "j", "word": "ANBIETEN"},
                        {"id": "k", "word": "ERHEBLICH"},
                        {"id": "l", "word": "SOLLTEN"},
                        {"id": "m", "word": "BESCHLOSSEN"},
                        {"id": "n", "word": "ERLEICHTERN"},
                        {"id": "o", "word": "KOSTEN"}
                    ],
                    "gaps": [
                        {"gap_num": 31, "correct_answer": "a"},
                        {"gap_num": 32, "correct_answer": "b"},
                        {"gap_num": 33, "correct_answer": "c"},
                        {"gap_num": 34, "correct_answer": "d"},
                        {"gap_num": 35, "correct_answer": "e"},
                        {"gap_num": 36, "correct_answer": "f"},
                        {"gap_num": 37, "correct_answer": "g"},
                        {"gap_num": 38, "correct_answer": "h"},
                        {"gap_num": 39, "correct_answer": "i"},
                        {"gap_num": 40, "correct_answer": "j"}
                    ]
                }
            ]
        },
        "schreiben": {
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "erörterung",
                    "instructions": "Schreiben Sie einen zusammenhängenden Text (ca. 200 Wörter) zu folgendem Thema. Nennen Sie Argumente für und gegen die These und formulieren Sie Ihre eigene Meinung.",
                    "thema": "Sollte Homeoffice für alle Arbeitnehmer ein gesetzliches Recht sein?",
                    "leitfragen": [
                        "Welche Vorteile hätte ein gesetzliches Recht auf Homeoffice?",
                        "Welche Nachteile oder Probleme könnten entstehen?",
                        "Was ist Ihre persönliche Meinung zu diesem Thema?"
                    ],
                    "example_answer": "Die Frage, ob Homeoffice ein gesetzliches Recht werden sollte, wird in Deutschland kontrovers diskutiert. Einerseits gibt es überzeugende Argumente dafür: Arbeitnehmer könnten ihre Zeit flexibler einteilen, Pendelwege entfallen und die Vereinbarkeit von Familie und Beruf würde erleichtert. Zudem haben Studien gezeigt, dass viele Menschen zu Hause produktiver arbeiten.\n\nAndererseits gibt es gewichtige Gegenargumente. Nicht alle Berufe sind für Homeoffice geeignet — Handwerker, Pflegepersonal oder Kassierer können ihre Arbeit nicht von zu Hause aus erledigen. Ein gesetzliches Recht würde hier zu Ungleichheiten führen. Außerdem haben viele Arbeitnehmer keine geeigneten Wohnverhältnisse für ein Homeoffice, und soziale Isolation kann ein ernsthaftes Problem sein.\n\nIch persönlich bin der Meinung, dass ein gesetzliches Recht auf Homeoffice grundsätzlich sinnvoll wäre, allerdings mit Ausnahmen für Berufe, die physische Präsenz erfordern. Wichtig wäre auch, dass Arbeitgeber verpflichtet werden, die notwendige Ausstattung bereitzustellen. Ein flexibles Hybridmodell, bei dem Mitarbeiter selbst entscheiden können, wann sie ins Büro kommen, scheint mir der ideale Kompromiss zu sein.",
                    "min_words": 180,
                    "max_words": 250
                }
            ]
        },
        "sprechen": {
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "titel": "Gespräch über Erfahrungen und Meinungen",
                    "instructions": "Sprechen Sie mit Ihrem Gesprächspartner über die folgenden Fragen zum Thema Arbeit und Homeoffice.",
                    "fragen": [
                        {"frage_num": 1, "frage_text": "Haben Sie Erfahrungen mit Homeoffice oder kennen Sie jemanden, der im Homeoffice arbeitet? Was sind Ihre Eindrücke?", "needs_audio": True},
                        {"frage_num": 2, "frage_text": "Welche Eigenschaften sind Ihrer Meinung nach wichtig, um erfolgreich im Homeoffice zu arbeiten?", "needs_audio": True}
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "titel": "Diskussion über ein aktuelles Thema",
                    "instructions": "Diskutieren Sie mit Ihrem Gesprächspartner das folgende Thema. Bringen Sie Argumente vor und reagieren Sie auf die Argumente Ihres Partners.",
                    "thema": "Homeoffice verändert die Unternehmenskultur — zum Guten oder zum Schlechten?",
                    "fragen": [
                        {"frage_num": 3, "frage_text": "Welche Aspekte der Unternehmenskultur leiden Ihrer Meinung nach unter dem Homeoffice am stärksten?", "needs_audio": True},
                        {"frage_num": 4, "frage_text": "Wie können Unternehmen eine starke Teamkultur aufrechterhalten, wenn viele Mitarbeiter remote arbeiten?", "needs_audio": True},
                        {"frage_num": 5, "frage_text": "Denken Sie, dass neue Mitarbeiter genauso gut ins Unternehmen integriert werden können, wenn sie hauptsächlich im Homeoffice arbeiten?", "needs_audio": True}
                    ]
                },
                {
                    "aufgabe_num": 3,
                    "titel": "Problemlösung und gemeinsame Entscheidung",
                    "instructions": "Sie und Ihr Gesprächspartner sollen gemeinsam eine Lösung für das folgende Problem entwickeln.",
                    "aufgabe": "Ihr Unternehmen möchte eine neue Homeoffice-Richtlinie einführen. Es gibt verschiedene Meinungen: Manche Führungskräfte wollen alle Mitarbeiter mindestens vier Tage pro Woche im Büro sehen, während viele Mitarbeiter lieber vollständig remote arbeiten möchten. Entwickeln Sie gemeinsam einen Kompromissvorschlag.",
                    "fragen": [
                        {"frage_num": 6, "frage_text": "Was würden Sie als faire Regelung für alle Beteiligten vorschlagen? Welche Mindestanforderungen an Büropräsenz wären sinnvoll?", "needs_audio": True},
                        {"frage_num": 7, "frage_text": "Wie würden Sie sicherstellen, dass die neue Regelung sowohl die Bedürfnisse der Mitarbeiter als auch die Anforderungen der Führungskräfte berücksichtigt?", "needs_audio": True}
                    ]
                }
            ]
        }
    }


# ── TELC B2 Seed 003 — Klimawandel und gesellschaftliche Verantwortung ────────
def get_telc_b2_seed_003():
    return {
        "exam_id": "exam_telc_b2_003",
        "title": "TELC Deutsch B2 - Übungstest 3",
        "exam_type": "telc",
        "telc_level": "B2",
        "pathway": "telc_b2",
        "status": "pending_audio",
        "audio_progress": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "lesen": {
            "total_questions": 20,
            "duration_minutes": 90,
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "zuordnung",
                    "title": "Aufgabe 1 – Texte und Überschriften",
                    "instruction": "Lesen Sie die Texte A–E und ordnen Sie ihnen die passenden Überschriften zu. Zwei Überschriften passen nicht.",
                    "short_texts": [
                        {"id": "A", "text": "Die Energiewende in Deutschland schreitet voran, doch das Tempo bleibt umstritten. Während erneuerbare Energien im Jahr 2023 bereits mehr als 50 Prozent des Stroms lieferten, kämpft das Land mit dem Ausbau der Übertragungsnetze. Ohne leistungsfähige Leitungen, die Windstrom aus dem Norden in den Süden transportieren, bleiben selbst neue Windparks wirtschaftlich ineffizient. Netzausbau und Erzeugungskapazität müssen synchronisiert werden."},
                        {"id": "B", "text": "Plastik in den Weltmeeren bedroht nicht nur Meerestiere, sondern gelangt über Mikroplastik in die menschliche Nahrungskette. Forscher haben winzige Plastikpartikel in Trinkwasser, Meeresfrüchten und sogar in menschlichem Blut nachgewiesen. Die Ursache liegt zu einem großen Teil in unsachgemäß entsorgten Einwegprodukten. Ohne wirksame internationale Abkommen zur Reduktion von Plastikmüll ist eine Trendumkehr kaum denkbar."},
                        {"id": "C", "text": "Städte wie Wien, Kopenhagen und Singapur gelten als Vorreiter urbaner Klimaanpassung. Sie investieren in begrünte Dächer, Schwammstädte und Frischluftschneisen, um Hitzewellen und Starkregen abzupuffern. Diese Maßnahmen kosten erhebliche Summen, rechnen sich aber langfristig, wenn man die vermiedenen Schäden durch Überschwemmungen und Hitzetote einrechnet. Andere Kommunen beobachten die Konzepte und beginnen schrittweise mit der Umsetzung."},
                        {"id": "D", "text": "Die Agrarwirtschaft steht vor einem Dilemma: Einerseits trägt sie mit Methanemissionen aus der Tierhaltung und Lachgasfreisetzung aus überdüngten Böden erheblich zum Klimawandel bei. Andererseits sind Bauern selbst die ersten Betroffenen von Dürren, Spätfrösten und Extremwettereignissen. Eine nachhaltige Transformation der Landwirtschaft erfordert finanzielle Anreize, Wissenstransfer und eine gesellschaftliche Neubewertung von Lebensmittelpreisen."},
                        {"id": "E", "text": "Carbonpreise und Emissionshandelssysteme gelten als effizienteste Instrumente, um klimaschädliche Aktivitäten zu verteuern und saubere Alternativen wettbewerbsfähig zu machen. Das EU-Emissionshandelssystem (ETS) erfasst inzwischen Industrie, Energie und Luftfahrt. Kritiker bemängeln jedoch, dass die Preise lange zu niedrig lagen, um wirkliche Verhaltensänderungen auszulösen. Seit 2022 sind die Preise deutlich gestiegen, was erste Verlagerungseffekte in Nicht-EU-Länder befürchten lässt."}
                    ],
                    "ueberschriften": [
                        {"id": "a", "text": "Warum Landwirte gleichzeitig Verursacher und Opfer des Klimawandels sind"},
                        {"id": "b", "text": "Wie Städte sich gegen extreme Wetterereignisse wappnen"},
                        {"id": "c", "text": "Der Preis für Kohlenstoff und seine wirtschaftlichen Auswirkungen"},
                        {"id": "d", "text": "Netzinfrastruktur als Engpass der Energiewende"},
                        {"id": "e", "text": "Warum Elektroautos allein den Verkehr nicht retten"},
                        {"id": "f", "text": "Mikroplastik als unsichtbare Bedrohung für Mensch und Tier"},
                        {"id": "g", "text": "Neue Anbaumethoden als Antwort auf den Klimawandel"},
                        {"id": "h", "text": "Internationaler Vergleich von Klimaschutzgesetzen"},
                        {"id": "i", "text": "Bürgerproteste als Motor der Klimapolitik"},
                        {"id": "j", "text": "Wie Schwammstädte und grüne Dächer Extremwetter abmildern"}
                    ],
                    "questions": [
                        {"question_num": 1, "question_text": "Text A", "correct_answer": "d"},
                        {"question_num": 2, "question_text": "Text B", "correct_answer": "f"},
                        {"question_num": 3, "question_text": "Text C", "correct_answer": "b"},
                        {"question_num": 4, "question_text": "Text D", "correct_answer": "a"},
                        {"question_num": 5, "question_text": "Text E", "correct_answer": "c"}
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "multiple_choice",
                    "title": "Aufgabe 2 – Einen Text verstehen",
                    "instruction": "Lesen Sie den Text und wählen Sie die richtige Antwort (a, b oder c).",
                    "text": "Die Deutsche Energiewende: Zwischen Anspruch und Wirklichkeit\n\nAls Deutschland 2011 beschloss, bis 2022 alle Kernkraftwerke abzuschalten und gleichzeitig den CO₂-Ausstoß drastisch zu reduzieren, wurde das als historisches Experiment gefeiert. Mehr als ein Jahrzehnt später zeigt sich, dass der Weg steiniger ist als erwartet – aber auch erfolgreicher in mancher Hinsicht.\n\nAuf der Habenseite steht der massive Ausbau erneuerbarer Energien: Wind- und Solarkraft deckten 2023 knapp 59 Prozent des Bruttostromverbrauchs. Gleichzeitig sanken die Kosten für Photovoltaik seit 2010 um über 90 Prozent, was neue Geschäftsmodelle wie Mieterstromprojekte und Energiegemeinschaften ermöglicht. Hunderttausende Bürgerinnen und Bürger sind inzwischen selbst Energieerzeuger.\n\nAuf der Schuldenseite stehen strukturelle Schwächen. Der Netzausbau hinkt der Erzeugungskapazität hinterher: Während im windreichen Norden zeitweise überschüssiger Strom ins Ausland verschenkt wird, müssen im Süden konventionelle Kraftwerke weiterlaufen, weil die Leitungen fehlen. Die Netzentgelte – also der Anteil der Stromrechnung, der für Infrastruktur bezahlt wird – stiegen 2024 auf ein Rekordniveau und belasten besonders Haushalte mit geringem Einkommen.\n\nEin weiteres Strukturproblem ist der Rückstand beim Wärmesektor. Heizungen, Warmwasser und Industrieöfen verbrauchen in Deutschland mehr Energie als der gesamte Stromsektor. Die Wärmewende – also die Umstellung auf Wärmepumpen, Fernwärme und solare Thermie – kommt deutlich langsamer voran als die Stromwende, unter anderem weil Sanierungsmaßnahmen teuer und bürokratisch aufwändig sind.\n\nDennoch: Im internationalen Vergleich gilt Deutschland trotz aller Widersprüche als Referenzmodell. Kein anderes großes Industrieland hat bisher eine vergleichbare Transformation gewagt. Die entscheidende Frage ist, ob der politische Wille ausreicht, die Lücken zu schließen – Netze ausbauen, Wärme dekarbonisieren, soziale Ausgleichsmechanismen stärken –, bevor die Klimaziele 2030 unwiederbringlich verfehlt werden.",
                    "questions": [
                        {"question_num": 6, "question_text": "Was wird auf der Habenseite der Energiewende genannt?", "options": ["a) Der vollständige Ausstieg aus fossilen Brennstoffen", "b) Der starke Ausbau von Wind- und Solarenergie", "c) Der pünktliche Abschluss des Netzausbaus"], "correct_answer": "b"},
                        {"question_num": 7, "question_text": "Warum wird im Norden Deutschlands manchmal Strom verschenkt?", "options": ["a) Weil der Strombedarf im Norden sehr gering ist", "b) Weil es zu wenig Windkraftanlagen gibt", "c) Weil fehlende Leitungen den Transport in den Süden verhindern"], "correct_answer": "c"},
                        {"question_num": 8, "question_text": "Was versteht der Text unter der 'Wärmewende'?", "options": ["a) Die Umstellung des Heizsektors auf erneuerbare Energien", "b) Die Erhöhung der Raumtemperatur in öffentlichen Gebäuden", "c) Die Verbesserung des Wetters durch weniger CO₂"], "correct_answer": "a"},
                        {"question_num": 9, "question_text": "Welche Haltung nimmt der Text zur Energiewende insgesamt ein?", "options": ["a) Rein kritisch — die Energiewende ist gescheitert", "b) Rein optimistisch — die Energiewende ist ein voller Erfolg", "c) Differenziert — Fortschritte und Probleme werden gleichzeitig anerkannt"], "correct_answer": "c"},
                        {"question_num": 10, "question_text": "Was ist laut Text die entscheidende Voraussetzung, um die Klimaziele 2030 zu erreichen?", "options": ["a) Mehr Kernkraftwerke bauen", "b) Netzausbau, Wärmedekarbonisierung und sozialen Ausgleich beschleunigen", "c) Den Stromverbrauch in Privathaushalten halbieren"], "correct_answer": "b"}
                    ]
                },
                {
                    "aufgabe_num": 3,
                    "typ": "anzeigen",
                    "title": "Aufgabe 3 – Anzeigen verstehen",
                    "instruction": "Lesen Sie die Situationen 11–20 und die Anzeigen a–l. Welche Anzeige passt zu welcher Situation? Zwei Anzeigen passen zu keiner Situation (x).",
                    "anzeigen": [
                        {"id": "a", "text": "Klimavolontariat – 6 Monate in Costa Rica: Mitarbeit an Wiederaufforstungsprojekten im Regenwald. Vorkenntnisse in Biologie oder Forstwirtschaft erwünscht, aber nicht Pflicht. Unterkunft inklusive. Mindestalter 20 Jahre."},
                        {"id": "b", "text": "Fernkurs: Energieberatung für Wohngebäude (IHK-zertifiziert). 12 Monate, vollständig online. Ideal für Handwerker, Architekten und Ingenieure, die Kunden beim energetischen Sanieren beraten möchten."},
                        {"id": "c", "text": "Stellenangebot: Klimaschutzmanager/in für mittelständisches Produktionsunternehmen (Vollzeit, Stuttgart). Aufgabe: Emissionsbilanzierung, Maßnahmenplanung, Reporting an Geschäftsleitung."},
                        {"id": "d", "text": "Bürgerwindpark Nordfriesland sucht Mitglieder: Investieren Sie ab 1.000 Euro in lokale Windenergie und erhalten Sie jährliche Ausschüttungen. Informationsabend am 15. Mai, Gemeindehaus Husum."},
                        {"id": "e", "text": "Forschungsstipendium Klimaökonomie: Die Heinrich-Böll-Stiftung vergibt 5 Promotionsstipendien für Projekte an der Schnittstelle von Klima- und Wirtschaftswissenschaften. Bewerbung bis 30. September."},
                        {"id": "f", "text": "Nachhilfekurs Deutsch B1/B2 für Umweltberufe: Fachsprachliche Kommunikation in Umweltbehörden, NGOs und Nachhaltigkeitsabteilungen. Online, Di/Do 18–20 Uhr."},
                        {"id": "g", "text": "Solar-Contracting für Mehrfamilienhäuser: Wir installieren eine Photovoltaikanlage auf Ihrem Dach ohne Investitionskosten. Mieter und Eigentümer profitieren vom Mieterstrom. Kostenlose Beratung: 0800 123 456."},
                        {"id": "h", "text": "Ehrenamtliche Klimabildung: Werden Sie Klimabotschafter/in und besuchen Sie Schulen in Ihrer Region. Schulungen erfolgen durch die Deutsche Klimastiftung. Zeitaufwand: ca. 4 Std./Monat."},
                        {"id": "i", "text": "Masterarbeit-Förderung Erneuerbare Energien: Hannover Rück und TU Hamburg vergeben gemeinsam drei Förderungen à 3.000 Euro für Abschlussarbeiten zu Versicherungslösungen für Klimarisiken. Nur für Masterstudierende."},
                        {"id": "j", "text": "Wohngemeinschaft sucht umweltbewusste Mitbewohner/in (80 m², 3 Zimmer, Passivhaus, Leipzig). Gemeinschaftsgarten, Lastenrad vorhanden. Miete 420 Euro warm."},
                        {"id": "k", "text": "Grüne Berufsausbildung: Ausbildung zum/zur Anlagenmechaniker/in mit Spezialisierung auf Wärmepumpen und Solarthermie. Betrieb in Hamburg, Ausbildungsdauer 3 Jahre. Bewerbung bis 1. März."},
                        {"id": "l", "text": "Workshop 'Klimakommunikation': Wie erkläre ich Klimawandel verständlich? Für Lehrer, Journalisten und NGO-Mitarbeitende. Samstag, 9–17 Uhr, München. Teilnahme kostenlos, Anmeldung erforderlich."}
                    ],
                    "questions": [
                        {"question_num": 11, "situation": "Frau Schulz ist Architektin und möchte sich auf die energetische Sanierung von Gebäuden spezialisieren, ohne ihren Job aufzugeben.", "question_text": "Welche Anzeige passt?", "correct_answer": "b"},
                        {"question_num": 12, "situation": "Herr Okafor hat einen Abschluss in Wirtschaftswissenschaften und möchte promovieren. Er interessiert sich für die ökonomischen Aspekte des Klimaschutzes.", "question_text": "Welche Anzeige passt?", "correct_answer": "e"},
                        {"question_num": 13, "situation": "Eine Wohnungseigentümerin möchte Solarstrom für ihre Mieter bereitstellen, hat aber kein Kapital für eine PV-Anlage.", "question_text": "Welche Anzeige passt?", "correct_answer": "g"},
                        {"question_num": 14, "situation": "Ein Lehrer möchte lernen, wie er das Thema Klimawandel für Schüler anschaulich erklären kann.", "question_text": "Welche Anzeige passt?", "correct_answer": "l"},
                        {"question_num": 15, "situation": "Herr Bauer ist Masterstudent Ingenieurwesen und sucht eine finanzielle Unterstützung für seine Abschlussarbeit über klimabezogene Versicherungsprodukte.", "question_text": "Welche Anzeige passt?", "correct_answer": "i"},
                        {"question_num": 16, "situation": "Frau Meier ist Betriebswirtin (35) und sucht eine Vollzeitstelle, bei der sie die Klimabilanz eines Unternehmens verbessern kann.", "question_text": "Welche Anzeige passt?", "correct_answer": "c"},
                        {"question_num": 17, "situation": "Ein junger Schulabgänger möchte eine handwerkliche Ausbildung machen und sich gleichzeitig auf Heiztechnik mit erneuerbaren Energien spezialisieren.", "question_text": "Welche Anzeige passt?", "correct_answer": "k"},
                        {"question_num": 18, "situation": "Frau Tanaka sucht eine Wohnung in einer umweltbewussten Wohngemeinschaft und möchte wenig Miete zahlen.", "question_text": "Welche Anzeige passt?", "correct_answer": "j"},
                        {"question_num": 19, "situation": "Eine Privatperson möchte 2.000 Euro in regionale erneuerbare Energie investieren und an Renditen beteiligt werden.", "question_text": "Welche Anzeige passt?", "correct_answer": "d"},
                        {"question_num": 20, "situation": "Herr Diallo ist Biologiestudent und möchte mehrere Monate im Ausland an einem ökologischen Projekt mitarbeiten.", "question_text": "Welche Anzeige passt?", "correct_answer": "a"}
                    ]
                }
            ]
        },
        "hoeren": {
            "total_questions": 20,
            "duration_minutes": 40,
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "kurzgespraeche",
                    "title": "Aufgabe 1 – Kurze Texte",
                    "instruction": "Sie hören fünf kurze Texte. Jeder Sprecher äußert sich zum Thema Klimaschutz. Sind die Aussagen richtig oder falsch? Sie hören jeden Text einmal.",
                    "heard_times": 1,
                    "preparation_seconds": 30,
                    "topic": "Klimaschutz — persönliche Verantwortung oder Aufgabe des Staates?",
                    "conversations": [
                        {
                            "conv_num": 1,
                            "sprecher": [{"name": "Frau Dr. Hoffmann", "voice_id": "Aoede"}],
                            "script_segments": [
                                {"sprecher": "Frau Dr. Hoffmann", "text": "[nachdenklich] Ich forsche seit zwanzig Jahren zu Klimapolitik, und eine Frage kehrt immer wieder: Wer ist eigentlich verantwortlich? Aus meiner Perspektive ist individuelles Verhalten zwar wichtig, aber ohne strukturelle Rahmenbedingungen verpufft es wirkungslos. Man kann nicht erwarten, dass Menschen freiwillig auf ihr Auto verzichten, solange der öffentliche Nahverkehr in ländlichen Regionen praktisch nicht existiert. Der Staat muss Alternativen schaffen, bevor er Verbote ausspricht. Klimaschutz durch schlechtes Gewissen funktioniert nicht dauerhaft."}
                            ],
                            "questions": [{"question_num": 1, "question_type": "richtig_falsch", "question_text": "Frau Dr. Hoffmann glaubt, dass individuelle Maßnahmen ohne staatliche Rahmenbedingungen ausreichen.", "correct_answer": "Falsch"}]
                        },
                        {
                            "conv_num": 2,
                            "sprecher": [{"name": "Herr Mayer", "voice_id": "Charon"}],
                            "script_segments": [
                                {"sprecher": "Herr Mayer", "text": "[entschlossen] Ich habe letztes Jahr mein Leben umgestellt: kein Fleisch mehr, kein Flugzeug, Solaranlage auf dem Dach. Natürlich weiß ich, dass mein Fußabdruck allein nicht die Welt rettet. Aber ich weigere mich, auf Veränderungen der Politik zu warten, während die Welt brennt. Jeder Mensch der aufhört, kurzfristig nach Thailand zu fliegen und stattdessen mit dem Zug in die Alpen fährt, sendet ein Signal – an die Industrie, an Politiker und an Freunde. Wandel beginnt immer im Kleinen."}
                            ],
                            "questions": [{"question_num": 2, "question_type": "richtig_falsch", "question_text": "Herr Mayer hat seinen Lebensstil aus Überzeugung geändert, obwohl er die globale Wirkung für begrenzt hält.", "correct_answer": "Richtig"}]
                        },
                        {
                            "conv_num": 3,
                            "sprecher": [{"name": "eine Studentin", "voice_id": "Kore"}],
                            "script_segments": [
                                {"sprecher": "eine Studentin", "text": "[kritisch] Ich studiere Politikwissenschaft und bin ehrlich gesagt frustriert. Auf der einen Seite werden wir jungen Menschen moralisch unter Druck gesetzt, unseren Konsum zu reduzieren. Auf der anderen Seite subventioniert der Staat weiterhin fossile Brennstoffe mit Milliarden. Das ist Heuchelei. Wenn ich meinen Fleischkonsum reduziere, spart das Gramme an CO₂, während ein einziger Kurzstreckenflug eines Managers das Vielfache emittiert. Ich halte die Individualisierung von Klimaschutz für eine Ablenkungsstrategie der Industrie."}
                            ],
                            "questions": [{"question_num": 3, "question_type": "richtig_falsch", "question_text": "Die Studentin findet es gerecht, dass Einzelpersonen stärker in der Pflicht stehen als Unternehmen.", "correct_answer": "Falsch"}]
                        },
                        {
                            "conv_num": 4,
                            "sprecher": [{"name": "Herr Prof. Bauer", "voice_id": "Orus"}],
                            "script_segments": [
                                {"sprecher": "Herr Prof. Bauer", "text": "[sachlich] Als Ökonom sehe ich das pragmatisch. Der effizienteste Hebel für Klimaschutz ist ein ausreichend hoher CO₂-Preis. Wenn Emissionen einen realen Preis tragen, verändert sich das Verhalten von Unternehmen und Konsumenten automatisch – ohne dass der Staat jede Entscheidung regulieren muss. Das setzt Innovationsanreize frei und lässt den Markt die kostengünstigsten Lösungen finden. Das Aufkommen aus dem Emissionshandel sollte gleichmäßig an alle Bürger zurückverteilt werden, um soziale Ungerechtigkeit zu vermeiden."}
                            ],
                            "questions": [{"question_num": 4, "question_type": "richtig_falsch", "question_text": "Prof. Bauer befürwortet eine direkte staatliche Regulierung aller Klimaentscheidungen.", "correct_answer": "Falsch"}]
                        },
                        {
                            "conv_num": 5,
                            "sprecher": [{"name": "Frau Keller", "voice_id": "Aoede"}],
                            "script_segments": [
                                {"sprecher": "Frau Keller", "text": "[besorgt] Ich bin Rentnerin und lebe auf dem Land. Für mich ist Klimaschutz kein abstraktes Thema mehr. Letzten Sommer hat der Starkregen unsere Keller überschwemmt und die Ernte meines Nachbarn vernichtet. Ich versuche, sparsam zu leben, aber ich bin auf mein Auto angewiesen, weil der Bus hier nur zweimal am Tag fährt. Was mich ärgert: Die Maßnahmen, die diskutiert werden, treffen immer Menschen wie mich – die wenig verdienen und keine Wahl haben. Klimagerechtigkeit muss mitgedacht werden."}
                            ],
                            "questions": [{"question_num": 5, "question_type": "richtig_falsch", "question_text": "Frau Keller lebt in der Stadt und nutzt öffentliche Verkehrsmittel.", "correct_answer": "Falsch"}]
                        }
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "gespraech",
                    "title": "Aufgabe 2 – Ein Interview",
                    "instruction": "Sie hören ein Radiointerview. Sind die Aussagen richtig oder falsch? Sie hören das Interview zweimal.",
                    "heard_times": 2,
                    "preparation_seconds": 60,
                    "sprecher": [
                        {"name": "Moderator", "voice_id": "Charon"},
                        {"name": "Prof. Dr. Anna Brenner", "voice_id": "Kore"}
                    ],
                    "script_segments": [
                        {"sprecher": "Moderator", "text": "[professionell] Herzlich willkommen zu 'Zukunft jetzt'. Heute sprechen wir über die Energiewende mit Prof. Dr. Anna Brenner von der Universität Freiburg. Frau Professor, ist die Energiewende auf Kurs?"},
                        {"sprecher": "Prof. Dr. Anna Brenner", "text": "[differenziert] Kommt darauf an, wie man Kurs definiert. Im Stromsektor sind wir gut vorangekommen — fast 60 Prozent erneuerbare Energie ist bemerkenswert. Aber Strom ist nur ein Drittel unseres Energiesystems. Im Wärme- und Verkehrssektor hinken wir massiv hinterher."},
                        {"sprecher": "Moderator", "text": "Was sind die größten Hindernisse?"},
                        {"sprecher": "Prof. Dr. Anna Brenner", "text": "Drei Faktoren. Erstens: der Netzausbau. Wir bauen Windkraft schneller als Leitungen. Das führt paradoxerweise dazu, dass wir Windstrom abregeln und gleichzeitig Kohlekraftwerke laufen lassen. Zweitens: der Sanierungsstau bei Gebäuden. Zwei Drittel der deutschen Gebäude wurden vor 1979 gebaut und haben kaum Dämmung. Die Sanierungsquote liegt bei unter einem Prozent pro Jahr — wir müssten auf drei Prozent kommen. Drittens: die mangelnde Akzeptanz. Neue Stromleitungen und Windparks werden vor Ort oft abgelehnt."},
                        {"sprecher": "Moderator", "text": "Wie könnte man die Sanierungsquote erhöhen?"},
                        {"sprecher": "Prof. Dr. Anna Brenner", "text": "[überlegend] Das ist ein Verteilungskonflikt. Viele Hausbesitzer können sich Sanierungen nicht leisten oder wollen den Aufwand scheuen. Steuerliche Anreize helfen denjenigen, die ohnehin Steuern zahlen — also eher wohlhabenderen Eigentümern. Für Geringverdiener brauchen wir zinsgünstige Kredite und staatliche Zuschüsse, die unbürokratisch zugänglich sind."},
                        {"sprecher": "Moderator", "text": "Kommen wir zur Akzeptanz. Warum ist sie so gering?"},
                        {"sprecher": "Prof. Dr. Anna Brenner", "text": "Lokale Betroffenheit ist real. Wenn ein Windpark sechzig Meter von der Wohnbebauung entfernt gebaut wird, dann ist der Widerstand verständlich. Meine Forschung zeigt allerdings, dass Akzeptanz deutlich steigt, wenn Anwohner finanziell beteiligt werden — sei es durch Bürgerenergieprojekte oder kommunale Gewinnbeteiligungen. Wer profitiert, akzeptiert eher."},
                        {"sprecher": "Moderator", "text": "Deutschland hat 2023 die letzten Kernkraftwerke abgeschaltet. War das richtig?"},
                        {"sprecher": "Prof. Dr. Anna Brenner", "text": "[bedächtig] Aus strikt klimawissenschaftlicher Sicht wäre es sinnvoller gewesen, Kernkraft als Brückentechnologie zu behalten, solange Kohlekraftwerke noch laufen. Gleichzeitig verstehe ich die gesellschaftliche Entscheidung nach Fukushima. Der Atomkonsens war demokratisch legitimiert, und ihn jetzt rückgängig zu machen wäre politisch kaum durchsetzbar. Ich plädiere nicht für den Wiedereinstieg, aber für Ehrlichkeit: Dieser Ausstieg hat uns CO₂-Ziele gekostet."},
                        {"sprecher": "Moderator", "text": "Was erwarten Sie von der Politik bis 2030?"},
                        {"sprecher": "Prof. Dr. Anna Brenner", "text": "Mehr Tempo beim Netzausbau, eine Wärmepumpenoffensive mit sozialer Abfederung und verbindliche CO₂-Preise, die auch wirklich wehtun. Und Ehrlichkeit gegenüber der Bevölkerung: Die Transformation ist teuer, aber das Nichthandeln wäre noch teurer. Jeder Euro, den wir heute in Klimaschutz investieren, spart laut Schätzungen drei bis sieben Euro an Klimaschäden in der Zukunft."},
                        {"sprecher": "Moderator", "text": "Frau Professor Brenner, vielen Dank für dieses Gespräch."},
                        {"sprecher": "Prof. Dr. Anna Brenner", "text": "Gern geschehen."}
                    ],
                    "questions": [
                        {"question_num": 6, "question_type": "richtig_falsch", "question_text": "Prof. Brenner ist der Ansicht, dass die Energiewende im Stromsektor gut vorankommt.", "correct_answer": "Richtig"},
                        {"question_num": 7, "question_type": "richtig_falsch", "question_text": "Laut Prof. Brenner ist der Wärmesektor weiter fortgeschritten als der Stromsektor.", "correct_answer": "Falsch"},
                        {"question_num": 8, "question_type": "richtig_falsch", "question_text": "Der Netzausbau hält mit dem Ausbau erneuerbarer Energien Schritt.", "correct_answer": "Falsch"},
                        {"question_num": 9, "question_type": "richtig_falsch", "question_text": "Prof. Brenner hält steuerliche Anreize für das beste Mittel, um auch einkommensschwache Hausbesitzer zur Sanierung zu bewegen.", "correct_answer": "Falsch"},
                        {"question_num": 10, "question_type": "richtig_falsch", "question_text": "Laut Forschung von Prof. Brenner steigt die Akzeptanz von Windparks, wenn Anwohner finanziell beteiligt werden.", "correct_answer": "Richtig"},
                        {"question_num": 11, "question_type": "richtig_falsch", "question_text": "Prof. Brenner befürwortet einen Wiedereinstieg in die Kernkraft.", "correct_answer": "Falsch"},
                        {"question_num": 12, "question_type": "richtig_falsch", "question_text": "Sie hält den Atomausstieg für klimapolitisch optimal.", "correct_answer": "Falsch"},
                        {"question_num": 13, "question_type": "richtig_falsch", "question_text": "Die aktuelle Sanierungsquote bei Gebäuden liegt laut Prof. Brenner bei unter einem Prozent.", "correct_answer": "Richtig"},
                        {"question_num": 14, "question_type": "richtig_falsch", "question_text": "Prof. Brenner ist der Meinung, dass Klimaschutzinvestitionen heute langfristig Geld sparen.", "correct_answer": "Richtig"},
                        {"question_num": 15, "question_type": "richtig_falsch", "question_text": "Die Professorin fordert niedrigere CO₂-Preise, um die Wirtschaft zu entlasten.", "correct_answer": "Falsch"}
                    ]
                },
                {
                    "aufgabe_num": 3,
                    "typ": "ansagen",
                    "title": "Aufgabe 3 – Kurze Radiotexte",
                    "instruction": "Sie hören fünf kurze Radiotexte. Sind die Aussagen richtig oder falsch? Sie hören jeden Text zweimal.",
                    "heard_times": 2,
                    "preparation_seconds": 30,
                    "ansagen": [
                        {
                            "ansage_num": 1, "sprecher": "Moderatorin", "voice_id": "Kore",
                            "text": "[sachlich] Nachrichten aus Brüssel: Das Europäische Parlament hat heute mit deutlicher Mehrheit verschärfte CO₂-Grenzwerte für Neuwagen ab 2030 beschlossen. Ab diesem Jahr dürfen neu zugelassene Pkw im Flottendurchschnitt maximal 50 Gramm CO₂ pro Kilometer ausstoßen — das entspricht einer Halbierung gegenüber den heutigen Werten. Automobilverbände kritisieren die Frist als zu kurz, Umweltorganisationen fordern einen vollständigen Verbrennerausstieg.",
                            "question_num": 16, "question_type": "richtig_falsch", "question_text": "Die neuen CO₂-Grenzwerte für Neuwagen sehen eine Halbierung der Emissionen bis 2030 vor.", "correct_answer": "Richtig"
                        },
                        {
                            "ansage_num": 2, "sprecher": "Ansager", "voice_id": "Fenrir",
                            "text": "Meldung aus der Forschung: Ein internationales Wissenschaftlerteam hat im Fachjournal 'Nature' eine neue Methode zur CO₂-Speicherung vorgestellt. Das Verfahren bindet Kohlendioxid aus der Atmosphäre in mineralischen Gesteinsformationen — dauerhaft und ohne Leckagerisiko, so die Forscher. Die Technologie befindet sich jedoch noch im Pilotmaßstab und ist derzeit rund dreimal teurer als der aktuelle EU-Emissionshandelspreis.",
                            "question_num": 17, "question_type": "richtig_falsch", "question_text": "Die neue CO₂-Speichermethode ist bereits kosteneffizient genug für den Massenmarkt.", "correct_answer": "Falsch"
                        },
                        {
                            "ansage_num": 3, "sprecher": "Moderatorin", "voice_id": "Kore",
                            "text": "[informativ] Verkehrsmeldung für den Klimastreik morgen in Berlin: Die Polizei rechnet mit bis zu 80.000 Teilnehmenden. Die Route führt vom Hauptbahnhof über die Invalidenstraße zum Brandenburger Tor. Mehrere S- und U-Bahnlinien werden ab 13 Uhr eingeschränkt. Reisende werden gebeten, frühere Verbindungen zu nutzen oder auf Umsteigemöglichkeiten auszuweichen.",
                            "question_num": 18, "question_type": "richtig_falsch", "question_text": "Der Klimastreik in Berlin endet am Hauptbahnhof.", "correct_answer": "Falsch"
                        },
                        {
                            "ansage_num": 4, "sprecher": "Ansager", "voice_id": "Fenrir",
                            "text": "Wirtschaftsnachrichten: Der Bundesverband der deutschen Industrie warnt vor Wettbewerbsnachteilen durch einseitige europäische Klimapolitik. Wenn außereuropäische Konkurrenten keine vergleichbaren CO₂-Kosten tragen müssten, drohten Produktionsverlagerungen. Der Verband fordert deshalb eine Verlängerung kostenloser Emissionszertifikate für energieintensive Branchen bis mindestens 2035 sowie rasche Einigung auf einen EU-weiten CO₂-Grenzausgleichsmechanismus.",
                            "question_num": 19, "question_type": "richtig_falsch", "question_text": "Der Industrieverband unterstützt eine sofortige vollständige Abschaffung kostenloser Emissionszertifikate.", "correct_answer": "Falsch"
                        },
                        {
                            "ansage_num": 5, "sprecher": "Moderatorin", "voice_id": "Kore",
                            "text": "[warm] Gute Nachrichten aus der Natur: Das Bundesamt für Naturschutz meldet, dass die Bestände des Weißstorchs in Deutschland erstmals seit Jahrzehnten wieder zugenommen haben. Rund 10.000 Brutpaare wurden 2023 gezählt — ein Anstieg von 15 Prozent gegenüber dem Vorjahr. Experten führen die Erholung auf mildere Winter und erfolgreiche Renaturierungsprojekte in der Elbaue und am Oberrhein zurück.",
                            "question_num": 20, "question_type": "richtig_falsch", "question_text": "Die Zunahme der Weißstorch-Population wird unter anderem auf Renaturierungsprojekte zurückgeführt.", "correct_answer": "Richtig"
                        }
                    ]
                }
            ]
        },
        "sprachbausteine": {
            "total_questions": 20,
            "duration_minutes": 30,
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "lueckentext_mc",
                    "title": "Aufgabe 1 – Lückentext",
                    "instruction": "Lesen Sie den Text und wählen Sie das passende Wort (a, b oder c).",
                    "text_with_gaps": "Sehr geehrte Damen und Herren des Stadtrats,\n\nals Vertreterin der Bürgerinitiative 'Klimagerechtes Neustadt' {21} ich mich mit einem dringenden Anliegen an Sie. Unsere Stadt {22} erheblichen Nachholbedarf beim Ausbau der Fahrradinfrastruktur. {23} in anderen deutschen Städten längst komfortable Radschnellwege entstanden sind, {24} unsere Innenstadt nach wie vor von mehrspurigen Autostraßen geprägt.\n\nWir {25}, dass ein sicheres und zusammenhängendes Radwegenetz nicht nur den Klimaschutzzielen der Stadt dienen {26}, sondern auch die Lebensqualität für alle Einwohner verbessern {27}. Untersuchungen belegen, dass Städte, {28} stark in Radinfrastruktur investiert haben, deutlich niedrigere Unfallzahlen im Straßenverkehr {29}.\n\nWir bitten Sie daher, im kommenden Haushaltsplan mindestens drei Millionen Euro für den Radwegeausbau einzustellen. Sollte dies aus finanziellen Gründen nicht sofort möglich sein, {30} wir zumindest die Einrichtung temporärer Radwege durch Ummarkierung bestehender Fahrspuren als Sofortmaßnahme.",
                    "options": [
                        {"question_num": 21, "a": "wende", "b": "weise", "c": "zeige", "correct_answer": "a"},
                        {"question_num": 22, "a": "besitzt", "b": "verfügt", "c": "hat", "correct_answer": "c"},
                        {"question_num": 23, "a": "Obwohl", "b": "Während", "c": "Weil", "correct_answer": "b"},
                        {"question_num": 24, "a": "bleibt", "b": "ist", "c": "wird", "correct_answer": "b"},
                        {"question_num": 25, "a": "behaupten", "b": "sind überzeugt", "c": "beweisen", "correct_answer": "b"},
                        {"question_num": 26, "a": "würde", "b": "wird", "c": "soll", "correct_answer": "a"},
                        {"question_num": 27, "a": "kann", "b": "würde", "c": "dürfte", "correct_answer": "b"},
                        {"question_num": 28, "a": "die", "b": "welche", "c": "wo", "correct_answer": "a"},
                        {"question_num": 29, "a": "aufweisen", "b": "haben", "c": "zeigen", "correct_answer": "a"},
                        {"question_num": 30, "a": "fordern", "b": "beantragen", "c": "ersuchen", "correct_answer": "a"}
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "lueckentext_wortbank",
                    "title": "Aufgabe 2 – Wortschatz",
                    "instruction": "Lesen Sie den Text. Welches Wort (a–o) passt in welche Lücke? Fünf Wörter passen nicht.",
                    "text_with_gaps": "Klimapolitik zwischen Ambition und Realität\n\nDie internationale Klimapolitik steckt in einem {31}: Einerseits drängt die Wissenschaft auf rasches Handeln, andererseits bremsen nationale Eigeninteressen und wirtschaftliche Bedenken den Fortschritt. Seit dem Pariser {32} von 2015 haben sich über 190 Staaten verpflichtet, die Erderwärmung auf deutlich unter zwei Grad zu begrenzen. Doch die {33} zwischen Versprechen und tatsächlichen Emissionsreduktionen bleibt besorgniserregend groß.\n\nExperten betonen, dass technologische Lösungen allein nicht ausreichen. Nötig sei ein tiefgreifender gesellschaftlicher {34}, der Konsum- und Produktionsmuster grundlegend verändere. Besonders kritisch ist die Lage in {35} Ländern, die noch mitten in ihrer Industrialisierung stecken und zu Recht darauf hinweisen, dass reiche Industriestaaten ihren {36} durch fossile Brennstoffe historisch aufgebaut haben.\n\nEin {37} Instrument ist der CO₂-Preis: Er setzt wirtschaftliche Anreize, ohne direkte Verbote auszusprechen. Allerdings ist seine Wirkung begrenzt, wenn er zu niedrig angesetzt wird oder wenn er nicht international {38} wird. Länder, die einseitig hohe Klimastandards einführen, riskieren andernfalls, Industrien ins Ausland zu {39}.\n\nLetztlich gilt: Der {40} liegt nicht in einem einzigen Instrument, sondern in einem kohärenten Mix aus Regulierung, Preissignalen, Investitionen und gesellschaftlichem Wandel.",
                    "wortbank": [
                        {"id": "a", "word": "DILEMMA"},
                        {"id": "b", "word": "ABKOMMEN"},
                        {"id": "c", "word": "LÜCKE"},
                        {"id": "d", "word": "WANDEL"},
                        {"id": "e", "word": "AUFSTREBENDEN"},
                        {"id": "f", "word": "WOHLSTAND"},
                        {"id": "g", "word": "BEWÄHRTES"},
                        {"id": "h", "word": "KOORDINIERT"},
                        {"id": "i", "word": "VERLAGERN"},
                        {"id": "j", "word": "SCHLÜSSEL"},
                        {"id": "k", "word": "KONFLIKT"},
                        {"id": "l", "word": "VERTRAG"},
                        {"id": "m", "word": "DIFFERENZ"},
                        {"id": "n", "word": "WACHSTUM"},
                        {"id": "o", "word": "EFFIZIENZ"}
                    ],
                    "options": [
                        {"question_num": 31, "correct_answer": "a"},
                        {"question_num": 32, "correct_answer": "b"},
                        {"question_num": 33, "correct_answer": "c"},
                        {"question_num": 34, "correct_answer": "d"},
                        {"question_num": 35, "correct_answer": "e"},
                        {"question_num": 36, "correct_answer": "f"},
                        {"question_num": 37, "correct_answer": "g"},
                        {"question_num": 38, "correct_answer": "h"},
                        {"question_num": 39, "correct_answer": "i"},
                        {"question_num": 40, "correct_answer": "j"}
                    ]
                }
            ]
        },
        "schreiben": {
            "total_time_minutes": 45,
            "aufgaben": [{
                "aufgabe_num": 1,
                "aufgabe_typ": "erorterung",
                "aufgabe": "In Deutschland wird diskutiert, ob Inlandsflüge verboten werden sollten, um CO₂-Emissionen zu senken. Alternativen wie der Ausbau des Hochgeschwindigkeitszugs werden als Ersatz vorgeschlagen.\n\nSchreiben Sie einen argumentativen Aufsatz zu diesem Thema. Berücksichtigen Sie dabei:\n• Argumente für ein Verbot von Inlandsflügen\n• Argumente dagegen\n• Ihre eigene Position mit Begründung\n\nSchreiben Sie ca. 200 Wörter.",
                "min_words": 180,
                "max_words": 250
            }]
        },
        "sprechen": {
            "total_time_minutes": 15,
            "teile": [
                {
                    "teil_num": 1,
                    "titel": "Persönliche Einstellung zum Klimaschutz",
                    "instructions": "Beschreiben Sie Ihre persönliche Haltung zum Thema Klimaschutz.",
                    "fragen": [
                        {"frage_num": 1, "frage_text": "[professionell] Herzlich willkommen. Bitte stellen Sie sich kurz vor und erzählen Sie mir, wie wichtig Ihnen Klimaschutz persönlich ist.", "needs_audio": True},
                        {"frage_num": 2, "frage_text": "Welche konkreten Maßnahmen haben Sie in Ihrem Alltag umgesetzt, um Ihren CO₂-Fußabdruck zu reduzieren?", "needs_audio": True},
                        {"frage_num": 3, "frage_text": "Glauben Sie, dass individuelle Maßnahmen wirklich einen Unterschied machen, oder liegt die Verantwortung hauptsächlich bei Staat und Wirtschaft?", "needs_audio": True}
                    ]
                },
                {
                    "teil_num": 2,
                    "titel": "Diskussion: Staatliche Verantwortung vs. individuelle Freiheit",
                    "instructions": "Diskutieren Sie das folgende Thema mit dem Prüfer.",
                    "fragen": [
                        {"frage_num": 4, "frage_text": "Manche sagen, der Staat soll Bürger durch Verbote und Steuern zu klimafreundlichem Verhalten zwingen. Andere finden das einen Eingriff in die persönliche Freiheit. Wie sehen Sie das?", "needs_audio": True},
                        {"frage_num": 5, "frage_text": "Würden Sie persönlich ein Verbot von Kurzstreckenflügen unterstützen, wenn es dafür günstigere und schnellere Zugverbindungen gäbe?", "needs_audio": True},
                        {"frage_num": 6, "frage_text": "Wie könnte man sicherstellen, dass Klimaschutzmaßnahmen sozial gerecht sind und einkommensschwache Haushalte nicht unverhältnismäßig stark belasten?", "needs_audio": True}
                    ]
                },
                {
                    "teil_num": 3,
                    "titel": "Gemeinsam einen Stadtplan entwickeln",
                    "instructions": "Entwickeln Sie gemeinsam mit dem Prüfer einen Klimaschutzplan für eine mittelgroße Stadt.",
                    "aufgabe": "Eine Stadt mit 150.000 Einwohnern möchte bis 2035 klimaneutral werden. Diskutieren Sie, welche drei Maßnahmen die höchste Priorität haben sollten und warum.",
                    "fragen": [
                        {"frage_num": 7, "frage_text": "Welche drei Klimaschutzmaßnahmen würden Sie für eine Stadt dieser Größe priorisieren — zum Beispiel im Bereich Verkehr, Energie oder Gebäude?", "needs_audio": True},
                        {"frage_num": 8, "frage_text": "Wie würden Sie mit Widerstand aus der Bevölkerung umgehen, wenn bestimmte Maßnahmen — wie eine City-Maut oder Parkgebührenerhöhungen — unpopulär sind?", "needs_audio": True}
                    ]
                }
            ]
        }
    }


# ── TELC B2 Seed 004 — Soziale Medien und digitale Gesellschaft ───────────────
def get_telc_b2_seed_004():
    return {
        "exam_id": "exam_telc_b2_004",
        "title": "TELC Deutsch B2 - Übungstest 4",
        "exam_type": "telc",
        "telc_level": "B2",
        "pathway": "telc_b2",
        "status": "pending_audio",
        "audio_progress": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "lesen": {
            "total_questions": 20,
            "duration_minutes": 90,
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "zuordnung",
                    "title": "Aufgabe 1 – Texte und Überschriften",
                    "instruction": "Lesen Sie die Texte A–E und ordnen Sie ihnen die passenden Überschriften zu. Zwei Überschriften passen nicht.",
                    "short_texts": [
                        {"id": "A", "text": "Plattformen wie TikTok, Instagram und YouTube nutzen Algorithmen, die Nutzern immer weitere Inhalte vorschlagen, die zu ihren bisherigen Vorlieben passen. Dieses Prinzip steigert die Verweildauer enorm, kann jedoch zu sogenannten Filterblasen führen: Nutzer sehen vorwiegend Meinungen und Informationen, die ihre eigene Weltsicht bestätigen, während abweichende Perspektiven weitgehend ausgeblendet werden."},
                        {"id": "B", "text": "Die Verbreitung von Falschinformationen über soziale Netzwerke hat in den letzten Jahren neue Dimensionen erreicht. Studien zeigen, dass unwahre Nachrichten auf Twitter/X sechsmal schneller geteilt werden als korrekte Meldungen. Emotionaler Inhalt, insbesondere Empörung und Angst, befördert die Weiterleitung — unabhängig vom Wahrheitsgehalt. Faktenchecks kommen oft zu spät, um der initialen Verbreitung entgegenzuwirken."},
                        {"id": "C", "text": "Für Unternehmen sind soziale Medien zum unverzichtbaren Kommunikationskanal geworden. Influencer-Marketing, gezielte Werbeanzeigen und Community-Management ermöglichen eine direkte Ansprache der Zielgruppe — oft effizienter und kostengünstiger als klassische Medien. Gleichzeitig birgt die Öffentlichkeit sozialer Plattformen erhebliche Risiken: Ein viraler Fehler oder ein Shitstorm kann den Ruf eines Unternehmens innerhalb von Stunden nachhaltig schädigen."},
                        {"id": "D", "text": "Psychologische Studien belegen einen Zusammenhang zwischen intensiver Social-Media-Nutzung und einem erhöhten Risiko für Depressionen und Angststörungen, besonders bei Jugendlichen. Der ständige Vergleich mit idealisierten Selbstdarstellungen anderer, Cybermobbing und die Angst, etwas zu verpassen (FOMO), belasten das psychische Wohlbefinden. Ob diese Korrelation kausal ist, bleibt wissenschaftlich umstritten."},
                        {"id": "E", "text": "Immer mehr Länder diskutieren oder beschließen Regulierungen für digitale Plattformen. Die EU hat mit dem Digital Services Act (DSA) einen ambitionierten Rahmen geschaffen, der Plattformen zu mehr Transparenz, Rechenschaftspflicht und Bekämpfung illegaler Inhalte verpflichtet. Kritiker befürchten eine Beschränkung der Meinungsfreiheit; Befürworter sehen darin überfälligen Verbraucherschutz im digitalen Raum."}
                    ],
                    "ueberschriften": [
                        {"id": "a", "text": "Warum emotionale Lügen sich schneller verbreiten als sachliche Wahrheiten"},
                        {"id": "b", "text": "Wie Empfehlungsalgorithmen Meinungsvielfalt einschränken können"},
                        {"id": "c", "text": "Soziale Medien als zweischneidiges Schwert für Unternehmen"},
                        {"id": "d", "text": "Regulierung digitaler Plattformen: zwischen Schutz und Zensur"},
                        {"id": "e", "text": "Psychische Gesundheit im Zeitalter der Selbstoptimierung online"},
                        {"id": "f", "text": "Wie Influencer die Kaufentscheidungen junger Menschen beeinflussen"},
                        {"id": "g", "text": "Warum Datenschutz im digitalen Zeitalter schwer durchzusetzen ist"},
                        {"id": "h", "text": "KI-generierte Inhalte als neue Bedrohung für die Glaubwürdigkeit"},
                        {"id": "i", "text": "Die wirtschaftliche Macht der sozialen Netzwerke"},
                        {"id": "j", "text": "Digitale Entgiftung als Trend gegen übermäßigen Medienkonsum"}
                    ],
                    "questions": [
                        {"question_num": 1, "question_text": "Text A", "correct_answer": "b"},
                        {"question_num": 2, "question_text": "Text B", "correct_answer": "a"},
                        {"question_num": 3, "question_text": "Text C", "correct_answer": "c"},
                        {"question_num": 4, "question_text": "Text D", "correct_answer": "e"},
                        {"question_num": 5, "question_text": "Text E", "correct_answer": "d"}
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "multiple_choice",
                    "title": "Aufgabe 2 – Einen Text verstehen",
                    "instruction": "Lesen Sie den Text und wählen Sie die richtige Antwort (a, b oder c).",
                    "text": "Algorithmen und gesellschaftliche Meinungsbildung\n\nWie entscheiden Milliarden von Menschen täglich, was wahr ist, was relevant ist und welche politischen Positionen vertretbar sind? Zunehmend übernehmen Algorithmen diese Funktion — und das mit weitreichenden Konsequenzen für demokratische Gesellschaften.\n\nDie Logik sozialer Plattformen ist ökonomisch: Werbeumsätze steigen mit der Verweildauer der Nutzer, also werden Inhalte bevorzugt ausgespielt, die maximale Aufmerksamkeit erzeugen. Aufmerksamkeit wird am stärksten durch emotionale Erregung ausgelöst — Empörung, Angst, Bestätigung. Das Ergebnis: Algorithmen belohnen tendenziell Extreme, weil Extreme engagieren.\n\nDieses Prinzip verschärft gesellschaftliche Polarisierung. Zwei Nutzer, die denselben Suchbegriff eingeben, erhalten unterschiedliche Ergebnisse — gefiltert nach Standort, Gerät, Klickhistorie und sozialen Netzwerken. Innerhalb dieser personalisierten Informationsumgebungen — oft als Filterblasen bezeichnet — werden abweichende Meinungen immer seltener wahrgenommen. Das erschwert den gesellschaftlichen Diskurs, da eine gemeinsame Faktenbasis erodiert.\n\nBesonders problematisch ist die Intransparenz der Algorithmen. Welche Inhalte privilegiert werden und nach welchen Kriterien, bleibt meist ein Betriebsgeheimnis. Forscher haben gezeigt, dass auf YouTube der Algorithmus Nutzer schrittweise zu radikaleren Inhalten leitet — nicht aus ideologischen Gründen, sondern weil radikale Inhalte länger angeschaut werden.\n\nEine mögliche Gegenmaßnahme ist die algorithmische Pflicht zur Diversität: Plattformen könnten verpflichtet werden, neben inhaltlich bevorzugten Inhalten auch solche anzuzeigen, die anderen Perspektiven entsprechen. Dies hätte allerdings Einfluss auf Geschäftsmodelle und wirft Fragen zur Zensur auf. Die Debatte darüber, wer digitale Öffentlichkeit gestaltet — Konzerne, Staaten oder Nutzer selbst — ist eine der zentralen demokratischen Fragen unserer Zeit.",
                    "questions": [
                        {"question_num": 6, "question_text": "Warum werden emotionale Inhalte von Algorithmen bevorzugt ausgespielt?", "options": ["a) Weil emotionale Inhalte sachlich korrekter sind", "b) Weil sie die Verweildauer der Nutzer erhöhen", "c) Weil Plattformen gesellschaftliche Debatten fördern wollen"], "correct_answer": "b"},
                        {"question_num": 7, "question_text": "Was versteht der Text unter 'Filterblasen'?", "options": ["a) Technische Fehler bei der Inhaltsanzeige", "b) Personalisierte Informationsumgebungen, die andere Meinungen ausblenden", "c) Sicherheitsmechanismen gegen Falschinformationen"], "correct_answer": "b"},
                        {"question_num": 8, "question_text": "Warum leitet der YouTube-Algorithmus laut Forschern zu radikaleren Inhalten?", "options": ["a) Weil die Plattform bestimmte politische Inhalte fördert", "b) Weil radikalere Inhalte länger angeschaut werden", "c) Weil junge Nutzer diese Inhalte aktiv suchen"], "correct_answer": "b"},
                        {"question_num": 9, "question_text": "Welche Maßnahme schlägt der Text als mögliche Lösung vor?", "options": ["a) Ein vollständiges Verbot von Empfehlungsalgorithmen", "b) Eine gesetzliche Pflicht für Plattformen, diverse Perspektiven anzuzeigen", "c) Die Einführung staatlicher Zensurbehörden"], "correct_answer": "b"},
                        {"question_num": 10, "question_text": "Was ist laut Text das übergeordnete Problem, das der Artikel beschreibt?", "options": ["a) Übermäßige Nutzung sozialer Medien durch Jugendliche", "b) Der Einfluss von Algorithmen auf demokratische Meinungsbildung", "c) Die wirtschaftliche Dominanz amerikanischer Technologiekonzerne"], "correct_answer": "b"}
                    ]
                },
                {
                    "aufgabe_num": 3,
                    "typ": "anzeigen",
                    "title": "Aufgabe 3 – Anzeigen verstehen",
                    "instruction": "Lesen Sie die Situationen 11–20 und die Anzeigen a–l. Welche Anzeige passt zu welcher Situation? Zwei Anzeigen passen zu keiner Situation (x).",
                    "anzeigen": [
                        {"id": "a", "text": "Medienkompetenztraining für Lehrkräfte: Wie erkennen Schüler Falschinformationen? Zweitägiger Workshop mit praktischen Unterrichtseinheiten. Zertifikat der Kultusministerkonferenz. Kosten: 120 Euro."},
                        {"id": "b", "text": "Stellenangebot: Social-Media-Manager/in für gemeinnützige Organisation (Berlin, Teilzeit 20 Std/Woche). Aufgaben: Content-Erstellung, Community-Management, Analytics. Erfahrung mit Instagram und LinkedIn erforderlich."},
                        {"id": "c", "text": "Masterprogramm Digital Communication & Society (M.A., 4 Semester, Universität Münster). Schwerpunkte: Algorithmen, Öffentlichkeit, Medienrecht. Zulassungsvoraussetzung: Bachelor in Sozial- oder Kommunikationswissenschaften."},
                        {"id": "d", "text": "Coaching: Digitale Entgiftung — weniger Bildschirmzeit, mehr Lebensqualität. Einzelsitzungen und Gruppenangebote. Erstgespräch kostenlos. Für Privatpersonen und Unternehmen."},
                        {"id": "e", "text": "Datenschutzberatung für KMU: Wir helfen Ihrem Unternehmen, DSGVO-konform zu werden. Audit, Dokumentation, Schulung. Erstberatung kostenfrei. Bundesweit tätig."},
                        {"id": "f", "text": "Freie Stelle: Junior-Content-Creator für Lifestyle-Kanal auf YouTube (München). Videoproduktion und Schnitt, Trend-Recherche, Zusammenarbeit mit Influencern. Vorkenntnisse in Premiere Pro oder DaVinci Resolve erwünscht."},
                        {"id": "g", "text": "Seminar: Algorithmen verstehen ohne Programmierkenntnisse — für Journalisten, Politologen und Sozialwissenschaftler. 2 Tage, Hamburg. Anmeldung bis 10 Tage vor Beginn."},
                        {"id": "h", "text": "Eltern-Info-Abend: Soziale Medien und Jugendliche — Risiken, Chancen und Gesprächsstrategien. Kostenlos, Volksschule Augsburg, Donnerstag 19 Uhr. Kinder bitte zu Hause lassen."},
                        {"id": "i", "text": "Doktorandenstelle: Lehrstuhl für Kommunikationswissenschaft, Universität Leipzig sucht Doktorand/in für DFG-Projekt zu politischer Polarisierung in sozialen Netzwerken. Voraussetzung: sehr guter Masterabschluss in Kommunikations-, Sozial- oder Politikwissenschaften."},
                        {"id": "j", "text": "App-Entwicklung: Wir suchen UX-Designer (m/w/d) für ein Start-up im Bereich EdTech (Lernplattform). Erfahrung mit Figma oder Sketch. Remote-Option möglich. Gehalt: 50.000–65.000 Euro."},
                        {"id": "k", "text": "Weiterbildung: Strategische Unternehmenskommunikation in sozialen Medien (IHK-Zertifikat). Abends und am Wochenende, 6 Monate. Ideal für PR- und Marketingfachleute, die sich aktualisieren möchten."},
                        {"id": "l", "text": "Online-Kurs: Einführung in Datenjournalismus — von Excel bis Python. Für Journalisten und Kommunikationsprofis ohne Programmierkenntnisse. 8 Wochen, flexibel. Kosten: 299 Euro."}
                    ],
                    "questions": [
                        {"question_num": 11, "situation": "Herr Koch ist Lehrer und möchte seinen Schülern beibringen, wie sie Falschinformationen im Internet erkennen. Er sucht eine professionelle Weiterbildung.", "question_text": "Welche Anzeige passt?", "correct_answer": "a"},
                        {"question_num": 12, "situation": "Frau Ndiaye hat einen Masterabschluss in Sozialwissenschaften und möchte über politische Polarisierung in sozialen Medien promovieren.", "question_text": "Welche Anzeige passt?", "correct_answer": "i"},
                        {"question_num": 13, "situation": "Ein kleines Unternehmen möchte sicherstellen, dass es alle Datenschutzvorschriften einhält, und sucht externe Unterstützung.", "question_text": "Welche Anzeige passt?", "correct_answer": "e"},
                        {"question_num": 14, "situation": "Eine PR-Fachfrau mit fünf Jahren Berufserfahrung möchte ihre Kenntnisse im Bereich Social Media auf den neuesten Stand bringen — auch abends möglich.", "question_text": "Welche Anzeige passt?", "correct_answer": "k"},
                        {"question_num": 15, "situation": "Frau Schmidt ist Journalistin ohne Programmierkenntnisse und möchte lernen, wie Algorithmen in sozialen Netzwerken funktionieren.", "question_text": "Welche Anzeige passt?", "correct_answer": "g"},
                        {"question_num": 16, "situation": "Eine gemeinnützige Organisation sucht eine Teilzeitkraft für die Verwaltung ihrer Social-Media-Präsenz in Berlin.", "question_text": "Welche Anzeige passt?", "correct_answer": "b"},
                        {"question_num": 17, "situation": "Herr Bauer hat einen Bachelor in Kommunikationswissenschaften und möchte tiefer in Themen wie Medienrecht und Algorithmen einsteigen.", "question_text": "Welche Anzeige passt?", "correct_answer": "c"},
                        {"question_num": 18, "situation": "Eltern machen sich Sorgen um den übermäßigen Social-Media-Konsum ihrer Tochter (14) und suchen Beratung.", "question_text": "Welche Anzeige passt?", "correct_answer": "h"},
                        {"question_num": 19, "situation": "Ein junger Absolvent mit Erfahrung in der Videoproduktion sucht eine Stelle in der Kreativbranche im Raum München.", "question_text": "Welche Anzeige passt?", "correct_answer": "f"},
                        {"question_num": 20, "situation": "Eine Privatperson findet, dass sie zu viel Zeit mit dem Smartphone verbringt, und sucht professionelle Unterstützung dabei, weniger Bildschirmzeit zu haben.", "question_text": "Welche Anzeige passt?", "correct_answer": "d"}
                    ]
                }
            ]
        },
        "hoeren": {
            "total_questions": 20,
            "duration_minutes": 40,
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "kurzgespraeche",
                    "title": "Aufgabe 1 – Kurze Texte",
                    "instruction": "Sie hören fünf kurze Texte. Jeder Sprecher äußert sich zum Thema soziale Medien. Sind die Aussagen richtig oder falsch? Sie hören jeden Text einmal.",
                    "heard_times": 1,
                    "preparation_seconds": 30,
                    "topic": "Soziale Medien — Fluch oder Segen?",
                    "conversations": [
                        {
                            "conv_num": 1,
                            "sprecher": [{"name": "Frau Lange", "voice_id": "Aoede"}],
                            "script_segments": [
                                {"sprecher": "Frau Lange", "text": "[kritisch] Ich bin Ärztin und sehe in meiner Praxis täglich, was exzessive Social-Media-Nutzung mit jungen Menschen macht. Schlafstörungen, Angststörungen, ein verzerrtes Körperbild — das sind keine Einzelfälle mehr. Das Problem ist nicht die Technologie selbst, sondern das Geschäftsmodell: Plattformen sind darauf ausgelegt, süchtig zu machen. Sie optimieren auf maximale Aufmerksamkeit, nicht auf Wohlbefinden. Solange das nicht reguliert wird, werden wir weiterhin eine Generation mit psychischen Problemen aufwachsen sehen."}
                            ],
                            "questions": [{"question_num": 1, "question_type": "richtig_falsch", "question_text": "Frau Lange sieht die Technologie selbst als Hauptproblem der Social-Media-Nutzung.", "correct_answer": "Falsch"}]
                        },
                        {
                            "conv_num": 2,
                            "sprecher": [{"name": "Herr Schneider", "voice_id": "Charon"}],
                            "script_segments": [
                                {"sprecher": "Herr Schneider", "text": "[begeistert] Ich habe über Instagram mein Unternehmen aufgebaut. Als Fotograf ohne großes Marketingbudget wäre das in früheren Zeiten schlicht unmöglich gewesen. Soziale Medien demokratisieren Aufmerksamkeit — wer gute Arbeit macht, kann eine Reichweite aufbauen, die früher nur Großkonzernen vorbehalten war. Natürlich gibt es Schattenseiten, aber für Menschen wie mich sind Plattformen ein echter Aufstiegsmechanismus in einer Gesellschaft, in der Netzwerke und Herkunft sonst entscheiden."}
                            ],
                            "questions": [{"question_num": 2, "question_type": "richtig_falsch", "question_text": "Herr Schneider hat soziale Medien genutzt, um sein Geschäft ohne großes Budget aufzubauen.", "correct_answer": "Richtig"}]
                        },
                        {
                            "conv_num": 3,
                            "sprecher": [{"name": "eine Journalistin", "voice_id": "Kore"}],
                            "script_segments": [
                                {"sprecher": "eine Journalistin", "text": "[besorgt] Als Journalistin mache ich mir vor allem Sorgen um den Informationsraum. Wir haben früher angenommen, mehr Information bedeutet mehr Demokratie. Heute sehe ich, dass Menge nicht gleich Qualität ist. Soziale Plattformen belohnen Empörung, Einfachheit und Bestätigung — Journalismus braucht Zeit, Differenzierung und manchmal unangenehme Wahrheiten. Wir verlieren gerade die gemeinsame Faktenbasis, auf der Demokratie angewiesen ist. Das bereitet mir wirklich Sorgen."}
                            ],
                            "questions": [{"question_num": 3, "question_type": "richtig_falsch", "question_text": "Die Journalistin ist der Ansicht, dass mehr Informationen automatisch zu mehr Demokratie führen.", "correct_answer": "Falsch"}]
                        },
                        {
                            "conv_num": 4,
                            "sprecher": [{"name": "Herr Dr. Fischer", "voice_id": "Orus"}],
                            "script_segments": [
                                {"sprecher": "Herr Dr. Fischer", "text": "[ausgewogen] Ich forsche zu digitaler Kommunikation und versuche, weder Technikoptimist noch Kulturpessimist zu sein. Faktencheck: Es gibt keine eindeutige wissenschaftliche Einigkeit, dass soziale Medien per se psychische Erkrankungen verursachen. Korrelation ist nicht Kausalität. Was wir wissen: Die Nutzungsweise und der soziale Kontext sind entscheidend. Wer soziale Medien nutzt, um sich mit anderen zu verbinden und Interessen zu teilen, erlebt andere Wirkungen als jemand, der passiv scrollt und sich mit anderen vergleicht."}
                            ],
                            "questions": [{"question_num": 4, "question_type": "richtig_falsch", "question_text": "Herr Dr. Fischer hält die Nutzungsweise sozialer Medien für entscheidender als die Plattform selbst.", "correct_answer": "Richtig"}]
                        },
                        {
                            "conv_num": 5,
                            "sprecher": [{"name": "Frau Weber", "voice_id": "Aoede"}],
                            "script_segments": [
                                {"sprecher": "Frau Weber", "text": "[energisch] Ich engagiere mich in der Bürgerrechtsbewegung und kann sagen: Ohne soziale Medien wären viele Proteste der letzten Jahre gar nicht möglich gewesen. Von #MeToo bis zu den Klimastreiks — Plattformen ermöglichen horizontale Organisation ohne Hierarchien und ohne Medien als Gatekeeper. Natürlich nutzen auch autoritäre Regime und Desinformationskampagnen dieselben Werkzeuge. Aber das Potenzial für emanzipatorische Bewegungen ist real und darf nicht unterschätzt werden."}
                            ],
                            "questions": [{"question_num": 5, "question_type": "richtig_falsch", "question_text": "Frau Weber ist der Ansicht, dass soziale Medien ausschließlich negative gesellschaftliche Auswirkungen haben.", "correct_answer": "Falsch"}]
                        }
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "gespraech",
                    "title": "Aufgabe 2 – Ein Interview",
                    "instruction": "Sie hören ein Radiointerview. Sind die Aussagen richtig oder falsch? Sie hören das Interview zweimal.",
                    "heard_times": 2,
                    "preparation_seconds": 60,
                    "sprecher": [
                        {"name": "Moderatorin", "voice_id": "Aoede"},
                        {"name": "Prof. Dr. Klaus Richter", "voice_id": "Orus"}
                    ],
                    "script_segments": [
                        {"sprecher": "Moderatorin", "text": "[professionell] Guten Abend und willkommen zu 'Gesellschaft im Gespräch'. Heute diskutieren wir mit Prof. Dr. Klaus Richter, Soziologe an der FU Berlin, über soziale Medien und ihre Auswirkungen auf die Demokratie. Herr Professor, beeinflussen Algorithmen wirklich unsere politische Meinung?"},
                        {"sprecher": "Prof. Dr. Klaus Richter", "text": "[differenziert] Das ist eine berechtigte und gleichzeitig komplizierte Frage. Ja, Algorithmen beeinflussen, welche Inhalte wir sehen — und damit mittelbar, worüber wir nachdenken. Ob sie aber direkt politische Meinungen ändern, ist wissenschaftlich umstritten. Was wir sicherer sagen können: Sie verstärken bestehende Überzeugungen eher, als dass sie sie grundlegend ändern."},
                        {"sprecher": "Moderatorin", "text": "Ist das dann nicht halb so schlimm?"},
                        {"sprecher": "Prof. Dr. Klaus Richter", "text": "Keineswegs. Verstärkung ist das Problem. Wenn extremere Positionen innerhalb eines politischen Lagers immer sichtbarer werden, verschiebt sich das, was als normal gilt — die sogenannte Overton-Fensterdynamik. Jemand, der vor zehn Jahren als radikal galt, erscheint heute manchmal als Mainstream. Das hat sehr reale Konsequenzen für Wahlen und politische Mehrheiten."},
                        {"sprecher": "Moderatorin", "text": "Brauchen wir also stärkere Regulierung?"},
                        {"sprecher": "Prof. Dr. Klaus Richter", "text": "[nachdenklich] Regulierung ist nötig, aber schwierig. Der EU-Digital-Services-Act ist ein Schritt in die richtige Richtung: Er zwingt Plattformen zu mehr Transparenz über ihre Algorithmen und zur aktiven Bekämpfung von Desinformation. Das Problem ist die Durchsetzung. Plattformen operieren global, und nationale oder europäische Regulierungen erzeugen Umgehungsstrategien."},
                        {"sprecher": "Moderatorin", "text": "Wie können Nutzer sich selbst schützen?"},
                        {"sprecher": "Prof. Dr. Klaus Richter", "text": "Medienkompetenz ist essenziell, aber ich halte sie für unzureichend als alleinige Strategie. Es ist naiv zu glauben, dass Information allein gegen psychologisch ausgefeilte Manipulationsmechanismen schützt. Wir brauchen beides: individuelle Kompetenz und strukturelle Rahmenbedingungen. Schulen müssen kritisches Denken und Quellenprüfung lehren — aber Plattformen müssen gleichzeitig verpflichtet werden, weniger manipulative Designs einzusetzen."},
                        {"sprecher": "Moderatorin", "text": "Stellt die Dominanz weniger US-amerikanischer Plattformen ein Problem dar?"},
                        {"sprecher": "Prof. Dr. Klaus Richter", "text": "[bestimmt] Ja, und das wird meines Erachtens unterschätzt. Diese Plattformen wurden nach amerikanischen Werten, Rechtsbegriffen und Geschäftsinteressen gebaut. Was in Europa als problematisch gilt — etwa bestimmte Formen der Hassrede — ist in den USA durch den First Amendment anders geregelt. Europas digitale Souveränität steht auf dem Spiel, solange keine wettbewerbsfähigen europäischen Alternativen existieren."},
                        {"sprecher": "Moderatorin", "text": "Gibt es Hoffnungszeichen?"},
                        {"sprecher": "Prof. Dr. Klaus Richter", "text": "Durchaus. Wir erleben gerade eine Renaissance dezentraler Netzwerke wie Mastodon oder Bluesky, die auf offenen Protokollen basieren und keine zentrale Kontrolle haben. Ob sie Massenrelevanz erreichen, ist offen. Aber sie zeigen, dass alternative Architekturen möglich sind — und dass wir das heutige Social-Media-System nicht als naturgegeben hinnehmen müssen."},
                        {"sprecher": "Moderatorin", "text": "Herr Professor Richter, herzlichen Dank für dieses aufschlussreiche Gespräch."},
                        {"sprecher": "Prof. Dr. Klaus Richter", "text": "Gerne, danke für die wichtigen Fragen."}
                    ],
                    "questions": [
                        {"question_num": 6, "question_type": "richtig_falsch", "question_text": "Prof. Richter sagt, dass Algorithmen politische Meinungen direkt und grundlegend verändern.", "correct_answer": "Falsch"},
                        {"question_num": 7, "question_type": "richtig_falsch", "question_text": "Laut Prof. Richter verstärken Algorithmen bestehende Überzeugungen eher, als sie zu ändern.", "correct_answer": "Richtig"},
                        {"question_num": 8, "question_type": "richtig_falsch", "question_text": "Prof. Richter hält den EU-Digital-Services-Act für einen sinnvollen ersten Schritt.", "correct_answer": "Richtig"},
                        {"question_num": 9, "question_type": "richtig_falsch", "question_text": "Er glaubt, dass Medienkompetenz allein ausreicht, um Nutzer vor Manipulation zu schützen.", "correct_answer": "Falsch"},
                        {"question_num": 10, "question_type": "richtig_falsch", "question_text": "Prof. Richter sieht die Dominanz US-amerikanischer Plattformen als ernstes Problem für Europas digitale Souveränität.", "correct_answer": "Richtig"},
                        {"question_num": 11, "question_type": "richtig_falsch", "question_text": "Er ist der Ansicht, dass dezentrale Netzwerke wie Mastodon bereits Massenrelevanz haben.", "correct_answer": "Falsch"},
                        {"question_num": 12, "question_type": "richtig_falsch", "question_text": "Laut Prof. Richter sind europäische und amerikanische Regeln zur Hassrede identisch.", "correct_answer": "Falsch"},
                        {"question_num": 13, "question_type": "richtig_falsch", "question_text": "Prof. Richter befürwortet sowohl individuelle Medienkompetenz als auch strukturelle Regulierung.", "correct_answer": "Richtig"},
                        {"question_num": 14, "question_type": "richtig_falsch", "question_text": "Er hält das heutige Social-Media-System für unveränderlich und alternativlos.", "correct_answer": "Falsch"},
                        {"question_num": 15, "question_type": "richtig_falsch", "question_text": "Die Verschiebung des 'Overton-Fensters' wird von Prof. Richter als bedeutungslos für Wahlergebnisse eingestuft.", "correct_answer": "Falsch"}
                    ]
                },
                {
                    "aufgabe_num": 3,
                    "typ": "ansagen",
                    "title": "Aufgabe 3 – Kurze Radiotexte",
                    "instruction": "Sie hören fünf kurze Radiotexte. Sind die Aussagen richtig oder falsch? Sie hören jeden Text zweimal.",
                    "heard_times": 2,
                    "preparation_seconds": 30,
                    "ansagen": [
                        {
                            "ansage_num": 1, "sprecher": "Moderatorin", "voice_id": "Aoede",
                            "text": "[sachlich] Brüssel: Das Europäische Parlament hat heute mit großer Mehrheit die überarbeitete KI-Verordnung verabschiedet. Erstmals werden KI-Systeme nach Risikostufen klassifiziert. Hochrisiko-Anwendungen — etwa in der Strafverfolgung oder der Kreditvergabe — unterliegen strengen Transparenz- und Sicherheitsanforderungen. Generative KI-Modelle wie große Sprachmodelle müssen künftig offenlegen, welche Trainingsdaten verwendet wurden.",
                            "question_num": 16, "question_type": "richtig_falsch", "question_text": "Die neue EU-KI-Verordnung stuft alle KI-Systeme als gleich riskant ein.", "correct_answer": "Falsch"
                        },
                        {
                            "ansage_num": 2, "sprecher": "Ansager", "voice_id": "Fenrir",
                            "text": "Neuigkeit aus der Tech-Branche: Das soziale Netzwerk Bluesky hat heute die Marke von 25 Millionen Nutzern überschritten — ein Anstieg von 400 Prozent innerhalb von sechs Monaten, ausgelöst durch eine Abwanderungswelle von der Plattform X nach umstrittenen Moderationsentscheidungen des Eigentümers. Anders als X basiert Bluesky auf einem dezentralen, offenen Protokoll, das es Nutzern ermöglicht, ihre Daten auf eigenen Servern zu speichern.",
                            "question_num": 17, "question_type": "richtig_falsch", "question_text": "Bluesky ist eine zentral kontrollierte Plattform wie X.", "correct_answer": "Falsch"
                        },
                        {
                            "ansage_num": 3, "sprecher": "Moderatorin", "voice_id": "Aoede",
                            "text": "[informativ] Bildungsnachrichten: Die Kultusministerkonferenz hat heute ein neues Rahmenkonzept zur Medienkompetenz in Schulen verabschiedet. Ab dem kommenden Schuljahr sollen Schülerinnen und Schüler ab Klasse fünf systematisch lernen, wie Algorithmen funktionieren, wie Fake News erkannt werden und welche Datenschutzrechte Nutzer haben. Die Umsetzung liegt bei den Bundesländern, die finanzielle Ausstattung ist noch nicht gesichert.",
                            "question_num": 18, "question_type": "richtig_falsch", "question_text": "Die Finanzierung des neuen Medienkompetenzkurses in Schulen ist bereits vollständig gesichert.", "correct_answer": "Falsch"
                        },
                        {
                            "ansage_num": 4, "sprecher": "Ansager", "voice_id": "Fenrir",
                            "text": "Wirtschaftsnachrichten: Meta hat im dritten Quartal einen Rekordumsatz von 40 Milliarden US-Dollar gemeldet. Über 98 Prozent der Einnahmen stammen aus Werbegeschäften. Kritiker weisen darauf hin, dass dieses Geschäftsmodell strukturelle Anreize setzt, nutzergenerierte Daten so umfassend wie möglich auszuwerten — und damit in Konflikt mit europäischen Datenschutzgesetzen steht. Die irische Datenschutzbehörde hat allein in diesem Jahr Bußgelder von insgesamt 1,3 Milliarden Euro gegen Meta verhängt.",
                            "question_num": 19, "question_type": "richtig_falsch", "question_text": "Der größte Teil von Metas Umsatz stammt aus Werbeeinnahmen.", "correct_answer": "Richtig"
                        },
                        {
                            "ansage_num": 5, "sprecher": "Moderatorin", "voice_id": "Aoede",
                            "text": "[ernst] Studie zu Online-Hassrede: Eine aktuelle Untersuchung des Leibniz-Instituts für Medienforschung zeigt, dass 62 Prozent der befragten Frauen in Führungspositionen angeben, in den letzten zwölf Monaten online bedroht oder beleidigt worden zu sein. Besonders betroffen seien Politikerinnen und Journalistinnen. Viele Betroffene schränken infolgedessen ihre öffentliche Meinungsäußerung ein — was die Forscher als ernste Bedrohung für die Meinungsfreiheit werten.",
                            "question_num": 20, "question_type": "richtig_falsch", "question_text": "Laut der Studie führen Bedrohungen online dazu, dass betroffene Frauen ihre öffentlichen Äußerungen einschränken.", "correct_answer": "Richtig"
                        }
                    ]
                }
            ]
        },
        "sprachbausteine": {
            "total_questions": 20,
            "duration_minutes": 30,
            "aufgaben": [
                {
                    "aufgabe_num": 1,
                    "typ": "lueckentext_mc",
                    "title": "Aufgabe 1 – Lückentext",
                    "instruction": "Lesen Sie den Text und wählen Sie das passende Wort (a, b oder c).",
                    "text_with_gaps": "Sollten soziale Medien stärker reguliert werden?\n\nDie Debatte über die Regulierung sozialer Netzwerke {21} an Schärfe gewonnen. Während Plattformen lange {22} sich auf das Prinzip der Meinungsfreiheit berufen konnten, wächst in der EU der politische Wille, klare Regeln {23}. Der Digital Services Act verpflichtet große Plattformen dazu, systematisch gegen illegale Inhalte vorzugehen und ihre Algorithmen transparenter zu {24}.\n\nKritiker warnen jedoch, dass zu weitreichende Eingriffe das Risiko staatlicher Zensur {25}. Eine Regulierungsbehörde, die entscheidet, welche Inhalte legal sind, könnte zu einem Instrument politischer Kontrolle {26}. Befürworter entgegnen, dass der Status quo untragbar {27}: Plattformen hätten faktisch mehr Einfluss auf den öffentlichen Diskurs als demokratisch gewählte Institutionen, {28} jegliche Rechenschaftspflicht.\n\nLetztlich geht es um eine grundlegende Frage: {29} Meinungsfreiheit das absolute Recht ist, auch wenn sie zur Destabilisierung demokratischer Systeme missbraucht {30}?",
                    "options": [
                        {"question_num": 21, "a": "hat", "b": "ist", "c": "wurde", "correct_answer": "a"},
                        {"question_num": 22, "a": "noch", "b": "immer", "c": "schon", "correct_answer": "b"},
                        {"question_num": 23, "a": "setzen", "b": "festlegen", "c": "einzuführen", "correct_answer": "c"},
                        {"question_num": 24, "a": "machen", "b": "gestalten", "c": "stellen", "correct_answer": "a"},
                        {"question_num": 25, "a": "birgt", "b": "enthält", "c": "trägt", "correct_answer": "a"},
                        {"question_num": 26, "a": "werden", "b": "werden können", "c": "werden müssen", "correct_answer": "a"},
                        {"question_num": 27, "a": "sei", "b": "wäre", "c": "ist", "correct_answer": "c"},
                        {"question_num": 28, "a": "ohne", "b": "aber ohne", "c": "jedoch ohne", "correct_answer": "c"},
                        {"question_num": 29, "a": "Ob", "b": "Wenn", "c": "Falls", "correct_answer": "a"},
                        {"question_num": 30, "a": "wird", "b": "werden kann", "c": "werden darf", "correct_answer": "b"}
                    ]
                },
                {
                    "aufgabe_num": 2,
                    "typ": "lueckentext_wortbank",
                    "title": "Aufgabe 2 – Wortschatz",
                    "instruction": "Lesen Sie den Text. Welches Wort (a–o) passt in welche Lücke? Fünf Wörter passen nicht.",
                    "text_with_gaps": "Digitale Kompetenz in der Schule — eine dringende Aufgabe\n\nIn einer Gesellschaft, in der Informationen zunehmend über digitale Kanäle {31} werden, ist Medienkompetenz keine optionale Zusatzqualifikation mehr, sondern eine {32} Kulturtechnik — vergleichbar mit Lesen und Schreiben. Trotzdem fehlt in deutschen Schulen bislang ein {33} Konzept zur systematischen Vermittlung digitaler Kompetenzen.\n\nUntersuchungen belegen, dass Schülerinnen und Schüler zwar technisch versiert im Umgang mit Smartphones sind, jedoch Schwierigkeiten haben, die {34} von Online-Quellen einzuschätzen oder Algorithmen als selektive Filter zu verstehen. Wer nicht erkennt, dass sein Newsfeed kuratiert ist, {35} ihn für eine objektive Abbildung der Realität.\n\nEine besondere Herausforderung stellt die rasante Entwicklung von KI-generierten Inhalten dar. Sogenannte Deepfakes — täuschend {36} gefälschte Video- oder Audioaufnahmen — sind mit bloßem Auge kaum noch zu identifizieren. Hier brauchen Schüler nicht nur technisches Wissen, sondern auch ein geschärftes {37} für Manipulationsabsichten.\n\nDie gute Nachricht: Medienkompetenz lässt sich lernen. Studien zeigen, dass bereits kurze {38} im kritischen Umgang mit Online-Informationen messbare Effekte haben. Nötig ist allerdings politischer {39}, entsprechende Ressourcen bereitzustellen — von der Lehrerausbildung bis zur technischen {40} der Schulen.",
                    "wortbank": [
                        {"id": "a", "word": "VERBREITET"},
                        {"id": "b", "word": "GRUNDLEGENDE"},
                        {"id": "c", "word": "VERBINDLICHES"},
                        {"id": "d", "word": "GLAUBWUERDIGKEIT"},
                        {"id": "e", "word": "HAELT"},
                        {"id": "f", "word": "ECHT"},
                        {"id": "g", "word": "BEWUSSTSEIN"},
                        {"id": "h", "word": "TRAININGSEINHEITEN"},
                        {"id": "i", "word": "WILLE"},
                        {"id": "j", "word": "AUSSTATTUNG"},
                        {"id": "k", "word": "ZUVERLAESSIGKEIT"},
                        {"id": "l", "word": "KONSUMIERT"},
                        {"id": "m", "word": "VERPFLICHTENDE"},
                        {"id": "n", "word": "BEURTEILUNG"},
                        {"id": "o", "word": "ENGAGEMENT"}
                    ],
                    "options": [
                        {"question_num": 31, "correct_answer": "a"},
                        {"question_num": 32, "correct_answer": "b"},
                        {"question_num": 33, "correct_answer": "c"},
                        {"question_num": 34, "correct_answer": "d"},
                        {"question_num": 35, "correct_answer": "e"},
                        {"question_num": 36, "correct_answer": "f"},
                        {"question_num": 37, "correct_answer": "g"},
                        {"question_num": 38, "correct_answer": "h"},
                        {"question_num": 39, "correct_answer": "i"},
                        {"question_num": 40, "correct_answer": "j"}
                    ]
                }
            ]
        },
        "schreiben": {
            "total_time_minutes": 45,
            "aufgaben": [{
                "aufgabe_num": 1,
                "aufgabe_typ": "erorterung",
                "aufgabe": "In vielen Ländern wird diskutiert, ob soziale Netzwerke für Jugendliche unter 16 Jahren verboten werden sollten. Australien hat ein solches Verbot 2024 eingeführt.\n\nSchreiben Sie einen argumentativen Aufsatz. Berücksichtigen Sie:\n• Argumente für ein Mindestalter bei sozialen Netzwerken\n• Argumente dagegen\n• Ihre eigene Position mit Begründung\n\nSchreiben Sie ca. 200 Wörter.",
                "min_words": 180,
                "max_words": 250
            }]
        },
        "sprechen": {
            "total_time_minutes": 15,
            "teile": [
                {
                    "teil_num": 1,
                    "titel": "Persönliche Mediennutzung",
                    "instructions": "Beschreiben Sie Ihre eigene Nutzung sozialer Medien.",
                    "fragen": [
                        {"frage_num": 1, "frage_text": "[professionell] Guten Tag, herzlich willkommen zur Prüfung. Bitte stellen Sie sich kurz vor und beschreiben Sie, welche sozialen Netzwerke Sie nutzen und warum.", "needs_audio": True},
                        {"frage_num": 2, "frage_text": "Haben Sie schon einmal bewusst versucht, Ihre Social-Media-Nutzung zu reduzieren? Was hat Sie dazu bewogen und wie haben Sie das erlebt?", "needs_audio": True},
                        {"frage_num": 3, "frage_text": "Glauben Sie, dass soziale Medien einen positiven oder negativen Einfluss auf Ihre Informationsgewohnheiten haben?", "needs_audio": True}
                    ]
                },
                {
                    "teil_num": 2,
                    "titel": "Diskussion: Fake News und gesellschaftliche Verantwortung",
                    "instructions": "Diskutieren Sie das folgende Thema mit dem Prüfer.",
                    "fragen": [
                        {"frage_num": 4, "frage_text": "Wer trägt Ihrer Meinung nach die größte Verantwortung für die Verbreitung von Falschinformationen: die Nutzer, die Plattformen oder der Staat?", "needs_audio": True},
                        {"frage_num": 5, "frage_text": "Sollten Plattformen Inhalte aktiv moderieren, auch wenn dabei das Risiko besteht, legitime Meinungen zu zensieren?", "needs_audio": True},
                        {"frage_num": 6, "frage_text": "Was halten Sie von der Idee, dass Nutzer für das Teilen von nachweislich falschen Informationen haftbar gemacht werden sollten?", "needs_audio": True}
                    ]
                },
                {
                    "teil_num": 3,
                    "titel": "Problemlösung: Schulen und Smartphones",
                    "instructions": "Lösen Sie gemeinsam mit dem Prüfer ein Problem.",
                    "aufgabe": "Eine Schule überlegt, ob Smartphones während des Unterrichts vollständig verboten werden sollen. Diskutieren Sie Vor- und Nachteile und entwickeln Sie einen Kompromissvorschlag.",
                    "fragen": [
                        {"frage_num": 7, "frage_text": "Stellen Sie sich vor, Sie sind Schulleiter. Welche Argumente sprechen für und gegen ein vollständiges Smartphone-Verbot im Unterricht?", "needs_audio": True},
                        {"frage_num": 8, "frage_text": "Wie könnte ein ausgewogener Kompromiss aussehen, der sowohl die pädagogischen Ziele als auch die Medienkompetenzvermittlung berücksichtigt?", "needs_audio": True}
                    ]
                }
            ]
        }
    }


@app.on_event("startup")
async def startup():
    try:
        await _startup_inner()
    except Exception as e:
        logger.warning(f"Startup seeding skipped (non-fatal): {e}")


async def _startup_inner():
    # Ensure guest user row exists (needed for FK constraint on attempts table)
    if not await db.users.find_one({"user_id": "guest"}):
        await db.users.insert_one({
            "user_id": "guest", "email": "guest@example.com",
            "name": "Guest", "picture": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("Seeded guest user")

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

    if not await db.exams.find_one({"exam_id": "exam_telc_b2_002"}):
        await db.exams.insert_one(get_telc_b2_seed_002())
        logger.info("Seeded exam_telc_b2_002")

    if not await db.exams.find_one({"exam_id": "exam_telc_b2_003"}):
        await db.exams.insert_one(get_telc_b2_seed_003())
        logger.info("Seeded exam_telc_b2_003")

    if not await db.exams.find_one({"exam_id": "exam_telc_b2_004"}):
        await db.exams.insert_one(get_telc_b2_seed_004())
        logger.info("Seeded exam_telc_b2_004")

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

_cors_raw = os.environ.get('CORS_ORIGINS', '')
_cors_origins = (
    [o.strip() for o in _cors_raw.split(',') if o.strip()]
    if _cors_raw.strip()
    else [FRONTEND_URL, "http://localhost:3000", "http://localhost:5173",
          "https://localhost:3000", "https://localhost:5173"]
)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown():
    pass  # InsForge REST client is stateless; nothing to close
