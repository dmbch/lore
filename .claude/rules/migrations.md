---
paths:
  - "src/lore/repositories/*/migrations/*.sql"
  - "src/lore/repositories/*/bootstrap.py"
---

# Migration Rules

@docs/architecture.md (Repositories) is the canonical reference. SQLite and
PostgreSQL keep separate migration sets by design: their vector and full-text
models differ enough that a shared dialect layer would cost more than the
duplication it saves.

## Edit both sets

Every structural change lands twice:

- `src/lore/repositories/_sqlite/migrations/`
- `src/lore/repositories/_postgres/migrations/`

Same tables, same columns, same logical types, same primary keys, same indexes,
same unique constraints, same foreign keys. Files apply in lexicographic order
and are tracked in the `_system` table, so a change is a new numbered file in
both sets, never an edit to a migration that may already have run.

## The drift guard is a backstop, not a safety net

`tests/repositories/test_drift.py` asserts structural equivalence between the
two schemas. Two gaps mean it cannot be the only check:

- **Vector storage is excluded.** Virtual table versus typed column is an
  implementation difference by design, so the guard does not compare it. A
  change to vector columns is verified by hand, in both sets.
- **It needs a PostgreSQL server.** CI always has one. Locally the DSN comes
  from `LORE_TEST_POSTGRES_DSN` or a testcontainer, and with neither the guard
  skips. A green local `mise run check` on a machine without Docker says
  nothing about drift.

## Parameterized SQL

Templates carry named placeholders substituted at apply time: `{embedding_dim}`
for vector dimensions, `{fulltext_config}` for the Postgres `regconfig` or the
SQLite FTS5 tokenize spec. Injection is closed off at the settings layer, where
`fulltext_config` is validated against a strict identifier regex and
`embedding_dim` is a strict int. A new placeholder needs a validated settings
field before a template may use it.

## Embedding precision

float32 in both backends. No `HALFVEC`, no float16: precision loss in the
vector space degrades retrieval silently, with no error signal.
