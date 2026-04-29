import { useState, useRef, useEffect } from "react";
import { Mic, Square, Play, Clock, Volume2, Loader2, CheckCircle } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { motion, AnimatePresence } from "framer-motion";
import { API } from "@/App";

export default function SpeakingModule({ exam, audioCache, answers, updateAnswer }) {
  const [currentPart, setCurrentPart] = useState(1);
  const [currentQuestion, setCurrentQuestion] = useState(0);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [isPlayingPrompt, setIsPlayingPrompt] = useState(false);
  const [prepTimer, setPrepTimer] = useState(null);
  const [recordings, setRecordings] = useState({});
  const [transcriptions, setTranscriptions] = useState({});
  const [transcribing, setTranscribing] = useState({});
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const timerRef = useRef(null);
  const prepTimerRef = useRef(null);

  const parts = exam?.speaking?.parts || [];
  const part = parts.find(p => p.part_num === currentPart);
  const questions = part?.questions || [];
  const question = questions[currentQuestion];

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

  const startPrep = () => {
    setPrepTimer(60);
    prepTimerRef.current = setInterval(() => {
      setPrepTimer(prev => {
        if (prev <= 1) { clearInterval(prepTimerRef.current); return 0; }
        return prev - 1;
      });
    }, 1000);
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };

      mediaRecorder.onstop = async () => {
        const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        const url = URL.createObjectURL(blob);
        const qNum = String(question.question_num);
        setRecordings(prev => ({ ...prev, [qNum]: { url, blob } }));
        stream.getTracks().forEach(t => t.stop());

        // Auto-transcribe via ElevenLabs STT
        setTranscribing(prev => ({ ...prev, [qNum]: true }));
        try {
          const formData = new FormData();
          formData.append("audio_file", blob, "recording.webm");
          const res = await fetch(`${API}/speaking/transcribe`, {
            method: "POST",
            credentials: "include",
            body: formData,
          });
          if (res.ok) {
            const data = await res.json();
            setTranscriptions(prev => ({ ...prev, [qNum]: data.text }));
            updateAnswer(qNum, data.text);
          }
        } catch (e) {
          console.error("Transcription error:", e);
        }
        setTranscribing(prev => ({ ...prev, [qNum]: false }));
      };

      mediaRecorder.start(250);
      setIsRecording(true);
      setRecordingTime(0);
      timerRef.current = setInterval(() => setRecordingTime(prev => prev + 1), 1000);
    } catch (e) {
      console.error("Microphone access denied:", e);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      clearInterval(timerRef.current);
    }
  };

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
          <motion.button key={p.part_num} whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
            onClick={() => { setCurrentPart(p.part_num); setCurrentQuestion(0); }}
            className={`px-6 py-2.5 rounded-full text-sm font-medium transition-all ${
              currentPart === p.part_num
                ? "bg-[var(--ps-blue)] text-white shadow-lg shadow-[var(--ps-blue)]/20"
                : "bg-white text-[var(--ps-body-gray)] hover:bg-[var(--ps-ice)] border border-[var(--ps-divider)]"
            }`} data-testid={`part-tab-${p.part_num}`}>
            Part {p.part_num}
          </motion.button>
        ))}
      </div>

      {/* Part info */}
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
        className="card-ps p-6 mb-6" data-testid="part-info">
        <h3 className="font-semibold text-lg mb-2" style={{ color: "var(--ps-charcoal)" }}>
          Part {currentPart}: {part?.title}
        </h3>
        <p className="text-sm text-[var(--ps-body-gray)] leading-relaxed">{part?.instructions}</p>

        {part?.cue_card && (
          <div className="mt-4 p-5 bg-[var(--ps-ice)] rounded-xl border border-[var(--ps-divider)]" data-testid="cue-card">
            <p className="text-[10px] font-bold text-[var(--ps-blue)] mb-3 uppercase tracking-wider">Topic Card</p>
            <div className="text-sm leading-relaxed" style={{ color: "var(--ps-charcoal)" }}>
              {part.cue_card.split("\n").map((line, i) => (
                <p key={i} className="mb-1">{line}</p>
              ))}
            </div>
            {prepTimer === null && (
              <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
                onClick={startPrep} className="btn-ps btn-ps-secondary mt-4" style={{ padding: "8px 20px", fontSize: "0.8rem" }}
                data-testid="start-prep-btn">
                <Clock size={14} /> Start 1-minute preparation
              </motion.button>
            )}
            {prepTimer !== null && prepTimer > 0 && (
              <div className="mt-4 flex items-center gap-2">
                <Clock size={14} className="text-[var(--ps-blue)]" />
                <span className="text-sm font-semibold text-[var(--ps-blue)]">{formatTime(prepTimer)}</span>
                <Progress value={((60 - prepTimer) / 60) * 100} className="flex-1 h-1.5" />
              </div>
            )}
            {prepTimer === 0 && (
              <p className="mt-4 text-sm font-medium text-[var(--ps-orange)]">Preparation time is over. Please begin speaking.</p>
            )}
          </div>
        )}
      </motion.div>

      {/* Current Question */}
      <AnimatePresence mode="wait">
        {question && (
          <motion.div key={`q-${question.question_num}`} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}
            className="card-ps p-6 mb-6" data-testid={`speaking-question-${question.question_num}`}>
            <div className="flex items-center gap-3 mb-5">
              <span className="q-pill" style={{ width: 36, height: 36, fontSize: "0.85rem" }}>{question.question_num}</span>
              <p className="text-sm font-medium flex-1" style={{ color: "var(--ps-charcoal)" }}>
                {question.question_text.replace(/\[.*?\]/g, "").trim()}
              </p>
            </div>

            {/* Play examiner audio */}
            <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
              onClick={playPrompt} disabled={isPlayingPrompt}
              className="btn-ps btn-ps-secondary mb-5 flex items-center gap-2" style={{ padding: "10px 24px", fontSize: "0.8rem" }}
              data-testid="play-prompt-btn">
              {isPlayingPrompt ? (
                <><div className="audio-playing" style={{ height: 16 }}><span /><span /><span /></div> Playing examiner...</>
              ) : (
                <><Volume2 size={14} /> Play Examiner Question</>
              )}
            </motion.button>

            {/* Recording controls */}
            <div className="flex flex-col gap-4">
              <div className="flex items-center gap-4">
                {!isRecording ? (
                  <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
                    onClick={startRecording}
                    className="btn-ps btn-ps-primary flex items-center gap-2" style={{ padding: "12px 28px", fontSize: "0.875rem" }}
                    data-testid="start-recording-btn">
                    <Mic size={16} /> Record Response
                  </motion.button>
                ) : (
                  <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
                    onClick={stopRecording}
                    className="btn-ps btn-ps-orange flex items-center gap-2" style={{ padding: "12px 28px", fontSize: "0.875rem" }}
                    data-testid="stop-recording-btn">
                    <Square size={16} /> Stop Recording ({formatTime(recordingTime)})
                  </motion.button>
                )}

                {isRecording && (
                  <div className="flex items-center gap-2">
                    <div className="w-3 h-3 rounded-full bg-red-500 animate-pulse" />
                    <span className="text-sm text-red-500 font-medium">Recording...</span>
                  </div>
                )}
              </div>

              {/* Playback + Transcription */}
              {recordings[String(question.question_num)] && (
                <div className="bg-[var(--ps-ice)] rounded-xl p-4 space-y-3">
                  <div className="flex items-center gap-3">
                    <audio controls src={recordings[String(question.question_num)].url} className="h-8 flex-1" data-testid="playback-audio" />
                    <CheckCircle size={16} className="text-emerald-500" />
                  </div>

                  {/* STT Transcription */}
                  {transcribing[String(question.question_num)] && (
                    <div className="flex items-center gap-2 text-sm text-[var(--ps-blue)]">
                      <Loader2 size={14} className="animate-spin" />
                      <span>Transcribing your response...</span>
                    </div>
                  )}
                  {transcriptions[String(question.question_num)] && (
                    <div data-testid="transcription-result">
                      <p className="text-[10px] font-bold text-[var(--ps-body-gray)] uppercase tracking-wider mb-1">Your transcription</p>
                      <p className="text-sm text-[var(--ps-charcoal)] leading-relaxed bg-white rounded-lg p-3 border border-[var(--ps-divider)]">
                        {transcriptions[String(question.question_num)]}
                      </p>
                      <p className="text-[10px] text-[var(--ps-body-gray)] mt-1">
                        {answers[String(question.question_num)]?.split(" ").length || 0} words
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Navigation */}
      <div className="flex justify-between items-center">
        <div className="text-xs text-[var(--ps-body-gray)]">
          Question {currentQuestion + 1} of {questions.length}
        </div>
        <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
          onClick={nextQuestion} className="btn-ps btn-ps-secondary flex items-center gap-2" style={{ padding: "10px 24px", fontSize: "0.8rem" }}
          data-testid="next-question-btn">
          {currentQuestion < questions.length - 1 ? "Next Question" : currentPart < parts.length ? "Next Part" : "Finish Speaking"}
        </motion.button>
      </div>
    </div>
  );
}
