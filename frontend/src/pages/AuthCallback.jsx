import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

// Legacy route — Emergent OAuth is no longer used.
// Redirect users to the home page.
export default function AuthCallback() {
  const navigate = useNavigate();

  useEffect(() => {
    navigate("/", { replace: true });
  }, [navigate]);

  return (
    <div className="flex items-center justify-center min-h-screen" data-testid="auth-callback">
      <div className="spinner" />
    </div>
  );
}
