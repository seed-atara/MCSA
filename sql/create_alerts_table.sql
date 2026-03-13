-- MCSA Phase 2: Alerts table
-- Run this in Supabase SQL Editor to create the alerts table.

CREATE TABLE IF NOT EXISTS alerts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agency_name TEXT NOT NULL,
    alert_type TEXT NOT NULL,       -- 'new_competitor', 'website_redesign', 'hiring_surge', etc.
    severity TEXT NOT NULL,         -- 'high', 'medium', 'low'
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    acknowledged BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_alerts_agency ON alerts(agency_name);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_unacknowledged ON alerts(acknowledged) WHERE acknowledged = false;
