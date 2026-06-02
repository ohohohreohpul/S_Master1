import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth, API } from "@/App";
import { apiFetch } from "@/lib/apiFetch";
import { BookOpen, Plus, Trash2, RefreshCw, ChartBar as BarChart3, Users, Database, Music, ArrowLeft, Loader as Loader2, ChevronDown, Crown } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Progress } from "@/components/ui/progress";

const fadeUp = { hidden: { opacity: 0, y: 20 }, show: { opacity: 1, y: 0 } };
const stagger = { hidden: {}, show: { transition: { staggerChildren: 0.06 } } };

export default function AdminPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [exams, setExams] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [genProgress, setGenProgress] = useState(null);
  const [generatingTelc, setGeneratingTelc] = useState(false);
  const [telcGenProgress, setTelcGenProgress] = useState(null);
  const [showTelcDropdown, setShowTelcDropdown] = useState(false);

  const fetchData = async () => {
    try {
      const [examsRes, statsRes] = await Promise.all([
        apiFetch(`${API}/admin/exams`),
        apiFetch(`${API}/admin/stats`)
      ]);
      if (examsRes.ok) setExams(await examsRes.json());
      if (statsRes.ok) setStats(await statsRes.json());
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);

  const generateExam = async () => {
    setGenerating(true);
    setGenProgress({ status: "generating_content", progress: 0 });
    try {
      const res = await apiFetch(`${API}/exams/generate`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        const interval = setInterval(async () => {
          const statusRes = await apiFetch(`${API}/exams/${data.exam_id}/status`);
          const status = await statusRes.json();
          setGenProgress({ status: status.status, progress: status.audio_progress || 0 });
          if (status.status === "ready" || status.status === "error" || status.status === "audio_error") {
            clearInterval(interval);
            setGenerating(false);
            setGenProgress(null);
            fetchData();
          }
        }, 4000);
      }
    } catch { setGenerating(false); setGenProgress(null); }
  };

  const generateTelcExam = async (level) => {
    setGeneratingTelc(true);
    setShowTelcDropdown(false);
    setTelcGenProgress({ status: "generating_content", progress: 0, level });
    try {
      const res = await apiFetch(`${API}/exams/generate-telc`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ level })
      });
      if (res.ok) {
        const data = await res.json();
        const interval = setInterval(async () => {
          const statusRes = await apiFetch(`${API}/exams/${data.exam_id}/status`);
          const status = await statusRes.json();
          setTelcGenProgress({ status: status.status, progress: status.audio_progress || 0, level });
          if (status.status === "ready" || status.status === "error" || status.status === "audio_error") {
            clearInterval(interval);
            setGeneratingTelc(false);
            setTelcGenProgress(null);
            fetchData();
          }
        }, 4000);
      }
    } catch { setGeneratingTelc(false); setTelcGenProgress(null); }
  };

  const deleteExam = async (examId) => {
    if (!window.confirm(`Delete exam ${examId}? This will also remove all audio files.`)) return;
    await apiFetch(`${API}/admin/exams/${examId}`, { method: "DELETE" });
    fetchData();
  };

  const regenerateAudio = async (examId) => {
    await apiFetch(`${API}/admin/exams/${examId}/regenerate-audio`, { method: "POST" });
    fetchData();
  };

  if (loading) return (
    <div className="flex items-center justify-center min-h-screen bg-[var(--ps-ice)]">
      <div className="spinner" />
    </div>
  );

  return (
    <div className="min-h-screen bg-[var(--ps-ice)]" data-testid="admin-page">
      <nav className="nav-ps">
        <div className="flex items-center gap-4">
          <button onClick={() => navigate("/dashboard")} className="text-gray-400 hover:text-white transition-colors"><ArrowLeft size={18} /></button>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-[var(--ps-blue)] flex items-center justify-center"><BookOpen size={16} className="text-white" /></div>
            <span className="font-semibold text-base">Admin Panel</span>
          </div>
        </div>
        <span className="text-sm text-gray-400">{user?.name}</span>
      </nav>

      <motion.div className="max-w-6xl mx-auto px-6 py-10" initial="hidden" animate="show" variants={stagger}>
        {/* Stats */}
        {stats && (
          <motion.div variants={fadeUp} className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-10">
            {[
              { icon: Database, label: "Total Exams", value: stats.total_exams, color: "#0070cc" },
              { icon: Music, label: "Audio Files", value: stats.total_audio_files, color: "#1eaedb" },
              { icon: Users, label: "Users", value: stats.total_users, color: "#0070cc" },
              { icon: BarChart3, label: "Attempts", value: stats.total_attempts, color: "#1eaedb" },
              { icon: Crown, label: "Pro Users", value: stats.pro_users ?? "—", color: "#f59e0b" },
            ].map((s, i) => (
              <motion.div key={i} whileHover={{ y: -2 }} className="card-ps p-5">
                <div className="flex items-center gap-2 mb-2">
                  <s.icon size={16} style={{ color: s.color }} />
                  <span className="text-xs font-medium text-[var(--ps-body-gray)]">{s.label}</span>
                </div>
                <div className="text-2xl font-light" style={{ color: s.color }}>{s.value}</div>
              </motion.div>
            ))}
          </motion.div>
        )}

        {/* Generate IELTS Section */}
        <motion.div variants={fadeUp} className="card-ps p-6 mb-6">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-base" style={{ color: "var(--ps-charcoal)" }}>Generate IELTS Exam</h3>
              <p className="text-xs text-[var(--ps-body-gray)] mt-1">Uses AI to generate exam content and natural-sounding audio</p>
            </div>
            <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }} onClick={generateExam} disabled={generating}
              className="btn-ps btn-ps-orange flex items-center gap-2 disabled:opacity-50" style={{ padding: "10px 24px", fontSize: "0.8rem" }}>
              {generating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              {generating ? "Generating..." : "Generate IELTS Exam"}
            </motion.button>
          </div>
          {genProgress && (
            <div className="mt-4">
              <div className="flex items-center justify-between text-xs mb-2">
                <span className="text-[var(--ps-body-gray)] capitalize">{genProgress.status.replace(/_/g, " ")}</span>
                <span className="text-[var(--ps-blue)] font-medium">{genProgress.progress}%</span>
              </div>
              <Progress value={genProgress.progress} />
            </div>
          )}
        </motion.div>

        {/* Generate TELC Section */}
        <motion.div variants={fadeUp} className="card-ps p-6 mb-8">
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-center gap-2">
                <h3 className="font-semibold text-base" style={{ color: "var(--ps-charcoal)" }}>Generate TELC Deutsch Exam</h3>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-50 text-amber-700 border border-amber-200">TELC</span>
              </div>
              <p className="text-xs text-[var(--ps-body-gray)] mt-1">Generate a TELC Deutsch exam at B1 or B2 level</p>
            </div>
            <div className="relative">
              <motion.button
                whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
                onClick={() => setShowTelcDropdown(v => !v)}
                disabled={generatingTelc}
                className="flex items-center gap-2 disabled:opacity-50 px-5 py-2.5 rounded-xl text-sm font-semibold bg-amber-500 hover:bg-amber-600 text-white transition-colors"
                style={{ fontSize: "0.8rem" }}>
                {generatingTelc ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                {generatingTelc ? "Generating..." : "Generate TELC"}
                {!generatingTelc && <ChevronDown size={13} />}
              </motion.button>
              <AnimatePresence>
                {showTelcDropdown && (
                  <motion.div
                    initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
                    className="absolute right-0 top-full mt-1 bg-white rounded-xl border border-[var(--ps-divider)] shadow-lg z-20 overflow-hidden min-w-[120px]">
                    {["B1", "B2"].map(level => (
                      <button key={level} onClick={() => generateTelcExam(level)}
                        className="w-full text-left px-4 py-2.5 text-sm hover:bg-[var(--ps-ice)] transition-colors font-medium"
                        style={{ color: "var(--ps-charcoal)" }}>
                        TELC {level}
                      </button>
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
          {telcGenProgress && (
            <div className="mt-4">
              <div className="flex items-center justify-between text-xs mb-2">
                <span className="text-[var(--ps-body-gray)] capitalize">
                  TELC {telcGenProgress.level} — {telcGenProgress.status.replace(/_/g, " ")}
                </span>
                <span className="text-amber-600 font-medium">{telcGenProgress.progress}%</span>
              </div>
              <Progress value={telcGenProgress.progress} className="[&>div]:bg-amber-500" />
            </div>
          )}
        </motion.div>

        {/* How Audio Storage Works */}
        <motion.div variants={fadeUp} className="card-ps p-6 mb-8 border-l-4 border-[var(--ps-blue)]">
          <h3 className="font-semibold text-sm mb-2" style={{ color: "var(--ps-charcoal)" }}>How Audio Storage Works</h3>
          <div className="text-xs text-[var(--ps-body-gray)] space-y-1 leading-relaxed">
            <p>Audio is generated <strong>once per exam</strong> and stored in cloud storage.</p>
            <p>When users take an exam, audio is served from the database. <strong>No re-generation happens.</strong> This means each exam's audio is a one-time cost.</p>
            <p>The more exams you generate upfront, the more your users can practice without additional API costs (network effects).</p>
          </div>
        </motion.div>

        {/* Exam List */}
        <motion.div variants={fadeUp}>
          <h3 className="font-semibold text-base mb-4" style={{ color: "var(--ps-charcoal)" }}>All Exams</h3>
          <div className="space-y-3">
            {exams.map(exam => {
              const isTelc = exam.exam_type === "telc";
              return (
                <motion.div key={exam.exam_id} whileHover={{ x: 2 }} className="card-ps p-5 flex items-center gap-4"
                  data-testid={`admin-exam-${exam.exam_id}`}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <span className="font-medium text-sm truncate" style={{ color: "var(--ps-charcoal)" }}>{exam.title}</span>
                      <span className={`px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider ${
                        exam.status === "ready" ? "bg-emerald-100 text-emerald-700" :
                        exam.status === "generating_audio" ? "bg-amber-100 text-amber-700" :
                        "bg-gray-100 text-gray-600"
                      }`}>{exam.status}</span>
                      {/* Exam type badge */}
                      {isTelc ? (
                        <span className="px-2 py-0.5 rounded-full text-[9px] font-bold bg-amber-50 text-amber-700 border border-amber-200">
                          TELC {exam.telc_level || ""}
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 rounded-full text-[9px] font-bold bg-[var(--ps-blue)]/10 text-[var(--ps-blue)] border border-[var(--ps-blue)]/20">
                          IELTS
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-4 text-[10px] text-[var(--ps-body-gray)]">
                      <span>ID: {exam.exam_id}</span>
                      <span>Audio files: {exam.audio_files_count || 0}</span>
                      {!isTelc && <span className="capitalize">{exam.pathway}</span>}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <motion.button whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }}
                      onClick={() => regenerateAudio(exam.exam_id)} title="Regenerate Audio"
                      className="p-2 rounded-lg hover:bg-[var(--ps-ice)] text-[var(--ps-body-gray)] hover:text-[var(--ps-blue)] transition-colors">
                      <RefreshCw size={16} />
                    </motion.button>
                    <motion.button whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }}
                      onClick={() => deleteExam(exam.exam_id)} title="Delete Exam"
                      className="p-2 rounded-lg hover:bg-red-50 text-[var(--ps-body-gray)] hover:text-red-500 transition-colors">
                      <Trash2 size={16} />
                    </motion.button>
                  </div>
                </motion.div>
              );
            })}
          </div>
        </motion.div>
      </motion.div>
    </div>
  );
}
