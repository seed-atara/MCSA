-- Run this in Supabase SQL Editor (Dashboard > SQL Editor > New query)
CREATE TABLE IF NOT EXISTS key_people (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    agency_name text NOT NULL,
    name text NOT NULL,
    title text DEFAULT '',
    company text DEFAULT '',
    linkedin_url text DEFAULT '',
    topics jsonb DEFAULT '[]'::jsonb,
    relevance text DEFAULT '',
    recent_activity text DEFAULT '',
    status text DEFAULT 'active',
    tracked_since timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    UNIQUE(agency_name, name)
);

-- Index for fast lookups by agency
CREATE INDEX IF NOT EXISTS idx_key_people_agency ON key_people(agency_name);

-- Index for active people
CREATE INDEX IF NOT EXISTS idx_key_people_active ON key_people(agency_name, status);

-- Enable RLS but allow service key full access
ALTER TABLE key_people ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service key full access" ON key_people
    FOR ALL
    USING (true)
    WITH CHECK (true);
