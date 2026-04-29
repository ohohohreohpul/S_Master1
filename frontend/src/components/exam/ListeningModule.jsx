import { useState, useRef, useEffect, useCallback } from "react";
import { Play, Pause, Volume2, Flag, SkipForward } from "lucide-react";
import { Progress } from "@/components/ui/progress";

export default function ListeningModule({ exam, audioCache, answers, updateAnswer, flagged, toggleFlag }) {
  const [currentSection, setCurrentSection] = useState(1);
  const [isPlaying, setIsPlaying] = useState(false);
  const [audioIndex, setAudioIndex] = useState(0);
  const [audioProgress, setAudioProgress] = useState(0);
  const [sectionStarted, setSectionStarted] = useState({});
  const [sectionCompleted, setSectionCompleted] = useState({});
  const audioRef = useRef(null);
  const progressIntervalRef = useRef(null);

  const sections = exam?.listening?.sections || [];
  const section = sections.find(s => s.section_num === currentSection);
  const sectionAudios = audioCache[currentSection] || [];

  // Play audio segments sequentially
  const playNextSegment = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (progressIntervalRef.current) {
      clearInterval(progressIntervalRef.current);
    }

    if (audioIndex >= sectionAudios.length) {
      setIsPlaying(false);
      setSectionCompleted(prev => ({ ...prev, [currentSection]: true }));
      return;
    }

    const url = sectionAudios[audioIndex];
    if (!url) {
      setAudioIndex(prev => prev + 1);
      return;
    }

    const audio = new Audio(url);
    audioRef.current = audio;

    audio.addEventListener("canplaythrough", () => {
      audio.play().catch(e => console.error("Audio play error:", e));
      setIsPlaying(true);

      progressIntervalRef.current = setInterval(() => {
        if (audio.duration) {
          const segmentProgress = (audio.currentTime / audio.duration) * 100;
          const overallProgress = ((audioIndex + audio.currentTime / audio.duration) / sectionAudios.length) * 100;
          setAudioProgress(overallProgress);
        }
      }, 200);
    });

    audio.addEventListener("ended", () => {
      clearInterval(progressIntervalRef.current);
      setAudioIndex(prev => prev + 1);
    });

    audio.addEventListener("error", (e) => {
      console.error("Audio error:", e);
      clearInterval(progressIntervalRef.current);
      setAudioIndex(prev => prev + 1);
    });
  }, [audioIndex, sectionAudios, currentSection]);

  // Auto-play next segment when audioIndex changes
  useEffect(() => {
    if (sectionStarted[currentSection] && audioIndex < sectionAudios.length) {
      playNextSegment();
    } else if (audioIndex >= sectionAudios.length && sectionStarted[currentSection]) {
      setIsPlaying(false);
      setSectionCompleted(prev => ({ ...prev, [currentSection]: true }));
    }
  }, [audioIndex, sectionStarted, currentSection, sectionAudios.length]);

  // Start section audio
  const startSection = () => {
    setSectionStarted(prev => ({ ...prev, [currentSection]: true }));
    setAudioIndex(0);
    setAudioProgress(0);
  };

  // Switch section
  const switchSection = (sectionNum) => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (progressIntervalRef.current) clearInterval(progressIntervalRef.current);
    setIsPlaying(false);
    setCurrentSection(sectionNum);
    setAudioIndex(0);
    setAudioProgress(0);
  };

  // Cleanup
  useEffect(() => {
    return () => {
      if (audioRef.current) audioRef.current.pause();
      if (progressIntervalRef.current) clearInterval(progressIntervalRef.current);
    };
  }, []);

  const renderQuestion = (q) => {
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
                  onChange={(e) => updateAnswer(qNum, e.target.value)}
                  className="w-4 h-4 accent-[var(--ps-blue)]" />
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
          <div className="mb-6">
            <h3 className="font-medium text-lg mb-1" style={{ color: "var(--ps-charcoal)" }}>
              Section {currentSection}: {section?.title}
            </h3>
            <p className="text-sm text-[var(--ps-body-gray)]">{section?.context}</p>
          </div>

          {/* Audio Controls */}
          <div className="card-ps p-5 mb-6" data-testid="audio-controls">
            {!sectionStarted[currentSection] ? (
              <div className="text-center">
                <p className="text-sm text-[var(--ps-body-gray)] mb-4">Audio is pre-loaded and ready. Click play to begin this section.</p>
                <button onClick={startSection} className="btn-ps btn-ps-primary" style={{ padding: "10px 28px", fontSize: "0.875rem" }}
                  data-testid="play-section-btn">
                  <Play size={16} /> Play Section {currentSection}
                </button>
              </div>
            ) : (
              <div>
                <div className="flex items-center gap-3 mb-3">
                  {isPlaying ? (
                    <div className="audio-playing" data-testid="audio-indicator">
                      <span /><span /><span /><span /><span />
                    </div>
                  ) : (
                    <Volume2 size={20} className="text-[var(--ps-mute)]" />
                  )}
                  <span className="text-sm font-medium">
                    {sectionCompleted[currentSection] ? "Audio complete" :
                     isPlaying ? `Playing segment ${audioIndex + 1} of ${sectionAudios.length}` : "Paused"}
                  </span>
                </div>
                <Progress value={audioProgress} className="h-2" />
                <p className="text-xs text-[var(--ps-body-gray)] mt-2">
                  {sectionCompleted[currentSection]
                    ? "Review your answers above. Audio cannot be replayed."
                    : "Listen carefully. Audio plays once only."}
                </p>
              </div>
            )}
          </div>

          {/* Question Navigator */}
          <div className="card-ps p-4" data-testid="question-navigator">
            <p className="text-xs font-medium text-[var(--ps-body-gray)] mb-3">Questions</p>
            <div className="flex flex-wrap gap-2">
              {(section?.questions || []).map(q => {
                const qNum = String(q.question_num);
                return (
                  <div key={qNum} className={`q-pill ${answers[qNum] ? "answered" : ""} ${flagged.has(qNum) ? "flagged" : ""}`}>
                    {q.question_num}
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right - Questions (VISIBLE IMMEDIATELY) */}
        <div className="exam-right" data-testid="questions-panel">
          <h3 className="font-medium text-base mb-4" style={{ color: "var(--ps-charcoal)" }}>
            Questions {section?.questions?.[0]?.question_num} - {section?.questions?.[section.questions.length - 1]?.question_num}
          </h3>
          <div className="space-y-4">
            {(section?.questions || []).map(q => renderQuestion(q))}
          </div>
        </div>
      </div>
    </div>
  );
}
