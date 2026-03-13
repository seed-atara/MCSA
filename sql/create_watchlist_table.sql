CREATE TABLE IF NOT EXISTS watchlist (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_name TEXT NOT NULL,
    agency_name TEXT,
    watch_type TEXT NOT NULL,  -- 'competitor', 'keyword', 'agency'
    watch_value TEXT NOT NULL, -- competitor name, keyword, or agency name
    notify_slack BOOLEAN DEFAULT true,
    notify_email BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_watchlist_type ON watchlist(watch_type);
