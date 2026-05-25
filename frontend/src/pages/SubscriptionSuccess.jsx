import { useNavigate } from "react-router-dom";
import { CheckCircle, ArrowRight, Zap } from "lucide-react";
import { motion } from "framer-motion";

export default function SubscriptionSuccess() {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-[var(--ps-ice)] flex items-center justify-center" data-testid="subscription-success">
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ type: "spring", duration: 0.6 }}
        className="card-ps p-12 max-w-md w-full text-center mx-4">
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ type: "spring", delay: 0.2, duration: 0.5 }}
          className="w-20 h-20 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-6">
          <CheckCircle size={44} className="text-emerald-500" />
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}>
          <div className="flex items-center justify-center gap-2 mb-3">
            <span className="bg-[var(--ps-blue)] text-white text-xs font-bold px-3 py-1 rounded-full flex items-center gap-1">
              <Zap size={11} /> Pro
            </span>
          </div>
          <h1 className="display-sm mb-3" style={{ color: "var(--ps-black)" }}>
            You're now Pro!
          </h1>
          <p className="text-[var(--ps-body-gray)] text-base mb-8 leading-relaxed">
            Welcome to Pro! You now have access to TELC Deutsch exams, full test mode, and unlimited attempts.
          </p>

          <motion.button
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
            onClick={() => navigate("/dashboard")}
            className="btn-ps btn-ps-primary flex items-center justify-center gap-2 w-full"
            style={{ padding: "14px", fontSize: "0.9rem" }}
            data-testid="go-to-dashboard-btn">
            Go to Dashboard <ArrowRight size={16} />
          </motion.button>
        </motion.div>
      </motion.div>
    </div>
  );
}
