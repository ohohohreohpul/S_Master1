import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth, API } from "@/App";
import { BookOpen, Headphones, Pen, Mic, ArrowRight, LogOut, Play, Plus, BarChart3, Clock, Target } from "lucide-react";
import { Progress } from "@/components/ui/progress";

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
  const moduleColors = { listening: "#0070cc", reading: "#1eaedb", writing: "#0070cc", speaking: "#1eaedb" };

  const handleLogout = async () => { await logout(); navigate("/"); };

  if (loading) return <div className="flex items-center justify-center min-h-screen" data-testid="dashboard-loading"><div className="spinner" /></div>;

  return (
    <div data-testid="dashboard-page" className="min-h-screen bg-[var(--ps-ice)]">
      {/* Nav */}
      <nav className="nav-ps" data-testid="dashboard-nav">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-[var(--ps-blue)] flex items-center justify-center"><BookOpen size={16} className="text-white" /></div>
          <span className="font-semibold text-base">IELTS Pro</span>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-sm text-gray-300">{user?.name}</span>
          <button data-testid="logout-btn" onClick={handleLogout} className="btn-ps-ghost text-white border-white/20" style={{ padding: "6px 14px", fontSize: "0.75rem", borderRadius: "999px" }}>
            <LogOut size={14} /> Sign out
          </button>
        </div>
      </nav>

      <div className="max-w-6xl mx-auto px-6 py-10">
        {/* Progress Cards */}
        {progress && progress.total_attempts > 0 && (
          <div className="mb-10" data-testid="progress-section">
            <h2 className="display-compact mb-6" style={{ color: "var(--ps-black)" }}>Your Progress</h2>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              {/* Overall */}
              <div className="card-ps p-5 text-center col-span-2 md:col-span-1">
                <div className="text-3xl font-light text-[var(--ps-blue)] mb-1">{progress.overall_estimated_band || "---"}</div>
                <div className="text-xs text-[var(--ps-body-gray)]">Overall Band</div>
              </div>
              {["listening", "reading", "writing", "speaking"].map(m => {
                const Icon = moduleIcons[m];
                const stats = progress.modules[m];
                return (
                  <div key={m} className="card-ps p-5" data-testid={`progress-${m}`}>
                    <div className="flex items-center gap-2 mb-2">
                      <Icon size={16} style={{ color: moduleColors[m] }} />
                      <span className="text-sm font-medium capitalize">{m}</span>
                    </div>
                    <div className="text-2xl font-light" style={{ color: moduleColors[m] }}>{stats.latest_band || "---"}</div>
                    <div className="text-xs text-[var(--ps-body-gray)] mt-1">{stats.attempts} attempt{stats.attempts !== 1 ? "s" : ""}</div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Available Exams */}
        <div className="mb-10">
          <div className="flex items-center justify-between mb-6">
            <h2 className="display-compact" style={{ color: "var(--ps-black)" }}>Practice Tests</h2>
            <button data-testid="generate-exam-btn" onClick={generateExam} disabled={generating}
              className="btn-ps btn-ps-secondary" style={{ padding: "8px 20px", fontSize: "0.875rem" }}>
              {generating ? <><div className="spinner" style={{ width: 16, height: 16 }} /> Generating...</> : <><Plus size={16} /> Generate New Test</>}
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {exams.map(exam => (
              <div key={exam.exam_id} className="card-ps p-6" data-testid={`exam-card-${exam.exam_id}`}>
                <div className="flex items-center gap-2 mb-3">
                  <span className={`px-3 py-1 rounded-full text-xs font-medium ${exam.status === "ready" ? "bg-green-50 text-green-700" :
                    exam.status === "generating_audio" || exam.status === "generating_content" ? "bg-yellow-50 text-yellow-700" :
                    exam.status === "pending_audio" ? "bg-blue-50 text-blue-700" : "bg-red-50 text-red-700"}`}>
                    {exam.status === "ready" ? "Ready" :
                     exam.status === "generating_audio" ? "Generating Audio..." :
                     exam.status === "generating_content" ? "Generating Content..." :
                     exam.status === "pending_audio" ? "Pending Audio" : exam.status}
                  </span>
                  <span className="text-xs text-[var(--ps-body-gray)] capitalize">{exam.pathway}</span>
                </div>
                <h3 className="font-medium text-base mb-3" style={{ color: "var(--ps-charcoal)" }}>{exam.title}</h3>
                <div className="flex items-center gap-4 text-xs text-[var(--ps-body-gray)] mb-5">
                  <span className="flex items-center gap-1"><Clock size={12} /> ~2.5 hours</span>
                  <span className="flex items-center gap-1"><Target size={12} /> 4 modules</span>
                </div>

                {/* Module selection */}
                <div className="grid grid-cols-2 gap-2 mb-4">
                  {["listening", "reading", "writing", "speaking"].map(m => {
                    const Icon = moduleIcons[m];
                    return (
                      <button key={m} data-testid={`start-${m}-${exam.exam_id}`}
                        onClick={() => navigate(`/exam/${exam.exam_id}?module=${m}`)}
                        disabled={exam.status !== "ready" && exam.status !== "pending_audio"}
                        className="flex items-center gap-2 p-3 rounded-xl border border-[var(--ps-divider)] hover:border-[var(--ps-blue)] transition-colors text-left disabled:opacity-40 disabled:cursor-not-allowed">
                        <Icon size={16} style={{ color: moduleColors[m] }} />
                        <span className="text-xs font-medium capitalize">{m}</span>
                      </button>
                    );
                  })}
                </div>

                <button data-testid={`start-full-${exam.exam_id}`}
                  onClick={() => navigate(`/exam/${exam.exam_id}?module=listening`)}
                  disabled={exam.status !== "ready" && exam.status !== "pending_audio"}
                  className="btn-ps btn-ps-primary w-full disabled:opacity-40 disabled:cursor-not-allowed"
                  style={{ padding: "10px", fontSize: "0.875rem" }}>
                  <Play size={16} /> Start Full Test <ArrowRight size={14} />
                </button>
              </div>
            ))}

            {exams.length === 0 && (
              <div className="card-ps p-10 text-center col-span-full">
                <p className="text-[var(--ps-body-gray)] mb-4">No practice tests available yet.</p>
                <button onClick={generateExam} className="btn-ps btn-ps-primary" style={{ padding: "10px 24px", fontSize: "0.875rem" }}>
                  <Plus size={16} /> Generate Your First Test
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
