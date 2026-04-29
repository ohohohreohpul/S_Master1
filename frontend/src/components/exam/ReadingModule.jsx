import { useState, useRef, useCallback } from "react";
import { Flag, Highlighter, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

export default function ReadingModule({ exam, answers, updateAnswer, flagged, toggleFlag }) {
  const [currentPassage, setCurrentPassage] = useState(1);
  const [highlights, setHighlights] = useState({}); // { passageNum: [{text, id}] }
  const [showHighlightBtn, setShowHighlightBtn] = useState(null); // { x, y, text }
  const [highlightMode, setHighlightMode] = useState(false);
  const passageRef = useRef(null);

  const passages = exam?.reading?.passages || [];
  const passage = passages.find(p => p.passage_num === currentPassage);
  const passageHighlights = highlights[currentPassage] || [];

  // Handle text selection for highlighting
  const handleMouseUp = useCallback(() => {
    if (!highlightMode) return;
    const selection = window.getSelection();
    const selectedText = selection?.toString().trim();
    if (selectedText && selectedText.length > 2 && passageRef.current?.contains(selection.anchorNode)) {
      const range = selection.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      setShowHighlightBtn({
        x: rect.left + rect.width / 2,
        y: rect.top - 10,
        text: selectedText
      });
    } else {
      setShowHighlightBtn(null);
    }
  }, [highlightMode]);

  const addHighlight = () => {
    if (!showHighlightBtn) return;
    const newHighlight = { text: showHighlightBtn.text, id: Date.now() };
    setHighlights(prev => ({
      ...prev,
      [currentPassage]: [...(prev[currentPassage] || []), newHighlight]
    }));
    setShowHighlightBtn(null);
    window.getSelection()?.removeAllRanges();
  };

  const removeHighlight = (id) => {
    setHighlights(prev => ({
      ...prev,
      [currentPassage]: (prev[currentPassage] || []).filter(h => h.id !== id)
    }));
  };

  const clearAllHighlights = () => {
    setHighlights(prev => ({ ...prev, [currentPassage]: [] }));
  };

  // Render passage text with highlights applied
  const renderPassageText = (fullText) => {
    if (!fullText) return null;
    const paragraphs = fullText.split("\n\n");

    return paragraphs.map((para, pi) => {
      if (!para.trim()) return null;

      // Apply highlights to this paragraph
      let segments = [{ text: para, highlighted: false }];

      for (const h of passageHighlights) {
        const newSegments = [];
        for (const seg of segments) {
          if (seg.highlighted) {
            newSegments.push(seg);
            continue;
          }
          const idx = seg.text.indexOf(h.text);
          if (idx >= 0) {
            if (idx > 0) newSegments.push({ text: seg.text.substring(0, idx), highlighted: false });
            newSegments.push({ text: h.text, highlighted: true, id: h.id });
            const after = seg.text.substring(idx + h.text.length);
            if (after) newSegments.push({ text: after, highlighted: false });
          } else {
            newSegments.push(seg);
          }
        }
        segments = newSegments;
      }

      return (
        <p key={pi} className="mb-4 text-sm leading-[1.9] text-[var(--ps-charcoal)]">
          {segments.map((seg, si) =>
            seg.highlighted ? (
              <mark key={si} className="ielts-highlight group relative cursor-pointer" data-testid={`highlight-${seg.id}`}
                onClick={() => removeHighlight(seg.id)}>
                {seg.text}
                <span className="hidden group-hover:flex absolute -top-6 left-1/2 -translate-x-1/2 bg-[var(--ps-charcoal)] text-white text-[9px] px-2 py-0.5 rounded whitespace-nowrap">
                  Click to remove
                </span>
              </mark>
            ) : (
              <span key={si}>{seg.text}</span>
            )
          )}
        </p>
      );
    });
  };

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
          <div className="space-y-1.5">
            {q.options.map((opt, i) => (
              <label key={i} className={`flex items-center gap-3 p-2.5 rounded-lg cursor-pointer transition-all text-sm ${
                answers[qNum] === opt.charAt(0)
                  ? "bg-[var(--ps-blue)]/5 border border-[var(--ps-blue)]/20"
                  : "hover:bg-[var(--ps-ice)] border border-transparent"
              }`} data-testid={`option-${qNum}-${i}`}>
                <input type="radio" name={`q_${qNum}`} value={opt.charAt(0)} checked={answers[qNum] === opt.charAt(0)}
                  onChange={(e) => updateAnswer(qNum, e.target.value)} className="w-4 h-4 accent-[var(--ps-blue)]" />
                <span>{opt}</span>
              </label>
            ))}
          </div>
        ) : q.question_type === "true_false_not_given" ? (
          <div className="flex gap-2">
            {["True", "False", "Not Given"].map(opt => (
              <label key={opt} className={`flex items-center gap-2 px-4 py-2 rounded-full border cursor-pointer transition-all text-sm ${
                answers[qNum] === opt
                  ? "border-[var(--ps-blue)] bg-[var(--ps-blue)]/5 text-[var(--ps-blue)] font-medium"
                  : "border-[var(--ps-divider)] hover:border-[var(--ps-blue)]/40"
              }`} data-testid={`tfng-${qNum}-${opt}`}>
                <input type="radio" name={`q_${qNum}`} value={opt} checked={answers[qNum] === opt}
                  onChange={(e) => updateAnswer(qNum, e.target.value)} className="sr-only" />
                {opt}
              </label>
            ))}
          </div>
        ) : q.question_type === "matching_headings" && q.options ? (
          <div className="space-y-1.5">
            {q.options.map((opt, i) => (
              <label key={i} className={`flex items-center gap-3 p-2.5 rounded-lg cursor-pointer transition-all text-sm ${
                answers[qNum] === opt.charAt(0)
                  ? "bg-[var(--ps-blue)]/5 border border-[var(--ps-blue)]/20"
                  : "hover:bg-[var(--ps-ice)] border border-transparent"
              }`} data-testid={`option-${qNum}-${i}`}>
                <input type="radio" name={`q_${qNum}`} value={opt.charAt(0)} checked={answers[qNum] === opt.charAt(0)}
                  onChange={(e) => updateAnswer(qNum, e.target.value)} className="w-4 h-4 accent-[var(--ps-blue)]" />
                <span>{opt}</span>
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
    <div data-testid="reading-module" onMouseUp={handleMouseUp}>
      {/* Passage tabs + highlight toolbar */}
      <div className="bg-[var(--ps-black)] px-8 py-3 flex items-center justify-between" data-testid="passage-tabs">
        <div className="flex items-center gap-3">
          {passages.map(p => (
            <button key={p.passage_num} onClick={() => { setCurrentPassage(p.passage_num); setShowHighlightBtn(null); }}
              className={`section-tab ${currentPassage === p.passage_num ? "active" : ""}`}
              data-testid={`passage-tab-${p.passage_num}`}>
              Passage {p.passage_num}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}
            onClick={() => { setHighlightMode(!highlightMode); setShowHighlightBtn(null); }}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all ${
              highlightMode
                ? "bg-yellow-400/20 text-yellow-300 border border-yellow-400/30"
                : "bg-white/10 text-white/60 border border-white/10 hover:bg-white/20"
            }`} data-testid="highlight-toggle">
            <Highlighter size={12} /> {highlightMode ? "Highlighting On" : "Highlight"}
          </motion.button>
          {passageHighlights.length > 0 && (
            <button onClick={clearAllHighlights} className="text-[10px] text-white/40 hover:text-white/80 transition-colors"
              data-testid="clear-highlights">
              Clear all
            </button>
          )}
        </div>
      </div>

      {/* Floating highlight button */}
      <AnimatePresence>
        {showHighlightBtn && (
          <motion.div initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 5 }}
            className="fixed z-50" style={{ left: showHighlightBtn.x - 40, top: showHighlightBtn.y - 40 }}>
            <button onClick={addHighlight}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--ps-charcoal)] text-white text-xs font-medium shadow-lg hover:bg-[var(--ps-black)] transition-colors"
              data-testid="add-highlight-btn">
              <Highlighter size={12} /> Highlight
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="exam-body">
        {/* Left - Passage */}
        <div className="exam-left" ref={passageRef} data-testid="passage-panel"
          style={{ cursor: highlightMode ? "text" : "default", userSelect: highlightMode ? "text" : "auto" }}>
          <div className="mb-4 pb-3 border-b border-[var(--ps-divider)]">
            <h3 className="font-bold text-lg" style={{ color: "var(--ps-charcoal)" }}>{passage?.title}</h3>
            {highlightMode && (
              <p className="text-[10px] text-[var(--ps-blue)] mt-1 font-medium">Select text to highlight it</p>
            )}
          </div>
          <div className="passage-text">
            {renderPassageText(passage?.text)}
          </div>
        </div>

        {/* Right - Questions */}
        <div className="exam-right" data-testid="questions-panel">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-bold text-sm" style={{ color: "var(--ps-charcoal)" }}>
              Questions {passage?.questions?.[0]?.question_num} - {passage?.questions?.[passage.questions.length - 1]?.question_num}
            </h3>
            <div className="flex flex-wrap gap-1">
              {(passage?.questions || []).map(q => {
                const qNum = String(q.question_num);
                return (
                  <div key={qNum} className={`q-pill ${answers[qNum] ? "answered" : ""} ${flagged.has(qNum) ? "flagged" : ""}`}
                    style={{ width: 22, height: 22, fontSize: "0.6rem" }}>
                    {q.question_num}
                  </div>
                );
              })}
            </div>
          </div>
          <div className="space-y-3">
            {(passage?.questions || []).map(q => renderQuestion(q))}
          </div>
        </div>
      </div>
    </div>
  );
}
