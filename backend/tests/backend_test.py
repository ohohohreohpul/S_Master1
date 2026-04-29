"""IELTS Mock Exam Backend Tests"""
import os, requests, pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://ielts-mock-exam-2.preview.emergentagent.com").rstrip("/")
SESSION_TOKEN = "test_session_1777464282346"
EXAM_ID = "exam_academic_001"
AUDIO_ID = "audio_82d3fdfeded8"


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def auth_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {SESSION_TOKEN}"})
    return s


# --- Exams ---
class TestExams:
    def test_list_exams(self, client):
        r = client.get(f"{BASE_URL}/api/exams")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list) and len(data) >= 1
        ids = [e["exam_id"] for e in data]
        assert EXAM_ID in ids
        e = next(x for x in data if x["exam_id"] == EXAM_ID)
        assert e["status"] == "ready"
        assert "title" in e and "pathway" in e

    def test_get_exam_no_answers(self, client):
        r = client.get(f"{BASE_URL}/api/exams/{EXAM_ID}")
        assert r.status_code == 200
        d = r.json()
        # ensure no correct_answer leakage
        for s in d.get("listening", {}).get("sections", []):
            for q in s.get("questions", []):
                assert "correct_answer" not in q, f"listening Q{q.get('question_num')} leaks answer"
        for p in d.get("reading", {}).get("passages", []):
            for q in p.get("questions", []):
                assert "correct_answer" not in q, f"reading Q{q.get('question_num')} leaks answer"
        # audio_ids should be present in segments (audio pre-generated)
        seg_with_audio = sum(1 for s in d["listening"]["sections"] for seg in s.get("script_segments", []) if seg.get("audio_id"))
        assert seg_with_audio >= 1

    def test_exam_status(self, client):
        r = client.get(f"{BASE_URL}/api/exams/{EXAM_ID}/status")
        assert r.status_code == 200
        assert r.json().get("status") == "ready"

    def test_exam_not_found(self, client):
        r = client.get(f"{BASE_URL}/api/exams/does_not_exist_xyz")
        assert r.status_code == 404


# --- Audio ---
class TestAudio:
    def test_get_audio_returns_mpeg(self, client):
        # Dynamically discover a real audio_id from exam (regen invalidates hardcoded ones)
        r0 = client.get(f"{BASE_URL}/api/exams/{EXAM_ID}")
        assert r0.status_code == 200
        audio_id = None
        for s in r0.json().get("listening", {}).get("sections", []):
            for seg in s.get("script_segments", []):
                if seg.get("audio_id"):
                    audio_id = seg["audio_id"]
                    break
            if audio_id:
                break
        assert audio_id, "No script_segment audio_id found"
        r = client.get(f"{BASE_URL}/api/audio/{audio_id}")
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("audio/mpeg")
        assert len(r.content) > 1000  # non-trivial mp3

    def test_audio_not_found(self, client):
        r = client.get(f"{BASE_URL}/api/audio/audio_invalidxxx")
        assert r.status_code == 404


# --- Auth ---
class TestAuth:
    def test_me_unauth(self, client):
        r = client.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 401

    def test_me_with_bearer(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 200
        d = r.json()
        assert d["user_id"] == "test-user-1777464282346"


# --- Attempts & Scoring ---
class TestAttempts:
    def test_create_attempt_requires_auth(self, client):
        r = client.post(f"{BASE_URL}/api/attempts", json={"exam_id": EXAM_ID, "module": "listening"})
        assert r.status_code == 401

    def test_create_and_submit_listening(self, auth_client):
        # create attempt
        r = auth_client.post(f"{BASE_URL}/api/attempts", json={"exam_id": EXAM_ID, "module": "listening"})
        assert r.status_code == 200
        attempt_id = r.json()["attempt_id"]
        assert attempt_id.startswith("attempt_")

        # submit some correct answers
        answers = {
            "1": "standard", "2": "85", "3": "7", "4": "ground", "5": "back",
            "6": "2", "7": "15", "8": "Thompson", "9": "893214", "10": "185",
            "11": "wrong",
        }
        r2 = auth_client.put(f"{BASE_URL}/api/attempts/{attempt_id}/submit", json={"answers": answers})
        assert r2.status_code == 200
        scores = r2.json()["scores"]
        assert scores is not None
        assert scores["correct"] == 10
        assert scores["total"] == 40
        assert "band_score" in scores

        # GET to verify persistence
        r3 = auth_client.get(f"{BASE_URL}/api/attempts/{attempt_id}")
        assert r3.status_code == 200
        assert r3.json()["status"] == "completed"
        assert r3.json()["scores"]["correct"] == 10

    def test_submit_reading_scoring(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/attempts", json={"exam_id": EXAM_ID, "module": "reading"})
        attempt_id = r.json()["attempt_id"]
        answers = {"1": "False", "2": "False", "4": "True"}
        r2 = auth_client.put(f"{BASE_URL}/api/attempts/{attempt_id}/submit", json={"answers": answers})
        assert r2.status_code == 200
        s = r2.json()["scores"]
        assert s["correct"] == 3
        assert s["total"] == 40

    def test_attempt_not_found(self, auth_client):
        r = auth_client.put(f"{BASE_URL}/api/attempts/attempt_doesnotexist/submit", json={"answers": {}})
        assert r.status_code == 404


# --- Progress ---
class TestProgress:
    def test_progress_requires_auth(self, client):
        r = client.get(f"{BASE_URL}/api/progress")
        assert r.status_code == 401

    def test_progress(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/progress")
        assert r.status_code == 200
        d = r.json()
        assert "modules" in d and "overall_estimated_band" in d
        for m in ["listening", "reading", "writing", "speaking"]:
            assert m in d["modules"]
            assert "attempts" in d["modules"][m]


# --- Admin Endpoints (NEW) ---
class TestAdmin:
    def test_admin_exams_unauth(self, client):
        r = client.get(f"{BASE_URL}/api/admin/exams")
        assert r.status_code == 401

    def test_admin_exams_list(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/admin/exams")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert any(e["exam_id"] == EXAM_ID for e in data)
        e = next(x for x in data if x["exam_id"] == EXAM_ID)
        assert "audio_files_count" in e
        assert isinstance(e["audio_files_count"], int)
        assert e["audio_files_count"] > 0
        assert e["status"] == "ready"

    def test_admin_stats_unauth(self, client):
        r = client.get(f"{BASE_URL}/api/admin/stats")
        assert r.status_code == 401

    def test_admin_stats(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/admin/stats")
        assert r.status_code == 200
        d = r.json()
        for k in ["total_exams", "ready_exams", "total_audio_files",
                  "total_users", "total_attempts", "completed_attempts"]:
            assert k in d, f"missing key {k}"
            assert isinstance(d[k], int)
        assert d["total_exams"] >= 1
        assert d["ready_exams"] >= 1
        assert d["total_audio_files"] >= 1

    def test_admin_delete_unauth(self, client):
        r = client.delete(f"{BASE_URL}/api/admin/exams/some_id")
        assert r.status_code == 401

    def test_admin_delete_nonexistent(self, auth_client):
        # Deleting a non-existent exam should still 200 (idempotent)
        r = auth_client.delete(f"{BASE_URL}/api/admin/exams/TEST_does_not_exist_zzz")
        assert r.status_code == 200
        d = r.json()
        assert d["deleted"] is True


# --- Section Instruction Audio (NEW) ---
class TestInstructionAudio:
    def test_listening_sections_have_instruction_audio(self, client):
        r = client.get(f"{BASE_URL}/api/exams/{EXAM_ID}")
        assert r.status_code == 200
        sections = r.json().get("listening", {}).get("sections", [])
        assert len(sections) >= 1
        sections_with_instr = [s for s in sections if s.get("instruction_audio_id")]
        assert len(sections_with_instr) >= 1, \
            f"Expected at least one section with instruction_audio_id, got 0 of {len(sections)}"
        # Verify the instruction audio is fetchable
        instr_id = sections_with_instr[0]["instruction_audio_id"]
        r2 = client.get(f"{BASE_URL}/api/audio/{instr_id}")
        assert r2.status_code == 200
        assert r2.headers.get("content-type", "").startswith("audio/mpeg")
        assert len(r2.content) > 1000


# --- Improved Answer Scoring (case-insensitive + variations) ---
class TestImprovedScoring:
    def test_case_insensitive_and_variations(self, auth_client):
        # New attempt
        r = auth_client.post(f"{BASE_URL}/api/attempts",
                             json={"exam_id": EXAM_ID, "module": "listening"})
        assert r.status_code == 200
        attempt_id = r.json()["attempt_id"]
        # Mix case + whitespace; correct answers from seed
        answers = {
            "1": "STANDARD",        # uppercase
            "2": " 85 ",             # with whitespace
            "3": "7",
            "4": "Ground",           # mixed case
            "5": "BACK",
            "6": "2",
            "7": "15",
            "8": "thompson",         # lowercase name
            "9": "893214",
            "10": "185",
        }
        r2 = auth_client.put(f"{BASE_URL}/api/attempts/{attempt_id}/submit",
                             json={"answers": answers})
        assert r2.status_code == 200
        s = r2.json()["scores"]
        assert s["correct"] == 10, \
            f"Case-insensitive scoring failed: only {s['correct']}/10 correct. Details: {s.get('details')}"
