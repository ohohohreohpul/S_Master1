import { useState, useRef, useEffect, useCallback } from "react";
import { Mic, Square, Volume2, Loader2, MessageCircle, User, Bot, Clock, ArrowRight, SkipForward } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { motion, AnimatePresence } from "framer-motion";
import { API } from "@/App";

const fadeIn = { initial: { opacity: 0, y: 8 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.3 } };

export default function SpeakingModule({ exam, audioCache, answers, updateAnswer }) {
  const [currentPart, setCurrentPart] = useState(1);
  const [phase, setPhase] = useState("idle"); // idle, examiner_speaking, user_turn, recording, processing, prep_time
  const [conversationHistory, setConversationHistory] = useState([]);
  const [transcript, setTranscript] = useState([]);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [prepTimer, setPrepTimer] = useState(null);
  const [exchangeCount, setExchangeCount] = useState(0);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const timerRef = useRef(null);
  const prepTimerRef = useRef(null);
  const transcriptEndRef = useRef(null);
  const currentAudioRef = useRef(null);

  const parts = exam?.speaking?.parts || [];
  const part = parts.find(p => p.part_num === currentPart);
  const maxExchanges = currentPart === 1 ? 5 : currentPart === 2 ? 3 : 4;

  // Auto-scroll transcript
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript]);

  // Start a part - examiner begins
  const startPart = useCallback(async () => {
    setPhase("processing");
    setExchangeCount(0);
    try {
      const res = await fetch(`${API}/speaking/converse`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          part_num: currentPart, action: "start",
          conversation_history: [], cue_card: part?.cue_card || ""
        })
      });
      if (res.ok) {
        const data = await res.json();
        setConversationHistory(data.conversation_history);
        addToTranscript("examiner", data.examiner_text);
        playExaminerAudio(data.audio_base64);
      }
    } catch (e) {
      console.error("Start part error:", e);
      setPhase("user_turn");
    }
  }, [currentPart, part]);

  const playExaminerAudio = (base64Audio) => {
    setPhase("examiner_speaking");
    const audio = new Audio(`data:audio/mpeg;base64,${base64Audio}`);
    currentAudioRef.current = audio;
    audio.addEventListener("ended", () => {
      currentAudioRef.current = null;
      // For Part 2, start prep timer after cue card is given
      if (currentPart === 2 && exchangeCount === 0) {
        setPhase("prep_time");
        setPrepTimer(60);
        prepTimerRef.current = setInterval(() => {
          setPrepTimer(prev => {
            if (prev <= 1) {
              clearInterval(prepTimerRef.current);
              setPhase("user_turn");
              return 0;
            }
            return prev - 1;
          });
        }, 1000);
      } else {
        setPhase("user_turn");
      }
    });
    audio.addEventListener("error", () => { setPhase("user_turn"); });
    audio.play().catch(() => setPhase("user_turn"));
  };

  const addToTranscript = (role, text) => {
    setTranscript(prev => [...prev, { role, text, time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }]);
  };

  // Recording
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
        stream.getTracks().forEach(t => t.stop());
        await processRecording(blob);
      };

      mediaRecorder.start(250);
      setIsRecording(true);
      setRecordingTime(0);
      setPhase("recording");
      timerRef.current = setInterval(() => setRecordingTime(prev => prev + 1), 1000);
    } catch (e) {
      console.error("Microphone denied:", e);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      clearInterval(timerRef.current);
    }
  };

  const processRecording = async (blob) => {
    setPhase("processing");

    // Step 1: Transcribe via ElevenLabs STT
    let userText = "";
    try {
      const formData = new FormData();
      formData.append("audio_file", blob, "recording.webm");
      const sttRes = await fetch(`${API}/speaking/transcribe`, {
        method: "POST", credentials: "include", body: formData
      });
      if (sttRes.ok) {
        const sttData = await sttRes.json();
        userText = sttData.text || "";
      }
    } catch (e) { console.error("STT error:", e); }

    if (userText) {
      addToTranscript("user", userText);
      updateAnswer(`part${currentPart}_exchange${exchangeCount}`, userText);
    }

    const newCount = exchangeCount + 1;
    setExchangeCount(newCount);

    // Check if part should end
    if (newCount >= maxExchanges) {
      addToTranscript("system", `Part ${currentPart} complete.`);
      setPhase("idle");
      // Store all transcriptions for scoring
      const allUserTexts = transcript.filter(t => t.role === "user").map(t => t.text).join(" ");
      if (userText) updateAnswer(`part_${currentPart}`, allUserTexts + " " + userText);
      return;
    }

    // Step 2: Get examiner follow-up via conversational AI
    try {
      const res = await fetch(`${API}/speaking/converse`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          part_num: currentPart, user_transcription: userText,
          conversation_history: conversationHistory, cue_card: part?.cue_card || "",
          action: "respond"
        })
      });
      if (res.ok) {
        const data = await res.json();
        setConversationHistory(data.conversation_history);
        addToTranscript("examiner", data.examiner_text);
        playExaminerAudio(data.audio_base64);
      } else {
        setPhase("user_turn");
      }
    } catch (e) {
      console.error("Converse error:", e);
      setPhase("user_turn");
    }
  };

  const nextPart = () => {
    if (currentPart < 3) {
      setCurrentPart(prev => prev + 1);
      setPhase("idle");
      setTranscript([]);
      setConversationHistory([]);
      setExchangeCount(0);
      setPrepTimer(null);
    }
  };

  const skipPrep = () => {
    if (prepTimerRef.current) clearInterval(prepTimerRef.current);
    setPrepTimer(0);
    setPhase("user_turn");
  };

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (prepTimerRef.current) clearInterval(prepTimerRef.current);
      if (currentAudioRef.current) currentAudioRef.current.pause();
    };
  }, []);

  const formatTime = (s) => `${Math.floor(s / 60).toString().padStart(2, "0")}:${(s % 60).toString().padStart(2, "0")}`;

  return (
    <div className="max-w-3xl mx-auto p-6" data-testid="speaking-module">
      {/* Part tabs */}
      <div className="flex gap-3 mb-6" data-testid="part-tabs">
        {parts.map(p => (
          <motion.button key={p.part_num} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}
            onClick={() => { if (phase === "idle") { setCurrentPart(p.part_num); setTranscript([]); setConversationHistory([]); setExchangeCount(0); } }}
            className={`px-5 py-2 rounded-full text-sm font-medium transition-all ${
              currentPart === p.part_num
                ? "bg-[var(--ps-blue)] text-white shadow-lg shadow-[var(--ps-blue)]/20"
                : "bg-white text-[var(--ps-body-gray)] border border-[var(--ps-divider)]"
            }`} data-testid={`part-tab-${p.part_num}`}>
            Part {p.part_num}
          </motion.button>
        ))}
      </div>

      {/* Part info + Cue card */}
      <motion.div {...fadeIn} className="card-ps p-5 mb-5">
        <h3 className="font-bold text-base mb-1" style={{ color: "var(--ps-charcoal)" }}>
          Part {currentPart}: {part?.title}
        </h3>
        <p className="text-xs text-[var(--ps-body-gray)] leading-relaxed">{part?.instructions}</p>

        {part?.cue_card && (
          <div className="mt-3 p-4 bg-[var(--ps-ice)] rounded-xl border border-[var(--ps-divider)]" data-testid="cue-card">
            <p className="text-[10px] font-bold text-[var(--ps-blue)] mb-2 uppercase tracking-wider">Topic Card</p>
            <div className="text-sm leading-relaxed" style={{ color: "var(--ps-charcoal)" }}>
              {part.cue_card.split("\n").map((line, i) => <p key={i} className="mb-0.5">{line}</p>)}
            </div>
          </div>
        )}
      </motion.div>

      {/* Conversation transcript */}
      <div className="card-ps overflow-hidden mb-5" style={{ minHeight: 300 }}>
        <div className="bg-[var(--ps-black)] px-5 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <MessageCircle size={14} className="text-[var(--ps-cyan)]" />
            <span className="text-xs font-semibold text-white">Live Conversation</span>
          </div>
          <div className="flex items-center gap-2">
            {phase === "recording" && <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />}
            {phase === "examiner_speaking" && <div className="audio-playing" style={{ height: 12 }}><span /><span /><span /></div>}
            <span className="text-[10px] text-gray-400 capitalize">{phase.replace(/_/g, " ")}</span>
          </div>
        </div>

        <div className="p-4 max-h-72 overflow-y-auto space-y-3" data-testid="conversation-transcript">
          {transcript.length === 0 && phase === "idle" && (
            <div className="text-center py-10">
              <Bot size={32} className="text-[var(--ps-mute)] mx-auto mb-3" />
              <p className="text-sm text-[var(--ps-body-gray)]">Start the conversation with the AI examiner</p>
            </div>
          )}

          <AnimatePresence>
            {transcript.map((msg, i) => (
              <motion.div key={i} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}
                className={`flex gap-3 ${msg.role === "user" ? "justify-end" : msg.role === "system" ? "justify-center" : "justify-start"}`}>
                {msg.role === "examiner" && (
                  <div className="w-7 h-7 rounded-full bg-[var(--ps-blue)]/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <Bot size={14} className="text-[var(--ps-blue)]" />
                  </div>
                )}
                {msg.role === "system" ? (
                  <span className="text-[10px] text-[var(--ps-mute)] italic py-2">{msg.text}</span>
                ) : (
                  <div className={`max-w-[80%] rounded-2xl px-4 py-2.5 ${
                    msg.role === "examiner" ? "bg-[var(--ps-ice)] text-[var(--ps-charcoal)]" : "bg-[var(--ps-blue)] text-white"
                  }`}>
                    <p className="text-sm leading-relaxed">{msg.text}</p>
                    <p className={`text-[9px] mt-1 ${msg.role === "examiner" ? "text-[var(--ps-mute)]" : "text-white/50"}`}>{msg.time}</p>
                  </div>
                )}
                {msg.role === "user" && (
                  <div className="w-7 h-7 rounded-full bg-[var(--ps-blue)] flex items-center justify-center flex-shrink-0 mt-0.5">
                    <User size={14} className="text-white" />
                  </div>
                )}
              </motion.div>
            ))}
          </AnimatePresence>

          {/* Processing indicator */}
          {phase === "processing" && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex gap-3 items-center">
              <div className="w-7 h-7 rounded-full bg-[var(--ps-blue)]/10 flex items-center justify-center">
                <Loader2 size={14} className="text-[var(--ps-blue)] animate-spin" />
              </div>
              <div className="bg-[var(--ps-ice)] rounded-2xl px-4 py-2.5">
                <div className="flex items-center gap-2 text-sm text-[var(--ps-body-gray)]">
                  <span className="inline-flex gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-[var(--ps-blue)] animate-bounce" style={{ animationDelay: "0ms" }} />
                    <span className="w-1.5 h-1.5 rounded-full bg-[var(--ps-blue)] animate-bounce" style={{ animationDelay: "150ms" }} />
                    <span className="w-1.5 h-1.5 rounded-full bg-[var(--ps-blue)] animate-bounce" style={{ animationDelay: "300ms" }} />
                  </span>
                </div>
              </div>
            </motion.div>
          )}
          <div ref={transcriptEndRef} />
        </div>
      </div>

      {/* Prep timer (Part 2) */}
      {phase === "prep_time" && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
          className="card-ps p-5 mb-5 text-center" data-testid="prep-timer">
          <Clock size={24} className="text-[var(--ps-blue)] mx-auto mb-2" />
          <p className="text-xs font-bold text-[var(--ps-body-gray)] uppercase tracking-wider mb-2">Preparation Time</p>
          <div className="text-3xl font-light text-[var(--ps-blue)] mb-3">{formatTime(prepTimer || 0)}</div>
          <Progress value={((60 - (prepTimer || 0)) / 60) * 100} className="max-w-xs mx-auto mb-3 h-1.5" />
          <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
            onClick={skipPrep} className="text-xs text-[var(--ps-body-gray)] hover:text-[var(--ps-blue)] transition-colors"
            data-testid="skip-prep-btn">
            Skip preparation <SkipForward size={12} className="inline ml-1" />
          </motion.button>
        </motion.div>
      )}

      {/* Controls */}
      <div className="flex items-center justify-between">
        <div>
          {phase === "idle" && (
            <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
              onClick={startPart} className="btn-ps btn-ps-primary flex items-center gap-2"
              style={{ padding: "12px 28px", fontSize: "0.875rem" }} data-testid="start-part-btn">
              <MessageCircle size={16} /> {transcript.length > 0 ? "Continue Conversation" : "Start Part " + currentPart}
            </motion.button>
          )}

          {phase === "user_turn" && !isRecording && (
            <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
              onClick={startRecording} className="btn-ps btn-ps-primary flex items-center gap-2"
              style={{ padding: "12px 28px", fontSize: "0.875rem" }} data-testid="start-recording-btn">
              <Mic size={16} /> Speak Now
            </motion.button>
          )}

          {phase === "recording" && (
            <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
              onClick={stopRecording} className="btn-ps btn-ps-orange flex items-center gap-2"
              style={{ padding: "12px 28px", fontSize: "0.875rem" }} data-testid="stop-recording-btn">
              <Square size={16} /> Stop ({formatTime(recordingTime)})
            </motion.button>
          )}

          {phase === "examiner_speaking" && (
            <div className="flex items-center gap-3 px-4 py-3 bg-[var(--ps-ice)] rounded-full">
              <div className="audio-playing" style={{ height: 16 }}><span /><span /><span /><span /><span /></div>
              <span className="text-sm font-medium text-[var(--ps-blue)]">Examiner speaking...</span>
            </div>
          )}

          {phase === "processing" && (
            <div className="flex items-center gap-3 px-4 py-3 bg-[var(--ps-ice)] rounded-full">
              <Loader2 size={16} className="text-[var(--ps-blue)] animate-spin" />
              <span className="text-sm text-[var(--ps-body-gray)]">Processing response...</span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <span className="text-[10px] text-[var(--ps-body-gray)]">{exchangeCount}/{maxExchanges} exchanges</span>
          {(phase === "idle" && transcript.length > 0 && currentPart < 3) && (
            <motion.button whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}
              onClick={nextPart} className="btn-ps btn-ps-secondary flex items-center gap-2"
              style={{ padding: "8px 20px", fontSize: "0.8rem" }} data-testid="next-part-btn">
              Next Part <ArrowRight size={14} />
            </motion.button>
          )}
        </div>
      </div>
    </div>
  );
}
