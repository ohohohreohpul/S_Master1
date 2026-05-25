-- ============================================================
-- IELTS / TELC Exam Platform — Supabase schema
-- Run once in the Supabase SQL editor
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    user_id            TEXT PRIMARY KEY,
    email              TEXT UNIQUE NOT NULL,
    name               TEXT,
    picture            TEXT,
    stripe_customer_id TEXT,
    subscription       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id            BIGSERIAL PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    session_token TEXT UNIQUE NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON user_sessions(session_token);

CREATE TABLE IF NOT EXISTS exams (
    exam_id        TEXT PRIMARY KEY,
    title          TEXT NOT NULL DEFAULT '',
    pathway        TEXT NOT NULL DEFAULT 'academic',
    exam_type      TEXT NOT NULL DEFAULT 'ielts',
    telc_level     TEXT,
    status         TEXT NOT NULL DEFAULT 'pending_audio',
    audio_progress INTEGER NOT NULL DEFAULT 0,
    error_message  TEXT,
    created_by     TEXT,
    -- IELTS modules
    listening      JSONB NOT NULL DEFAULT '{}'::jsonb,
    reading        JSONB NOT NULL DEFAULT '{}'::jsonb,
    writing        JSONB NOT NULL DEFAULT '{}'::jsonb,
    speaking       JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- TELC modules (German keys, used for exam_type = 'telc')
    hoeren         JSONB NOT NULL DEFAULT '{}'::jsonb,
    lesen          JSONB NOT NULL DEFAULT '{}'::jsonb,
    schreiben      JSONB NOT NULL DEFAULT '{}'::jsonb,
    sprechen       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_exams_type   ON exams(exam_type);
CREATE INDEX IF NOT EXISTS idx_exams_status ON exams(status);

-- Audio files: actual bytes live in Supabase Storage; this table holds metadata + path
CREATE TABLE IF NOT EXISTS audio_files (
    audio_id      TEXT PRIMARY KEY,
    exam_id       TEXT NOT NULL REFERENCES exams(exam_id) ON DELETE CASCADE,
    section_num   INTEGER,
    segment_index INTEGER,
    audio_type    TEXT NOT NULL DEFAULT 'content',
    storage_path  TEXT NOT NULL,
    format        TEXT NOT NULL DEFAULT 'mp3',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audio_exam ON audio_files(exam_id);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id        TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users(user_id),
    exam_id           TEXT NOT NULL REFERENCES exams(exam_id),
    module            TEXT NOT NULL,
    mode              TEXT,
    status            TEXT NOT NULL DEFAULT 'in_progress',
    answers           JSONB NOT NULL DEFAULT '{}'::jsonb,
    scores            JSONB,
    module_answers    JSONB NOT NULL DEFAULT '{}'::jsonb,
    module_scores     JSONB NOT NULL DEFAULT '{}'::jsonb,
    modules_completed JSONB NOT NULL DEFAULT '[]'::jsonb,
    current_module    TEXT,
    overall_band      FLOAT,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_attempts_user   ON attempts(user_id);
CREATE INDEX IF NOT EXISTS idx_attempts_exam   ON attempts(exam_id);
CREATE INDEX IF NOT EXISTS idx_attempts_status ON attempts(status);

-- ============================================================
-- Supabase Storage: create a public bucket named "audio-files"
-- Run in Storage settings or via the dashboard.
-- ============================================================
-- insert into storage.buckets (id, name, public)
-- values ('audio-files', 'audio-files', true)
-- on conflict do nothing;
