import { useState } from "react";
import { Flag } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";

export default function ReadingModule({ exam, answers, updateAnswer, flagged, toggleFlag }) {
  const [currentPassage, setCurrentPassage] = useState(1);
  const passages = exam?.reading?.passages || [];
  const passage = passages.find(p => p.passage_num === currentPassage);

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
                  onChange={(e) => updateAnswer(qNum, e.target.value)} className="w-4 h-4 accent-[var(--ps-blue)]" />
                <span className="text-sm">{opt}</span>
              </label>
            ))}
          </div>
        ) : q.question_type === "true_false_not_given" ? (
          <div className="flex gap-3">
            {["True", "False", "Not Given"].map(opt => (
              <label key={opt} className={`flex items-center gap-2 px-4 py-2 rounded-full border cursor-pointer transition-all text-sm ${
                answers[qNum] === opt ? "border-[var(--ps-blue)] bg-[var(--ps-blue)]/5 text-[var(--ps-blue)]" : "border-[var(--ps-divider)] hover:border-[var(--ps-blue)]"
              }`} data-testid={`tfng-${qNum}-${opt}`}>
                <input type="radio" name={`q_${qNum}`} value={opt} checked={answers[qNum] === opt}
                  onChange={(e) => updateAnswer(qNum, e.target.value)} className="sr-only" />
                {opt}
              </label>
            ))}
          </div>
        ) : q.question_type === "matching_headings" && q.options ? (
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

  return (
    <div data-testid="reading-module">
      {/* Passage tabs */}
      <div className="bg-[var(--ps-black)] px-8 py-3 flex items-center gap-3" data-testid="passage-tabs">
        {passages.map(p => (
          <button key={p.passage_num} onClick={() => setCurrentPassage(p.passage_num)}
            className={`section-tab ${currentPassage === p.passage_num ? "active" : ""}`}
            data-testid={`passage-tab-${p.passage_num}`}>
            Passage {p.passage_num}
          </button>
        ))}
      </div>

      <div className="exam-body">
        {/* Left - Passage */}
        <div className="exam-left" data-testid="passage-panel">
          <h3 className="font-semibold text-lg mb-4" style={{ color: "var(--ps-charcoal)" }}>{passage?.title}</h3>
          <div className="passage-text">
            {passage?.text?.split("\n\n").map((para, i) => (
              <p key={i}>{para}</p>
            ))}
          </div>
        </div>

        {/* Right - Questions */}
        <div className="exam-right" data-testid="questions-panel">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-medium text-base" style={{ color: "var(--ps-charcoal)" }}>
              Questions {passage?.questions?.[0]?.question_num} - {passage?.questions?.[passage.questions.length - 1]?.question_num}
            </h3>
            <div className="flex flex-wrap gap-1">
              {(passage?.questions || []).map(q => {
                const qNum = String(q.question_num);
                return (
                  <div key={qNum} className={`q-pill ${answers[qNum] ? "answered" : ""} ${flagged.has(qNum) ? "flagged" : ""}`} style={{ width: 24, height: 24, fontSize: "0.65rem" }}>
                    {q.question_num}
                  </div>
                );
              })}
            </div>
          </div>
          <div className="space-y-4">
            {(passage?.questions || []).map(q => renderQuestion(q))}
          </div>
        </div>
      </div>
    </div>
  );
}
