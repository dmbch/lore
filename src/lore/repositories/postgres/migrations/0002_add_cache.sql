-- Add _cache: operational key-value cache (OAuth client registrations,
-- upstream tokens, MCP session state), keyed by (collection, key). Rows are
-- upserted in place; deliberately NOT append-only like the attestations ledger.

CREATE TABLE IF NOT EXISTS _cache (
    collection TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    expires_at BIGINT,
    PRIMARY KEY (collection, key)
);

-- The primary key serves every point lookup; the expiry sweep scans by
-- expires_at. Partial: NULL-expiry rows (client registrations, which
-- persist by design) stay out of the index entirely.
CREATE INDEX IF NOT EXISTS idx_cache_expires_at
    ON _cache (expires_at) WHERE expires_at IS NOT NULL;
