# LLM Spend

Every live model call is metered spend. The programmer holds the budget.

- **Claude never runs e2e or rate tests.** Not `mise run e2e`, not `mise run
  rate`, not `pytest -m e2e`, not any selection or script that reaches a live
  model. This includes single "quick" probes, verification runs after edits,
  and retries of failed runs.
- **No Gemini usage without explicit programmer approval.** Approval is per
  run, names the exact command, and does not carry over to the next run.
- `GEMINI_API_KEY` is ambient in the shell, so e2e selections run live by
  default. The absence of a key is never the safeguard; this rule is. Keyless
  plumbing checks unset it explicitly: `env -u GEMINI_API_KEY ...`.
- When a task needs a live run, stop and ask, or hand the command to the
  programmer as a `! <command>` line. Waiting is correct; spending is not.
- The settings.json guard catches natural spellings, not all spellings; a
  multi-word quoted selection (`-m "e2e and slow"`) slips past it. The guard
  is a tripwire. This rule is the fence.
