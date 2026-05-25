import { useState, useRef, useEffect, useCallback } from "react";
import { Play, Volume2, Flag, RotateCcw, CheckCircle2, XCircle, Clock, ChevronRight } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { motion, AnimatePresence } from "framer-motion";

// ── Phase machine ──────────────────────────────────────────────────────────────
// idle → preparing → playing → [replay_ready →] reviewing
// "replay_ready" only when heard_times === 2

export default function ListeningModule({ exam, audioCache, answers, updateAnswer, flagged, toggleFlag }) {
  const isTelc = exam?.exam_type === "telc";
  const [currentSection, setCurrentSection] = useState(1);
  const [audioPhase, setAudioPhase] = useState("idle"); // idle | preparing | playing | replay_ready | replaying | reviewing
  const [audioIndex, setAudioIndex] = useState(0);
  const [audioProgress, setAudioProgress] = useState(0);
  const [prepCountdown, setPrepCountdown] = useState(0);
  const [playingInstruction, setPlayingInstruction] = useState(false);

  const audioRef = useRef(null);
  const progressIntervalRef = useRef(null);
  const prepTimerRef = useRef(null);

  const sections = exam?.listening?.sections || [];
  const section = sections.find(s => s.section_num === currentSection);
  const sectionAudios = audioCache[currentSection] || [];
  const heardTimes = section?.heard_times ?? (currentSection === 1 ? 1 : 2);
  const prepSeconds = section?.preparation_seconds ?? (currentSection === 2 ? 60 : 30);

  // ── Audio playback ─────────────────────────────────────────────────────────
  const stopAudio = useCallback(() => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    if (progressIntervalRef.current) clearInterval(progressIntervalRef.current);
  }, []);

  const playNextSegment = useCallback(() => {
    stopAudio();
    if (audioIndex >= sectionAudios.length) {
      setAudioPhase(prev => {
        if (heardTimes >= 2 && prev === "playing") return "replay_ready";
        return "reviewing";
      });
      return;
    }
    const url = sectionAudios[audioIndex];
    if (!url) { setAudioIndex(i => i + 1); return; }

    const audio = new Audio(url);
    audioRef.current = audio;

    audio.addEventListener("canplaythrough", () => {
      audio.play().catch(() => {});
      progressIntervalRef.current = setInterval(() => {
        if (audio.duration) {
          setAudioProgress(((audioIndex + audio.currentTime / audio.duration) / sectionAudios.length) * 100);
        }
      }, 200);
    });
    audio.addEventListener("ended", () => {
      clearInterval(progressIntervalRef.current);
      setTimeout(() => setAudioIndex(i => i + 1), 650);
    });
    audio.addEventListener("error", () => {
      clearInterval(progressIntervalRef.current);
      setAudioIndex(i => i + 1);
    });
  }, [audioIndex, sectionAudios, heardTimes, stopAudio]);

  useEffect(() => {
    if ((audioPhase === "playing" || audioPhase === "replaying") && !playingInstruction) {
      playNextSegment();
    }
  }, [audioIndex, audioPhase, playingInstruction]);

  useEffect(() => {
    if (audioIndex >= sectionAudios.length && (audioPhase === "playing" || audioPhase === "replaying")) {
      // handled inside playNextSegment via phase check
    }
  }, [audioIndex, sectionAudios.length, audioPhase]);

  // ── Preparation timer ──────────────────────────────────────────────────────
  const startPrepTimer = useCallback((seconds, onDone) => {
    clearInterval(prepTimerRef.current);
    setPrepCountdown(seconds);
    let remaining = seconds;
    prepTimerRef.current = setInterval(() => {
      remaining -= 1;
      setPrepCountdown(remaining);
      if (remaining <= 0) {
        clearInterval(prepTimerRef.current);
        onDone();
      }
    }, 1000);
  }, []);

  // ── Section control ────────────────────────────────────────────────────────
  const beginSection = () => {
    const instrUrl = audioCache[`instruction_${currentSection}`];
    if (instrUrl) {
      setAudioPhase("preparing"); // show questions while instruction plays
      setPlayingInstruction(true);
      const instrAudio = new Audio(instrUrl);
      instrAudio.addEventListener("ended", () => {
        startPrepTimer(prepSeconds, () => {
          setPlayingInstruction(false);
          setAudioIndex(0);
          setAudioProgress(0);
          setAudioPhase("playing");
        });
      });
      instrAudio.addEventListener("error", () => {
        setPlayingInstruction(false);
        startPrepTimer(prepSeconds, () => {
          setAudioIndex(0);
          setAudioProgress(0);
          setAudioPhase("playing");
        });
      });
      instrAudio.play().catch(() => {
        setPlayingInstruction(false);
        setAudioIndex(0);
        setAudioProgress(0);
        setAudioPhase("playing");
      });
    } else {
      setAudioPhase("preparing");
      startPrepTimer(prepSeconds, () => {
        setAudioIndex(0);
        setAudioProgress(0);
        setAudioPhase("playing");
      });
    }
  };

  const skipPrep = () => {
    clearInterval(prepTimerRef.current);
    setPlayingInstruction(false);
    stopAudio();
    setAudioIndex(0);
    setAudioProgress(0);
    setAudioPhase("playing");
  };

  const replaySection = () => {
    setAudioIndex(0);
    setAudioProgress(0);
    setAudioPhase("replaying");
  };

  const switchSection = (sectionNum) => {
    clearInterval(prepTimerRef.current);
    stopAudio();
    setCurrentSection(sectionNum);
    setAudioPhase("idle");
    setAudioIndex(0);
    setAudioProgress(0);
    setPrepCountdown(0);
    setPlayingInstruction(false);
  };

  useEffect(() => () => {
    stopAudio();
    clearInterval(prepTimerRef.current);
  }, []);

  // ── R/F answer button ──────────────────────────────────────────────────────
  const RFButton = ({ qNum, value }) => {
    const selected = answers[qNum] === value;
    const isRichtig = value === "Richtig";
    return (
      <button
        onClick={() => updateAnswer(qNum, value)}
        className={`flex items-center gap-1.5 px-4 py-2 rounded-xl border-2 text-sm font-semibold transition-all select-none ${
          selected
            ? isRichtig
              ? "border-emerald-500 bg-emerald-500/10 text-emerald-700"
              : "border-red-500 bg-red-500/10 text-red-700"
            : "border-[var(--ps-divider)] hover:border-[var(--ps-mute)] text-[var(--ps-body-gray)]"
        }`}
        data-testid={`option-${qNum}-${value}`}
      >
        {isRichtig
          ? <CheckCircle2 size={14} className={selected ? "text-emerald-600" : "text-[var(--ps-mute)]"} />
          : <XCircle size={14} className={selected ? "text-red-600" : "text-[var(--ps-mute)]"} />
        }
        {value}
      </button>
    );
  };

  // ── Render inline field ────────────────────────────────────────────────────
  const renderInlineField = (text) => {
    const parts = text.split(/(\{\d+\})/);
    return (
      <span className="inline-field-row">
        {parts.map((part, i) => {
          const match = part.match(/\{(\d+)\}/);
          if (match) {
            const qNum = match[1];
            const isFlagged = flagged.has(qNum);
            return (
              <span key={i} className="relative inline-flex items-center">
                <span className="ielts-q-number">{qNum}</span>
                <input
                  type="text"
                  className={`ielts-inline-input ${answers[qNum] ? "answered" : ""} ${isFlagged ? "flagged" : ""}`}
                  value={answers[qNum] || ""}
                  onChange={(e) => updateAnswer(qNum, e.target.value)}
                  data-testid={`input-${qNum}`}
                />
              </span>
            );
          }
          return <span key={i} className="text-sm" style={{ color: "var(--ps-charcoal)" }}>{part}</span>;
        })}
      </span>
    );
  };

  // ── Structured form layout (IELTS) ─────────────────────────────────────────
  const renderStructuredForm = (layout) => {
    if (!layout) return null;
    return (
      <div className="ielts-form" data-testid="ielts-form">
        <div className="ielts-form-header">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-bold uppercase tracking-wider text-[var(--ps-body-gray)]">
              Questions {section?.questions?.[0]?.question_num} – {section?.questions?.[section.questions.length - 1]?.question_num}
            </span>
          </div>
          <p className="text-xs text-[var(--ps-body-gray)] leading-relaxed">{layout.instruction}</p>
        </div>
        <div className="ielts-form-body">
          <h4 className="font-bold text-base mb-4" style={{ color: "var(--ps-charcoal)" }}>{layout.title}</h4>
          {(layout.groups || []).map((group, gi) => (
            <div key={gi} className="mb-5">
              <h5 className="font-bold text-sm mb-2.5" style={{ color: "var(--ps-charcoal)" }}>{group.heading}</h5>
              <div className="space-y-2 ml-1">
                {(group.items || []).map((item, ii) => (
                  <div key={ii} className="flex items-center gap-2 py-1">
                    <span className="text-[var(--ps-mute)] text-xs">–</span>
                    {renderInlineField(item)}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  };

  // ── Fallback question (IELTS MC or TELC R/F) ──────────────────────────────
  const renderQuestion = (q) => {
    const qNum = String(q.question_num);
    const isFlagged = flagged.has(qNum);
    const isRF = q.question_type === "richtig_falsch" || q.correct_answer === "Richtig" || q.correct_answer === "Falsch";

    return (
      <div key={qNum}
        className={`question-block ${isFlagged ? "border-[var(--ps-orange)]" : ""}`}
        data-testid={`question-${qNum}`}
      >
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2">
            <span className={`q-pill ${answers[qNum] ? "answered" : ""} ${isFlagged ? "flagged" : ""}`}>
              {q.question_num}
            </span>
          </div>
          <button onClick={() => toggleFlag(qNum)} className="p-1 rounded hover:bg-[var(--ps-ice)]" data-testid={`flag-${qNum}`}>
            <Flag size={14} className={isFlagged ? "text-[var(--ps-orange)] fill-[var(--ps-orange)]" : "text-[var(--ps-mute)]"} />
          </button>
        </div>

        <p className="text-sm font-medium mb-3" style={{ color: "var(--ps-charcoal)" }}>{q.question_text}</p>

        {isRF ? (
          <div className="flex gap-3">
            <RFButton qNum={qNum} value="Richtig" />
            <RFButton qNum={qNum} value="Falsch" />
          </div>
        ) : q.question_type === "multiple_choice" && q.options ? (
          <div className="space-y-2">
            {q.options.map((opt, i) => (
              <label key={i} className="flex items-center gap-3 p-2 rounded-lg hover:bg-[var(--ps-ice)] cursor-pointer" data-testid={`option-${qNum}-${i}`}>
                <input type="radio" name={`q_${qNum}`} value={opt.charAt(0)} checked={answers[qNum] === opt.charAt(0)}
                  onChange={(e) => updateAnswer(qNum, e.target.value)} className="w-4 h-4 accent-[var(--ps-blue)]" />
                <span className="text-sm">{opt}</span>
              </label>
            ))}
          </div>
        ) : (
          <input type="text" className="exam-input" placeholder="Antwort..." value={answers[qNum] || ""}
            onChange={(e) => updateAnswer(qNum, e.target.value)} data-testid={`input-${qNum}`} />
        )}
      </div>
    );
  };

  // Questions not covered by a structured layout (MC etc.)
  const layoutQuestionNums = new Set();
  if (section?.question_layout) {
    (section.question_layout.groups || []).forEach(g => {
      (g.items || []).forEach(item => {
        (item.match(/\{(\d+)\}/g) || []).forEach(m => layoutQuestionNums.add(parseInt(m.replace(/[{}]/g, ""))));
      });
    });
  }
  const fallbackQuestions = (section?.questions || []).filter(q => !layoutQuestionNums.has(q.question_num));

  // ── Audio control panel ────────────────────────────────────────────────────
  const renderAudioControls = () => {
    const isActive = audioPhase !== "idle";
    const isPlaying = audioPhase === "playing" || audioPhase === "replaying";
    const isDone = audioPhase === "reviewing";
    const canReplay = audioPhase === "replay_ready" && heardTimes >= 2;
    const isPreparing = audioPhase === "preparing";

    if (audioPhase === "idle") {
      return (
        <div className="card-ps p-5 mb-6 text-center" data-testid="audio-controls">
          {/* Heard-times badge */}
          <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold mb-4"
            style={{ background: heardTimes === 1 ? "rgba(239,68,68,0.1)" : "rgba(59,130,246,0.1)",
                     color: heardTimes === 1 ? "#dc2626" : "#2563eb" }}>
            <Volume2 size={11} />
            {heardTimes === 1 ? "Einmal gehört — keine Wiederholung" : "Zweimal gehört"}
          </div>

          {section?.instruction && (
            <p className="text-xs text-[var(--ps-body-gray)] leading-relaxed mb-4 text-left border-l-2 border-[var(--ps-divider)] pl-3">
              {section.instruction}
            </p>
          )}
          {section?.topic && (
            <p className="text-xs font-semibold text-[var(--ps-body-gray)] mb-4">
              Thema: <span className="text-[var(--ps-charcoal)]">{section.topic}</span>
            </p>
          )}

          <p className="text-xs text-[var(--ps-mute)] mb-4">
            Lesen Sie die Aufgaben. Dann starten Sie das Audio.
          </p>
          <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
            onClick={beginSection}
            className="btn-ps btn-ps-primary"
            style={{ padding: "11px 28px", fontSize: "0.875rem" }}
            data-testid="play-section-btn">
            <Play size={15} />
            {isTelc ? `Teil ${currentSection} starten` : `Section ${currentSection} starten`}
          </motion.button>
        </div>
      );
    }

    if (isPreparing) {
      return (
        <div className="card-ps p-5 mb-6" data-testid="audio-controls">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Clock size={16} className="text-[var(--ps-blue)]" />
              <span className="text-sm font-semibold text-[var(--ps-blue)]">
                {playingInstruction ? "Anweisung läuft..." : "Vorbereitungszeit"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {!playingInstruction && (
                <span className="text-2xl font-bold tabular-nums" style={{ color: prepCountdown <= 5 ? "#dc2626" : "var(--ps-charcoal)" }}>
                  {prepCountdown}s
                </span>
              )}
              <button onClick={skipPrep} className="text-xs text-[var(--ps-mute)] hover:text-[var(--ps-charcoal)] underline ml-1">
                Überspringen
              </button>
            </div>
          </div>
          {!playingInstruction && (
            <Progress value={((prepSeconds - prepCountdown) / prepSeconds) * 100} className="h-1.5 mb-2" />
          )}
          <p className="text-[10px] text-[var(--ps-mute)]">
            Lesen Sie jetzt die Aufgaben. Das Audio startet automatisch.
          </p>
        </div>
      );
    }

    if (canReplay) {
      return (
        <div className="card-ps p-5 mb-6 text-center" data-testid="audio-controls">
          <div className="flex items-center gap-2 mb-3 justify-center">
            <Volume2 size={16} className="text-[var(--ps-mute)]" />
            <span className="text-sm font-medium text-[var(--ps-body-gray)]">Erste Wiedergabe abgeschlossen</span>
          </div>
          <p className="text-xs text-[var(--ps-mute)] mb-4">
            Sie können diesen Teil noch einmal hören.
          </p>
          <div className="flex gap-3 justify-center">
            <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
              onClick={replaySection}
              className="btn-ps"
              style={{ padding: "9px 20px", fontSize: "0.8rem", background: "var(--ps-ice)", color: "var(--ps-charcoal)" }}
              data-testid="replay-btn">
              <RotateCcw size={13} /> Noch einmal hören
            </motion.button>
            <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
              onClick={() => setAudioPhase("reviewing")}
              className="btn-ps btn-ps-primary"
              style={{ padding: "9px 20px", fontSize: "0.8rem" }}
              data-testid="skip-replay-btn">
              Weiter <ChevronRight size={13} />
            </motion.button>
          </div>
        </div>
      );
    }

    return (
      <div className="card-ps p-5 mb-6" data-testid="audio-controls">
        <div className="flex items-center gap-3 mb-3">
          {isPlaying ? (
            <div className="flex items-center gap-2">
              <div className="audio-playing" data-testid="audio-indicator"><span /><span /><span /><span /><span /></div>
              <span className="text-sm font-medium">
                {audioPhase === "replaying" ? "Zweite Wiedergabe..." : "Audio läuft..."}
              </span>
            </div>
          ) : isDone ? (
            <div className="flex items-center gap-2">
              <Volume2 size={18} className="text-[var(--ps-mute)]" />
              <span className="text-sm font-medium text-[var(--ps-body-gray)]">Audio abgeschlossen</span>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Volume2 size={18} className="text-[var(--ps-mute)]" />
              <span className="text-sm text-[var(--ps-body-gray)]">Wird geladen...</span>
            </div>
          )}
        </div>
        {isActive && (
          <Progress value={isPlaying ? audioProgress : 100} className="h-1.5" />
        )}
        <p className="text-[10px] text-[var(--ps-mute)] mt-2">
          {isDone
            ? "Überprüfen Sie Ihre Antworten. Das Audio kann nicht erneut abgespielt werden."
            : heardTimes === 1
              ? "Audio wird einmal abgespielt."
              : "Audio wird zweimal abgespielt."}
        </p>
      </div>
    );
  };

  return (
    <div data-testid="listening-module">
      {/* Section / Teil tabs */}
      <div className="bg-[var(--ps-black)] px-8 py-3 flex items-center gap-3" data-testid="section-tabs">
        {sections.map(s => (
          <button key={s.section_num} onClick={() => switchSection(s.section_num)}
            className={`section-tab ${currentSection === s.section_num ? "active" : ""}`}
            data-testid={`section-tab-${s.section_num}`}>
            {isTelc ? `Teil ${s.section_num}` : `Section ${s.section_num}`}
          </button>
        ))}
      </div>

      <div className="exam-body">
        {/* Left — Audio controls */}
        <div className="exam-left">
          <div className="mb-4 pb-4 border-b border-[var(--ps-divider)]">
            <h3 className="font-bold text-lg" style={{ color: "var(--ps-charcoal)" }}>
              {isTelc ? `Teil ${currentSection}` : `Part ${currentSection}`}
            </h3>
            <p className="text-xs text-[var(--ps-body-gray)] mt-0.5">
              {section?.questions?.length || 0} {isTelc ? "Aufgaben" : "questions"}
              {" · "}q{section?.questions?.[0]?.question_num}–{section?.questions?.[section.questions.length - 1]?.question_num}
            </p>
          </div>

          {renderAudioControls()}

          {/* Question navigator */}
          <div className="card-ps p-4" data-testid="question-navigator">
            <p className="text-[10px] font-bold text-[var(--ps-body-gray)] mb-3 uppercase tracking-wider">Aufgaben</p>
            <div className="flex flex-wrap gap-1.5">
              {(section?.questions || []).map(q => {
                const qNum = String(q.question_num);
                return (
                  <div key={qNum}
                    className={`q-pill ${answers[qNum] ? "answered" : ""} ${flagged.has(qNum) ? "flagged" : ""}`}
                    style={{ width: 28, height: 28, fontSize: "0.7rem" }}>
                    {q.question_num}
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right — Questions (always visible) */}
        <div className="exam-right" data-testid="questions-panel">
          {/* Structured form layout (IELTS gap-fill) */}
          {section?.question_layout && renderStructuredForm(section.question_layout)}

          {/* Standard questions: R/F for TELC, MC / text for IELTS */}
          {fallbackQuestions.length > 0 && (
            <div className={section?.question_layout ? "mt-6 pt-6 border-t border-[var(--ps-divider)]" : ""}>
              {/* Show idle overlay hint */}
              <AnimatePresence>
                {audioPhase === "idle" && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="mb-5 px-4 py-3 rounded-xl border border-[var(--ps-divider)] bg-[var(--ps-ice)] flex items-center gap-2"
                  >
                    <Clock size={14} className="text-[var(--ps-mute)] flex-shrink-0" />
                    <p className="text-xs text-[var(--ps-body-gray)]">
                      Lesen Sie die Aufgaben, bevor Sie das Audio starten.
                    </p>
                  </motion.div>
                )}
              </AnimatePresence>

              {fallbackQuestions.map(q => renderQuestion(q))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
