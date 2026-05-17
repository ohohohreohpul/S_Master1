import { useNavigate } from "react-router-dom";
import { useAuth } from "@/App";
import { BookOpen, Headphones, Pen, Mic, ArrowRight, Shield, Zap, BarChart3, Users, Globe, Award, Languages } from "lucide-react";
import { motion } from "framer-motion";

const fadeUp = { hidden: { opacity: 0, y: 30 }, show: { opacity: 1, y: 0 } };
const stagger = { hidden: {}, show: { transition: { staggerChildren: 0.12 } } };

export default function LandingPage() {
  const navigate = useNavigate();
  const { user } = useAuth();

  const handleLogin = () => {
    // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
    const redirectUrl = window.location.origin + "/dashboard";
    window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
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
      {/* Nav */}
      <nav className="nav-ps" data-testid="nav-bar">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-[var(--ps-blue)] flex items-center justify-center">
            <BookOpen size={16} className="text-white" />
          </div>
          <span className="font-semibold text-base tracking-wide">IELTS Pro</span>
        </div>
        <div className="flex items-center gap-6">
          {user ? (
            <button data-testid="go-dashboard-btn" onClick={() => navigate("/dashboard")} className="btn-ps btn-ps-primary" style={{ padding: "8px 20px", fontSize: "0.875rem" }}>
              Dashboard <ArrowRight size={14} />
            </button>
          ) : (
            <button data-testid="login-btn" onClick={handleLogin} className="btn-ps btn-ps-primary" style={{ padding: "8px 20px", fontSize: "0.875rem" }}>
              Sign in with Google
            </button>
          )}
        </div>
      </nav>

      {/* Hero - Dark */}
      <section className="panel-dark relative py-28 px-8 md:px-16 lg:px-24" data-testid="hero-section">
        {/* Subtle grid pattern */}
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
          {/* Exam type chips */}
          <motion.div variants={fadeUp} className="flex items-center justify-center gap-3 mb-10 flex-wrap">
            <span className="flex items-center gap-2 px-4 py-2 rounded-full bg-[var(--ps-blue)]/20 border border-[var(--ps-blue)]/30 text-sm font-medium text-white">
              <BookOpen size={14} className="text-[var(--ps-cyan)]" /> IELTS Academic
            </span>
            <span className="flex items-center gap-2 px-4 py-2 rounded-full bg-amber-500/20 border border-amber-500/30 text-sm font-medium text-white">
              <Languages size={14} className="text-amber-400" /> TELC Deutsch B1 / B2
            </span>
          </motion.div>
          <motion.div variants={fadeUp} className="flex items-center justify-center gap-4 flex-wrap">
            <button data-testid="hero-start-btn" onClick={user ? () => navigate("/dashboard") : handleLogin}
              className="btn-ps btn-ps-orange" style={{ fontSize: "1rem", padding: "16px 40px" }}>
              Start Practice Test <ArrowRight size={18} />
            </button>
            <button onClick={() => navigate("/pricing")} className="flex items-center gap-2 text-sm text-gray-400 hover:text-white transition-colors">
              See pricing <ArrowRight size={14} />
            </button>
          </motion.div>

          {/* Stats row */}
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

      {/* Modules - Light */}
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

      {/* How it works - Dark */}
      <section className="panel-dark py-24 px-8 md:px-16 lg:px-24" data-testid="features-section">
        <div className="max-w-5xl mx-auto">
          <motion.h2 initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} viewport={{ once: true }}
            className="display-sm text-center mb-16 text-white">How it works</motion.h2>
          <motion.div className="grid grid-cols-1 md:grid-cols-3 gap-12"
            initial="hidden" whileInView="show" viewport={{ once: true }} variants={stagger}>
            {[
              { step: "01", icon: Shield, title: "Choose your test", desc: "Select Academic or General Training. Pick a full test or individual module." },
              { step: "02", icon: Zap, title: "Audio pre-loads", desc: "All listening audio generates via ElevenLabs V3 and loads before your timer starts. Zero buffering." },
              { step: "03", icon: BarChart3, title: "Get your band score", desc: "AI scores your responses against official IELTS criteria with detailed feedback." },
            ].map((f, i) => (
              <motion.div key={i} variants={fadeUp} className="text-center">
                <div className="text-5xl font-extralight text-white/10 mb-4">{f.step}</div>
                <div className="w-14 h-14 rounded-2xl mx-auto mb-5 flex items-center justify-center" style={{ background: "rgba(255,255,255,0.05)", backdropFilter: "blur(8px)" }}>
                  <f.icon size={26} className="text-[var(--ps-cyan)]" />
                </div>
                <h3 className="font-medium text-base mb-2 text-white">{f.title}</h3>
                <p className="text-sm text-gray-400 leading-relaxed">{f.desc}</p>
              </motion.div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* CTA - Light */}
      <section className="panel-light py-24 px-8 text-center" data-testid="cta-section">
        <motion.div className="max-w-2xl mx-auto" initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}>
          <h2 className="display-sm mb-4" style={{ color: "var(--ps-black)" }}>Ready to practice?</h2>
          <p className="text-[var(--ps-body-gray)] mb-8 text-base">Sign in with Google to access your first full practice test. Free.</p>
          <button data-testid="cta-start-btn" onClick={user ? () => navigate("/dashboard") : handleLogin}
            className="btn-ps btn-ps-primary" style={{ fontSize: "1rem", padding: "16px 40px" }}>
            Get Started <ArrowRight size={18} />
          </button>
        </motion.div>
      </section>

      {/* Footer - Blue */}
      <footer className="panel-blue py-12 px-8 text-center" data-testid="footer">
        <p className="text-sm text-white/80 font-light">IELTS &amp; TELC Deutsch Mock Exam Platform</p>
        <p className="text-xs text-white/40 mt-2">Not affiliated with IDP, British Council, or TELC GmbH. All content is AI-generated.</p>
      </footer>
    </div>
  );
}
