import { supabase } from "@/lib/supabase";

const ANON_KEY = process.env.REACT_APP_SUPABASE_ANON_KEY;

/**
 * Drop-in replacement for fetch that:
 * - Replaces `credentials: "include"` with a Supabase Bearer token header
 * - Passes through all other options unchanged
 */
export async function apiFetch(url, options = {}) {
  const { credentials: _drop, ...rest } = options;

  const { data: { session } } = await supabase.auth.getSession();
  const token = session?.access_token || ANON_KEY;

  const headers = {
    Authorization: `Bearer ${token}`,
    Apikey: ANON_KEY,
    ...(rest.headers || {}),
  };

  return fetch(url, { ...rest, headers });
}
