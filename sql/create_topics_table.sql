-- Run this in Supabase SQL Editor (Dashboard > SQL Editor > New query)
CREATE TABLE IF NOT EXISTS topics (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    agency_name text NOT NULL,
    topic text NOT NULL,
    category text DEFAULT '',
    momentum text DEFAULT 'stable',
    mention_count integer DEFAULT 0,
    confidence text DEFAULT 'MEDIUM',
    relevance text DEFAULT '',
    sources jsonb DEFAULT '[]'::jsonb,
    first_seen_at timestamptz DEFAULT now(),
    last_seen_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    UNIQUE(agency_name, topic)
);

-- Index for fast lookups by agency
CREATE INDEX IF NOT EXISTS idx_topics_agency ON topics(agency_name);

-- Index for momentum filtering
CREATE INDEX IF NOT EXISTS idx_topics_momentum ON topics(agency_name, momentum);

-- Enable RLS but allow service key full access
ALTER TABLE topics ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service key full access" ON topics
    FOR ALL
    USING (true)
    WITH CHECK (true);
