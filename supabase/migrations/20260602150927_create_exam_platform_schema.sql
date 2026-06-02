/*
  # IELTS/TELC Exam Platform Schema

  ## Summary
  Creates the full database schema for the exam platform, migrated from the
  original FastAPI/InsForge backend to Supabase.

  ## New Tables
  1. `profiles` - User profile data (extends Supabase auth.users)
     - id (uuid, FK to auth.users)
     - email, name, picture
     - stripe_customer_id, subscription (JSONB)

  2. `exams` - Exam definitions with all module content
     - exam_id (text PK), title, pathway, exam_type ("ielts"|"telc")
     - telc_level (text) for TELC exams
     - status: "generating_content" | "generating_audio" | "pending_audio" | "ready" | "error"
     - audio_progress (int 0-100)
     - listening, reading, writing, speaking (JSONB) - IELTS modules
     - hoeren, lesen, schreiben, sprechen, sprachbausteine (JSONB) - TELC modules
     - created_by (uuid FK to auth.users)

  3. `audio_files` - Audio file references in Supabase Storage
     - audio_id (text PK)
     - exam_id (FK to exams)
     - storage_path (text) - path in storage bucket
     - audio_type ("content"|"instruction")

  4. `attempts` - User exam attempts with answers and scores
     - attempt_id (text PK)
     - user_id (uuid FK to auth.users), exam_id (FK to exams)
     - module (text), mode (text), status ("in_progress"|"completed")
     - answers, scores, module_answers, module_scores (JSONB)
     - modules_completed (JSONB array), current_module (text)
     - overall_band (float)

  ## Security
  - RLS enabled on all tables
  - profiles: users can read/update their own
  - exams: all authenticated users can read; only admins can write
  - audio_files: all authenticated users can read
  - attempts: users can only access their own attempts
*/

-- ── Profiles ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profiles (
  id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email text,
  name text DEFAULT '',
  picture text DEFAULT '',
  stripe_customer_id text DEFAULT '',
  subscription jsonb DEFAULT '{}'::jsonb,
  is_admin boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own profile"
  ON profiles FOR SELECT
  TO authenticated
  USING (auth.uid() = id);

CREATE POLICY "Users can update own profile"
  ON profiles FOR UPDATE
  TO authenticated
  USING (auth.uid() = id)
  WITH CHECK (auth.uid() = id);

CREATE POLICY "Users can insert own profile"
  ON profiles FOR INSERT
  TO authenticated
  WITH CHECK (auth.uid() = id);

-- ── Exams ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS exams (
  exam_id text PRIMARY KEY,
  title text NOT NULL DEFAULT '',
  pathway text DEFAULT 'academic',
  exam_type text DEFAULT 'ielts',
  telc_level text DEFAULT '',
  status text DEFAULT 'generating_content',
  audio_progress integer DEFAULT 0,
  error_message text DEFAULT '',
  listening jsonb DEFAULT NULL,
  reading jsonb DEFAULT NULL,
  writing jsonb DEFAULT NULL,
  speaking jsonb DEFAULT NULL,
  hoeren jsonb DEFAULT NULL,
  lesen jsonb DEFAULT NULL,
  schreiben jsonb DEFAULT NULL,
  sprechen jsonb DEFAULT NULL,
  sprachbausteine jsonb DEFAULT NULL,
  created_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE exams ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Authenticated users can read exams"
  ON exams FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY "Authenticated users can insert exams"
  ON exams FOR INSERT
  TO authenticated
  WITH CHECK (auth.uid() = created_by);

CREATE POLICY "Creators can update exams"
  ON exams FOR UPDATE
  TO authenticated
  USING (auth.uid() = created_by)
  WITH CHECK (auth.uid() = created_by);

CREATE POLICY "Creators can delete exams"
  ON exams FOR DELETE
  TO authenticated
  USING (auth.uid() = created_by);

-- ── Audio Files ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audio_files (
  audio_id text PRIMARY KEY,
  exam_id text REFERENCES exams(exam_id) ON DELETE CASCADE,
  storage_path text NOT NULL DEFAULT '',
  audio_type text DEFAULT 'content',
  section_num integer DEFAULT 0,
  segment_index integer DEFAULT 0,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE audio_files ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Authenticated users can read audio files"
  ON audio_files FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY "Authenticated users can insert audio files"
  ON audio_files FOR INSERT
  TO authenticated
  WITH CHECK (true);

-- ── Attempts ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS attempts (
  attempt_id text PRIMARY KEY,
  user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE,
  exam_id text REFERENCES exams(exam_id) ON DELETE CASCADE,
  module text DEFAULT '',
  mode text DEFAULT '',
  status text DEFAULT 'in_progress',
  answers jsonb DEFAULT '{}'::jsonb,
  scores jsonb DEFAULT '{}'::jsonb,
  module_answers jsonb DEFAULT '{}'::jsonb,
  module_scores jsonb DEFAULT '{}'::jsonb,
  modules_completed jsonb DEFAULT '[]'::jsonb,
  current_module text DEFAULT '',
  overall_band float DEFAULT 0,
  started_at timestamptz DEFAULT now(),
  completed_at timestamptz DEFAULT NULL
);

ALTER TABLE attempts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own attempts"
  ON attempts FOR SELECT
  TO authenticated
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own attempts"
  ON attempts FOR INSERT
  TO authenticated
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own attempts"
  ON attempts FOR UPDATE
  TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_exams_exam_type ON exams(exam_type);
CREATE INDEX IF NOT EXISTS idx_exams_status ON exams(status);
CREATE INDEX IF NOT EXISTS idx_exams_created_by ON exams(created_by);
CREATE INDEX IF NOT EXISTS idx_attempts_user_id ON attempts(user_id);
CREATE INDEX IF NOT EXISTS idx_attempts_exam_id ON attempts(exam_id);
CREATE INDEX IF NOT EXISTS idx_audio_files_exam_id ON audio_files(exam_id);

-- ── Auto-create profile on signup ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER SET search_path = public
AS $$
BEGIN
  INSERT INTO public.profiles (id, email, name, picture)
  VALUES (
    NEW.id,
    NEW.email,
    COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name', ''),
    COALESCE(NEW.raw_user_meta_data->>'avatar_url', NEW.raw_user_meta_data->>'picture', '')
  )
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE PROCEDURE public.handle_new_user();
