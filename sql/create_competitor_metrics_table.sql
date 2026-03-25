-- Competitor metrics for longitudinal trend tracking
-- Stores weekly structured metrics per competitor for trend analysis.

CREATE TABLE IF NOT EXISTS competitor_metrics (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agency_name TEXT NOT NULL,
    competitor_name TEXT NOT NULL,
    year INT NOT NULL,
    week INT NOT NULL,
    publishing_frequency TEXT,           -- e.g. "3 posts/week", "daily"
    primary_topics JSONB DEFAULT '[]',   -- text array of topic strings
    positioning_keywords JSONB DEFAULT '[]', -- text array of keyword strings
    format_mix TEXT,                      -- e.g. "60% text, 30% video, 10% carousel"
    activity_level TEXT,                  -- HIGH / MEDIUM / LOW / INACTIVE
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (agency_name, competitor_name, year, week)
);

-- Index for fast trend queries
CREATE INDEX IF NOT EXISTS idx_competitor_metrics_agency
    ON competitor_metrics (agency_name, competitor_name, year DESC, week DESC);

-- Enable RLS
ALTER TABLE competitor_metrics ENABLE ROW LEVEL SECURITY;

-- Allow service role full access
CREATE POLICY "Service role full access"
    ON competitor_metrics
    FOR ALL
    USING (true)
    WITH CHECK (true);
