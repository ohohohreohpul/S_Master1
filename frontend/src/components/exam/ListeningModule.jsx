import { useState, useRef, useEffect, useCallback } from "react";
import { Play, Volume2, Flag } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { motion } from "framer-motion";

export default function ListeningModule({ exam, audioCache, answers, updateAnswer, flagged, toggleFlag }) {
  const [currentSection, setCurrentSection] = useState(1);
  const [isPlaying, setIsPlaying] = useState(false);
  const [audioIndex, setAudioIndex] = useState(0);
  const [audioProgress, setAudioProgress] = useState(0);
  const [sectionStarted, setSectionStarted] = useState({});
  const [sectionCompleted, setSectionCompleted] = useState({});
  const [playingInstruction, setPlayingInstruction] = useState(false);
  const audioRef = useRef(null);
  const progressIntervalRef = useRef(null);

  const sections = exam?.listening?.sections || [];
  const section = sections.find(s => s.section_num === currentSection);
  const sectionAudios = audioCache[currentSection] || [];

  const playNextSegment = useCallback(() => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    if (progressIntervalRef.current) clearInterval(progressIntervalRef.current);

    if (audioIndex >= sectionAudios.length) {
      setIsPlaying(false);
      setSectionCompleted(prev => ({ ...prev, [currentSection]: true }));
      return;
    }

    const url = sectionAudios[audioIndex];
    if (!url) { setAudioIndex(prev => prev + 1); return; }

    const audio = new Audio(url);
    audioRef.current = audio;

    audio.addEventListener("canplaythrough", () => {
      audio.play().catch(e => console.error("Audio play error:", e));
      setIsPlaying(true);
      progressIntervalRef.current = setInterval(() => {
        if (audio.duration) {
          const overallProgress = ((audioIndex + audio.currentTime / audio.duration) / sectionAudios.length) * 100;
          setAudioProgress(overallProgress);
        }
      }, 200);
    });

    audio.addEventListener("ended", () => {
      clearInterval(progressIntervalRef.current);
      // Natural pause between speaker turns (750ms breathing room)
      setTimeout(() => {
        setAudioIndex(prev => prev + 1);
      }, 750);
    });

    audio.addEventListener("error", () => {
      clearInterval(progressIntervalRef.current);
      setAudioIndex(prev => prev + 1);
    });
  }, [audioIndex, sectionAudios, currentSection]);

  useEffect(() => {
    if (sectionStarted[currentSection] && !playingInstruction && audioIndex < sectionAudios.length) {
      playNextSegment();
    } else if (audioIndex >= sectionAudios.length && sectionStarted[currentSection]) {
      setIsPlaying(false);
      setSectionCompleted(prev => ({ ...prev, [currentSection]: true }));
    }
  }, [audioIndex, sectionStarted, currentSection, sectionAudios.length, playingInstruction]);

  const startSection = () => {
    setSectionStarted(prev => ({ ...prev, [currentSection]: true }));
    const instrUrl = audioCache[`instruction_${currentSection}`];
    if (instrUrl) {
      setPlayingInstruction(true);
      const instrAudio = new Audio(instrUrl);
      instrAudio.addEventListener("ended", () => {
        setPlayingInstruction(false);
        setAudioIndex(0);
        setAudioProgress(0);
      });
      instrAudio.addEventListener("error", () => {
        setPlayingInstruction(false);
        setAudioIndex(0);
        setAudioProgress(0);
      });
      instrAudio.play().catch(() => {
        setPlayingInstruction(false);
        setAudioIndex(0);
        setAudioProgress(0);
      });
    } else {
      setAudioIndex(0);
      setAudioProgress(0);
    }
  };

  const switchSection = (sectionNum) => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    if (progressIntervalRef.current) clearInterval(progressIntervalRef.current);
    setIsPlaying(false);
    setCurrentSection(sectionNum);
    setAudioIndex(0);
    setAudioProgress(0);
  };

  useEffect(() => {
    return () => {
      if (audioRef.current) audioRef.current.pause();
      if (progressIntervalRef.current) clearInterval(progressIntervalRef.current);
    };
  }, []);

  // Render inline form field - replaces {N} with input
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
                  placeholder=""
                />
              </span>
            );
          }
          return <span key={i} className="text-sm" style={{ color: "var(--ps-charcoal)" }}>{part}</span>;
        })}
      </span>
    );
  };

  // IELTS CBT-style structured form renderer
  const renderStructuredForm = (layout) => {
    if (!layout) return null;
    return (
      <div className="ielts-form" data-testid="ielts-form">
        {/* Form header */}
        <div className="ielts-form-header">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-bold uppercase tracking-wider text-[var(--ps-body-gray)]">
              Questions {section?.questions?.[0]?.question_num} - {section?.questions?.[section.questions.length - 1]?.question_num}
            </span>
          </div>
          <p className="text-xs text-[var(--ps-body-gray)] leading-relaxed">{layout.instruction}</p>
        </div>

        {/* Form title */}
        <div className="ielts-form-body">
          <h4 className="font-bold text-base mb-4" style={{ color: "var(--ps-charcoal)" }}>
            {layout.title}
          </h4>

          {/* Groups */}
          {(layout.groups || []).map((group, gi) => (
            <div key={gi} className="mb-5">
              <h5 className="font-bold text-sm mb-2.5" style={{ color: "var(--ps-charcoal)" }}>
                {group.heading}
              </h5>
              <div className="space-y-2 ml-1">
                {(group.items || []).map((item, ii) => (
                  <div key={ii} className="flex items-center gap-2 py-1">
                    <span className="text-[var(--ps-mute)] text-xs">-</span>
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

  // Fallback: old-style question rendering (for multiple_choice etc.)
  const renderFallbackQuestion = (q) => {
    const qNum = String(q.question_num);
    const isFlagged = flagged.has(qNum);

    return (
      <div key={qNum} className={`question-block ${isFlagged ? "border-[var(--ps-orange)]" : ""}`} data-testid={`question-${qNum}`}>
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2">
            <span className={`q-pill ${answers[qNum] ? "answered" : ""} ${isFlagged ? "flagged" : ""}`}>{q.question_num}</span>
            <span className="text-xs text-[var(--ps-body-gray)] capitalize">{q.question_type?.replace(/_/g, " ")}</span>
          </div>
          <button onClick={() => toggleFlag(qNum)} className="p-1 rounded hover:bg-[var(--ps-ice)]" data-testid={`flag-${qNum}`}>
            <Flag size={14} className={isFlagged ? "text-[var(--ps-orange)] fill-[var(--ps-orange)]" : "text-[var(--ps-mute)]"} />
          </button>
        </div>
        <p className="text-sm font-medium mb-3" style={{ color: "var(--ps-charcoal)" }}>{q.question_text}</p>
        {q.question_type === "multiple_choice" && q.options ? (
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
          <input type="text" className="exam-input" placeholder="Type your answer..." value={answers[qNum] || ""}
            onChange={(e) => updateAnswer(qNum, e.target.value)} data-testid={`input-${qNum}`} />
        )}
      </div>
    );
  };

  // Determine which questions to render as fallback (MC questions not in layout)
  const layoutQuestionNums = new Set();
  if (section?.question_layout) {
    (section.question_layout.groups || []).forEach(g => {
      (g.items || []).forEach(item => {
        const matches = item.match(/\{(\d+)\}/g) || [];
        matches.forEach(m => layoutQuestionNums.add(parseInt(m.replace(/[{}]/g, ""))));
      });
    });
  }
  const fallbackQuestions = (section?.questions || []).filter(q => !layoutQuestionNums.has(q.question_num));

  return (
    <div data-testid="listening-module">
      {/* Section tabs */}
      <div className="bg-[var(--ps-black)] px-8 py-3 flex items-center gap-3" data-testid="section-tabs">
        {sections.map(s => (
          <button key={s.section_num} onClick={() => switchSection(s.section_num)}
            className={`section-tab ${currentSection === s.section_num ? "active" : ""}`}
            data-testid={`section-tab-${s.section_num}`}>
            Section {s.section_num}
          </button>
        ))}
      </div>

      <div className="exam-body">
        {/* Left - Audio & Info */}
        <div className="exam-left">
          {/* Part header - IELTS style */}
          <div className="mb-4 pb-4 border-b border-[var(--ps-divider)]">
            <h3 className="font-bold text-lg" style={{ color: "var(--ps-charcoal)" }}>
              Part {currentSection}
            </h3>
            <p className="text-sm text-[var(--ps-body-gray)] mt-1">
              Listen and answer questions {section?.questions?.[0]?.question_num} - {section?.questions?.[section.questions.length - 1]?.question_num}
            </p>
          </div>

          {/* Audio Controls */}
          <div className="card-ps p-5 mb-6" data-testid="audio-controls">
            {!sectionStarted[currentSection] ? (
              <div className="text-center py-4">
                <p className="text-sm text-[var(--ps-body-gray)] mb-4">Audio is pre-loaded. Click to begin.</p>
                <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
                  onClick={startSection} className="btn-ps btn-ps-primary"
                  style={{ padding: "12px 32px", fontSize: "0.875rem" }}
                  data-testid="play-section-btn">
                  <Play size={16} /> Play Section {currentSection}
                </motion.button>
              </div>
            ) : (
              <div>
                <div className="flex items-center gap-3 mb-3">
                  {playingInstruction ? (
                    <div className="flex items-center gap-2">
                      <div className="audio-playing" data-testid="instruction-indicator">
                        <span /><span /><span /><span /><span />
                      </div>
                      <span className="text-sm font-medium text-[var(--ps-blue)]">Playing instructions...</span>
                    </div>
                  ) : isPlaying ? (
                    <div className="flex items-center gap-2">
                      <div className="audio-playing" data-testid="audio-indicator">
                        <span /><span /><span /><span /><span />
                      </div>
                      <span className="text-sm font-medium">Segment {audioIndex + 1} of {sectionAudios.length}</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <Volume2 size={18} className="text-[var(--ps-mute)]" />
                      <span className="text-sm font-medium text-[var(--ps-body-gray)]">
                        {sectionCompleted[currentSection] ? "Audio complete" : "Loading..."}
                      </span>
                    </div>
                  )}
                </div>
                <Progress value={audioProgress} className="h-1.5" />
                <p className="text-[10px] text-[var(--ps-body-gray)] mt-2">
                  {sectionCompleted[currentSection]
                    ? "Review your answers. Audio cannot be replayed."
                    : "Listen carefully. Audio plays once only."}
                </p>
              </div>
            )}
          </div>

          {/* Question Navigator */}
          <div className="card-ps p-4" data-testid="question-navigator">
            <p className="text-[10px] font-bold text-[var(--ps-body-gray)] mb-3 uppercase tracking-wider">Questions</p>
            <div className="flex flex-wrap gap-1.5">
              {(section?.questions || []).map(q => {
                const qNum = String(q.question_num);
                return (
                  <div key={qNum} className={`q-pill ${answers[qNum] ? "answered" : ""} ${flagged.has(qNum) ? "flagged" : ""}`}
                    style={{ width: 28, height: 28, fontSize: "0.7rem" }}>
                    {q.question_num}
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right - Questions (IELTS CBT style) */}
        <div className="exam-right" data-testid="questions-panel">
          {/* Structured form layout */}
          {section?.question_layout && renderStructuredForm(section.question_layout)}

          {/* Fallback questions (MC, etc.) not in layout */}
          {fallbackQuestions.length > 0 && (
            <div className={section?.question_layout ? "mt-6 pt-6 border-t border-[var(--ps-divider)]" : ""}>
              {fallbackQuestions.map(q => renderFallbackQuestion(q))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
