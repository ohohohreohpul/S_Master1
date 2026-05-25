import { useAuth } from "@/App";
import { useNavigate, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";

// Open-access mode: no auth required. requireAdmin still guards the admin panel.
export default function ProtectedRoute({ children, requireAdmin = false }) {
  const { user } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (requireAdmin && user && !user.is_admin) {
      navigate("/dashboard");
    }
  }, [user, requireAdmin, navigate]);

  // Allow everyone through; only block non-admins from admin-only routes
  if (requireAdmin && user && !user.is_admin) return null;
  return children;
}
