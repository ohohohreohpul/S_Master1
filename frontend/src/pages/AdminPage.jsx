import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth, API } from "@/App";
import { BookOpen, Plus, Trash2, RefreshCw, BarChart3, Users, Database, Music, ArrowLeft, Loader2 } from "lucide-react";
import { motion } from "framer-motion";
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

  const fetchData = async () => {
    try {
      const [examsRes, statsRes] = await Promise.all([
        fetch(`${API}/admin/exams`, { credentials: "include" }),
        fetch(`${API}/admin/stats`, { credentials: "include" })
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
      const res = await fetch(`${API}/exams/generate`, { method: "POST", credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        const interval = setInterval(async () => {
          const statusRes = await fetch(`${API}/exams/${data.exam_id}/status`, { credentials: "include" });
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

  const deleteExam = async (examId) => {
    if (!window.confirm(`Delete exam ${examId}? This will also remove all audio files.`)) return;
    await fetch(`${API}/admin/exams/${examId}`, { method: "DELETE", credentials: "include" });
    fetchData();
  };

  const regenerateAudio = async (examId) => {
    await fetch(`${API}/admin/exams/${examId}/regenerate-audio`, { method: "POST", credentials: "include" });
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
          <motion.div variants={fadeUp} className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-10">
            {[
              { icon: Database, label: "Total Exams", value: stats.total_exams, color: "#0070cc" },
              { icon: Music, label: "Audio Files", value: stats.total_audio_files, color: "#1eaedb" },
              { icon: Users, label: "Users", value: stats.total_users, color: "#0070cc" },
              { icon: BarChart3, label: "Attempts", value: stats.total_attempts, color: "#1eaedb" },
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

        {/* Generate Section */}
        <motion.div variants={fadeUp} className="card-ps p-6 mb-8">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-base" style={{ color: "var(--ps-charcoal)" }}>Generate New Exam</h3>
              <p className="text-xs text-[var(--ps-body-gray)] mt-1">Uses OpenRouter AI to create content + ElevenLabs V3 for audio</p>
            </div>
            <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }} onClick={generateExam} disabled={generating}
              className="btn-ps btn-ps-orange flex items-center gap-2 disabled:opacity-50" style={{ padding: "10px 24px", fontSize: "0.8rem" }}>
              {generating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              {generating ? "Generating..." : "Generate Exam"}
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

        {/* How Audio Storage Works */}
        <motion.div variants={fadeUp} className="card-ps p-6 mb-8 border-l-4 border-[var(--ps-blue)]">
          <h3 className="font-semibold text-sm mb-2" style={{ color: "var(--ps-charcoal)" }}>How Audio Storage Works</h3>
          <div className="text-xs text-[var(--ps-body-gray)] space-y-1 leading-relaxed">
            <p>Audio is generated <strong>once per exam</strong> via ElevenLabs V3 and stored as base64 in MongoDB's <code className="bg-gray-100 px-1 rounded">audio_files</code> collection.</p>
            <p>When users take an exam, audio is served from the database. <strong>No re-generation happens.</strong> This means each exam's audio is a one-time cost.</p>
            <p>The more exams you generate upfront, the more your users can practice without additional API costs (network effects).</p>
          </div>
        </motion.div>

        {/* Exam List */}
        <motion.div variants={fadeUp}>
          <h3 className="font-semibold text-base mb-4" style={{ color: "var(--ps-charcoal)" }}>All Exams</h3>
          <div className="space-y-3">
            {exams.map(exam => (
              <motion.div key={exam.exam_id} whileHover={{ x: 2 }} className="card-ps p-5 flex items-center gap-4"
                data-testid={`admin-exam-${exam.exam_id}`}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-sm truncate" style={{ color: "var(--ps-charcoal)" }}>{exam.title}</span>
                    <span className={`px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider ${
                      exam.status === "ready" ? "bg-emerald-100 text-emerald-700" :
                      exam.status === "generating_audio" ? "bg-amber-100 text-amber-700" :
                      "bg-gray-100 text-gray-600"
                    }`}>{exam.status}</span>
                  </div>
                  <div className="flex items-center gap-4 text-[10px] text-[var(--ps-body-gray)]">
                    <span>ID: {exam.exam_id}</span>
                    <span>Audio files: {exam.audio_files_count || 0}</span>
                    <span className="capitalize">{exam.pathway}</span>
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
            ))}
          </div>
        </motion.div>
      </motion.div>
    </div>
  );
}
