import { useState, useRef, useEffect } from "react";
import { Mic, MicOff, Play, Square, Clock, Volume2 } from "lucide-react";
import { Progress } from "@/components/ui/progress";

export default function SpeakingModule({ exam, audioCache, answers, updateAnswer }) {
  const [currentPart, setCurrentPart] = useState(1);
  const [currentQuestion, setCurrentQuestion] = useState(0);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [isPlayingPrompt, setIsPlayingPrompt] = useState(false);
  const [prepTimer, setPrepTimer] = useState(null);
  const [recordings, setRecordings] = useState({});
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const timerRef = useRef(null);
  const prepTimerRef = useRef(null);

  const parts = exam?.speaking?.parts || [];
  const part = parts.find(p => p.part_num === currentPart);
  const questions = part?.questions || [];
  const question = questions[currentQuestion];

  // Play examiner prompt
  const playPrompt = () => {
    if (!question) return;
    const audioUrl = audioCache[`speaking_${question.question_num}`];

    if (audioUrl) {
      const audio = new Audio(audioUrl);
      setIsPlayingPrompt(true);
      audio.addEventListener("ended", () => setIsPlayingPrompt(false));
      audio.addEventListener("error", () => setIsPlayingPrompt(false));
      audio.play().catch(() => setIsPlayingPrompt(false));
    }
  };

  // Start preparation timer (Part 2)
  const startPrep = () => {
    setPrepTimer(60);
    prepTimerRef.current = setInterval(() => {
      setPrepTimer(prev => {
        if (prev <= 1) {
          clearInterval(prepTimerRef.current);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
  };

  // Start recording
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };

      mediaRecorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        const url = URL.createObjectURL(blob);
        const qNum = String(question.question_num);
        setRecordings(prev => ({ ...prev, [qNum]: url }));
        updateAnswer(qNum, `[Recording saved - ${recordingTime}s]`);
        stream.getTracks().forEach(t => t.stop());
      };

      mediaRecorder.start();
      setIsRecording(true);
      setRecordingTime(0);

      timerRef.current = setInterval(() => {
        setRecordingTime(prev => prev + 1);
      }, 1000);
    } catch (e) {
      console.error("Microphone access denied:", e);
    }
  };

  // Stop recording
  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      clearInterval(timerRef.current);
    }
  };

  // Next question
  const nextQuestion = () => {
    if (currentQuestion < questions.length - 1) {
      setCurrentQuestion(prev => prev + 1);
    } else if (currentPart < parts.length) {
      setCurrentPart(prev => prev + 1);
      setCurrentQuestion(0);
    }
    setPrepTimer(null);
    if (prepTimerRef.current) clearInterval(prepTimerRef.current);
  };

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (prepTimerRef.current) clearInterval(prepTimerRef.current);
    };
  }, []);

  const formatTime = (s) => `${Math.floor(s / 60).toString().padStart(2, "0")}:${(s % 60).toString().padStart(2, "0")}`;

  return (
    <div className="max-w-3xl mx-auto p-8" data-testid="speaking-module">
      {/* Part tabs */}
      <div className="flex gap-3 mb-8" data-testid="part-tabs">
        {parts.map(p => (
          <button key={p.part_num} onClick={() => { setCurrentPart(p.part_num); setCurrentQuestion(0); }}
            className={`px-5 py-2 rounded-full text-sm font-medium transition-all ${
              currentPart === p.part_num ? "bg-[var(--ps-blue)] text-white" : "bg-[var(--ps-ice)] text-[var(--ps-body-gray)] hover:bg-[var(--ps-divider)]"
            }`} data-testid={`part-tab-${p.part_num}`}>
            Part {p.part_num}
          </button>
        ))}
      </div>

      {/* Part info */}
      <div className="card-ps p-6 mb-6" data-testid="part-info">
        <h3 className="font-medium text-lg mb-2" style={{ color: "var(--ps-charcoal)" }}>
          Part {currentPart}: {part?.title}
        </h3>
        <p className="text-sm text-[var(--ps-body-gray)]">{part?.instructions}</p>

        {/* Cue card (Part 2) */}
        {part?.cue_card && (
          <div className="mt-4 p-4 bg-[var(--ps-ice)] rounded-xl border border-[var(--ps-divider)]" data-testid="cue-card">
            <p className="text-xs font-medium text-[var(--ps-body-gray)] mb-2">Topic Card</p>
            <div className="text-sm" style={{ color: "var(--ps-charcoal)" }}>
              {part.cue_card.split("\n").map((line, i) => (
                <p key={i} className="mb-1">{line}</p>
              ))}
            </div>
            {prepTimer === null && (
              <button onClick={startPrep} className="btn-ps btn-ps-secondary mt-3" style={{ padding: "6px 16px", fontSize: "0.8rem" }}
                data-testid="start-prep-btn">
                <Clock size={14} /> Start 1-minute preparation
              </button>
            )}
            {prepTimer !== null && prepTimer > 0 && (
              <div className="mt-3 flex items-center gap-2">
                <Clock size={14} className="text-[var(--ps-blue)]" />
                <span className="text-sm font-medium text-[var(--ps-blue)]">Preparation time: {formatTime(prepTimer)}</span>
              </div>
            )}
            {prepTimer === 0 && (
              <p className="mt-3 text-sm font-medium text-[var(--ps-orange)]">Preparation time is over. Please begin speaking.</p>
            )}
          </div>
        )}
      </div>

      {/* Current Question */}
      {question && (
        <div className="card-ps p-6 mb-6" data-testid={`speaking-question-${question.question_num}`}>
          <div className="flex items-center gap-2 mb-4">
            <span className="q-pill">{question.question_num}</span>
            <p className="text-sm font-medium" style={{ color: "var(--ps-charcoal)" }}>
              {question.question_text.replace(/\[.*?\]/g, "").trim()}
            </p>
          </div>

          {/* Play examiner audio */}
          <button onClick={playPrompt} disabled={isPlayingPrompt}
            className="btn-ps btn-ps-secondary mb-4" style={{ padding: "8px 20px", fontSize: "0.8rem" }}
            data-testid="play-prompt-btn">
            {isPlayingPrompt ? (
              <><div className="audio-playing" style={{ height: 16 }}><span /><span /><span /></div> Playing...</>
            ) : (
              <><Volume2 size={14} /> Play Examiner Question</>
            )}
          </button>

          {/* Recording controls */}
          <div className="flex items-center gap-4">
            {!isRecording ? (
              <button onClick={startRecording} className="btn-ps btn-ps-primary" style={{ padding: "10px 24px", fontSize: "0.875rem" }}
                data-testid="start-recording-btn">
                <Mic size={16} /> Record Response
              </button>
            ) : (
              <button onClick={stopRecording} className="btn-ps btn-ps-orange" style={{ padding: "10px 24px", fontSize: "0.875rem" }}
                data-testid="stop-recording-btn">
                <Square size={16} /> Stop ({formatTime(recordingTime)})
              </button>
            )}

            {recordings[String(question.question_num)] && (
              <div className="flex items-center gap-2">
                <audio controls src={recordings[String(question.question_num)]} className="h-8" data-testid="playback-audio" />
                <span className="text-xs text-green-600 font-medium">Recorded</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Navigation */}
      <div className="flex justify-between">
        <div />
        <button onClick={nextQuestion} className="btn-ps btn-ps-secondary" style={{ padding: "8px 20px", fontSize: "0.875rem" }}
          data-testid="next-question-btn">
          {currentQuestion < questions.length - 1 ? "Next Question" : currentPart < parts.length ? "Next Part" : "Finish Speaking"}
        </button>
      </div>
    </div>
  );
}
