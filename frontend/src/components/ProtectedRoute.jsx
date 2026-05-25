import { useAuth } from "@/App";
import { useNavigate, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";

export default function ProtectedRoute({ children, requireAdmin = false }) {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [isAuthenticated, setIsAuthenticated] = useState(location.state?.user ? true : null);

  useEffect(() => {
    if (location.state?.user) return;
    if (!loading) {
      if (user) {
        if (requireAdmin && !user.is_admin) {
          navigate("/dashboard");
        } else {
          setIsAuthenticated(true);
        }
      } else {
        setIsAuthenticated(false);
        navigate("/");
      }
    }
  }, [user, loading, navigate, location.state, requireAdmin]);

  if (loading || isAuthenticated === null) {
    return (
      <div className="flex items-center justify-center min-h-screen" data-testid="loading-screen">
        <div className="spinner" />
      </div>
    );
  }

  if (!isAuthenticated && !user) return null;
  return children;
}
