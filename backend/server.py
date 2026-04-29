"""
IELTS Mock Exam Platform - Backend Server
==========================================
ElevenLabs V3 audio pipeline + OpenRouter AI + Emergent Google Auth
"""
from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, BackgroundTasks, UploadFile, File
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os, logging, json, base64, uuid, httpx, asyncio
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
mongo_client = AsyncIOMotorClient(mongo_url)
db = mongo_client[os.environ['DB_NAME']]

ELEVENLABS_API_KEY = os.environ.get('ELEVENLABS_API_KEY', '')
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')

VOICES = {
    "examiner": "nPczCjzI2devNBz1zQrb",        # Brian - Deep narrator (instructions only)
    "british_female_1": "pFZP5JQG7iQjIQuC4Bku", # Lily - Velvety Actress
    "british_male_1": "onwK4e9ZLuTAKqWW03F9",   # Daniel - Steady Broadcaster
    "british_female_2": "Xb7hH8MSUJpSbSDYk0k2", # Alice - Clear Educator
    "british_male_2": "JBFqnCBsd6RMkjVDRZzb",   # George - Warm Storyteller
    "british_male_3": "eUlIljct4YrEQRcEqrii",    # Ben - Calm British Male
    "professor": "NNl6r8mD7vthiJatiJt1",          # Bradford - Expressive Academic
}

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
    return user

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

    response.set_cookie(key="session_token", value=session_token, httponly=True, secure=True, samesite="none", path="/", max_age=7*24*3600)
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    return user

@api_router.get("/auth/me")
async def get_me(request: Request):
    return await get_current_user(request)

@api_router.post("/auth/logout")
async def logout(request: Request, response: Response):
    session_token = request.cookies.get("session_token")
    if session_token:
        await db.user_sessions.delete_one({"session_token": session_token})
    response.delete_cookie(key="session_token", path="/")
    return {"message": "Logged out"}

# ==========================================
# ELEVENLABS V3 AUDIO PIPELINE
# ==========================================
def _generate_audio_sync(text: str, voice_id: str) -> bytes:
    from elevenlabs import ElevenLabs
    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    audio = client.text_to_speech.convert(
        voice_id=voice_id, text=text, model_id="eleven_v3", output_format="mp3_44100_128"
    )
    return b"".join(audio)

async def generate_audio_for_text(text: str, voice_id: str) -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _generate_audio_sync, text, voice_id)

async def generate_exam_audio(exam_id: str):
    """Generate all audio for an exam"""
    try:
        exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
        if not exam:
            return

        await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "generating_audio", "audio_progress": 0}})

        total_segments = 0
        generated = 0

        for section in exam.get("listening", {}).get("sections", []):
            total_segments += len(section.get("script_segments", []))
        for part in exam.get("speaking", {}).get("parts", []):
            for q in part.get("questions", []):
                if q.get("needs_audio"):
                    total_segments += 1

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
                    await db.audio_files.insert_one({
                        "audio_id": audio_id, "exam_id": exam_id, "section_num": section_num,
                        "type": "instruction", "audio_base64": base64.b64encode(audio_bytes).decode(),
                        "format": "mp3", "created_at": datetime.now(timezone.utc).isoformat()
                    })
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
                    voice_id = VOICES.get("british_female_1")
                    for sp in section.get("speakers", []):
                        if sp["name"] == segment["speaker"]:
                            voice_id = sp["voice_id"]
                            break

                    audio_bytes = await generate_audio_for_text(segment["text"], voice_id)
                    audio_id = f"audio_{uuid.uuid4().hex[:12]}"

                    await db.audio_files.insert_one({
                        "audio_id": audio_id, "exam_id": exam_id, "section_num": section_num,
                        "segment_index": i, "audio_base64": base64.b64encode(audio_bytes).decode(),
                        "format": "mp3", "created_at": datetime.now(timezone.utc).isoformat()
                    })

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
                        await db.audio_files.insert_one({
                            "audio_id": audio_id, "exam_id": exam_id, "type": "speaking",
                            "audio_base64": base64.b64encode(audio_bytes).decode(),
                            "format": "mp3", "created_at": datetime.now(timezone.utc).isoformat()
                        })
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

# ==========================================
# OPENROUTER AI
# ==========================================
async def call_openrouter(messages: list, model: str = "google/gemini-2.0-flash-001", json_mode: bool = True) -> str:
    async with httpx.AsyncClient(timeout=120.0) as client:
        payload = {"model": model, "messages": messages}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json=payload
        )
        if r.status_code != 200:
            logger.error(f"OpenRouter error: {r.status_code} - {r.text}")
            raise HTTPException(500, "AI service error")
        return r.json()["choices"][0]["message"]["content"]

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

# ==========================================
# EXAM ENDPOINTS
# ==========================================
@api_router.get("/exams")
async def list_exams():
    exams = await db.exams.find({}, {"_id": 0, "exam_id": 1, "title": 1, "pathway": 1, "status": 1, "created_at": 1}).to_list(100)
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
    doc = await db.audio_files.find_one({"audio_id": audio_id}, {"_id": 0, "audio_base64": 1})
    if not doc:
        raise HTTPException(404, "Audio not found")
    audio_bytes = base64.b64decode(doc["audio_base64"])
    return Response(content=audio_bytes, media_type="audio/mpeg",
        headers={"Content-Disposition": f"inline; filename={audio_id}.mp3", "Cache-Control": "public, max-age=86400"})

# ==========================================
# SPEECH-TO-TEXT (ElevenLabs Scribe)
# ==========================================
def _transcribe_audio_sync(audio_bytes: bytes) -> str:
    """Transcribe audio using ElevenLabs STT (sync, run in executor)"""
    import io
    from elevenlabs import ElevenLabs
    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    result = client.speech_to_text.convert(
        file=io.BytesIO(audio_bytes),
        model_id="scribe_v1"
    )
    return result.text if hasattr(result, 'text') else str(result)

async def transcribe_audio_async(audio_bytes: bytes) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _transcribe_audio_sync, audio_bytes)

@api_router.post("/speaking/transcribe")
async def transcribe_speaking(audio_file: UploadFile = File(...), request: Request = None):
    """Transcribe recorded speaking audio via ElevenLabs STT"""
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

        # Assign voice IDs to speakers
        voice_list = list(VOICES.values())
        for section in listening_data.get("sections", []):
            for i, sp in enumerate(section.get("speakers", [])):
                sp["voice_id"] = voice_list[i % len(voice_list)]

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
                        {"name": "Receptionist", "voice_id": "pFZP5JQG7iQjIQuC4Bku"},
                        {"name": "Guest", "voice_id": "onwK4e9ZLuTAKqWW03F9"}
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
                    "speakers": [{"name": "Officer", "voice_id": "Xb7hH8MSUJpSbSDYk0k2"}],
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
                        {"name": "Tutor", "voice_id": "JBFqnCBsd6RMkjVDRZzb"},
                        {"name": "Sarah", "voice_id": "pFZP5JQG7iQjIQuC4Bku"},
                        {"name": "James", "voice_id": "eUlIljct4YrEQRcEqrii"}
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
                    "speakers": [{"name": "Professor", "voice_id": "NNl6r8mD7vthiJatiJt1"}],
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

# ==========================================
# ADMIN ENDPOINTS
# ==========================================
@api_router.get("/admin/exams")
async def admin_list_exams(request: Request):
    """Admin view - all exams with full details"""
    await get_current_user(request)
    exams = await db.exams.find({}, {"_id": 0, "exam_id": 1, "title": 1, "pathway": 1, "status": 1,
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
    """Delete an exam and its audio"""
    await get_current_user(request)
    await db.exams.delete_one({"exam_id": exam_id})
    result = await db.audio_files.delete_many({"exam_id": exam_id})
    return {"deleted": True, "audio_files_removed": result.deleted_count}

@api_router.post("/admin/exams/{exam_id}/regenerate-audio")
async def admin_regenerate_audio(exam_id: str, background_tasks: BackgroundTasks, request: Request):
    """Force regenerate all audio for an exam"""
    await get_current_user(request)
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
    await get_current_user(request)
    return {
        "total_exams": await db.exams.count_documents({}),
        "ready_exams": await db.exams.count_documents({"status": "ready"}),
        "total_audio_files": await db.audio_files.count_documents({}),
        "total_users": await db.users.count_documents({}),
        "total_attempts": await db.attempts.count_documents({}),
        "completed_attempts": await db.attempts.count_documents({"status": "completed"}),
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
    mongo_client.close()
