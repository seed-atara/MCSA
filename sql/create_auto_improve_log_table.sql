-- Run this in Supabase SQL Editor
CREATE TABLE IF NOT EXISTS auto_improve_log (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    action_type text NOT NULL DEFAULT 'unknown',
    summary text DEFAULT '',
    channel text DEFAULT '',
    user_name text DEFAULT '',
    plan jsonb DEFAULT '{}'::jsonb,
    status text DEFAULT '',
    message_hash text DEFAULT '',
    created_at timestamptz DEFAULT now()
);

-- Index for dedup lookups
CREATE INDEX IF NOT EXISTS idx_auto_improve_hash ON auto_improve_log(message_hash);

-- Enable RLS
ALTER TABLE auto_improve_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service key full access" ON auto_improve_log FOR ALL USING (true) WITH CHECK (true);
