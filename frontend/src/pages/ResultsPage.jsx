import { useState, useEffect } from "react";
import { useParams, useLocation, useNavigate } from "react-router-dom";
import { API, useAuth } from "@/App";
import { BookOpen, Headphones, Pen, Mic, ArrowLeft, CheckCircle, XCircle, Target, TrendingUp } from "lucide-react";
import { Progress } from "@/components/ui/progress";

export default function ResultsPage() {
  const { attemptId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [attempt, setAttempt] = useState(null);
  const [loading, setLoading] = useState(true);

  const scores = location.state?.scores || attempt?.scores;
  const module = location.state?.module || attempt?.module;

  useEffect(() => {
    if (location.state?.scores) {
      setLoading(false);
      return;
    }
    fetch(`${API}/attempts/${attemptId}`, { credentials: "include" })
      .then(r => r.json())
      .then(data => { setAttempt(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, [attemptId, location.state]);

  if (loading) return <div className="flex items-center justify-center min-h-screen"><div className="spinner" /></div>;

  const moduleIcons = { listening: Headphones, reading: BookOpen, writing: Pen, speaking: Mic };
  const ModuleIcon = moduleIcons[module] || Target;

  const getBandColor = (band) => {
    if (band >= 7) return "#22c55e";
    if (band >= 6) return "#0070cc";
    if (band >= 5) return "#f59e0b";
    return "#ef4444";
  };

  const renderObjectiveResults = () => {
    if (!scores) return null;
    return (
      <div data-testid="objective-results">
        {/* Band score */}
        <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: "spring" }}
          className="card-ps p-8 text-center mb-8 relative overflow-hidden" data-testid="band-score-card">
          <div className="absolute inset-0" style={{ background: `radial-gradient(circle at 50% 0%, ${getBandColor(scores.band_score)}10 0%, transparent 60%)` }} />
          <div className="relative">
            <p className="text-xs font-semibold text-[var(--ps-body-gray)] mb-2 uppercase tracking-wider">Your Band Score</p>
            <div className="text-7xl font-extralight mb-2" style={{ color: getBandColor(scores.band_score) }}>
              {scores.band_score}
            </div>
            <p className="text-sm text-[var(--ps-body-gray)]">
              {scores.correct} of {scores.total} correct
            </p>
            <Progress value={(scores.correct / scores.total) * 100} className="mt-4 max-w-xs mx-auto h-2" />
          </div>
        </motion.div>

        {/* Question details */}
        <div className="card-ps p-6" data-testid="answer-details">
          <h3 className="font-medium text-base mb-4" style={{ color: "var(--ps-charcoal)" }}>Answer Review</h3>
          <div className="space-y-3">
            {(scores.details || []).map(d => (
              <div key={d.question_num} className={`flex items-center gap-3 p-3 rounded-xl ${d.is_correct ? "bg-green-50" : "bg-red-50"}`}
                data-testid={`result-q-${d.question_num}`}>
                {d.is_correct ? <CheckCircle size={18} className="text-green-600" /> : <XCircle size={18} className="text-red-500" />}
                <span className="text-sm font-medium w-8">Q{d.question_num}</span>
                <span className="text-sm flex-1">
                  Your answer: <span className={d.is_correct ? "text-green-700 font-medium" : "text-red-600 font-medium"}>
                    {d.user_answer || "(no answer)"}
                  </span>
                </span>
                {!d.is_correct && (
                  <span className="text-sm text-green-700">Correct: {d.correct_answer}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  };

  const renderWritingResults = () => {
    if (!scores) return null;
    const tasks = ["task_1", "task_2"];
    const criteria = ["task_achievement", "coherence_cohesion", "lexical_resource", "grammatical_range"];
    const criteriaLabels = { task_achievement: "Task Achievement", coherence_cohesion: "Coherence & Cohesion",
      lexical_resource: "Lexical Resource", grammatical_range: "Grammatical Range" };

    return (
      <div data-testid="writing-results">
        <div className="card-ps p-8 text-center mb-8" data-testid="writing-band-card">
          <p className="text-sm text-[var(--ps-body-gray)] mb-2">Overall Writing Band</p>
          <div className="text-6xl font-light mb-2" style={{ color: getBandColor(scores.overall_writing_band) }}>
            {scores.overall_writing_band}
          </div>
        </div>

        {tasks.map(taskKey => {
          const taskData = scores[taskKey];
          if (!taskData) return null;
          return (
            <div key={taskKey} className="card-ps p-6 mb-6" data-testid={`${taskKey}-results`}>
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-medium capitalize">{taskKey.replace("_", " ")}</h3>
                <span className="text-2xl font-light" style={{ color: getBandColor(taskData.overall_band) }}>
                  Band {taskData.overall_band}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-4 mb-4">
                {criteria.map(c => (
                  <div key={c} className="p-3 bg-[var(--ps-ice)] rounded-xl">
                    <p className="text-xs text-[var(--ps-body-gray)] mb-1">{criteriaLabels[c]}</p>
                    <p className="text-lg font-light" style={{ color: getBandColor(taskData[c]?.band) }}>
                      {taskData[c]?.band}
                    </p>
                    <p className="text-xs text-[var(--ps-body-gray)] mt-1">{taskData[c]?.feedback}</p>
                  </div>
                ))}
              </div>
              {taskData.general_feedback && (
                <div className="p-3 bg-blue-50 rounded-xl">
                  <p className="text-xs font-medium text-[var(--ps-blue)] mb-1">Feedback</p>
                  <p className="text-sm">{taskData.general_feedback}</p>
                </div>
              )}
            </div>
          );
        })}
      </div>
    );
  };

  const renderSpeakingResults = () => {
    if (!scores) return null;
    const criteria = [
      { key: "fluency_coherence", label: "Fluency & Coherence" },
      { key: "lexical_resource", label: "Lexical Resource" },
      { key: "grammatical_range", label: "Grammatical Range" },
      { key: "pronunciation", label: "Pronunciation" }
    ];

    return (
      <div data-testid="speaking-results">
        <div className="card-ps p-8 text-center mb-8" data-testid="speaking-band-card">
          <p className="text-sm text-[var(--ps-body-gray)] mb-2">Overall Speaking Band</p>
          <div className="text-6xl font-light mb-2" style={{ color: getBandColor(scores.overall_band) }}>
            {scores.overall_band}
          </div>
        </div>

        <div className="card-ps p-6 mb-6">
          <div className="grid grid-cols-2 gap-4 mb-4">
            {criteria.map(c => (
              <div key={c.key} className="p-3 bg-[var(--ps-ice)] rounded-xl">
                <p className="text-xs text-[var(--ps-body-gray)] mb-1">{c.label}</p>
                <p className="text-lg font-light" style={{ color: getBandColor(scores[c.key]?.band) }}>
                  {scores[c.key]?.band}
                </p>
                <p className="text-xs text-[var(--ps-body-gray)] mt-1">{scores[c.key]?.feedback}</p>
              </div>
            ))}
          </div>
          {scores.general_feedback && (
            <div className="p-3 bg-blue-50 rounded-xl">
              <p className="text-xs font-medium text-[var(--ps-blue)] mb-1">Overall Feedback</p>
              <p className="text-sm">{scores.general_feedback}</p>
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-[var(--ps-ice)]" data-testid="results-page">
      {/* Nav */}
      <nav className="nav-ps">
        <button onClick={() => navigate("/dashboard")} className="flex items-center gap-2 text-white hover:text-[var(--ps-cyan)]" data-testid="back-to-dashboard">
          <ArrowLeft size={18} /> Back to Dashboard
        </button>
        <span className="font-semibold text-base">Results</span>
        <div />
      </nav>

      <div className="max-w-3xl mx-auto px-6 py-10">
        <div className="flex items-center gap-3 mb-8" data-testid="results-header">
          <ModuleIcon size={24} className="text-[var(--ps-blue)]" />
          <h1 className="display-compact capitalize" style={{ color: "var(--ps-black)" }}>{module} Results</h1>
        </div>

        {module === "listening" || module === "reading" ? renderObjectiveResults() : null}
        {module === "writing" ? renderWritingResults() : null}
        {module === "speaking" ? renderSpeakingResults() : null}

        <div className="flex gap-4 mt-8">
          <button onClick={() => navigate("/dashboard")} className="btn-ps btn-ps-primary" style={{ padding: "10px 24px", fontSize: "0.875rem" }}
            data-testid="return-dashboard-btn">
            <ArrowLeft size={16} /> Back to Dashboard
          </button>
        </div>
      </div>
    </div>
  );
}
