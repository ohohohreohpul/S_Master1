const SUPABASE_URL = process.env.REACT_APP_SUPABASE_URL || "";
const SUPABASE_ANON_KEY = process.env.REACT_APP_SUPABASE_ANON_KEY || "";

export { SUPABASE_URL, SUPABASE_ANON_KEY };

/**
 * Call a Supabase Edge Function.
 * sessionToken – custom session token stored in localStorage; falls back to anon key.
 */
export const efetch = async (fn, path, method = "GET", body = null, sessionToken = null) => {
  const token = sessionToken || localStorage.getItem("session_token");
  const url = `${SUPABASE_URL}/functions/v1/${fn}${path}`;

  const headers = {
    Authorization: token ? `Bearer ${token}` : `Bearer ${SUPABASE_ANON_KEY}`,
    apikey: SUPABASE_ANON_KEY,
    "Content-Type": "application/json",
  };

  const res = await fetch(url, {
    method,
    headers,
    ...(body !== null ? { body: JSON.stringify(body) } : {}),
  });

  if (!res.ok) {
    const errBody = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    throw new Error(errBody.error || `HTTP ${res.status}`);
  }
  return res.json();
};

/** Build a public Supabase Storage URL for an audio file by its audio_id. */
export const audioUrl = (audioId) =>
  audioId
    ? `${SUPABASE_URL}/storage/v1/object/public/audio-files/${audioId}.mp3`
    : null;
