-- Create schema: hypotheses, requests, attestations, vec_hypotheses, fts_hypotheses.

CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT NOT NULL PRIMARY KEY,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

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
    timestamp INTEGER NOT NULL,
    question TEXT,
    context TEXT,
    hypothesis TEXT,
    reasoning TEXT,
    confidence REAL
);

-- Belt-and-braces: CHECK constraints enforce TrustSignal numeric bounds at the
-- storage layer alongside the application-side Pydantic constructor.
-- ``n_oracle_prior`` is a write-time snapshot of distinct prior attesters,
-- computed by the Recorder against the transaction snapshot and persisted
-- so trust scans read the column instead of recomputing it.
CREATE TABLE IF NOT EXISTS attestations (
    id TEXT NOT NULL PRIMARY KEY,
    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id),
    oracle_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL REFERENCES requests(id),
    timestamp INTEGER NOT NULL,
    t_oracle REAL NOT NULL CHECK (t_oracle BETWEEN 0.0 AND 1.0),
    c_oracle_raw REAL NOT NULL CHECK (c_oracle_raw BETWEEN -1.0 AND 1.0),
    c_oracle_discounted REAL NOT NULL CHECK (c_oracle_discounted BETWEEN -1.0 AND 1.0),
    c_herd REAL NOT NULL CHECK (c_herd BETWEEN -1.0 AND 1.0),
    n_oracle_prior INTEGER NOT NULL CHECK (n_oracle_prior >= 0)
);

CREATE INDEX IF NOT EXISTS idx_attestations_oracle_timestamp
    ON attestations(oracle_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_attestations_hypothesis_timestamp
    ON attestations(hypothesis_id, timestamp);

-- Vector table: sqlite-vec virtual table, requires extension loaded.
-- Cosine distance for semantic similarity.
-- {embedding_dim} is injected from config at migration time.
CREATE VIRTUAL TABLE IF NOT EXISTS vec_hypotheses USING vec0(
    embedding float[{embedding_dim}] distance_metric=cosine,
    +hypothesis_id text
);

-- FTS5 index for full-text search (Lane 2: authority).
-- Standalone virtual table — hypotheses uses TEXT PK, not rowid,
-- so external content tables are not viable.
-- hypothesis_id is UNINDEXED: stored for joins, not searched.
-- {fulltext_config} is the operator-chosen FTS5 tokenize spec.
-- Bound at virtual-table creation; mismatch on change is refused by check_health.
CREATE VIRTUAL TABLE IF NOT EXISTS fts_hypotheses USING fts5(
    content,
    hypothesis_id UNINDEXED,
    tokenize='{fulltext_config}'
);
