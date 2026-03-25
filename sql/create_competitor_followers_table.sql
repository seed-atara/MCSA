-- Run this in Supabase SQL Editor (Dashboard > SQL Editor > New query)
CREATE TABLE IF NOT EXISTS competitor_followers (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    agency_name text NOT NULL,
    competitor_name text NOT NULL,
    linkedin_followers integer,
    linkedin_employees integer,
    instagram_handle text DEFAULT '',
    tiktok_handle text DEFAULT '',
    twitter_handle text DEFAULT '',
    notes text DEFAULT '',
    checked_at timestamptz DEFAULT now()
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_comp_followers_agency ON competitor_followers(agency_name);
CREATE INDEX IF NOT EXISTS idx_comp_followers_comp ON competitor_followers(agency_name, competitor_name);
CREATE INDEX IF NOT EXISTS idx_comp_followers_date ON competitor_followers(checked_at DESC);

-- Enable RLS but allow service key full access
ALTER TABLE competitor_followers ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service key full access" ON competitor_followers
    FOR ALL
    USING (true)
    WITH CHECK (true);
