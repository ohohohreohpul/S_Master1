import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { API, useAuth } from "@/App";
import { Headphones, BookOpen, Pen, Mic, Clock, Flag, ArrowLeft, ArrowRight, Send, AlertCircle } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import ListeningModule from "@/components/exam/ListeningModule";
import ReadingModule from "@/components/exam/ReadingModule";
import WritingModule from "@/components/exam/WritingModule";
import SpeakingModule from "@/components/exam/SpeakingModule";

export default function ExamPage() {
  const { examId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { user } = useAuth();

  const [exam, setExam] = useState(null);
  const [phase, setPhase] = useState("loading"); // loading, preparing, ready, in_progress, completed
  const [audioProgress, setAudioProgress] = useState(0);
  const [audioCache, setAudioCache] = useState({});
  const [module, setModule] = useState(searchParams.get("module") || "listening");
  const [answers, setAnswers] = useState({});
  const [flagged, setFlagged] = useState(new Set());
  const [attemptId, setAttemptId] = useState(null);
  const [timeLeft, setTimeLeft] = useState(0);
  const timerRef = useRef(null);
  const [error, setError] = useState(null);

  const moduleConfig = {
    listening: { icon: Headphones, label: "Listening", time: 30 * 60, color: "#0070cc" },
    reading: { icon: BookOpen, label: "Reading", time: 60 * 60, color: "#1eaedb" },
    writing: { icon: Pen, label: "Writing", time: 60 * 60, color: "#0070cc" },
    speaking: { icon: Mic, label: "Speaking", time: 14 * 60, color: "#1eaedb" },
  };

  // Load exam data
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API}/exams/${examId}`, { credentials: "include" });
        if (!res.ok) throw new Error("Failed to load exam");
        const data = await res.json();
        setExam(data);

        // For listening/speaking, check if audio needs preparation
        if (module === "listening" || module === "speaking") {
          if (data.status === "ready") {
            setPhase("preloading");
            await preloadAudio(data);
          } else {
            setPhase("preparing");
            await triggerAudioGeneration();
          }
        } else {
          setPhase("ready");
        }
      } catch (e) {
        setError(e.message);
        setPhase("error");
      }
    })();
  }, [examId, module]);

  const triggerAudioGeneration = async () => {
    try {
      await fetch(`${API}/exams/${examId}/prepare`, { method: "POST", credentials: "include" });
      const interval = setInterval(async () => {
        const res = await fetch(`${API}/exams/${examId}/status`, { credentials: "include" });
        const status = await res.json();
        setAudioProgress(status.audio_progress || 0);

        if (status.status === "ready") {
          clearInterval(interval);
          // Reload exam with audio IDs
          const examRes = await fetch(`${API}/exams/${examId}`, { credentials: "include" });
          const examData = await examRes.json();
          setExam(examData);
          setPhase("preloading");
          await preloadAudio(examData);
        } else if (status.status === "audio_error" || status.status === "error") {
          clearInterval(interval);
          setError(status.error_message || "Audio generation failed");
          setPhase("error");
        }
      }, 3000);
    } catch (e) {
      setError(e.message);
      setPhase("error");
    }
  };

  const preloadAudio = async (examData) => {
    const cache = {};
    try {
      if (module === "listening") {
        const sections = examData?.listening?.sections || [];
        let loaded = 0;
        const totalAudios = sections.reduce((acc, s) => acc + (s.script_segments?.filter(seg => seg.audio_id)?.length || 0), 0);

        for (const section of sections) {
          const sectionAudios = [];
          for (const seg of (section.script_segments || [])) {
            if (seg.audio_id) {
              try {
                const res = await fetch(`${API}/audio/${seg.audio_id}`);
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                sectionAudios.push(url);
                loaded++;
                setAudioProgress(Math.round((loaded / totalAudios) * 100));
              } catch { sectionAudios.push(null); }
            }
          }
          cache[section.section_num] = sectionAudios;
        }
      } else if (module === "speaking") {
        const parts = examData?.speaking?.parts || [];
        for (const part of parts) {
          for (const q of (part.questions || [])) {
            if (q.audio_id) {
              try {
                const res = await fetch(`${API}/audio/${q.audio_id}`);
                const blob = await res.blob();
                cache[`speaking_${q.question_num}`] = URL.createObjectURL(blob);
              } catch { /* skip */ }
            }
          }
        }
      }
      setAudioCache(cache);
      setPhase("ready");
    } catch (e) {
      setError("Failed to preload audio");
      setPhase("error");
    }
  };

  const startExam = async () => {
    try {
      const res = await fetch(`${API}/attempts`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ exam_id: examId, module })
      });
      const data = await res.json();
      setAttemptId(data.attempt_id);
      setTimeLeft(moduleConfig[module].time);
      setPhase("in_progress");

      // Start timer
      timerRef.current = setInterval(() => {
        setTimeLeft(prev => {
          if (prev <= 1) {
            clearInterval(timerRef.current);
            handleSubmit();
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    } catch (e) {
      setError("Failed to start exam");
    }
  };

  const handleSubmit = async () => {
    clearInterval(timerRef.current);
    setPhase("submitting");

    try {
      if (module === "writing") {
        const res = await fetch(`${API}/attempts/${attemptId}/score-writing`, {
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ task_1: answers.task_1 || "", task_2: answers.task_2 || "" })
        });
        const data = await res.json();
        navigate(`/results/${attemptId}`, { state: { scores: data.scores, module, exam } });
      } else if (module === "speaking") {
        const res = await fetch(`${API}/attempts/${attemptId}/score-speaking`, {
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ transcriptions: answers })
        });
        const data = await res.json();
        navigate(`/results/${attemptId}`, { state: { scores: data.scores, module, exam } });
      } else {
        const res = await fetch(`${API}/attempts/${attemptId}/submit`, {
          method: "PUT", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answers })
        });
        const data = await res.json();
        navigate(`/results/${attemptId}`, { state: { scores: data.scores, module, exam } });
      }
    } catch (e) {
      setError("Failed to submit answers");
      setPhase("in_progress");
    }
  };

  const formatTime = (seconds) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  };

  const updateAnswer = useCallback((questionNum, value) => {
    setAnswers(prev => ({ ...prev, [questionNum]: value }));
  }, []);

  const toggleFlag = useCallback((questionNum) => {
    setFlagged(prev => {
      const next = new Set(prev);
      next.has(questionNum) ? next.delete(questionNum) : next.add(questionNum);
      return next;
    });
  }, []);

  // Loading phase
  if (phase === "loading") {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[var(--ps-ice)]" data-testid="exam-loading">
        <div className="text-center">
          <div className="spinner mx-auto mb-4" />
          <p className="text-[var(--ps-body-gray)]">Loading exam...</p>
        </div>
      </div>
    );
  }

  // Preparing / Preloading phase
  if (phase === "preparing" || phase === "preloading") {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[var(--ps-ice)]" data-testid="exam-preparing">
        <div className="card-ps p-10 max-w-md w-full text-center">
          <div className="w-16 h-16 rounded-2xl mx-auto mb-6 flex items-center justify-center bg-[var(--ps-blue)]/10">
            <Headphones size={32} className="text-[var(--ps-blue)]" />
          </div>
          <h2 className="display-compact mb-2" style={{ color: "var(--ps-black)" }}>
            {phase === "preparing" ? "Generating Audio" : "Loading Audio"}
          </h2>
          <p className="text-sm text-[var(--ps-body-gray)] mb-6">
            {phase === "preparing"
              ? "Creating natural audio with ElevenLabs V3. This may take a few minutes..."
              : "Preloading all audio files for seamless playback..."}
          </p>
          <Progress value={audioProgress} className="mb-3" />
          <p className="text-xs text-[var(--ps-body-gray)]">{audioProgress}% complete</p>
        </div>
      </div>
    );
  }

  // Error phase
  if (phase === "error") {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[var(--ps-ice)]" data-testid="exam-error">
        <div className="card-ps p-10 max-w-md w-full text-center">
          <AlertCircle size={48} className="text-[var(--ps-error)] mx-auto mb-4" />
          <h2 className="font-semibold text-lg mb-2">Something went wrong</h2>
          <p className="text-sm text-[var(--ps-body-gray)] mb-6">{error}</p>
          <button onClick={() => navigate("/dashboard")} className="btn-ps btn-ps-primary" style={{ padding: "10px 24px", fontSize: "0.875rem" }}>
            Back to Dashboard
          </button>
        </div>
      </div>
    );
  }

  // Ready phase
  if (phase === "ready") {
    const ModuleIcon = moduleConfig[module].icon;
    return (
      <div className="flex items-center justify-center min-h-screen bg-[var(--ps-ice)]" data-testid="exam-ready">
        <div className="card-ps p-10 max-w-lg w-full text-center">
          <div className="w-20 h-20 rounded-2xl mx-auto mb-6 flex items-center justify-center" style={{ background: `${moduleConfig[module].color}15` }}>
            <ModuleIcon size={40} style={{ color: moduleConfig[module].color }} />
          </div>
          <h2 className="display-sm mb-2" style={{ color: "var(--ps-black)" }}>
            {moduleConfig[module].label} Module
          </h2>
          <p className="text-[var(--ps-body-gray)] mb-2">{exam?.title}</p>
          <div className="flex items-center justify-center gap-4 text-sm text-[var(--ps-body-gray)] mb-8">
            <span className="flex items-center gap-1"><Clock size={14} /> {moduleConfig[module].time / 60} minutes</span>
          </div>

          {module === "listening" && (
            <div className="bg-[var(--ps-ice)] rounded-xl p-4 mb-8 text-left">
              <p className="text-sm font-medium mb-2" style={{ color: "var(--ps-charcoal)" }}>Instructions:</p>
              <ul className="text-xs text-[var(--ps-body-gray)] space-y-1">
                <li>- Audio will play automatically for each section</li>
                <li>- Audio plays once only (no replay)</li>
                <li>- All questions are visible immediately</li>
                <li>- Answer questions while you listen</li>
                <li>- All audio has been pre-loaded for seamless playback</li>
              </ul>
            </div>
          )}

          <button data-testid="begin-exam-btn" onClick={startExam} className="btn-ps btn-ps-orange" style={{ fontSize: "1rem", padding: "14px 40px" }}>
            Begin {moduleConfig[module].label} Test
          </button>
        </div>
      </div>
    );
  }

  // Submitting phase
  if (phase === "submitting") {
    return (
      <div className="flex items-center justify-center min-h-screen bg-[var(--ps-ice)]" data-testid="exam-submitting">
        <div className="text-center">
          <div className="spinner mx-auto mb-4" />
          <p className="text-[var(--ps-body-gray)]">Scoring your answers...</p>
        </div>
      </div>
    );
  }

  // In Progress phase
  const ModuleIcon = moduleConfig[module].icon;
  const timeWarning = timeLeft < 300;
  const timeCritical = timeLeft < 60;

  return (
    <div className="min-h-screen flex flex-col" data-testid="exam-in-progress">
      {/* Top bar */}
      <div className="exam-topbar" data-testid="exam-topbar">
        <div className="flex items-center gap-4">
          <ModuleIcon size={20} style={{ color: moduleConfig[module].color }} />
          <span className="font-medium">{moduleConfig[module].label}</span>
        </div>
        <div className={`timer-display ${timeCritical ? "timer-critical" : timeWarning ? "timer-warning" : ""}`} data-testid="exam-timer">
          {formatTime(timeLeft)}
        </div>
        <button data-testid="submit-exam-btn" onClick={handleSubmit} className="btn-ps btn-ps-orange" style={{ padding: "6px 20px", fontSize: "0.8rem" }}>
          <Send size={14} /> Submit
        </button>
      </div>

      {/* Module content */}
      <div className="flex-1">
        {module === "listening" && (
          <ListeningModule exam={exam} audioCache={audioCache} answers={answers} updateAnswer={updateAnswer}
            flagged={flagged} toggleFlag={toggleFlag} />
        )}
        {module === "reading" && (
          <ReadingModule exam={exam} answers={answers} updateAnswer={updateAnswer}
            flagged={flagged} toggleFlag={toggleFlag} />
        )}
        {module === "writing" && (
          <WritingModule exam={exam} answers={answers} updateAnswer={updateAnswer} />
        )}
        {module === "speaking" && (
          <SpeakingModule exam={exam} audioCache={audioCache} answers={answers} updateAnswer={updateAnswer} />
        )}
      </div>
    </div>
  );
}
