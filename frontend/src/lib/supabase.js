import { createClient } from "@supabase/supabase-js";

const supabaseUrl = process.env.REACT_APP_SUPABASE_URL;
const supabaseAnonKey = process.env.REACT_APP_SUPABASE_ANON_KEY;

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

// Edge Function base URL — all /api/* routes are served from the "api" function
export const API = `${supabaseUrl}/functions/v1/api`;

export function getAuthHeaders(session) {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${session?.access_token || supabaseAnonKey}`,
    Apikey: supabaseAnonKey,
  };
}
