import { useAuth } from "@/App";
import { useNavigate } from "react-router-dom";
import { useEffect } from "react";

export default function ProtectedRoute({ children, requireAdmin = false }) {
  const { user, loading } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (!loading && !user) {
      navigate("/");
    }
    if (!loading && requireAdmin && user && !user.is_admin) {
      navigate("/dashboard");
    }
  }, [user, loading, requireAdmin, navigate]);

  if (loading) return null;
  if (!user) return null;
  if (requireAdmin && !user.is_admin) return null;
  return children;
}
