import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/App";
import { efetch } from "@/lib/supabase";
import { Check, Zap, Lock, BookOpen, Headphones, Pen, Mic, ArrowRight, CreditCard, Settings } from "lucide-react";
import { motion } from "framer-motion";

const fadeUp = { hidden: { opacity: 0, y: 20 }, show: { opacity: 1, y: 0 } };
const stagger = { hidden: {}, show: { transition: { staggerChildren: 0.1 } } };

export default function PricingPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [billing, setBilling] = useState("monthly");
  const [loading, setLoading] = useState(false);
  const [portalLoading, setPortalLoading] = useState(false);

  const handleGetPro = async () => {
    if (!user) {
      const redirectUrl = window.location.origin + "/dashboard";
      window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
      return;
    }
    setLoading(true);
    try {
      const data = await efetch("stripe", "/checkout", "POST", { plan: billing });
      if (data?.url) window.location.href = data.url;
    } catch (e) {
      console.error("Checkout error:", e);
    } finally {
      setLoading(false);
    }
  };

  const handleManageSubscription = async () => {
    setPortalLoading(true);
    try {
      const data = await efetch("stripe", "/portal");
      if (data?.url) window.location.href = data.url;
    } catch (e) {
      console.error("Portal error:", e);
    } finally {
      setPortalLoading(false);
    }
  };

  const freeFeatures = [
    "IELTS mock exams only",
    "5 attempts per month",
    "Single module mode",
    "AI band scoring",
    "Answer review",
  ];

  const proFeatures = [
    "IELTS + TELC Deutsch exams",
    "Unlimited attempts",
    "Full test mode (all 4 modules)",
    "Priority AI scoring",
    "Detailed band feedback",
    "Scratch pad & question flagging",
    "Priority support",
  ];

  const monthlyPrice = "€9.99";
  const annualPrice = "€89.99";
  const annualMonthly = "€7.50";

  return (
    <div className="min-h-screen bg-[var(--ps-ice)]" data-testid="pricing-page">
      {/* Nav */}
      <nav className="nav-ps">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-[var(--ps-blue)] flex items-center justify-center">
            <BookOpen size={16} className="text-white" />
          </div>
          <span className="font-semibold text-base tracking-wide">IELTS Pro</span>
        </div>
        <div className="flex items-center gap-4">
          {user ? (
            <button onClick={() => navigate("/dashboard")} className="btn-ps btn-ps-secondary" style={{ padding: "8px 20px", fontSize: "0.875rem" }}>
              Dashboard
            </button>
          ) : (
            <button onClick={() => {
              const redirectUrl = window.location.origin + "/dashboard";
              window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
            }} className="btn-ps btn-ps-primary" style={{ padding: "8px 20px", fontSize: "0.875rem" }}>
              Sign in
            </button>
          )}
        </div>
      </nav>

      <motion.div className="max-w-5xl mx-auto px-6 py-16" initial="hidden" animate="show" variants={stagger}>
        {/* Header */}
        <motion.div variants={fadeUp} className="text-center mb-12">
          <p className="text-[var(--ps-blue)] text-xs font-semibold uppercase tracking-widest mb-3">Pricing</p>
          <h1 className="display-sm mb-4" style={{ color: "var(--ps-black)" }}>
            Choose your plan
          </h1>
          <p className="text-[var(--ps-body-gray)] text-base max-w-xl mx-auto">
            Start free with IELTS practice, or unlock TELC Deutsch, full test mode, and unlimited attempts with Pro.
          </p>
        </motion.div>

        {/* Billing toggle */}
        <motion.div variants={fadeUp} className="flex items-center justify-center gap-4 mb-10">
          <div className="flex items-center bg-white rounded-full p-1 border border-[var(--ps-divider)] shadow-sm">
            <button
              onClick={() => setBilling("monthly")}
              className={`px-5 py-2 rounded-full text-sm font-medium transition-all ${
                billing === "monthly"
                  ? "bg-[var(--ps-blue)] text-white shadow-sm"
                  : "text-[var(--ps-body-gray)] hover:text-[var(--ps-charcoal)]"
              }`}>
              Monthly
            </button>
            <button
              onClick={() => setBilling("annual")}
              className={`px-5 py-2 rounded-full text-sm font-medium transition-all flex items-center gap-2 ${
                billing === "annual"
                  ? "bg-[var(--ps-blue)] text-white shadow-sm"
                  : "text-[var(--ps-body-gray)] hover:text-[var(--ps-charcoal)]"
              }`}>
              Annual
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-bold ${
                billing === "annual" ? "bg-white/20 text-white" : "bg-emerald-100 text-emerald-700"
              }`}>
                Save 25%
              </span>
            </button>
          </div>
        </motion.div>

        {/* Plan cards */}
        <motion.div variants={stagger} className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-3xl mx-auto">
          {/* Free */}
          <motion.div variants={fadeUp} whileHover={{ y: -4 }} className="card-ps p-8 relative overflow-hidden">
            <div className="absolute top-0 left-0 right-0 h-[3px] bg-gradient-to-r from-gray-300 to-gray-400" />
            <div className="mb-6">
              <span className="text-xs font-bold uppercase tracking-widest text-[var(--ps-body-gray)]">Free</span>
              <div className="flex items-end gap-2 mt-3">
                <span className="text-4xl font-light" style={{ color: "var(--ps-black)" }}>€0</span>
                <span className="text-sm text-[var(--ps-body-gray)] mb-1.5">/ month</span>
              </div>
              <p className="text-sm text-[var(--ps-body-gray)] mt-2">Perfect for getting started with IELTS practice.</p>
            </div>

            <ul className="space-y-3 mb-8">
              {freeFeatures.map((f, i) => (
                <li key={i} className="flex items-center gap-3 text-sm text-[var(--ps-charcoal)]">
                  <Check size={16} className="text-gray-400 flex-shrink-0" />
                  {f}
                </li>
              ))}
            </ul>

            <button
              onClick={() => navigate("/dashboard")}
              className="w-full btn-ps btn-ps-secondary"
              style={{ padding: "12px", fontSize: "0.875rem" }}>
              Get Started Free
            </button>
          </motion.div>

          {/* Pro */}
          <motion.div variants={fadeUp} whileHover={{ y: -4 }}
            className="card-ps p-8 relative overflow-hidden"
            style={{ borderColor: "var(--ps-blue)", borderWidth: 2 }}>
            <div className="absolute top-0 left-0 right-0 h-[3px] bg-gradient-to-r from-[var(--ps-blue)] to-[var(--ps-cyan)]" />
            <div className="absolute top-4 right-4">
              <span className="bg-[var(--ps-blue)] text-white text-[10px] font-bold px-2.5 py-1 rounded-full uppercase tracking-wider">
                Most Popular
              </span>
            </div>

            <div className="mb-6">
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold uppercase tracking-widest text-[var(--ps-blue)]">Pro</span>
                <Zap size={12} className="text-[var(--ps-blue)]" />
              </div>
              <div className="flex items-end gap-2 mt-3">
                <span className="text-4xl font-light" style={{ color: "var(--ps-black)" }}>
                  {billing === "annual" ? annualMonthly : monthlyPrice}
                </span>
                <span className="text-sm text-[var(--ps-body-gray)] mb-1.5">/ month</span>
              </div>
              {billing === "annual" && (
                <p className="text-xs text-emerald-600 font-medium mt-1">{annualPrice} billed annually</p>
              )}
              {billing === "monthly" && (
                <p className="text-xs text-[var(--ps-body-gray)] mt-1">or {annualPrice}/year (save 25%)</p>
              )}
              <p className="text-sm text-[var(--ps-body-gray)] mt-2">Full access to IELTS and TELC Deutsch exams.</p>
            </div>

            <ul className="space-y-3 mb-8">
              {proFeatures.map((f, i) => (
                <li key={i} className="flex items-center gap-3 text-sm text-[var(--ps-charcoal)]">
                  <Check size={16} className="text-[var(--ps-blue)] flex-shrink-0" />
                  {f}
                </li>
              ))}
            </ul>

            <motion.button
              whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}
              onClick={handleGetPro}
              disabled={loading}
              className="w-full btn-ps btn-ps-primary flex items-center justify-center gap-2"
              style={{ padding: "12px", fontSize: "0.875rem" }}
              data-testid="get-pro-btn">
              {loading ? (
                <><div className="spinner" style={{ width: 16, height: 16 }} /> Redirecting...</>
              ) : (
                <><CreditCard size={16} /> Get Pro {billing === "annual" ? "(Annual)" : "(Monthly)"}</>
              )}
            </motion.button>
          </motion.div>
        </motion.div>

        {/* Manage subscription */}
        {user && (
          <motion.div variants={fadeUp} className="text-center mt-8">
            <button
              onClick={handleManageSubscription}
              disabled={portalLoading}
              className="inline-flex items-center gap-2 text-sm text-[var(--ps-body-gray)] hover:text-[var(--ps-blue)] transition-colors"
              data-testid="manage-subscription-btn">
              <Settings size={14} />
              {portalLoading ? "Loading..." : "Manage Subscription"}
            </button>
          </motion.div>
        )}

        {/* Feature comparison */}
        <motion.div variants={fadeUp} className="mt-16">
          <h2 className="font-semibold text-lg text-center mb-8" style={{ color: "var(--ps-black)" }}>
            What's included
          </h2>
          <div className="card-ps overflow-hidden">
            <div className="grid grid-cols-3 text-xs font-semibold text-[var(--ps-body-gray)] uppercase tracking-wider p-4 border-b border-[var(--ps-divider)] bg-[var(--ps-ice)]">
              <span>Feature</span>
              <span className="text-center">Free</span>
              <span className="text-center text-[var(--ps-blue)]">Pro</span>
            </div>
            {[
              { feature: "IELTS Practice Exams", free: true, pro: true },
              { feature: "TELC Deutsch Exams", free: false, pro: true },
              { feature: "Monthly Attempts", free: "5", pro: "Unlimited" },
              { feature: "Full Test Mode", free: false, pro: true },
              { feature: "AI Scoring & Feedback", free: true, pro: true },
              { feature: "Scratch Pad", free: true, pro: true },
              { feature: "Priority Support", free: false, pro: true },
            ].map((row, i) => (
              <div key={i} className={`grid grid-cols-3 p-4 text-sm ${i % 2 === 0 ? "" : "bg-[var(--ps-ice)]/30"}`}>
                <span className="text-[var(--ps-charcoal)]">{row.feature}</span>
                <span className="text-center">
                  {typeof row.free === "boolean" ? (
                    row.free ? <Check size={16} className="text-emerald-500 mx-auto" /> : <Lock size={14} className="text-gray-300 mx-auto" />
                  ) : (
                    <span className="text-[var(--ps-body-gray)]">{row.free}</span>
                  )}
                </span>
                <span className="text-center">
                  {typeof row.pro === "boolean" ? (
                    row.pro ? <Check size={16} className="text-[var(--ps-blue)] mx-auto" /> : <Lock size={14} className="text-gray-300 mx-auto" />
                  ) : (
                    <span className="text-[var(--ps-blue)] font-medium">{row.pro}</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </motion.div>
      </motion.div>
    </div>
  );
}
