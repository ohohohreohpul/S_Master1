import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { API, useAuth } from "@/App";
import { Headphones, BookOpen, Pen, Mic, Clock, Flag, ArrowLeft, ArrowRight, Send, AlertCircle, PencilLine, X, CheckCircle } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { motion, AnimatePresence } from "framer-motion";
import ListeningModule from "@/components/exam/ListeningModule";
import ReadingModule from "@/components/exam/ReadingModule";
import WritingModule from "@/components/exam/WritingModule";
import SpeakingModule from "@/components/exam/SpeakingModule";
import SprachbausteineModule from "@/components/exam/SprachbausteineModule";

const FULL_TEST_STEPS = ["listening", "reading", "writing", "speaking"];

export default function ExamPage() {
  const { examId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { user } = useAuth();

  const fullTestMode = searchParams.get("mode") === "full_test";

  const [exam, setExam] = useState(null);
  const [phase, setPhase] = useState("loading"); // loading, preparing, ready, in_progress, completed, transitioning
  const [audioProgress, setAudioProgress] = useState(0);
  const [audioCache, setAudioCache] = useState({});
  const [module, setModule] = useState(searchParams.get("module") || (fullTestMode ? "listening" : "listening"));
  const [answers, setAnswers] = useState({});
  const [flagged, setFlagged] = useState(new Set());
  const [attemptId, setAttemptId] = useState(null);
  const [timeLeft, setTimeLeft] = useState(0);
  const timerRef = useRef(null);
  const [error, setError] = useState(null);

  // Full test state
  const [fullTestAttemptId, setFullTestAttemptId] = useState(null);
  const [fullTestStepTransition, setFullTestStepTransition] = useState(null); // { completedModule, nextModule, countdown }
  const [fullTestCurrentStep, setFullTestCurrentStep] = useState(0);
  const transitionTimerRef = useRef(null);

  // Scratch pad state
  const [scratchPadOpen, setScratchPadOpen] = useState(false);
  const [scratchPadText, setScratchPadText] = useState("");

  // Review modal state
  const [reviewOpen, setReviewOpen] = useState(false);

  const isTelc = exam?.exam_type === "telc";

  // ── TELC normalisation ───────────────────────────────────────────────────────
  // All modules read English keys (listening/reading/writing/speaking).
  // TELC exams use German keys (hoeren/lesen/schreiben/sprechen).
  // We build one normalised exam object that every module can consume.

  function normalizeTelcSections(examData) {
    const aufgaben = examData?.hoeren?.aufgaben || [];
    return aufgaben.map((aufgabe, idx) => {
      let script_segments = [];
      let questions = [];

      if (aufgabe.typ === "kurzgespraeche") {
        (aufgabe.conversations || []).forEach(conv => {
          script_segments.push(...(conv.script_segments || []));
          questions.push(...(conv.questions || []).map(q => ({
            ...q,
            question_type: "richtig_falsch",
          })));
        });
        // Also pick up any top-level script_segments (unusual but safe)
        script_segments.push(...(aufgabe.script_segments || []));
      } else if (aufgabe.typ === "gespraech") {
        script_segments = aufgabe.script_segments || [];
        questions = (aufgabe.questions || []).map(q => ({
          ...q,
          question_type: "richtig_falsch",
        }));
      } else if (aufgabe.typ === "ansagen") {
        (aufgabe.ansagen || []).forEach(ansage => {
          script_segments.push({ sprecher: ansage.sprecher || "Ansager", text: ansage.text, audio_id: ansage.audio_id });
          if (ansage.question_num) {
            questions.push({
              question_num: ansage.question_num,
              question_type: "richtig_falsch",
              question_text: ansage.question_text,
              correct_answer: ansage.correct_answer,
            });
          }
        });
      }

      return {
        section_num: idx + 1,
        title: aufgabe.title || `Teil ${idx + 1}`,
        instruction: aufgabe.instruction || "",
        heard_times: aufgabe.heard_times || (idx === 0 ? 1 : 2),
        preparation_seconds: aufgabe.preparation_seconds || (idx === 1 ? 60 : 30),
        topic: aufgabe.topic || null,
        script_segments,
        questions,
        speakers: aufgabe.sprecher || [],
      };
    });
  }

  function normalizeTelcReading(examData) {
    const aufgaben = examData?.lesen?.aufgaben || [];
    return aufgaben.map((aufgabe, idx) => {
      const isAnzeigen = aufgabe.typ === "anzeigen" || !!aufgabe.anzeigen;
      const adLetters = isAnzeigen
        ? [...(aufgabe.anzeigen || []).map(a => a.id), "x"]
        : [];
      const text = aufgabe.text ||
        (aufgabe.short_texts || []).map(t => `[${t.id}]  ${t.text}`).join("\n\n") ||
        (aufgabe.anzeigen || []).map(a => `[${a.id}]  ${a.text}`).join("\n\n") || "";
      const questions = (aufgabe.questions || []).map(q => ({
        ...q,
        // Use the situation text as the question text when available
        question_text: q.situation || q.question_text || "Welche Anzeige passt?",
        question_type: isAnzeigen ? "anzeigen_match" : (q.question_type || (q.options ? "multiple_choice" : "short_answer")),
        ad_letters: isAnzeigen ? adLetters : undefined,
        options: q.options ? (typeof q.options === "object" && !Array.isArray(q.options)
          ? Object.entries(q.options).map(([k, v]) => `${k}) ${v}`)
          : q.options) : undefined,
      }));
      return { passage_num: idx + 1, title: `Aufgabe ${aufgabe.aufgabe_num || idx + 1}`, text, questions, typ: aufgabe.typ };
    });
  }

  function normalizeTelcWriting(examData) {
    const aufgaben = examData?.schreiben?.aufgaben || [];
    return aufgaben.map((aufgabe, idx) => ({
      task_num: idx + 1,
      task_type: aufgabe.aufgabe_typ || "email",
      prompt: aufgabe.aufgabe || aufgabe.prompt || aufgabe.instruction || "",
      min_words: aufgabe.min_words || 80,
    }));
  }

  function normalizeTelcSpeaking(examData) {
    const teile = examData?.sprechen?.teile || [];
    return teile.map((teil, idx) => ({
      part_num: idx + 1,
      title: teil.titel || `Teil ${idx + 1}`,
      type: "discussion",
      instructions: teil.instructions || "",
      topic_card: teil.aufgabe || teil.thema || null,
      questions: (teil.fragen || []).map(f => ({
        question_num: f.frage_num,
        question_text: f.frage_text,
        question_type: "speaking",
        needs_audio: f.needs_audio,
        audio_id: f.audio_id,
      })),
    }));
  }

  const normalizedExam = isTelc && exam ? {
    ...exam,
    listening:       { sections: normalizeTelcSections(exam) },
    reading:         { passages: normalizeTelcReading(exam) },
    writing:         { tasks: normalizeTelcWriting(exam) },
    speaking:        { parts: normalizeTelcSpeaking(exam) },
    sprachbausteine: exam.sprachbausteine || { aufgaben: [] },
  } : exam;

  const moduleConfig = isTelc ? {
    listening:       { icon: Headphones, label: "Hören",            time: 30 * 60, color: "#0070cc" },
    reading:         { icon: BookOpen,   label: "Lesen",             time: 60 * 60, color: "#1eaedb" },
    sprachbausteine: { icon: BookOpen,   label: "Sprachbausteine",   time: 30 * 60, color: "#0070cc" },
    writing:         { icon: Pen,        label: "Schreiben",          time: 30 * 60, color: "#0070cc" },
    speaking:        { icon: Mic,        label: "Sprechen",           time: 15 * 60, color: "#1eaedb" },
  } : {
    listening: { icon: Headphones, label: "Listening", time: 30 * 60, color: "#0070cc" },
    reading: { icon: BookOpen, label: "Reading", time: 60 * 60, color: "#1eaedb" },
    writing: { icon: Pen, label: "Writing", time: 60 * 60, color: "#0070cc" },
    speaking: { icon: Mic, label: "Speaking", time: 14 * 60, color: "#1eaedb" },
  };

  // Load exam data
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const res = await fetch(`${API}/exams/${examId}`, { credentials: "include", signal: controller.signal });
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
        if (e.name !== "AbortError") {
          setError(e.message);
          setPhase("error");
        }
      }
    })();
    return () => controller.abort();
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
        // Normalise TELC hoeren → IELTS-compatible sections for preloading
        const sections = examData?.exam_type === "telc"
          ? normalizeTelcSections(examData)
          : (examData?.listening?.sections || []);

        let loaded = 0;
        const totalAudios = sections.reduce((acc, s) => {
          let count = (s.script_segments?.filter(seg => seg.audio_id)?.length || 0);
          if (s.instruction_audio_id) count++;
          return acc + count;
        }, 0);

        for (const section of sections) {
          const sectionAudios = [];
          if (section.instruction_audio_id) {
            try {
              const res = await fetch(`${API}/audio/${section.instruction_audio_id}`);
              const blob = await res.blob();
              cache[`instruction_${section.section_num}`] = URL.createObjectURL(blob);
              loaded++;
              setAudioProgress(Math.round((loaded / totalAudios) * 100));
            } catch { /* skip */ }
          }
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
      if (fullTestMode) {
        // Create full test attempt
        const res = await fetch(`${API}/attempts/full-test`, {
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ exam_id: examId })
        });
        const data = await res.json();
        setFullTestAttemptId(data.attempt_id);
        setAttemptId(data.attempt_id);
      } else {
        const res = await fetch(`${API}/attempts`, {
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ exam_id: examId, module })
        });
        const data = await res.json();
        setAttemptId(data.attempt_id);
      }

      setTimeLeft(moduleConfig[module].time);
      setPhase("in_progress");

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
      if (fullTestMode) {
        await submitFullTestModule();
      } else {
        await submitSingleModule();
      }
    } catch (e) {
      setError("Failed to submit answers");
      setPhase("in_progress");
    }
  };

  const submitSingleModule = async () => {
    if (module === "writing") {
      const endpoint = isTelc
        ? `${API}/attempts/${attemptId}/score-telc-writing`
        : `${API}/attempts/${attemptId}/score-writing`;
      const body = isTelc
        ? { aufgabe_1: answers.task_1 || "" }
        : { task_1: answers.task_1 || "", task_2: answers.task_2 || "" };
      const res = await fetch(endpoint, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
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
  };

  const submitFullTestModule = async () => {
    const currentAttemptId = fullTestAttemptId || attemptId;

    let nextModule = null;
    let scores = null;

    if (module === "writing") {
      const res = await fetch(`${API}/attempts/${currentAttemptId}/full-test/score-writing`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_1: answers.task_1 || "", task_2: answers.task_2 || "" })
      });
      const data = await res.json();
      scores = data.scores;
      // After writing, next is speaking
      const moduleRes = await fetch(`${API}/attempts/${currentAttemptId}/full-test/module`, {
        method: "PUT", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ module, answers })
      });
      const moduleData = await moduleRes.json();
      nextModule = moduleData.next_module;
    } else if (module === "speaking") {
      const res = await fetch(`${API}/attempts/${currentAttemptId}/full-test/score-speaking`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcriptions: answers })
      });
      const data = await res.json();
      scores = data.scores;
      nextModule = null; // Speaking is last
    } else {
      const res = await fetch(`${API}/attempts/${currentAttemptId}/full-test/module`, {
        method: "PUT", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ module, answers })
      });
      const data = await res.json();
      nextModule = data.next_module;
      scores = data.scores;
    }

    if (!nextModule || module === "speaking") {
      // Full test complete
      navigate(`/results/${currentAttemptId}`, { state: { mode: "full_test", exam } });
      return;
    }

    // Show transition screen
    const completedLabel = moduleConfig[module]?.label || module;
    const nextLabel = moduleConfig[nextModule]?.label || nextModule;
    setFullTestStepTransition({ completedModule: module, completedLabel, nextModule, nextLabel, countdown: 3 });
    setPhase("transitioning");

    let count = 3;
    transitionTimerRef.current = setInterval(() => {
      count--;
      setFullTestStepTransition(prev => prev ? { ...prev, countdown: count } : null);
      if (count <= 0) {
        clearInterval(transitionTimerRef.current);
        proceedToNextModule(nextModule);
      }
    }, 1000);
  };

  const proceedToNextModule = async (nextMod) => {
    setFullTestStepTransition(null);
    setAnswers({});
    setFlagged(new Set());
    const stepIdx = FULL_TEST_STEPS.indexOf(nextMod);
    setFullTestCurrentStep(stepIdx);
    setModule(nextMod);

    // Reload audio if needed
    if (nextMod === "listening" || nextMod === "speaking") {
      if (exam?.status === "ready") {
        setPhase("preloading");
        await preloadAudio(exam);
      } else {
        setPhase("ready");
      }
    } else {
      setPhase("ready");
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

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (transitionTimerRef.current) clearInterval(transitionTimerRef.current);
    };
  }, []);

  // Full test step stepper
  const FullTestStepper = () => (
    <div className="flex items-center justify-center gap-0 px-4 py-2 bg-[var(--ps-black)]" data-testid="full-test-stepper">
      {FULL_TEST_STEPS.map((step, idx) => {
        const cfg = moduleConfig[step];
        const Icon = cfg?.icon;
        const isActive = step === module;
        const isDone = FULL_TEST_STEPS.indexOf(module) > idx;
        return (
          <div key={step} className="flex items-center">
            <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[11px] font-semibold transition-all ${
              isActive
                ? "bg-[var(--ps-blue)] text-white"
                : isDone
                  ? "bg-emerald-500/20 text-emerald-400"
                  : "text-gray-500"
            }`}>
              {isDone ? <CheckCircle size={12} /> : Icon ? <Icon size={12} /> : null}
              {cfg?.label}
            </div>
            {idx < FULL_TEST_STEPS.length - 1 && (
              <div className={`w-8 h-px mx-1 ${isDone ? "bg-emerald-500/40" : "bg-gray-600"}`} />
            )}
          </div>
        );
      })}
    </div>
  );

  // Transition screen
  if (phase === "transitioning" && fullTestStepTransition) {
    const { completedLabel, nextLabel, countdown } = fullTestStepTransition;
    return (
      <div className="flex items-center justify-center min-h-screen bg-[var(--ps-dark)]" data-testid="exam-transition">
        <FullTestStepper />
        <motion.div
          key="transition"
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          className="card-ps p-12 max-w-md w-full text-center mx-4 absolute">
          <div className="w-16 h-16 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-6">
            <CheckCircle size={32} className="text-emerald-500" />
          </div>
          <h2 className="display-compact mb-2" style={{ color: "var(--ps-black)" }}>
            {completedLabel} Complete!
          </h2>
          <p className="text-[var(--ps-body-gray)] mb-6">
            Next: <span className="font-semibold text-[var(--ps-blue)]">{nextLabel}</span>
          </p>
          <div className="text-5xl font-extralight text-[var(--ps-blue)] mb-4">{countdown}</div>
          <p className="text-xs text-[var(--ps-body-gray)]">Starting next module automatically...</p>
        </motion.div>
      </div>
    );
  }

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
              ? "Generating audio for this exam. This may take a few minutes..."
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
    const ModuleIcon = moduleConfig[module]?.icon || BookOpen;
    return (
      <div className="flex items-center justify-center min-h-screen bg-[var(--ps-ice)]" data-testid="exam-ready">
        {fullTestMode && (
          <div className="fixed top-0 left-0 right-0 z-10">
            <FullTestStepper />
          </div>
        )}
        <div className={`card-ps p-10 max-w-lg w-full text-center ${fullTestMode ? "mt-14" : ""}`}>
          <div className="w-20 h-20 rounded-2xl mx-auto mb-6 flex items-center justify-center" style={{ background: `${moduleConfig[module]?.color || "#0070cc"}15` }}>
            <ModuleIcon size={40} style={{ color: moduleConfig[module]?.color || "#0070cc" }} />
          </div>
          <h2 className="display-sm mb-2" style={{ color: "var(--ps-black)" }}>
            {moduleConfig[module]?.label} {isTelc ? "Prüfung" : "Module"}
          </h2>
          <p className="text-[var(--ps-body-gray)] mb-2">{exam?.title}</p>
          {fullTestMode && (
            <span className="inline-block px-3 py-1 rounded-full text-xs font-semibold bg-[var(--ps-blue)]/10 text-[var(--ps-blue)] mb-4">
              Full Test Mode — Step {FULL_TEST_STEPS.indexOf(module) + 1} of 4
            </span>
          )}
          <div className="flex items-center justify-center gap-4 text-sm text-[var(--ps-body-gray)] mb-8">
            <span className="flex items-center gap-1"><Clock size={14} /> {(moduleConfig[module]?.time || 1800) / 60} minutes</span>
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
            Begin {moduleConfig[module]?.label} {isTelc ? "Prüfung" : "Test"}
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
  const ModuleIcon = moduleConfig[module]?.icon || BookOpen;
  const timeWarning = timeLeft < 300;
  const timeCritical = timeLeft < 60;

  // Flagged questions for review
  const flaggedList = Array.from(flagged);

  return (
    <div className="min-h-screen flex flex-col" data-testid="exam-in-progress">
      {/* Full test stepper */}
      {fullTestMode && <FullTestStepper />}

      {/* Top bar */}
      <div className="exam-topbar" data-testid="exam-topbar">
        <div className="flex items-center gap-4">
          <ModuleIcon size={20} style={{ color: moduleConfig[module]?.color || "#0070cc" }} />
          <span className="font-medium">{moduleConfig[module]?.label}</span>
          {flagged.size > 0 && (
            <button
              onClick={() => setReviewOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium bg-[var(--ps-orange)]/15 text-[var(--ps-orange)] border border-[var(--ps-orange)]/30 hover:bg-[var(--ps-orange)]/25 transition-colors"
              data-testid="review-flagged-btn">
              <Flag size={11} /> Review ({flagged.size})
            </button>
          )}
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
          <ListeningModule exam={normalizedExam} audioCache={audioCache} answers={answers} updateAnswer={updateAnswer}
            flagged={flagged} toggleFlag={toggleFlag} />
        )}
        {module === "sprachbausteine" && (
          <SprachbausteineModule exam={normalizedExam} answers={answers} updateAnswer={updateAnswer}
            flagged={flagged} toggleFlag={toggleFlag} />
        )}
        {module === "reading" && (
          <ReadingModule exam={normalizedExam} answers={answers} updateAnswer={updateAnswer}
            flagged={flagged} toggleFlag={toggleFlag} />
        )}
        {module === "writing" && (
          <WritingModule exam={normalizedExam} answers={answers} updateAnswer={updateAnswer} />
        )}
        {module === "speaking" && (
          <SpeakingModule exam={normalizedExam} audioCache={audioCache} answers={answers} updateAnswer={updateAnswer} />
        )}
      </div>

      {/* Scratch Pad floating button */}
      <div className="fixed bottom-6 right-6 z-40 flex flex-col items-end gap-3">
        <AnimatePresence>
          {scratchPadOpen && (
            <motion.div
              initial={{ opacity: 0, y: 10, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.95 }}
              transition={{ duration: 0.2 }}
              className="bg-white rounded-2xl shadow-2xl border border-[var(--ps-divider)] overflow-hidden"
              style={{ width: 320 }}
              data-testid="scratch-pad-panel">
              <div className="flex items-center justify-between px-4 py-3 bg-[var(--ps-black)]">
                <div className="flex items-center gap-2">
                  <PencilLine size={14} className="text-[var(--ps-cyan)]" />
                  <span className="text-xs font-semibold text-white">Scratch Pad</span>
                </div>
                <button onClick={() => setScratchPadOpen(false)} className="text-gray-400 hover:text-white transition-colors">
                  <X size={14} />
                </button>
              </div>
              <textarea
                className="w-full h-48 p-4 text-sm resize-none focus:outline-none text-[var(--ps-charcoal)] placeholder-gray-300"
                placeholder="Write notes here..."
                value={scratchPadText}
                onChange={e => setScratchPadText(e.target.value)}
                data-testid="scratch-pad-textarea"
              />
              <div className="px-4 py-2 border-t border-[var(--ps-divider)] flex justify-end">
                <span className="text-[10px] text-[var(--ps-mute)]">Notes are not submitted</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <motion.button
          whileHover={{ scale: 1.08 }} whileTap={{ scale: 0.95 }}
          onClick={() => setScratchPadOpen(v => !v)}
          className={`w-12 h-12 rounded-full shadow-lg flex items-center justify-center transition-colors ${
            scratchPadOpen
              ? "bg-[var(--ps-blue)] text-white"
              : "bg-white border border-[var(--ps-divider)] text-[var(--ps-body-gray)] hover:text-[var(--ps-blue)]"
          }`}
          data-testid="scratch-pad-btn"
          title="Scratch Pad">
          <PencilLine size={18} />
        </motion.button>
      </div>

      {/* Review Flagged Modal */}
      <AnimatePresence>
        {reviewOpen && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
            onClick={() => setReviewOpen(false)}
            data-testid="review-modal-overlay">
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.95, opacity: 0 }}
              className="card-ps p-6 max-w-sm w-full mx-4"
              onClick={e => e.stopPropagation()}
              data-testid="review-modal">
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-semibold text-base" style={{ color: "var(--ps-charcoal)" }}>
                  Flagged Questions
                </h3>
                <button onClick={() => setReviewOpen(false)} className="text-[var(--ps-mute)] hover:text-[var(--ps-charcoal)]">
                  <X size={16} />
                </button>
              </div>
              {flaggedList.length === 0 ? (
                <p className="text-sm text-[var(--ps-body-gray)]">No flagged questions.</p>
              ) : (
                <div className="space-y-2">
                  {flaggedList.map(qNum => (
                    <div key={qNum} className="flex items-center justify-between p-3 rounded-xl bg-[var(--ps-ice)] border border-[var(--ps-orange)]/20">
                      <span className="text-sm font-medium" style={{ color: "var(--ps-charcoal)" }}>Question {qNum}</span>
                      <div className="flex items-center gap-2">
                        <span className={`text-xs ${answers[qNum] ? "text-emerald-600" : "text-[var(--ps-mute)]"}`}>
                          {answers[qNum] ? "Answered" : "Unanswered"}
                        </span>
                        <button onClick={() => toggleFlag(qNum)} className="text-[var(--ps-orange)] hover:text-red-500 transition-colors">
                          <Flag size={14} className="fill-[var(--ps-orange)]" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              <button
                onClick={() => setReviewOpen(false)}
                className="btn-ps btn-ps-primary w-full mt-5"
                style={{ padding: "10px", fontSize: "0.875rem" }}>
                Back to Exam
              </button>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
