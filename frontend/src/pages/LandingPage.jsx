import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/App";
import { supabase } from "@/lib/supabase";
import { BookOpen, Headphones, Pen, Mic, ArrowRight, Shield, Zap, ChartBar as BarChart3, Languages, X, Eye, EyeOff } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

const fadeUp = { hidden: { opacity: 0, y: 30 }, show: { opacity: 1, y: 0 } };
const stagger = { hidden: {}, show: { transition: { staggerChildren: 0.12 } } };

function AuthModal({ mode, onClose, onSwitch }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const isSignUp = mode === "signup";

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      if (isSignUp) {
        const { error: err } = await supabase.auth.signUp({
          email,
          password,
          options: { data: { full_name: name, name } },
        });
        if (err) throw err;
        navigate("/dashboard");
      } else {
        const { error: err } = await supabase.auth.signInWithPassword({ email, password });
        if (err) throw err;
        navigate("/dashboard");
      }
    } catch (err) {
      setError(err.message || "Authentication failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 16 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 16 }}
        className="bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden"
      >
        {/* Header */}
        <div className="px-7 pt-7 pb-5 relative" style={{ background: "linear-gradient(135deg, #040d1a, #0a1628)" }}>
          <button onClick={onClose} className="absolute top-4 right-4 text-gray-500 hover:text-white transition-colors">
            <X size={18} />
          </button>
          <div className="w-10 h-10 rounded-xl bg-[var(--ps-blue)] flex items-center justify-center mb-4">
            <BookOpen size={18} className="text-white" />
          </div>
          <h2 className="text-white font-semibold text-lg">
            {isSignUp ? "Create account" : "Welcome back"}
          </h2>
          <p className="text-gray-400 text-sm mt-1">
            {isSignUp ? "Start your exam preparation journey" : "Sign in to your IELTS Pro account"}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="px-7 py-6 space-y-4">
          {isSignUp && (
            <div>
              <label className="block text-xs font-semibold text-[var(--ps-charcoal)] mb-1.5">Full name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Your name"
                required
                className="w-full px-4 py-2.5 rounded-xl border border-[var(--ps-divider)] text-sm focus:outline-none focus:border-[var(--ps-blue)] transition-colors"
              />
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-[var(--ps-charcoal)] mb-1.5">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              required
              className="w-full px-4 py-2.5 rounded-xl border border-[var(--ps-divider)] text-sm focus:outline-none focus:border-[var(--ps-blue)] transition-colors"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-[var(--ps-charcoal)] mb-1.5">Password</label>
            <div className="relative">
              <input
                type={showPass ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
                minLength={6}
                className="w-full px-4 py-2.5 pr-10 rounded-xl border border-[var(--ps-divider)] text-sm focus:outline-none focus:border-[var(--ps-blue)] transition-colors"
              />
              <button
                type="button"
                onClick={() => setShowPass((v) => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-[var(--ps-charcoal)]"
              >
                {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>

          {error && (
            <p className="text-xs text-red-600 bg-red-50 px-3 py-2 rounded-lg">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full btn-ps btn-ps-primary flex items-center justify-center gap-2"
            style={{ padding: "11px", fontSize: "0.875rem" }}
          >
            {loading ? (
              <><div className="spinner" style={{ width: 15, height: 15 }} /> {isSignUp ? "Creating account…" : "Signing in…"}</>
            ) : (
              <>{isSignUp ? "Create account" : "Sign in"} <ArrowRight size={15} /></>
            )}
          </button>

          <p className="text-center text-xs text-[var(--ps-body-gray)]">
            {isSignUp ? "Already have an account? " : "Don't have an account? "}
            <button
              type="button"
              onClick={onSwitch}
              className="text-[var(--ps-blue)] font-semibold hover:underline"
            >
              {isSignUp ? "Sign in" : "Sign up free"}
            </button>
          </p>
        </form>
      </motion.div>
    </motion.div>
  );
}

export default function LandingPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [authMode, setAuthMode] = useState(null); // null | "signin" | "signup"

  const handleCTA = () => {
    if (user) { navigate("/dashboard"); return; }
    setAuthMode("signup");
  };

  const modules = [
    { icon: Headphones, title: "Listening", desc: "4 sections, 40 questions with natural AI audio and British accents.", time: "30 min" },
    { icon: BookOpen, title: "Reading", desc: "3 academic passages with authentic IELTS question formats.", time: "60 min" },
    { icon: Pen, title: "Writing", desc: "2 tasks scored by AI against official band descriptors.", time: "60 min" },
    { icon: Mic, title: "Speaking", desc: "3 parts with AI examiner voice and instant scoring.", time: "14 min" },
  ];

  const stats = [
    { value: "9.0", label: "Max Band Score" },
    { value: "40+", label: "Questions per Module" },
    { value: "4", label: "Full Modules" },
    { value: "AI", label: "Powered Scoring" },
  ];

  return (
    <div data-testid="landing-page" className="overflow-hidden">
      <AnimatePresence>
        {authMode && (
          <AuthModal
            mode={authMode}
            onClose={() => setAuthMode(null)}
            onSwitch={() => setAuthMode(authMode === "signin" ? "signup" : "signin")}
          />
        )}
      </AnimatePresence>

      {/* Nav */}
      <nav className="nav-ps" data-testid="nav-bar">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-[var(--ps-blue)] flex items-center justify-center">
            <BookOpen size={16} className="text-white" />
          </div>
          <span className="font-semibold text-base tracking-wide">IELTS Pro</span>
        </div>
        <div className="flex items-center gap-4">
          {user ? (
            <button data-testid="go-dashboard-btn" onClick={() => navigate("/dashboard")} className="btn-ps btn-ps-primary" style={{ padding: "8px 20px", fontSize: "0.875rem" }}>
              Dashboard <ArrowRight size={14} />
            </button>
          ) : (
            <>
              <button data-testid="login-btn" onClick={() => setAuthMode("signin")} className="text-sm text-gray-300 hover:text-white transition-colors">
                Sign in
              </button>
              <button onClick={() => setAuthMode("signup")} className="btn-ps btn-ps-primary" style={{ padding: "8px 20px", fontSize: "0.875rem" }}>
                Get started free
              </button>
            </>
          )}
        </div>
      </nav>

      {/* Hero */}
      <section className="panel-dark relative py-28 px-8 md:px-16 lg:px-24" data-testid="hero-section">
        <div className="absolute inset-0 opacity-[0.03]" style={{ backgroundImage: "radial-gradient(circle at 1px 1px, white 1px, transparent 0)", backgroundSize: "40px 40px" }} />
        <motion.div className="max-w-5xl mx-auto text-center relative" initial="hidden" animate="show" variants={stagger}>
          <motion.p variants={fadeUp} className="text-[var(--ps-cyan)] font-medium text-xs tracking-[0.25em] mb-8 uppercase">
            AI-powered exam preparation platform
          </motion.p>
          <motion.h1 variants={fadeUp} className="display-xl mb-6 text-white">
            Master IELTS and TELC<br />Deutsch with AI
          </motion.h1>
          <motion.p variants={fadeUp} className="text-lg font-light text-gray-400 max-w-2xl mx-auto mb-8 leading-relaxed">
            AI-powered mock exams with real scoring, natural audio, and detailed feedback. Prepare for IELTS Academic or TELC Deutsch at B1 and B2.
          </motion.p>
          <motion.div variants={fadeUp} className="flex items-center justify-center gap-3 mb-10 flex-wrap">
            <span className="flex items-center gap-2 px-4 py-2 rounded-full bg-[var(--ps-blue)]/20 border border-[var(--ps-blue)]/30 text-sm font-medium text-white">
              <BookOpen size={14} className="text-[var(--ps-cyan)]" /> IELTS Academic
            </span>
            <span className="flex items-center gap-2 px-4 py-2 rounded-full bg-amber-500/20 border border-amber-500/30 text-sm font-medium text-white">
              <Languages size={14} className="text-amber-400" /> TELC Deutsch B1 / B2
            </span>
          </motion.div>
          <motion.div variants={fadeUp} className="flex items-center justify-center gap-4 flex-wrap">
            <button data-testid="hero-start-btn" onClick={handleCTA}
              className="btn-ps btn-ps-orange" style={{ fontSize: "1rem", padding: "16px 40px" }}>
              Start Practice Test <ArrowRight size={18} />
            </button>
            <button onClick={() => navigate("/pricing")} className="flex items-center gap-2 text-sm text-gray-400 hover:text-white transition-colors">
              See pricing <ArrowRight size={14} />
            </button>
          </motion.div>
          <motion.div variants={fadeUp} className="grid grid-cols-4 gap-6 mt-16 max-w-2xl mx-auto">
            {stats.map((s, i) => (
              <div key={i} className="text-center">
                <div className="text-2xl font-light text-white mb-1">{s.value}</div>
                <div className="text-xs text-gray-500">{s.label}</div>
              </div>
            ))}
          </motion.div>
        </motion.div>
      </section>

      {/* Modules */}
      <section className="panel-light py-24 px-8 md:px-16 lg:px-24" data-testid="modules-section">
        <div className="max-w-6xl mx-auto">
          <motion.div initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ duration: 0.6 }}>
            <h2 className="display-sm text-center mb-2" style={{ color: "var(--ps-black)" }}>All modules, both exams</h2>
            <p className="text-center text-[var(--ps-body-gray)] mb-14 text-base">Exactly as you'll experience on test day — IELTS and TELC Deutsch</p>
          </motion.div>
          <motion.div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5"
            initial="hidden" whileInView="show" viewport={{ once: true }} variants={stagger}>
            {modules.map((m, i) => (
              <motion.div key={i} variants={fadeUp} whileHover={{ y: -6, boxShadow: "0 12px 40px rgba(0,0,0,0.1)" }}
                className="card-ps p-7 cursor-default" data-testid={`module-card-${m.title.toLowerCase()}`}>
                <div className="flex items-center justify-between mb-5">
                  <div className="w-12 h-12 rounded-2xl flex items-center justify-center bg-[var(--ps-blue)]/5">
                    <m.icon size={24} className="text-[var(--ps-blue)]" />
                  </div>
                  <span className="text-xs font-medium text-[var(--ps-mute)]">{m.time}</span>
                </div>
                <h3 className="font-semibold text-base mb-2" style={{ color: "var(--ps-charcoal)" }}>{m.title}</h3>
                <p className="text-sm text-[var(--ps-body-gray)] leading-relaxed">{m.desc}</p>
              </motion.div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* How it works */}
      <section className="panel-dark py-24 px-8 md:px-16 lg:px-24" data-testid="features-section">
        <div className="max-w-5xl mx-auto">
          <motion.h2 initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} viewport={{ once: true }}
            className="display-sm text-center mb-16 text-white">How it works</motion.h2>
          <motion.div className="grid grid-cols-1 md:grid-cols-3 gap-12"
            initial="hidden" whileInView="show" viewport={{ once: true }} variants={stagger}>
            {[
              { step: "01", icon: Shield, title: "Choose your test", desc: "Select IELTS Academic or TELC Deutsch. Pick a full test or individual module." },
              { step: "02", icon: Zap, title: "Audio pre-loads", desc: "All listening audio is generated and pre-loaded before your timer starts. Zero buffering." },
              { step: "03", icon: BarChart3, title: "Get your band score", desc: "AI scores your responses against official criteria with detailed feedback." },
            ].map((f, i) => (
              <motion.div key={i} variants={fadeUp} className="text-center">
                <div className="text-5xl font-extralight text-white/10 mb-4">{f.step}</div>
                <div className="w-14 h-14 rounded-2xl mx-auto mb-5 flex items-center justify-center" style={{ background: "rgba(255,255,255,0.05)" }}>
                  <f.icon size={26} className="text-[var(--ps-cyan)]" />
                </div>
                <h3 className="font-medium text-base mb-2 text-white">{f.title}</h3>
                <p className="text-sm text-gray-400 leading-relaxed">{f.desc}</p>
              </motion.div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* CTA */}
      <section className="panel-light py-24 px-8 text-center" data-testid="cta-section">
        <motion.div className="max-w-2xl mx-auto" initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}>
          <h2 className="display-sm mb-4" style={{ color: "var(--ps-black)" }}>Ready to practice?</h2>
          <p className="text-[var(--ps-body-gray)] mb-8 text-base">Create a free account to access your first full practice test.</p>
          <button data-testid="cta-start-btn" onClick={handleCTA}
            className="btn-ps btn-ps-primary" style={{ fontSize: "1rem", padding: "16px 40px" }}>
            Get Started Free <ArrowRight size={18} />
          </button>
        </motion.div>
      </section>

      {/* Footer */}
      <footer className="panel-blue py-12 px-8 text-center" data-testid="footer">
        <p className="text-sm text-white/80 font-light">IELTS &amp; TELC Deutsch Mock Exam Platform</p>
        <p className="text-xs text-white/40 mt-2">Not affiliated with IDP, British Council, or TELC GmbH. All content is AI-generated.</p>
      </footer>
    </div>
  );
}
