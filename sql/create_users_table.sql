CREATE TABLE IF NOT EXISTS users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slack_user_id TEXT UNIQUE,
    name TEXT NOT NULL,
    email TEXT,
    role TEXT NOT NULL DEFAULT 'user',  -- 'admin', 'md', 'user'
    agency TEXT,                         -- default agency filter for MDs
    preferences JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_slack ON users(slack_user_id);
CREATE INDEX IF NOT EXISTS idx_users_name ON users(name);
