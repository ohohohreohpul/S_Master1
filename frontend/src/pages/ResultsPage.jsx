import { useState, useEffect } from "react";
import { useParams, useLocation, useNavigate } from "react-router-dom";
import { API, useAuth } from "@/App";
import { apiFetch } from "@/lib/apiFetch";
import { BookOpen, Headphones, Pen, Mic, ArrowLeft, CircleCheck as CheckCircle, Circle as XCircle, Target, TrendingUp, ChevronDown, ChevronUp } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { motion, AnimatePresence } from "framer-motion";

export default function ResultsPage() {
  const { attemptId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [attempt, setAttempt] = useState(null);
  const [loading, setLoading] = useState(true);
  const [expandedModules, setExpandedModules] = useState({});

  const scores = location.state?.scores || attempt?.scores;
  const module = location.state?.module || attempt?.module;
  const isFullTest = location.state?.mode === "full_test" || attempt?.mode === "full_test";
  const exam = location.state?.exam || attempt?.exam;
  const isTelc = exam?.exam_type === "telc" || attempt?.exam_type === "telc";

  useEffect(() => {
    if (location.state?.scores && !isFullTest) {
      setLoading(false);
      return;
    }
    apiFetch(`${API}/attempts/${attemptId}`)
      .then(r => r.json())
      .then(data => { setAttempt(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, [attemptId, location.state]);

  if (loading) return <div className="flex items-center justify-center min-h-screen"><div className="spinner" /></div>;

  const moduleIcons = { listening: Headphones, reading: BookOpen, writing: Pen, speaking: Mic };
  const ModuleIcon = moduleIcons[module] || Target;

  const getBandColor = (band) => {
    if (!band && band !== 0) return "#94a3b8";
    if (band >= 7) return "#22c55e";
    if (band >= 6) return "#0070cc";
    if (band >= 5) return "#f59e0b";
    return "#ef4444";
  };

  const getPercentColor = (pct) => {
    if (pct >= 75) return "#22c55e";
    if (pct >= 60) return "#0070cc";
    if (pct >= 45) return "#f59e0b";
    return "#ef4444";
  };

  const toggleModule = (mod) => {
    setExpandedModules(prev => ({ ...prev, [mod]: !prev[mod] }));
  };

  // TELC objective results (lesen/hoeren)
  const renderTelcObjectiveResults = () => {
    if (!scores) return null;
    const passed = scores.passed;
    const pct = scores.percentage;
    return (
      <div data-testid="telc-objective-results">
        <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: "spring" }}
          className="card-ps p-8 text-center mb-8 relative overflow-hidden" data-testid="telc-score-card">
          <div className="absolute inset-0" style={{ background: `radial-gradient(circle at 50% 0%, ${getPercentColor(pct)}10 0%, transparent 60%)` }} />
          <div className="relative">
            <p className="text-xs font-semibold text-[var(--ps-body-gray)] mb-2 uppercase tracking-wider">Ergebnis</p>
            <div className="text-7xl font-extralight mb-2" style={{ color: getPercentColor(pct) }}>
              {pct}%
            </div>
            <div className={`inline-flex items-center gap-2 px-4 py-1.5 rounded-full font-semibold text-sm mb-3 ${
              passed ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"
            }`}>
              {passed ? <CheckCircle size={16} /> : <XCircle size={16} />}
              {passed ? "Bestanden" : "Nicht Bestanden"}
            </div>
            <p className="text-sm text-[var(--ps-body-gray)]">
              {scores.correct} von {scores.total} richtig
            </p>
            <Progress value={pct} className="mt-4 max-w-xs mx-auto h-2" />
          </div>
        </motion.div>

        {scores.details && (
          <div className="card-ps p-6" data-testid="answer-details">
            <h3 className="font-medium text-base mb-4" style={{ color: "var(--ps-charcoal)" }}>Antworten</h3>
            <div className="space-y-3">
              {(scores.details || []).map(d => (
                <div key={d.question_num} className={`flex items-center gap-3 p-3 rounded-xl ${d.is_correct ? "bg-green-50" : "bg-red-50"}`}
                  data-testid={`result-q-${d.question_num}`}>
                  {d.is_correct ? <CheckCircle size={18} className="text-green-600" /> : <XCircle size={18} className="text-red-500" />}
                  <span className="text-sm font-medium w-8">A{d.question_num}</span>
                  <span className="text-sm flex-1">
                    Ihre Antwort: <span className={d.is_correct ? "text-green-700 font-medium" : "text-red-600 font-medium"}>
                      {d.user_answer || "(keine Antwort)"}
                    </span>
                  </span>
                  {!d.is_correct && (
                    <span className="text-sm text-green-700">Richtig: {d.correct_answer}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  // TELC writing results (schreiben)
  const renderTelcWritingResults = () => {
    if (!scores) return null;
    const bestanden = scores.bestanden;
    const gesamt = scores.gesamt_punkte;
    return (
      <div data-testid="telc-writing-results">
        <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: "spring" }}
          className="card-ps p-8 text-center mb-8">
          <p className="text-xs font-semibold text-[var(--ps-body-gray)] mb-2 uppercase tracking-wider">Schreiben Ergebnis</p>
          <div className="text-6xl font-extralight mb-2" style={{ color: getBandColor(gesamt) }}>
            {gesamt}<span className="text-2xl text-[var(--ps-body-gray)]">/30</span>
          </div>
          <div className={`inline-flex items-center gap-2 px-4 py-1.5 rounded-full font-semibold text-sm ${
            bestanden ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"
          }`}>
            {bestanden ? <CheckCircle size={16} /> : <XCircle size={16} />}
            {bestanden ? "Bestanden" : "Nicht Bestanden"}
          </div>
        </motion.div>

        <div className="card-ps p-6 mb-6">
          <div className="grid grid-cols-3 gap-4 mb-4">
            {[
              { key: "kommunikative_aufgabe", label: "Kommunikative Aufgabe" },
              { key: "textaufbau", label: "Textaufbau" },
              { key: "sprachliche_mittel", label: "Sprachliche Mittel" },
            ].map(c => {
              const val = scores[c.key];
              return (
                <div key={c.key} className="p-3 bg-[var(--ps-ice)] rounded-xl text-center">
                  <p className="text-xs text-[var(--ps-body-gray)] mb-2">{c.label}</p>
                  <p className="text-2xl font-light" style={{ color: "var(--ps-blue)" }}>
                    {val?.punkte ?? "—"}
                  </p>
                </div>
              );
            })}
          </div>
          {scores.allgemeines_feedback && (
            <div className="p-3 bg-blue-50 rounded-xl">
              <p className="text-xs font-medium text-[var(--ps-blue)] mb-1">Feedback</p>
              <p className="text-sm">{scores.allgemeines_feedback}</p>
            </div>
          )}
        </div>
      </div>
    );
  };

  const renderObjectiveResults = () => {
    if (!scores) return null;
    return (
      <div data-testid="objective-results">
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

  // Full test results
  const renderFullTestResults = () => {
    const moduleScores = attempt?.module_scores || {};
    const overallBand = attempt?.overall_band;
    const moduleOrder = ["listening", "reading", "writing", "speaking"];
    const moduleLabels = { listening: "Listening", reading: "Reading", writing: "Writing", speaking: "Speaking" };
    const moduleIcons2 = { listening: Headphones, reading: BookOpen, writing: Pen, speaking: Mic };

    return (
      <div data-testid="full-test-results">
        {/* Overall band */}
        {overallBand && (
          <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: "spring" }}
            className="card-ps p-8 text-center mb-8 relative overflow-hidden" data-testid="overall-band-card">
            <div className="absolute inset-0" style={{ background: `radial-gradient(circle at 50% 0%, ${getBandColor(overallBand)}10 0%, transparent 60%)` }} />
            <div className="relative">
              <p className="text-xs font-semibold text-[var(--ps-body-gray)] mb-2 uppercase tracking-wider">Overall Band Score</p>
              <div className="text-8xl font-extralight mb-2" style={{ color: getBandColor(overallBand) }}>
                {overallBand}
              </div>
              <p className="text-sm text-[var(--ps-body-gray)]">Full Test — All 4 Modules</p>
            </div>
          </motion.div>
        )}

        {/* Module score grid */}
        <div className="grid grid-cols-2 gap-4 mb-8" data-testid="module-scores-grid">
          {moduleOrder.map(mod => {
            const Icon = moduleIcons2[mod];
            const modScore = moduleScores[mod];
            const isExpanded = expandedModules[mod];
            const band = modScore?.band_score || modScore?.overall_band || modScore?.overall_writing_band;

            return (
              <motion.div key={mod} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
                transition={{ delay: moduleOrder.indexOf(mod) * 0.1 }}
                className="card-ps overflow-hidden" data-testid={`module-score-${mod}`}>
                <div className="p-5">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <Icon size={16} className="text-[var(--ps-blue)]" />
                      <span className="font-semibold text-sm capitalize">{moduleLabels[mod]}</span>
                    </div>
                    <span className="text-2xl font-light" style={{ color: getBandColor(band) }}>
                      {band || "—"}
                    </span>
                  </div>
                  {modScore && (
                    <button
                      onClick={() => toggleModule(mod)}
                      className="flex items-center gap-1 text-xs text-[var(--ps-body-gray)] hover:text-[var(--ps-blue)] transition-colors mt-2">
                      {isExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                      {isExpanded ? "Hide Details" : "View Details"}
                    </button>
                  )}
                </div>

                <AnimatePresence>
                  {isExpanded && modScore && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="border-t border-[var(--ps-divider)] overflow-hidden">
                      <div className="p-4 bg-[var(--ps-ice)]/50">
                        {(mod === "listening" || mod === "reading") && (
                          <p className="text-xs text-[var(--ps-body-gray)]">
                            {modScore.correct} / {modScore.total} correct ({modScore.percentage}%)
                          </p>
                        )}
                        {mod === "writing" && modScore.task_1 && (
                          <div className="space-y-1">
                            <p className="text-xs text-[var(--ps-body-gray)]">Task 1: Band {modScore.task_1?.overall_band}</p>
                            <p className="text-xs text-[var(--ps-body-gray)]">Task 2: Band {modScore.task_2?.overall_band}</p>
                          </div>
                        )}
                        {mod === "speaking" && (
                          <div className="space-y-1">
                            {["fluency_coherence", "lexical_resource", "grammatical_range", "pronunciation"].map(c => (
                              <p key={c} className="text-xs text-[var(--ps-body-gray)] capitalize">
                                {c.replace(/_/g, " ")}: {modScore[c]?.band}
                              </p>
                            ))}
                          </div>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            );
          })}
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
        <span className="font-semibold text-base">
          {isFullTest ? "Full Test Results" : isTelc ? "TELC Ergebnisse" : "Results"}
        </span>
        <div />
      </nav>

      <div className="max-w-3xl mx-auto px-6 py-10">
        <div className="flex items-center gap-3 mb-8" data-testid="results-header">
          {isFullTest ? (
            <TrendingUp size={24} className="text-[var(--ps-blue)]" />
          ) : (
            <ModuleIcon size={24} className="text-[var(--ps-blue)]" />
          )}
          <h1 className="display-compact capitalize" style={{ color: "var(--ps-black)" }}>
            {isFullTest ? "Full Test Results" : isTelc ? `${module === "writing" ? "Schreiben" : module === "reading" ? "Lesen" : module === "listening" ? "Hören" : "Sprechen"} Ergebnisse` : `${module} Results`}
          </h1>
          {isTelc && !isFullTest && (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-50 text-amber-700 border border-amber-200">
              TELC
            </span>
          )}
        </div>

        {isFullTest ? renderFullTestResults() : (
          <>
            {isTelc ? (
              <>
                {(module === "listening" || module === "reading") ? renderTelcObjectiveResults() : null}
                {module === "writing" ? renderTelcWritingResults() : null}
                {module === "speaking" ? renderSpeakingResults() : null}
              </>
            ) : (
              <>
                {(module === "listening" || module === "reading") ? renderObjectiveResults() : null}
                {module === "writing" ? renderWritingResults() : null}
                {module === "speaking" ? renderSpeakingResults() : null}
              </>
            )}
          </>
        )}

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
