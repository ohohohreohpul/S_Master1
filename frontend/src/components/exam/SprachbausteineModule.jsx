import { useState } from "react";
import { Flag } from "lucide-react";
import { motion } from "framer-motion";

// Support both {21} (curly) and [21] (square) gap formats
const GAP_RE = /(?:\{(\d+)\}|\[(\d+)\])/g;

/**
 * Normalise AI-generated gap text.
 * The AI sometimes emits bare numbers (e.g. "... ein 32 ...") instead of {32}.
 * We convert any bare integer that falls in the expected gap range to {N}.
 * Works for both Aufgabe 1 (21-30) and Aufgabe 2 (31-40).
 */
function normaliseGapText(text, options) {
  if (!text) return text;
  // Already has proper markers — leave alone
  if (/\{\d+\}|\[\d+\]/.test(text)) return text;
  // Extract expected gap numbers from options
  const expectedNums = new Set((options || []).map(o => o.question_num));
  if (expectedNums.size === 0) return text;
  // Replace bare numbers that match expected gap nums (surrounded by non-digit)
  return text.replace(/(?<!\d)(\d{2,3})(?!\d)/g, (match, num) => {
    return expectedNums.has(parseInt(num, 10)) ? `{${num}}` : match;
  });
}

function extractGapNums(text) {
  const nums = [];
  const re = new RegExp(GAP_RE.source, "g");
  let m;
  while ((m = re.exec(text)) !== null) nums.push(parseInt(m[1] ?? m[2], 10));
  return nums;
}

// Split text on both gap formats, returning parts and gap tokens
function splitTextByGaps(text) {
  return text.split(/(\{\d+\}|\[\d+\])/);
}

function parseGapToken(part) {
  const m = part.match(/^(?:\{(\d+)\}|\[(\d+)\])$/);
  return m ? parseInt(m[1] ?? m[2], 10) : null;
}

function FlagBtn({ qNum, flagged, toggleFlag }) {
  const active = flagged.has(qNum);
  return (
    <button onClick={() => toggleFlag(qNum)} className="p-1 rounded hover:bg-[var(--ps-ice)]" data-testid={`flag-${qNum}`}>
      <Flag size={13} className={active ? "text-[var(--ps-orange)] fill-[var(--ps-orange)]" : "text-[var(--ps-mute)]"} />
    </button>
  );
}

/* ── Aufgabe 1: Multiple Choice ───────────────────────────── */
function AufgabeMC({ aufgabe, answers, updateAnswer, flagged, toggleFlag }) {
  const allQuestions = aufgabe.options || [];
  const normText = normaliseGapText(aufgabe.text_with_gaps, allQuestions);

  return (
    <div className="exam-body" data-testid="aufgabe-mc">
      <div className="exam-left">
        <div className="mb-4 pb-3 border-b border-[var(--ps-divider)]">
          <h3 className="font-bold text-base text-[var(--ps-charcoal)]">Aufgabe 1</h3>
          <p className="text-xs text-[var(--ps-mute)] mt-1">
            {(() => {
              const nums = (aufgabe.options || []).map(o => o.question_num).filter(Boolean);
              const first = nums.length ? Math.min(...nums) : null;
              const last  = nums.length ? Math.max(...nums) : null;
              return first && last
                ? `Wählen Sie für jede Lücke (${first}–${last}) a, b oder c.`
                : "Wählen Sie für jede Lücke a, b oder c.";
            })()}
          </p>
        </div>
        <div className="text-sm leading-[1.9] text-[var(--ps-charcoal)]">
          {splitTextByGaps(normText || "").map((part, i) => {
            const num = parseGapToken(part);
            if (num === null) return <span key={i}>{part}</span>;
            const ans = answers[String(num)];
            return (
              <span key={i} className={`inline-flex items-center justify-center mx-1 px-2.5 py-0.5 rounded border text-xs font-bold ${
                ans ? "bg-[var(--ps-blue)] text-white border-[var(--ps-blue)]" : "bg-[var(--ps-ice)] text-[var(--ps-mute)] border-[var(--ps-divider)]"
              }`}>
                {ans ? `${num}(${ans})` : num}
              </span>
            );
          })}
        </div>
      </div>

      <div className="exam-right" data-testid="questions-panel">
        <div className="flex flex-wrap gap-1 mb-5">
          {allQuestions.map(o => {
            const n = String(o.question_num);
            return (
              <div key={n} className={`q-pill ${answers[n] ? "answered" : ""} ${flagged.has(n) ? "flagged" : ""}`}
                style={{ width: 26, height: 26, fontSize: "0.6rem" }}>{n}</div>
            );
          })}
        </div>

        <div className="space-y-3">
          {allQuestions.map(opt => {
            const qNum = String(opt.question_num);
            const isFlagged = flagged.has(qNum);
            return (
              <div key={qNum} className={`question-block ${isFlagged ? "border-[var(--ps-orange)]" : ""}`} data-testid={`question-${qNum}`}>
                <div className="flex items-center justify-between mb-2">
                  <span className={`q-pill ${answers[qNum] ? "answered" : ""} ${isFlagged ? "flagged" : ""}`}>{opt.question_num}</span>
                  <FlagBtn qNum={qNum} flagged={flagged} toggleFlag={toggleFlag} />
                </div>
                <div className="flex gap-2">
                  {["a", "b", "c"].map(letter => {
                    const isSelected = answers[qNum] === letter;
                    return (
                      <motion.button key={letter} whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.96 }}
                        onClick={() => updateAnswer(qNum, letter)} data-testid={`option-${qNum}-${letter}`}
                        className={`flex-1 py-2 rounded-lg border text-xs font-medium transition-all ${
                          isSelected
                            ? "bg-[var(--ps-blue)] text-white border-[var(--ps-blue)] shadow-sm"
                            : "bg-white text-[var(--ps-charcoal)] border-[var(--ps-divider)] hover:border-[var(--ps-blue)]/40 hover:text-[var(--ps-blue)]"
                        }`}>
                        <span className="font-bold mr-1">{letter}</span>{opt[letter]}
                      </motion.button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ── Aufgabe 2: Wortbank ──────────────────────────────────── */
function AufgabeWortbank({ aufgabe, answers, updateAnswer, flagged, toggleFlag }) {
  const [selectedGap, setSelectedGap] = useState(null);
  const wortbank = aufgabe.wortbank || [];
  const normText = normaliseGapText(aufgabe.text_with_gaps, aufgabe.options);
  const gapNums = extractGapNums(normText || "");
  const usedWords = new Set(gapNums.map(n => answers[String(n)]).filter(Boolean));

  const toggleGap = (num) => setSelectedGap(prev => (prev === num ? null : num));

  const handleWordSelect = (wordId) => {
    if (!selectedGap) return;
    const qNum = String(selectedGap);
    if (answers[qNum] === wordId) { updateAnswer(qNum, ""); setSelectedGap(null); return; }
    updateAnswer(qNum, wordId);
    const nextEmpty = gapNums.find(n => n !== selectedGap && !answers[String(n)]);
    setSelectedGap(nextEmpty ?? null);
  };

  // Render text with clickable gap buttons
  const textParts = splitTextByGaps(normText || "").map((part, i) => {
    const num = parseGapToken(part);
    if (num === null) return <span key={i}>{part}</span>;
    const ans = answers[String(num)];
    const isActive = selectedGap === num;
    const wordLabel = ans ? (wortbank.find(w => w.id === ans)?.word ?? ans) : String(num);
    return (
      <button key={i} onClick={() => toggleGap(num)} data-testid={`gap-${num}`}
        className={`inline-flex items-center justify-center mx-1 px-3 py-0.5 rounded-md border text-xs font-semibold transition-all ${
          isActive ? "border-[var(--ps-blue)] bg-[var(--ps-blue)] text-white shadow"
            : ans ? "border-[var(--ps-blue)]/40 bg-[var(--ps-blue)]/8 text-[var(--ps-blue)]"
            : "border-[var(--ps-divider)] bg-[var(--ps-ice)] text-[var(--ps-mute)] hover:border-[var(--ps-blue)]/50"
        }`}>
        {wordLabel}
      </button>
    );
  });

  return (
    <div className="exam-body" data-testid="aufgabe-wortbank">
      <div className="exam-left">
        <div className="mb-4 pb-3 border-b border-[var(--ps-divider)]">
          <h3 className="font-bold text-base text-[var(--ps-charcoal)]">Aufgabe 2</h3>
          <p className="text-xs text-[var(--ps-mute)] mt-1">Klicken Sie eine Lücke an, dann wählen Sie das passende Wort.</p>
          {selectedGap && <p className="text-xs text-[var(--ps-blue)] font-medium mt-1">Lücke {selectedGap} ausgewählt</p>}
        </div>
        <div className="text-sm leading-[2] text-[var(--ps-charcoal)]">{textParts}</div>

        <div className="mt-8 pt-5 border-t border-[var(--ps-divider)]">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[var(--ps-mute)] mb-3">
            Wortbank — {gapNums.filter(n => answers[String(n)]).length}/{gapNums.length} vergeben
          </p>
          <div className="flex flex-wrap gap-2">
            {wortbank.map(({ id, word }) => {
              const isUsed = usedWords.has(id);
              return (
                <motion.button key={id} whileHover={!isUsed ? { scale: 1.05 } : {}} whileTap={!isUsed ? { scale: 0.95 } : {}}
                  onClick={() => !isUsed && handleWordSelect(id)} data-testid={`word-${id}`}
                  className={`px-3 py-1.5 rounded-full border text-xs font-medium transition-all ${
                    isUsed ? "bg-gray-100 text-gray-400 border-gray-200 line-through cursor-default"
                      : selectedGap ? "bg-white text-[var(--ps-blue)] border-[var(--ps-blue)]/50 hover:bg-[var(--ps-blue)] hover:text-white cursor-pointer"
                      : "bg-white text-[var(--ps-charcoal)] border-[var(--ps-divider)] cursor-pointer hover:border-[var(--ps-blue)]/40"
                  }`}>
                  <span className="text-[9px] text-[var(--ps-mute)] mr-1 font-normal">{id}</span>{word}
                </motion.button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="exam-right" data-testid="gap-navigator">
        <p className="text-xs font-semibold text-[var(--ps-mute)] uppercase tracking-wider mb-3">Lückenübersicht</p>
        <div className="flex flex-wrap gap-1.5 mb-6">
          {gapNums.map(n => {
            const qNum = String(n);
            return (
              <button key={n} onClick={() => toggleGap(n)} data-testid={`gap-pill-${n}`}
                className={`q-pill ${answers[qNum] ? "answered" : ""} ${flagged.has(qNum) ? "flagged" : ""} ${selectedGap === n ? "current" : ""}`}
                style={{ width: 30, height: 30, fontSize: "0.65rem" }}>
                {n}
              </button>
            );
          })}
        </div>

        <div className="space-y-2">
          {gapNums.map(n => {
            const qNum = String(n);
            const filled = answers[qNum];
            const wordLabel = filled ? (wortbank.find(w => w.id === filled)?.word ?? filled) : null;
            return (
              <div key={n} onClick={() => toggleGap(n)} data-testid={`gap-row-${n}`}
                className={`flex items-center justify-between px-3 py-2 rounded-lg border text-xs cursor-pointer transition-all ${
                  selectedGap === n ? "border-[var(--ps-blue)] bg-[var(--ps-blue)]/5" : "border-[var(--ps-divider)] hover:border-[var(--ps-blue)]/30"
                }`}>
                <span className="font-semibold text-[var(--ps-charcoal)]">{n}</span>
                {wordLabel
                  ? <span className="text-[var(--ps-blue)] font-medium">{wordLabel}</span>
                  : <span className="text-[var(--ps-mute)]">—</span>}
                <span onClick={e => e.stopPropagation()}>
                  <FlagBtn qNum={qNum} flagged={flagged} toggleFlag={toggleFlag} />
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ── Main export ──────────────────────────────────────────── */
export default function SprachbausteineModule({ exam, answers, updateAnswer, flagged, toggleFlag }) {
  const [activeTab, setActiveTab] = useState(0);
  const aufgaben = exam?.sprachbausteine?.aufgaben || [];
  const aufgabe1 = aufgaben.find(a => a.typ === "lueckentext_mc") ?? aufgaben[0];
  const aufgabe2 = aufgaben.find(a => a.typ === "lueckentext_wortbank") ?? aufgaben[1];

  if (!aufgaben.length) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="card-ps p-10 text-center max-w-sm">
          <p className="font-semibold text-sm mb-2" style={{ color: "var(--ps-charcoal)" }}>
            Keine Sprachbausteine vorhanden
          </p>
          <p className="text-xs" style={{ color: "var(--ps-body-gray)" }}>
            Dieses Übungsexemplar enthält noch keine Sprachbausteine. Generieren Sie ein neues TELC-Exam über das Dashboard, um alle 5 Module zu üben.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div data-testid="sprachbausteine-module">
      <div className="bg-[var(--ps-black)] px-8 py-3 flex items-center gap-3" data-testid="sprachbausteine-tabs">
        {["Aufgabe 1 — Multiple Choice", "Aufgabe 2 — Wortschatz"].map((label, i) => (
          <button key={i} onClick={() => setActiveTab(i)} className={`section-tab ${activeTab === i ? "active" : ""}`} data-testid={`tab-${i}`}>
            {label}
          </button>
        ))}
      </div>

      {activeTab === 0 && aufgabe1 && (
        <AufgabeMC aufgabe={aufgabe1} answers={answers} updateAnswer={updateAnswer} flagged={flagged} toggleFlag={toggleFlag} />
      )}
      {activeTab === 1 && aufgabe2 && (
        <AufgabeWortbank aufgabe={aufgabe2} answers={answers} updateAnswer={updateAnswer} flagged={flagged} toggleFlag={toggleFlag} />
      )}
    </div>
  );
}
