# Lore

**As far as we know.**

## The Imperatives

Heinz von Foerster's two principles govern this project:

- **Ethical:** "Act always so as to increase the number of choices."
- **Aesthetic:** "If you desire to see, learn how to act."

## References

@IDEA.md is the canonical spec. Edits require explicit programmer approval.

@docs/architecture.md — layers, bootstrap, concurrency. `docs/logic.md` — the math (loaded on demand when touching `src/lore/math/`).

@.claude/rules/math.md

## The Centaur

The programmer provides judgment. Claude provides reasoning. Never autonomous.

The programmer must explain every committed line. When using unfamiliar patterns, explain briefly in context. Don't lecture — illuminate.

## Workflow

brainstorm → `/plan` → `/build` (TDD) → `/review`. Claude does not auto-advance. The programmer controls transitions.

Each task is a self-contained chunk. Between chunks: review, decide, commit, reset context. Always consult @PLAN.md at the start of a session. Update chunk statuses as work progresses.

Three passes: work → right/secure → fast/cheap. Never skip ahead.

## Verification

All three must pass before committing. They are independent — run in parallel.

```bash
uv run pytest                                        # tests + coverage (fail_under in pyproject.toml)
uv run pyright                                       # strict type checking, zero errors
uv run ruff check . && uv run ruff format --check .  # lint + format, read-only
```

Conventional Commits, scope optional. Types: the canonical set (`build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, `test`), enforced by `cz check` (see `[tool.commitizen]`). Enable the local hook once: `git config core.hooksPath .githooks`.

## Code style

All function parameters after `self`/`cls` are keyword-only. Two exceptions: single-parameter pure functions where the name carries no information at the call site, and library-imposed signatures (Pydantic validators, FastMCP routes, structlog processors, asyncio callbacks).

Additional carve-outs:
- The first positional argument may stay positional when a keyword would only echo what the verb in the function or method name already says — e.g. `embed(text)`, `find_by_id(id)`, `find_by_hypotheses(hypothesis_ids)`, `Recorder._corroborate(self, corroborates, *, contradicts)`. Subsequent parameters stay keyword-only regardless (e.g. `embed(text, *, task_type_key=...)`).
- Math binary operators in `lore.math.conflict` and private fusion helpers (`compute_projected_distance(a, b)`, `_acbf_pair(a, b)`, etc.) stay positional — `a, b` carries no information.

## Critical Thinking

- Three ways it could fail.
- Steel-man the alternative.
- Does this increase or decrease future choices?

## The Boy Scout Rule

Spot stale comments, imprecise types, missing edge cases, unclear names. During RED and GREEN, note them — don't fix inline. The REFACTOR step is where small, focused improvements land. Anything too large for REFACTOR goes in @TODO.md.

**Never cheat.** The suite catches real problems; passing it isn't the goal, correctness is, and the suite is the evidence. Never silence a checker to bury a real finding — a true type error, an untested branch, a lint rule aimed at an actual smell. Hard to type or hard to test is a design signal: fix the design. Banned outright, no exceptions: `Any` to dodge the type system, weakened or deleted assertions, stubs left to green a test — these fake the artifact, not just the checker.

A suppression (`# noqa: CODE`, `# pyright: ignore[rule]`, `# pragma: no cover`) is honest in one case only: **the checker is wrong for this line** — a false positive, a library-imposed signature, or notation correct by design. Then suppress at the narrowest scope, with the specific code and a reason — `# noqa: FBT003 — sqlite3 enable_load_extension is positional-only`. Never a bare `# noqa`; a reason-less suppression is a cheat, a false reason worse. Prefer a documented line-level suppression to a `per-file-ignores` entry — local, precise, self-explaining. Reserve config-level `per-file-ignores` for a property that genuinely spans a file or package (canonical unicode across `lore.math`), never as a blunter stand-in for a line you'd rather not annotate.

Carve-outs that clear this bar: the `__main__` guard's single `# pragma: no cover` (process entry point, untestable without a subprocess; `amain()` fully covered); `RUF002`/`RUF003` across `lore.math` (canonical notation); `FBT003` on sqlite extension-loading (positional-only API).
