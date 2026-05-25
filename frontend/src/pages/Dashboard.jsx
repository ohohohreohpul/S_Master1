import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth, API } from "@/App";
import {
  BookOpen, Headphones, Pen, Mic, ArrowRight, LogOut, Play, Plus,
  Clock, Target, Settings, TrendingUp, Lock, Crown, ChevronDown,
  Languages, CheckCircle2, Trash2, AlertCircle, RotateCcw,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

const fadeUp = { hidden: { opacity: 0, y: 20 }, show: { opacity: 1, y: 0 } };
const stagger = { hidden: {}, show: { transition: { staggerChildren: 0.08 } } };

const IELTS_MODULES = [
  { key: "listening", label: "Listening", sub: "30 min · 40 questions", icon: Headphones },
  { key: "reading",   label: "Reading",   sub: "60 min · 40 questions", icon: BookOpen },
  { key: "writing",   label: "Writing",   sub: "60 min · 2 tasks",      icon: Pen },
  { key: "speaking",  label: "Speaking",  sub: "14 min · 3 parts",      icon: Mic },
];

const TELC_MODULES = [
  { key: "listening",       label: "Hören",           sub: "~30 min · 20 Fragen", icon: Headphones },
  { key: "reading",         label: "Lesen",           sub: "60 min · 20 Fragen",  icon: BookOpen },
  { key: "sprachbausteine", label: "Sprachbausteine", sub: "30 min · 20 Fragen",  icon: Languages },
  { key: "writing",         label: "Schreiben",       sub: "30 min · 1 Aufgabe",  icon: Pen },
  { key: "speaking",        label: "Sprechen",        sub: "~15 min · 3 Teile",   icon: Mic },
];

const TELC_LEVELS = ["B1", "B2"];

function ExamTypeCard({ type, selected, onSelect }) {
  const isIelts = type === "ielts";
  return (
    <motion.button
      onClick={onSelect}
      whileHover={{ y: -3 }}
      whileTap={{ scale: 0.98 }}
      className="relative text-left rounded-2xl overflow-hidden border-2 transition-all duration-200 focus:outline-none"
      style={{
        borderColor: selected
          ? (isIelts ? "var(--ps-blue)" : "#f59e0b")
          : "var(--ps-divider)",
        boxShadow: selected
          ? (isIelts
              ? "0 0 0 4px rgba(0,112,204,0.12), 0 8px 32px rgba(0,112,204,0.1)"
              : "0 0 0 4px rgba(245,158,11,0.12), 0 8px 32px rgba(245,158,11,0.1)")
          : "0 2px 12px rgba(0,0,0,0.04)",
        background: "white",
      }}
    >
      {/* Top dark header */}
      <div
        className="px-5 pt-5 pb-4"
        style={{
          background: isIelts
            ? "linear-gradient(135deg, #0a1628 0%, #0c1f3d 100%)"
            : "linear-gradient(135deg, #1a1200 0%, #2d1f00 100%)",
        }}
      >
        <div className="flex items-start justify-between mb-3">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center"
            style={{ background: isIelts ? "var(--ps-blue)" : "#f59e0b" }}
          >
            {isIelts
              ? <BookOpen size={18} className="text-white" />
              : <Languages size={18} className="text-white" />}
          </div>
          {selected && (
            <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 400 }}>
              <CheckCircle2 size={20} style={{ color: isIelts ? "var(--ps-cyan)" : "#fbbf24" }} />
            </motion.div>
          )}
        </div>
        <p className="text-white font-semibold text-base leading-tight">
          {isIelts ? "IELTS Academic" : "TELC Deutsch"}
        </p>
        <p className="text-gray-400 text-xs mt-0.5">
          {isIelts ? "English proficiency · Band 1–9" : "German certificate · A1–C2"}
        </p>
      </div>

      {/* Module list — cap at 4 to keep card height consistent */}
      <div className="px-5 py-4 space-y-2">
        {(isIelts ? IELTS_MODULES : TELC_MODULES).slice(0, 4).map(({ key, label, sub, icon: Icon }) => (
          <div key={key} className="flex items-center gap-3">
            <div
              className="w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0"
              style={{ background: isIelts ? "rgba(0,112,204,0.08)" : "rgba(245,158,11,0.08)" }}
            >
              <Icon size={11} style={{ color: isIelts ? "var(--ps-blue)" : "#f59e0b" }} />
            </div>
            <span className="text-xs font-semibold" style={{ color: "var(--ps-charcoal)" }}>{label}</span>
            <span className="text-[10px] ml-auto" style={{ color: "var(--ps-body-gray)" }}>{sub}</span>
          </div>
        ))}
      </div>

      {/* Selected indicator bar */}
      <div
        className="h-[3px] transition-opacity duration-200"
        style={{
          background: isIelts
            ? "linear-gradient(to right, var(--ps-blue), var(--ps-cyan))"
            : "linear-gradient(to right, #f59e0b, #fbbf24)",
          opacity: selected ? 1 : 0,
        }}
      />
    </motion.button>
  );
}

function StatusBadge({ status }) {
  const styles = {
    ready:             "bg-emerald-50 text-emerald-700 border-emerald-200",
    generating_audio:  "bg-amber-50 text-amber-700 border-amber-200",
    generating_content:"bg-amber-50 text-amber-700 border-amber-200",
    pending_audio:     "bg-sky-50 text-sky-700 border-sky-200",
  };
  const labels = {
    ready:             "Ready",
    generating_audio:  "Generating audio…",
    generating_content:"Generating…",
    pending_audio:     "Pending audio",
  };
  return (
    <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-semibold border ${styles[status] || "bg-red-50 text-red-700 border-red-200"}`}>
      {labels[status] || status}
    </span>
  );
}

function ExamCard({ exam, isPro, onNavigate, onDelete }) {
  const isTelc = exam.exam_type === "telc";
  const isReady = exam.status === "ready" || exam.status === "pending_audio";
  const isError = exam.status === "error" || exam.status === "audio_error";
  const isGenerating = exam.status === "generating_content" || exam.status === "generating_audio";
  const modules = isTelc ? TELC_MODULES : IELTS_MODULES;
  const accent = isTelc ? "#f59e0b" : "var(--ps-blue)";
  const level = isTelc ? (exam.telc_level || "") : "Academic";

  return (
    <motion.div
      variants={fadeUp}
      whileHover={isReady ? { y: -3 } : {}}
      className="rounded-2xl overflow-hidden flex flex-col"
      style={{
        background: "white",
        boxShadow: "0 1px 3px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04)",
        border: isError ? "1.5px solid #fecaca" : "1.5px solid var(--ps-divider)",
        transition: "box-shadow 250ms, transform 250ms",
      }}
      data-testid={`exam-card-${exam.exam_id}`}
    >
      {/* ── Header ── */}
      <div
        className="px-5 pt-5 pb-4 relative overflow-hidden"
        style={{
          background: isError
            ? "linear-gradient(135deg, #1a0505 0%, #2d0a0a 100%)"
            : isTelc
              ? "linear-gradient(135deg, #1a1000 0%, #2d1c00 100%)"
              : "linear-gradient(135deg, #040d1a 0%, #0a1628 100%)",
        }}
      >
        {/* Subtle dot grid */}
        <div className="absolute inset-0 opacity-[0.04]"
          style={{ backgroundImage: "radial-gradient(circle at 1px 1px, white 1px, transparent 0)", backgroundSize: "24px 24px" }} />

        <div className="relative flex items-start justify-between mb-3">
          {/* Icon badge */}
          <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{ background: isError ? "#ef4444" : accent }}>
            {isError
              ? <AlertCircle size={16} className="text-white" />
              : isTelc
                ? <Languages size={16} className="text-white" />
                : <BookOpen size={16} className="text-white" />}
          </div>
          {/* Level pill */}
          <span className="text-[10px] font-bold px-2.5 py-1 rounded-full"
            style={{
              background: isError ? "rgba(239,68,68,0.2)" : isTelc ? "rgba(245,158,11,0.18)" : "rgba(0,112,204,0.2)",
              color: isError ? "#fca5a5" : isTelc ? "#fbbf24" : "#60a5fa",
            }}>
            {isTelc ? `TELC ${level}` : "IELTS Academic"}
          </span>
        </div>

        <h3 className="font-semibold text-sm text-white leading-snug">
          {exam.title}
        </h3>

        {!isError && (
          <div className="flex items-center gap-3 mt-2">
            <span className="flex items-center gap-1 text-[10px] font-medium" style={{ color: "rgba(255,255,255,0.45)" }}>
              <Clock size={9} /> {isTelc ? "~2h 45m" : "~2h 45m"}
            </span>
            <span className="flex items-center gap-1 text-[10px] font-medium" style={{ color: "rgba(255,255,255,0.45)" }}>
              <Target size={9} /> {modules.length} modules
            </span>
            <span className="ml-auto">
              <StatusBadge status={exam.status} />
            </span>
          </div>
        )}
      </div>

      {/* ── Body ── */}
      <div className="p-4 flex flex-col gap-3 flex-1">

        {/* Error state */}
        {isError && (
          <div className="flex-1 flex flex-col gap-3">
            <div className="p-3 rounded-xl bg-red-50 border border-red-100">
              <p className="text-xs font-semibold text-red-700 mb-0.5">Generation failed</p>
              <p className="text-[10px] text-red-500 leading-relaxed">
                {exam.error_message || "The server restarted while generating. Delete and try again."}
              </p>
            </div>
            <motion.button whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}
              onClick={() => onDelete(exam.exam_id)}
              className="flex items-center justify-center gap-2 w-full py-2.5 rounded-xl text-xs font-semibold text-red-600 border border-red-200 bg-red-50 hover:bg-red-100 transition-colors">
              <Trash2 size={13} /> Delete Exam
            </motion.button>
          </div>
        )}

        {/* Generating state */}
        {isGenerating && (
          <div className="flex-1 flex flex-col items-center justify-center py-4 gap-2">
            <div className="spinner" style={{ width: 20, height: 20 }} />
            <p className="text-[11px] font-medium" style={{ color: "var(--ps-body-gray)" }}>
              {exam.status === "generating_audio" ? `Generating audio… ${exam.audio_progress || 0}%` : "Generating content…"}
            </p>
            {exam.status === "generating_audio" && (
              <div className="w-full bg-gray-100 rounded-full h-1.5 mt-1">
                <div className="h-1.5 rounded-full transition-all duration-300"
                  style={{ width: `${exam.audio_progress || 0}%`, background: accent }} />
              </div>
            )}
          </div>
        )}

        {/* Ready state */}
        {isReady && (
          <>
            {/* Module chips */}
            <div className="flex flex-wrap gap-1.5">
              {modules.map(({ key, label, icon: Icon }) => (
                <motion.button key={key}
                  whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.96 }}
                  data-testid={`start-${key}-${exam.exam_id}`}
                  onClick={() => onNavigate(`/exam/${exam.exam_id}?module=${key}`)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[11px] font-semibold border transition-all"
                  style={{ borderColor: "var(--ps-divider)", color: "var(--ps-charcoal)", background: "var(--ps-ice)" }}
                  onMouseEnter={e => {
                    e.currentTarget.style.background = isTelc ? "rgba(245,158,11,0.08)" : "rgba(0,112,204,0.06)";
                    e.currentTarget.style.borderColor = isTelc ? "rgba(245,158,11,0.4)" : "rgba(0,112,204,0.3)";
                    e.currentTarget.style.color = isTelc ? "#d97706" : "var(--ps-blue)";
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.background = "var(--ps-ice)";
                    e.currentTarget.style.borderColor = "var(--ps-divider)";
                    e.currentTarget.style.color = "var(--ps-charcoal)";
                  }}>
                  <Icon size={11} />
                  {label}
                </motion.button>
              ))}
            </div>

            {/* CTA buttons */}
            {isTelc ? (
              <motion.button whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}
                data-testid={`start-full-${exam.exam_id}`}
                onClick={() => onNavigate(`/exam/${exam.exam_id}?module=listening`)}
                className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-semibold text-white transition-colors"
                style={{ background: accent, fontSize: "0.82rem" }}>
                <Play size={14} /> Prüfung starten
              </motion.button>
            ) : (
              <div className="flex gap-2">
                <motion.button whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}
                  data-testid={`start-full-test-${exam.exam_id}`}
                  onClick={() => { if (!isPro) { onNavigate("/pricing"); return; } onNavigate(`/exam/${exam.exam_id}?mode=full_test`); }}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2.5 rounded-xl text-xs font-semibold text-white transition-colors"
                  style={{ background: isPro ? "var(--ps-blue)" : "#94a3b8", fontSize: "0.78rem" }}>
                  {!isPro ? <Lock size={12} /> : <TrendingUp size={12} />}
                  {isPro ? "Full Test" : "Full Test (Pro)"}
                </motion.button>
                <motion.button whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}
                  data-testid={`start-full-${exam.exam_id}`}
                  onClick={() => onNavigate(`/exam/${exam.exam_id}?module=listening`)}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2.5 rounded-xl text-xs font-semibold border border-[var(--ps-blue)] text-[var(--ps-blue)] hover:bg-[var(--ps-blue)] hover:text-white transition-colors"
                  style={{ fontSize: "0.78rem" }}>
                  <Play size={12} /> By Module
                </motion.button>
              </div>
            )}
          </>
        )}
      </div>
    </motion.div>
  );
}

export default function Dashboard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const [exams, setExams] = useState([]);
  const [progress, setProgress] = useState(null);
  const [loading, setLoading] = useState(true);
  const [subscription, setSubscription] = useState(null);

  const [selectedType, setSelectedType] = useState("ielts");
  const [generating, setGenerating] = useState(false);
  const [generatingTelc, setGeneratingTelc] = useState(false);
  const [showTelcDropdown, setShowTelcDropdown] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    const safe = (r) => r.ok ? r.json() : null;

    Promise.all([
      fetch(`${API}/exams`, { credentials: "include", signal }).then(r => r.json()),
      fetch(`${API}/progress`, { credentials: "include", signal }).then(safe).catch(() => null),
      fetch(`${API}/subscription/status`, { credentials: "include", signal }).then(safe).catch(() => null),
    ]).then(([examsData, progressData, subData]) => {
      setExams(Array.isArray(examsData) ? examsData : []);
      setProgress(progressData);
      setSubscription(subData);
      setLoading(false);
    }).catch((err) => {
      if (err.name !== "AbortError") setLoading(false);
    });

    return () => controller.abort();
  }, []);

  const generateExam = async () => {
    setGenerating(true);
    try {
      const res = await fetch(`${API}/exams/generate`, { method: "POST", credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        const interval = setInterval(async () => {
          const statusRes = await fetch(`${API}/exams/${data.exam_id}/status`, { credentials: "include" });
          const status = await statusRes.json();
          if (["ready", "error", "audio_error"].includes(status.status)) {
            clearInterval(interval);
            setGenerating(false);
            fetch(`${API}/exams`, { credentials: "include" }).then(r => r.json()).then(setExams);
          }
        }, 5000);
      } else {
        setGenerating(false);
      }
    } catch { setGenerating(false); }
  };

  const generateTelcExam = async (level) => {
    setGeneratingTelc(true);
    setShowTelcDropdown(false);
    try {
      const res = await fetch(`${API}/exams/generate-telc`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ level }),
      });
      if (res.ok) {
        const data = await res.json();
        const interval = setInterval(async () => {
          const statusRes = await fetch(`${API}/exams/${data.exam_id}/status`, { credentials: "include" });
          const status = await statusRes.json();
          if (["ready", "error", "audio_error"].includes(status.status)) {
            clearInterval(interval);
            setGeneratingTelc(false);
            fetch(`${API}/exams`, { credentials: "include" }).then(r => r.json()).then(setExams);
          }
        }, 5000);
      } else {
        setGeneratingTelc(false);
      }
    } catch { setGeneratingTelc(false); }
  };

  const handleLogout = async () => { await logout(); navigate("/"); };

  const isPro = subscription?.tier === "pro" || user?.is_admin === true;

  const filteredExams = exams.filter(exam => {
    if (selectedType === "ielts") return !exam.exam_type || exam.exam_type === "ielts";
    if (selectedType === "telc")  return exam.exam_type === "telc";
    return true;
  });

  const moduleIcons = { listening: Headphones, reading: BookOpen, writing: Pen, speaking: Mic };

  if (loading) return (
    <div className="flex items-center justify-center min-h-screen bg-[var(--ps-ice)]" data-testid="dashboard-loading">
      <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="text-center">
        <div className="spinner mx-auto mb-4" />
        <p className="text-sm" style={{ color: "var(--ps-body-gray)" }}>Loading dashboard…</p>
      </motion.div>
    </div>
  );

  return (
    <div data-testid="dashboard-page" className="min-h-screen bg-[var(--ps-ice)]">

      {/* Nav */}
      <nav className="nav-ps" data-testid="dashboard-nav">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-[var(--ps-blue)] flex items-center justify-center">
            <BookOpen size={16} className="text-white" />
          </div>
          <span className="font-semibold text-base">IELTS Pro</span>
        </div>
        <div className="flex items-center gap-4">
          {user?.is_admin && (
            <button data-testid="admin-link" onClick={() => navigate("/admin")}
              className="text-gray-400 hover:text-[var(--ps-cyan)] transition-colors" title="Admin Panel">
              <Settings size={18} />
            </button>
          )}
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-400">{user?.name}</span>
            {isPro && (
              <span className="flex items-center gap-1 bg-[var(--ps-blue)]/20 text-[var(--ps-cyan)] text-[10px] font-bold px-2 py-0.5 rounded-full border border-[var(--ps-blue)]/30">
                <Crown size={10} /> Pro
              </span>
            )}
          </div>
          <button data-testid="logout-btn" onClick={handleLogout}
            className="flex items-center gap-1 text-xs text-gray-400 hover:text-[var(--ps-cyan)] transition-colors">
            <LogOut size={14} /> Sign out
          </button>
        </div>
      </nav>

      <motion.div className="max-w-5xl mx-auto px-6 py-10" initial="hidden" animate="show" variants={stagger}>

        {/* Welcome */}
        <motion.div variants={fadeUp} className="mb-8">
          <h1 className="display-compact" style={{ color: "var(--ps-black)" }}>
            Welcome back{user?.name ? `, ${user.name.split(" ")[0]}` : ""}
          </h1>
          <p className="text-sm mt-1" style={{ color: "var(--ps-body-gray)" }}>
            Choose your exam and start practicing
          </p>
        </motion.div>

        {/* ── Exam type selector ── */}
        <motion.section variants={fadeUp} className="mb-10">
          <p className="text-xs font-semibold uppercase tracking-widest mb-4" style={{ color: "var(--ps-body-gray)" }}>
            What are you practicing today?
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <ExamTypeCard type="ielts" selected={selectedType === "ielts"} onSelect={() => setSelectedType("ielts")} />
            <ExamTypeCard type="telc"  selected={selectedType === "telc"}  onSelect={() => { if (isPro) setSelectedType("telc"); else navigate("/pricing"); }} />
          </div>
          {!isPro && (
            <p className="text-xs mt-3 flex items-center gap-1.5" style={{ color: "var(--ps-body-gray)" }}>
              <Lock size={11} style={{ color: "var(--ps-blue)" }} />
              TELC Deutsch requires a Pro subscription.{" "}
              <button onClick={() => navigate("/pricing")}
                className="font-semibold hover:underline" style={{ color: "var(--ps-blue)" }}>
                Upgrade now
              </button>
            </p>
          )}
        </motion.section>

        {/* ── Progress ── */}
        {progress && progress.total_attempts > 0 && (
          <motion.div variants={fadeUp} className="mb-10" data-testid="progress-section">
            <p className="text-xs font-semibold uppercase tracking-widest mb-4" style={{ color: "var(--ps-body-gray)" }}>
              Your Progress
            </p>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <motion.div whileHover={{ y: -2 }}
                className="card-ps p-5 text-center col-span-2 md:col-span-1 relative overflow-hidden">
                <div className="absolute inset-0 bg-gradient-to-br from-[var(--ps-blue)]/5 to-transparent" />
                <div className="relative">
                  <div className="text-3xl font-light mb-1" style={{ color: "var(--ps-blue)" }}>
                    {progress.overall_estimated_band || "—"}
                  </div>
                  <div className="text-[10px] font-medium uppercase tracking-wider" style={{ color: "var(--ps-body-gray)" }}>
                    Overall Band
                  </div>
                </div>
              </motion.div>
              {["listening", "reading", "writing", "speaking"].map(m => {
                const Icon = moduleIcons[m];
                const stats = progress.modules?.[m];
                return (
                  <motion.div key={m} whileHover={{ y: -2 }} className="card-ps p-5" data-testid={`progress-${m}`}>
                    <div className="flex items-center gap-2 mb-3">
                      <Icon size={13} style={{ color: "var(--ps-blue)" }} />
                      <span className="text-xs font-semibold capitalize tracking-wide">{m}</span>
                    </div>
                    <div className="text-2xl font-light" style={{ color: "var(--ps-charcoal)" }}>
                      {stats?.latest_band || "—"}
                    </div>
                    <div className="text-[10px] mt-1" style={{ color: "var(--ps-body-gray)" }}>
                      {stats?.attempts ?? 0} attempt{stats?.attempts !== 1 ? "s" : ""}
                    </div>
                  </motion.div>
                );
              })}
            </div>
          </motion.div>
        )}

        {/* ── Practice tests ── */}
        <motion.section variants={fadeUp}>
          <div className="flex items-center justify-between mb-5">
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest mb-0.5" style={{ color: "var(--ps-body-gray)" }}>
                Practice Tests
              </p>
              <h2 className="font-semibold text-lg" style={{ color: "var(--ps-black)" }}>
                {selectedType === "ielts" ? "IELTS Academic" : "TELC Deutsch"}
              </h2>
            </div>

            {/* Generate button */}
            {selectedType === "ielts" ? (
              <button
                data-testid="generate-exam-btn"
                onClick={generateExam}
                disabled={generating}
                className="btn-ps btn-ps-secondary flex items-center gap-2"
                style={{ padding: "8px 20px", fontSize: "0.8rem" }}
              >
                {generating
                  ? <><div className="spinner" style={{ width: 14, height: 14 }} /> Generating…</>
                  : <><Plus size={14} /> New IELTS Test</>}
              </button>
            ) : (
              <div className="relative">
                <button
                  data-testid="generate-telc-btn"
                  onClick={() => setShowTelcDropdown(v => !v)}
                  disabled={generatingTelc || !isPro}
                  className="btn-ps btn-ps-secondary flex items-center gap-2"
                  style={{ padding: "8px 16px", fontSize: "0.8rem" }}
                >
                  {generatingTelc
                    ? <><div className="spinner" style={{ width: 14, height: 14 }} /> Generating…</>
                    : <><Plus size={14} /> New TELC Test <ChevronDown size={12} /></>}
                </button>
                <AnimatePresence>
                  {showTelcDropdown && isPro && (
                    <motion.div
                      initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
                      className="absolute right-0 top-full mt-1 bg-white rounded-xl border shadow-lg z-20 overflow-hidden min-w-[120px]"
                      style={{ borderColor: "var(--ps-divider)" }}
                    >
                      {TELC_LEVELS.map(level => (
                        <button key={level} onClick={() => generateTelcExam(level)}
                          className="w-full text-left px-4 py-2.5 text-sm font-medium hover:bg-[var(--ps-ice)] transition-colors"
                          style={{ color: "var(--ps-charcoal)" }}>
                          TELC {level}
                        </button>
                      ))}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )}
          </div>

          <AnimatePresence mode="wait">
            <motion.div
              key={selectedType}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.18 }}
            >
              {filteredExams.length === 0 ? (
                <div className="card-ps p-14 text-center">
                  <div className="w-16 h-16 rounded-2xl mx-auto mb-4 flex items-center justify-center"
                    style={{ background: selectedType === "telc" ? "rgba(245,158,11,0.08)" : "rgba(0,112,204,0.05)" }}>
                    {selectedType === "telc"
                      ? <Languages size={28} style={{ color: "#f59e0b" }} />
                      : <BookOpen size={28} style={{ color: "var(--ps-blue)" }} />}
                  </div>
                  <p className="mb-1 font-medium text-sm" style={{ color: "var(--ps-charcoal)" }}>
                    No {selectedType === "telc" ? "TELC" : "IELTS"} tests yet
                  </p>
                  <p className="text-xs mb-5" style={{ color: "var(--ps-body-gray)" }}>
                    Generate your first practice test to get started
                  </p>
                  {selectedType === "ielts" ? (
                    <button onClick={generateExam} disabled={generating}
                      className="btn-ps btn-ps-primary" style={{ padding: "10px 24px", fontSize: "0.8rem" }}>
                      {generating
                        ? <><div className="spinner" style={{ width: 14, height: 14 }} /> Generating…</>
                        : <><Plus size={14} /> Generate IELTS Test</>}
                    </button>
                  ) : (
                    <div className="relative inline-block">
                      <button onClick={() => setShowTelcDropdown(v => !v)} disabled={generatingTelc || !isPro}
                        className="btn-ps flex items-center gap-2"
                        style={{ padding: "10px 24px", fontSize: "0.8rem", background: "#f59e0b", color: "white" }}>
                        {generatingTelc
                          ? <><div className="spinner" style={{ width: 14, height: 14 }} /> Generating…</>
                          : <><Plus size={14} /> Generate TELC Test <ChevronDown size={12} /></>}
                      </button>
                      <AnimatePresence>
                        {showTelcDropdown && isPro && (
                          <motion.div
                            initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
                            className="absolute left-0 top-full mt-1 bg-white rounded-xl border shadow-lg z-20 overflow-hidden min-w-[120px]"
                            style={{ borderColor: "var(--ps-divider)" }}
                          >
                            {TELC_LEVELS.map(level => (
                              <button key={level} onClick={() => generateTelcExam(level)}
                                className="w-full text-left px-4 py-2.5 text-sm font-medium hover:bg-[var(--ps-ice)] transition-colors"
                                style={{ color: "var(--ps-charcoal)" }}>
                                TELC {level}
                              </button>
                            ))}
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>
                  )}
                </div>
              ) : (
                <motion.div
                  className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5"
                  variants={stagger} initial="hidden" animate="show"
                >
                  {filteredExams.map(exam => (
                    <ExamCard
                      key={exam.exam_id}
                      exam={exam}
                      isPro={isPro}
                      onNavigate={navigate}
                      onDelete={async (examId) => {
                        await fetch(`${API}/admin/exams/${examId}`, { method: "DELETE", credentials: "include" });
                        setExams(prev => prev.filter(e => e.exam_id !== examId));
                      }}
                    />
                  ))}
                </motion.div>
              )}
            </motion.div>
          </AnimatePresence>
        </motion.section>

      </motion.div>
    </div>
  );
}
