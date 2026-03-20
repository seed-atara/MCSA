-- Run this in Supabase SQL Editor (Dashboard > SQL Editor > New query)
CREATE TABLE IF NOT EXISTS content_calendar (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    agency_name text NOT NULL,
    week_start date NOT NULL,
    items jsonb NOT NULL DEFAULT '[]'::jsonb,
    report text DEFAULT '',
    status text DEFAULT 'draft',
    generated_at timestamptz DEFAULT now(),
    UNIQUE(agency_name, week_start)
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_content_calendar_agency ON content_calendar(agency_name);
CREATE INDEX IF NOT EXISTS idx_content_calendar_week ON content_calendar(agency_name, week_start);

-- Enable RLS but allow service key full access
ALTER TABLE content_calendar ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service key full access" ON content_calendar
    FOR ALL
    USING (true)
    WITH CHECK (true);
