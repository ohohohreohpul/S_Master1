import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth, API } from "@/App";

export default function AuthCallback() {
  const hasProcessed = useRef(false);
  const navigate = useNavigate();
  const { setUser } = useAuth();

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const hash = window.location.hash;
    const sessionId = new URLSearchParams(hash.substring(1)).get("session_id");

    if (!sessionId) {
      navigate("/");
      return;
    }

    (async () => {
      try {
        const res = await fetch(`${API}/auth/session`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ session_id: sessionId }),
        });
        if (res.ok) {
          const user = await res.json();
          setUser(user);
          navigate("/dashboard", { state: { user }, replace: true });
        } else {
          navigate("/");
        }
      } catch {
        navigate("/");
      }
    })();
  }, [navigate, setUser]);

  return (
    <div className="flex items-center justify-center min-h-screen" data-testid="auth-callback">
      <div className="spinner" />
    </div>
  );
}
