import { useState, useEffect, useCallback, createContext, useContext } from "react";
import "@/App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import LandingPage from "@/pages/LandingPage";
import Dashboard from "@/pages/Dashboard";
import ExamPage from "@/pages/ExamPage";
import ResultsPage from "@/pages/ResultsPage";
import AdminPage from "@/pages/AdminPage";
import PricingPage from "@/pages/PricingPage";
import SubscriptionSuccess from "@/pages/SubscriptionSuccess";
import ProtectedRoute from "@/components/ProtectedRoute";
import { supabase, API as SUPABASE_API } from "@/lib/supabase";

// Re-export so existing imports of { API } from "@/App" still work
export { SUPABASE_API as API };

// Auth Context
const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [profile, setProfile] = useState(null);
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadProfile = useCallback(async (userId) => {
    const { data } = await supabase
      .from("profiles")
      .select("*")
      .eq("id", userId)
      .maybeSingle();
    setProfile(data);
    return data;
  }, []);

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session: s } }) => {
      setSession(s);
      setUser(s?.user ?? null);
      if (s?.user) {
        loadProfile(s.user.id).finally(() => setLoading(false));
      } else {
        setLoading(false);
      }
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, s) => {
      setSession(s);
      setUser(s?.user ?? null);
      if (s?.user) {
        (async () => {
          await loadProfile(s.user.id);
          setLoading(false);
        })();
      } else {
        setProfile(null);
        setLoading(false);
      }
    });

    return () => subscription.unsubscribe();
  }, [loadProfile]);

  const logout = async () => {
    await supabase.auth.signOut();
    setUser(null);
    setProfile(null);
    setSession(null);
  };

  // Merged user object compatible with existing page components
  const mergedUser = user ? {
    ...user,
    name: profile?.name || user.email?.split("@")[0] || "User",
    email: user.email,
    picture: profile?.picture || "",
    is_admin: profile?.is_admin || false,
  } : null;

  return (
    <AuthContext.Provider value={{ user: mergedUser, profile, session, setUser, loading, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
          }
          <Route path="/admin" element={<ProtectedRoute requireAdmin><AdminPage /></ProtectedRoute>} />
          }
          <Route path="/exam/:examId" element={<ProtectedRoute><ExamPage /></ProtectedRoute>} />
          }
          <Route path="/results/:attemptId" element={<ProtectedRoute><ResultsPage /></ProtectedRoute>} />
          }
          <Route path="/pricing" element={<PricingPage />} />
          <Route path="/subscription/success" element={<ProtectedRoute><SubscriptionSuccess /></ProtectedRoute>} />
          }
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
