# Releasing Lore

Releases are cut by a single gated pipeline (`.github/workflows/release.yml`),
triggered on every push to `main`. Versions are computed from commit messages,
never chosen by hand, and **no image is published unless the full suite, the
e2e tests, and the container smoke test all pass against the exact commit being
released.**

## How a release happens

1. **Conventional Commits drive the version.** `python-semantic-release` reads
   the commits since the last tag: a `feat:` bumps the minor, a `fix:` the
   patch. On 0.x a `feat:` stays in 0.x (`major_on_zero = false`). `ci:`,
   `docs:`, `chore:`, `test:`, `refactor:`, `style:` don't trigger a release.
2. **`plan` decides whether a release is due** — purely by comparing the
   computed next version against the last released one. No side effects.
3. **`e2e` + `smoke` gate the tag.** Both must be green before anything is
   tagged: e2e runs against real Gemini + SQLite; smoke builds the image and
   proves it boots and serves `/health` + `/ready`.
4. **`release` tags the merged HEAD** — `git tag vX.Y.Z && git push`. No commit
   to `main`.
5. **`publish` builds and ships** — a multi-arch (amd64 + arm64) image to GHCR,
   provenance + SBOM attestation, and a GitHub Release with auto-generated
   notes, the `docker pull` command, and the content digest.

The gate is structural: the tag is cut *only* after the suite, e2e, and smoke
pass — never over a red build. If `publish` later fails (a registry hiccup, a
multi-arch build break), delete the tag and re-run; tags are unprotected and
carry no history.

## Why the pipeline is tag-only

`main` is a protected branch (the `Main Protection` ruleset: pull requests,
linear history, required status checks). CI cannot push *commits* to it, so the
pipeline never tries — it computes the version and pushes a **tag** at the
already-merged `main` HEAD. Tags are unprotected, so the built-in
`GITHUB_TOKEN` is enough:

- **No PAT.** A single pipeline run needs nothing to trigger across runs, so the
  automatic `GITHUB_TOKEN` suffices — no personal access token, no annual expiry
  to rotate.
- **No commit to `main`.** A version-bump commit would violate the ruleset, and
  is unnecessary: the git tag is the single source of truth for the version, and
  the package itself carries none. The version surfaces to operators as the
  image tag and the OCI `org.opencontainers.image.version` label — what `docker
  inspect` and the registry show.

## Merge method

Merge PRs with **rebase-and-merge**. It replays each commit — already validated
by the PR commit-lint gate — onto `main`, so `semantic-release` reads the
individual conventional commits directly. Squash would collapse a PR into one
commit whose subject (the PR title) isn't lint-checked, leaving a hole exactly
where versioning reads.

## First release (one-time)

The first release is seeded to land on exactly `v0.1.0`:

- A one-time `v0.0.0` baseline tag was pushed at the initial commit — a marker,
  no image behind it.
- The first `feat:` to reach `main` therefore computes to `v0.1.0` — a minor
  bump on `0.0.0`.

`semantic-release` increments from there.
