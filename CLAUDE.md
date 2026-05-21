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

Conventional Commits, no scope. Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`.

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

**Never cheat.** The verification suite exists to catch real problems. Passing it is not the goal — correctness is the goal, and the suite is the evidence. No `type: ignore`, no `noqa`, no `pragma: no cover`, no weakened tests, no `Any` to dodge the type system, no stubs left behind. If something is hard to type or hard to test, that's a design signal — not a reason to suppress the checker. Exception: the `if __name__ == "__main__": main()` guard carries a single `# pragma: no cover` — it is the process entry point, untestable without spawning a subprocess, and `amain()` is fully covered.
