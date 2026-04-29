import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth, API } from "@/App";
import { BookOpen, Headphones, Pen, Mic, ArrowRight, LogOut, Play, Plus, Clock, Target, Settings, TrendingUp } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { motion } from "framer-motion";

const fadeUp = { hidden: { opacity: 0, y: 20 }, show: { opacity: 1, y: 0 } };
const stagger = { hidden: {}, show: { transition: { staggerChildren: 0.08 } } };

export default function Dashboard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [exams, setExams] = useState([]);
  const [progress, setProgress] = useState(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    Promise.all([
      fetch(`${API}/exams`, { credentials: "include" }).then(r => r.json()),
      fetch(`${API}/progress`, { credentials: "include" }).then(r => r.ok ? r.json() : null).catch(() => null)
    ]).then(([examsData, progressData]) => {
      setExams(examsData);
      setProgress(progressData);
      setLoading(false);
    });
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
          if (status.status === "ready" || status.status === "error" || status.status === "audio_error") {
            clearInterval(interval);
            setGenerating(false);
            const newExams = await fetch(`${API}/exams`, { credentials: "include" }).then(r => r.json());
            setExams(newExams);
          }
        }, 5000);
      }
    } catch { setGenerating(false); }
  };

  const moduleIcons = { listening: Headphones, reading: BookOpen, writing: Pen, speaking: Mic };
  const handleLogout = async () => { await logout(); navigate("/"); };

  if (loading) return (
    <div className="flex items-center justify-center min-h-screen bg-[var(--ps-ice)]" data-testid="dashboard-loading">
      <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="text-center">
        <div className="spinner mx-auto mb-4" /><p className="text-[var(--ps-body-gray)] text-sm">Loading dashboard...</p>
      </motion.div>
    </div>
  );

  const statusBadge = (status) => {
    const styles = {
      ready: "bg-emerald-50 text-emerald-700 border-emerald-200",
      generating_audio: "bg-amber-50 text-amber-700 border-amber-200",
      generating_content: "bg-amber-50 text-amber-700 border-amber-200",
      pending_audio: "bg-sky-50 text-sky-700 border-sky-200",
    };
    const labels = {
      ready: "Ready", generating_audio: "Generating Audio...",
      generating_content: "Generating...", pending_audio: "Pending Audio",
    };
    return (
      <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-semibold border ${styles[status] || "bg-red-50 text-red-700 border-red-200"}`}>
        {labels[status] || status}
      </span>
    );
  };

  return (
    <div data-testid="dashboard-page" className="min-h-screen bg-[var(--ps-ice)]">
      <nav className="nav-ps" data-testid="dashboard-nav">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-[var(--ps-blue)] flex items-center justify-center"><BookOpen size={16} className="text-white" /></div>
          <span className="font-semibold text-base">IELTS Pro</span>
        </div>
        <div className="flex items-center gap-4">
          <button data-testid="admin-link" onClick={() => navigate("/admin")} className="text-gray-400 hover:text-[var(--ps-cyan)] transition-colors" title="Admin Panel">
            <Settings size={18} />
          </button>
          <span className="text-sm text-gray-400">{user?.name}</span>
          <button data-testid="logout-btn" onClick={handleLogout} className="flex items-center gap-1 text-xs text-gray-400 hover:text-[var(--ps-cyan)] transition-colors">
            <LogOut size={14} /> Sign out
          </button>
        </div>
      </nav>

      <motion.div className="max-w-6xl mx-auto px-6 py-10" initial="hidden" animate="show" variants={stagger}>
        {/* Welcome */}
        <motion.div variants={fadeUp} className="mb-10">
          <h1 className="display-compact" style={{ color: "var(--ps-black)" }}>
            Welcome back{user?.name ? `, ${user.name.split(' ')[0]}` : ''}
          </h1>
          <p className="text-sm text-[var(--ps-body-gray)] mt-1">Continue your IELTS preparation</p>
        </motion.div>

        {/* Progress Cards */}
        {progress && progress.total_attempts > 0 && (
          <motion.div variants={fadeUp} className="mb-10" data-testid="progress-section">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              <motion.div whileHover={{ y: -2 }} className="card-ps p-5 text-center col-span-2 md:col-span-1 relative overflow-hidden">
                <div className="absolute inset-0 bg-gradient-to-br from-[var(--ps-blue)]/5 to-transparent" />
                <div className="relative">
                  <div className="text-3xl font-light text-[var(--ps-blue)] mb-1">{progress.overall_estimated_band || "---"}</div>
                  <div className="text-[10px] font-medium text-[var(--ps-body-gray)] uppercase tracking-wider">Overall Band</div>
                </div>
              </motion.div>
              {["listening", "reading", "writing", "speaking"].map(m => {
                const Icon = moduleIcons[m];
                const stats = progress.modules[m];
                return (
                  <motion.div key={m} whileHover={{ y: -2 }} className="card-ps p-5" data-testid={`progress-${m}`}>
                    <div className="flex items-center gap-2 mb-3">
                      <Icon size={14} className="text-[var(--ps-blue)]" />
                      <span className="text-xs font-semibold capitalize tracking-wide">{m}</span>
                    </div>
                    <div className="text-2xl font-light text-[var(--ps-charcoal)]">{stats.latest_band || "---"}</div>
                    <div className="text-[10px] text-[var(--ps-body-gray)] mt-1">{stats.attempts} attempt{stats.attempts !== 1 ? "s" : ""}</div>
                  </motion.div>
                );
              })}
            </div>
          </motion.div>
        )}

        {/* Available Exams */}
        <motion.div variants={fadeUp}>
          <div className="flex items-center justify-between mb-6">
            <h2 className="font-semibold text-lg" style={{ color: "var(--ps-black)" }}>Practice Tests</h2>
            <button data-testid="generate-exam-btn" onClick={generateExam} disabled={generating}
              className="btn-ps btn-ps-secondary flex items-center gap-2" style={{ padding: "8px 20px", fontSize: "0.8rem" }}>
              {generating ? <><div className="spinner" style={{ width: 14, height: 14 }} /> Generating...</> : <><Plus size={14} /> Generate New Test</>}
            </button>
          </div>

          <motion.div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5" variants={stagger} initial="hidden" animate="show">
            {exams.map((exam, idx) => (
              <motion.div key={exam.exam_id} variants={fadeUp} whileHover={{ y: -4, boxShadow: "0 12px 40px rgba(0,0,0,0.08)" }}
                className="card-ps p-6 relative overflow-hidden" data-testid={`exam-card-${exam.exam_id}`}>
                {/* Accent line */}
                <div className="absolute top-0 left-0 right-0 h-[3px] bg-gradient-to-r from-[var(--ps-blue)] to-[var(--ps-cyan)]" />

                <div className="flex items-center gap-2 mb-3">
                  {statusBadge(exam.status)}
                  <span className="text-[10px] text-[var(--ps-mute)] capitalize font-medium">{exam.pathway}</span>
                </div>
                <h3 className="font-semibold text-sm mb-4" style={{ color: "var(--ps-charcoal)" }}>{exam.title}</h3>
                <div className="flex items-center gap-4 text-[10px] text-[var(--ps-body-gray)] mb-5 font-medium">
                  <span className="flex items-center gap-1"><Clock size={10} /> ~2h 45m</span>
                  <span className="flex items-center gap-1"><Target size={10} /> 4 modules</span>
                </div>

                {/* Module pills */}
                <div className="grid grid-cols-2 gap-2 mb-5">
                  {["listening", "reading", "writing", "speaking"].map(m => {
                    const Icon = moduleIcons[m];
                    const disabled = exam.status !== "ready" && exam.status !== "pending_audio";
                    return (
                      <motion.button key={m} whileHover={disabled ? {} : { scale: 1.02 }} whileTap={disabled ? {} : { scale: 0.98 }}
                        data-testid={`start-${m}-${exam.exam_id}`}
                        onClick={() => navigate(`/exam/${exam.exam_id}?module=${m}`)}
                        disabled={disabled}
                        className="flex items-center gap-2 p-2.5 rounded-xl border border-[var(--ps-divider)] hover:border-[var(--ps-blue)]/40 hover:bg-[var(--ps-blue)]/[0.02] transition-all text-left disabled:opacity-30 disabled:cursor-not-allowed">
                        <Icon size={14} className="text-[var(--ps-blue)]" />
                        <span className="text-[11px] font-semibold capitalize">{m}</span>
                      </motion.button>
                    );
                  })}
                </div>

                <motion.button whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}
                  data-testid={`start-full-${exam.exam_id}`}
                  onClick={() => navigate(`/exam/${exam.exam_id}?module=listening`)}
                  disabled={exam.status !== "ready" && exam.status !== "pending_audio"}
                  className="btn-ps btn-ps-primary w-full disabled:opacity-30 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                  style={{ padding: "10px", fontSize: "0.8rem" }}>
                  <Play size={14} /> Start Full Test
                </motion.button>
              </motion.div>
            ))}

            {exams.length === 0 && (
              <motion.div variants={fadeUp} className="card-ps p-12 text-center col-span-full">
                <div className="w-16 h-16 rounded-2xl mx-auto mb-4 bg-[var(--ps-blue)]/5 flex items-center justify-center">
                  <BookOpen size={28} className="text-[var(--ps-blue)]" />
                </div>
                <p className="text-[var(--ps-body-gray)] mb-4 text-sm">No practice tests yet</p>
                <button onClick={generateExam} className="btn-ps btn-ps-primary" style={{ padding: "10px 24px", fontSize: "0.8rem" }}>
                  <Plus size={14} /> Generate Your First Test
                </button>
              </motion.div>
            )}
          </motion.div>
        </motion.div>
      </motion.div>
    </div>
  );
}
