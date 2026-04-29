import { useNavigate } from "react-router-dom";
import { useAuth } from "@/App";
import { BookOpen, Headphones, Pen, Mic, ArrowRight, Shield, Zap, BarChart3 } from "lucide-react";

export default function LandingPage() {
  const navigate = useNavigate();
  const { user } = useAuth();

  const handleLogin = () => {
    // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
    const redirectUrl = window.location.origin + "/dashboard";
    window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
  };

  const modules = [
    { icon: Headphones, title: "Listening", desc: "4 sections, 40 questions. Natural AI-generated audio with British accents.", color: "#0070cc" },
    { icon: BookOpen, title: "Reading", desc: "3 passages, 40 questions. Academic texts with authentic question types.", color: "#1eaedb" },
    { icon: Pen, title: "Writing", desc: "2 tasks with AI scoring against official IELTS band descriptors.", color: "#0070cc" },
    { icon: Mic, title: "Speaking", desc: "3 parts with AI examiner. Record responses and get instant feedback.", color: "#1eaedb" },
  ];

  const features = [
    { icon: Shield, title: "Exam Fidelity", desc: "Replicates the official IELTS Computer-Based Test interface with 1:1 accuracy." },
    { icon: Zap, title: "AI-Powered Audio", desc: "ElevenLabs V3 generates natural, multi-accent audio. Pre-loaded before every exam." },
    { icon: BarChart3, title: "Band Score Analytics", desc: "Detailed scoring against official IELTS criteria with improvement suggestions." },
  ];

  return (
    <div data-testid="landing-page">
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
      <section className="panel-dark py-24 px-8 md:px-16 lg:px-24" data-testid="hero-section">
        <div className="max-w-5xl mx-auto text-center">
          <p className="text-[var(--ps-cyan)] font-medium text-sm tracking-widest mb-6 uppercase">Computer-based IELTS simulation</p>
          <h1 className="display-xl mb-6 text-white">
            The most authentic IELTS mock exam
          </h1>
          <p className="text-lg font-light text-gray-300 max-w-2xl mx-auto mb-10 leading-relaxed">
            Full four-module practice tests with AI-generated audio, real-time scoring, and detailed band feedback. Pre-loaded audio ensures perfect exam timing.
          </p>
          <div className="flex items-center justify-center gap-4 flex-wrap">
            <button data-testid="hero-start-btn" onClick={user ? () => navigate("/dashboard") : handleLogin}
              className="btn-ps btn-ps-orange" style={{ fontSize: "1rem", padding: "14px 36px" }}>
              Start Practice Test <ArrowRight size={18} />
            </button>
            <button className="btn-ps btn-ps-ghost" style={{ color: "white", borderColor: "rgba(255,255,255,0.3)" }}>
              View Sample Results
            </button>
          </div>
        </div>
      </section>

      {/* Modules - Light */}
      <section className="panel-light py-20 px-8 md:px-16 lg:px-24" data-testid="modules-section">
        <div className="max-w-6xl mx-auto">
          <h2 className="display-sm text-center mb-4" style={{ color: "var(--ps-black)" }}>All four IELTS modules</h2>
          <p className="text-center text-[var(--ps-body-gray)] mb-12 text-base">Exactly as you'll experience on test day</p>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {modules.map((m, i) => (
              <div key={i} className="card-ps p-8 text-center" data-testid={`module-card-${m.title.toLowerCase()}`}>
                <div className="w-14 h-14 rounded-2xl mx-auto mb-5 flex items-center justify-center" style={{ background: `${m.color}10` }}>
                  <m.icon size={28} style={{ color: m.color }} />
                </div>
                <h3 className="font-semibold text-lg mb-2" style={{ color: "var(--ps-charcoal)" }}>{m.title}</h3>
                <p className="text-sm text-[var(--ps-body-gray)] leading-relaxed">{m.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Features - Dark */}
      <section className="panel-dark py-20 px-8 md:px-16 lg:px-24" data-testid="features-section">
        <div className="max-w-5xl mx-auto">
          <h2 className="display-sm text-center mb-12 text-white">Built for serious preparation</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            {features.map((f, i) => (
              <div key={i} className="text-center">
                <div className="w-12 h-12 rounded-xl mx-auto mb-4 flex items-center justify-center" style={{ background: "rgba(255,255,255,0.1)" }}>
                  <f.icon size={24} className="text-[var(--ps-cyan)]" />
                </div>
                <h3 className="font-medium text-base mb-2 text-white">{f.title}</h3>
                <p className="text-sm text-gray-400 leading-relaxed">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA - Light */}
      <section className="panel-light py-20 px-8 text-center" data-testid="cta-section">
        <div className="max-w-2xl mx-auto">
          <h2 className="display-sm mb-4" style={{ color: "var(--ps-black)" }}>Ready to practice?</h2>
          <p className="text-[var(--ps-body-gray)] mb-8">Sign in with Google to access your first full practice test.</p>
          <button data-testid="cta-start-btn" onClick={user ? () => navigate("/dashboard") : handleLogin} className="btn-ps btn-ps-primary" style={{ fontSize: "1rem", padding: "14px 36px" }}>
            Get Started Free <ArrowRight size={18} />
          </button>
        </div>
      </section>

      {/* Footer - Blue */}
      <footer className="panel-blue py-10 px-8 text-center" data-testid="footer">
        <p className="text-sm text-white/70">IELTS Pro Mock Exam Platform. Powered by AI.</p>
        <p className="text-xs text-white/40 mt-2">Not affiliated with IDP or British Council. All content is AI-generated for practice purposes.</p>
      </footer>
    </div>
  );
}
