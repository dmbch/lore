-- Create schema: hypotheses (with pgvector embedding + tsvector FTS),
-- requests, attestations.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS hypotheses (
    id UUID NOT NULL PRIMARY KEY,
    content TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    -- pgvector column — cosine distance via <=> operator.
    -- {embedding_dim} is injected from config at migration time.
    embedding VECTOR({embedding_dim}) NOT NULL,
    -- Generated tsvector for full-text search (Lane 2: authority).
    -- Parallel to embedding — both are derived search representations of content.
    -- Zero application code, always in sync with content.
    -- {fulltext_config} is the operator-chosen Postgres text-search configuration.
    -- Bound at schema creation; mismatch on change is refused by check_health.
    fulltext TSVECTOR GENERATED ALWAYS AS (to_tsvector('{fulltext_config}', content)) STORED
);

-- GIN index for @@ full-text queries.
CREATE INDEX IF NOT EXISTS idx_hypotheses_fulltext ON hypotheses USING GIN (fulltext);

-- HNSW index for cosine-similarity search on the embedding column.
-- Without this, two-lane retrieval's proximity lane is a sequential
-- scan of every hypothesis row. pgvector defaults (m=16, ef_construction=64)
-- are reasonable for the herd sizes Lore targets at initial release.
-- Dialect divergence: sqlite-vec has no index analogue and brute-forces
-- proximity queries — the SQLite migration is intentionally not
-- mirrored. See ``docs/architecture.md`` §Two-lane retrieval.
CREATE INDEX IF NOT EXISTS hypotheses_embedding_hnsw
    ON hypotheses USING hnsw (embedding vector_cosine_ops);

-- Structured provenance log. One row per consult call, keyed by correlation_id.
-- The ``hypothesis`` column stores the raw, pre-Interpreter string the oracle
-- submitted — distinct from the ``hypotheses`` table (atomic, decomposed
-- propositions with embeddings). Content columns are nullable at the storage
-- layer; the at-least-one rule is enforced at the domain-type boundary.
-- Declared before ``attestations`` because of the FK from
-- ``attestations.correlation_id``.
CREATE TABLE IF NOT EXISTS requests (
    id TEXT NOT NULL PRIMARY KEY,
    oracle_id TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    question TEXT,
    context TEXT,
    hypothesis TEXT,
    reasoning TEXT,
    confidence DOUBLE PRECISION
);

-- Belt-and-braces: CHECK constraints enforce TrustSignal numeric bounds at the
-- storage layer alongside the application-side Pydantic constructor.
-- ``n_oracle_prior`` is a write-time snapshot of distinct prior attesters,
-- computed by the Recorder against the transaction snapshot and persisted
-- so trust scans read the column instead of recomputing it.
CREATE TABLE IF NOT EXISTS attestations (
    id UUID NOT NULL PRIMARY KEY,
    hypothesis_id UUID NOT NULL REFERENCES hypotheses(id),
    oracle_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL REFERENCES requests(id),
    timestamp BIGINT NOT NULL,
    t_oracle DOUBLE PRECISION NOT NULL
        CHECK (t_oracle BETWEEN 0.0 AND 1.0),
    c_oracle_raw DOUBLE PRECISION NOT NULL
        CHECK (c_oracle_raw BETWEEN -1.0 AND 1.0),
    c_oracle_discounted DOUBLE PRECISION NOT NULL
        CHECK (c_oracle_discounted BETWEEN -1.0 AND 1.0),
    c_herd DOUBLE PRECISION NOT NULL
        CHECK (c_herd BETWEEN -1.0 AND 1.0),
    n_oracle_prior BIGINT NOT NULL CHECK (n_oracle_prior >= 0)
);

CREATE INDEX IF NOT EXISTS idx_attestations_oracle_timestamp
    ON attestations(oracle_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_attestations_hypothesis_timestamp
    ON attestations(hypothesis_id, timestamp);
