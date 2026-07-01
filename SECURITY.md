# Security Policy

## Supported Versions

Lore ships as a single rolling container image, `ghcr.io/dmbch/lore`, released by
semantic-release on every qualifying merge to `main`. There is no LTS track and no
backport branches: security fixes land in the next release and are published to the
`latest` tag and the matching `MAJOR.MINOR` tag.

| Artifact                                       | Supported                     |
| ---------------------------------------------- | ----------------------------- |
| Latest release (`latest` / newest `MAJOR.MINOR`) | Yes                         |
| Any older tag                                  | No, upgrade to the latest     |

Lore is pre-1.0. The public surface (the MCP `consult` tool, config, the migration
path) may change between minor versions; read the release notes before upgrading.
Pin by digest in production and watch releases so you can move forward when a fix ships.

## Reporting a Vulnerability

Report privately through GitHub. Do not open a public issue, pull request, or
discussion for a suspected vulnerability.

1. Open the repository's **Security** tab and choose **Report a vulnerability**, or go
   straight to
   [github.com/dmbch/lore/security/advisories/new](https://github.com/dmbch/lore/security/advisories/new).
2. Private Vulnerability Reporting is enabled, so the report and every follow-up stay in
   a private advisory thread visible only to you and the maintainers.

Include as much as you can:

- affected version or image digest, and the deployment topology (stdio, HTTP with
  in-process OIDC, or HTTP behind an authenticating proxy);
- what the issue is and its impact;
- reproduction steps or a proof of concept;
- any fix or mitigation you have in mind.

### What to expect

This is a small open-source project. These are best-effort targets, not a contract.

| Stage                                              | Target                              |
| -------------------------------------------------- | ----------------------------------- |
| Acknowledge receipt                                | within 3 business days              |
| Initial assessment (accepted or declined, severity) | within 7 business days            |
| Fix or mitigation for accepted reports             | tracked in the advisory, by severity |
| Coordinated public disclosure                      | after a fix ships, within 90 days   |

Accepted reports are handled under coordinated disclosure: the fix is prepared
privately, released, then the advisory is published, with a CVE requested through GHSA
where warranted. Declined reports get a reason, not silence. Reporters are credited in
the published advisory unless you ask to stay anonymous.

### Safe harbor

Good-faith research that follows this policy will not draw legal action from us. Test
only against a deployment you own, avoid privacy violations and service degradation for
others, and give us reasonable time to fix before disclosing. Do not test against
infrastructure you do not operate.

## Scope and Security Model

Lore is infrastructure you self-host. By design, several security properties belong to
the operator or to an upstream component. Knowing the boundary keeps reports actionable.

In scope (Lore's responsibility):

- the `consult` path: input validation, the SQL it builds, the trust and fusion math,
  transaction integrity;
- not leaking secrets: `OIDC_URL` credentials are stripped before telemetry starts, LLM
  API keys pass from the environment to LiteLLM without Lore reading, storing, or logging
  them, and client-facing errors are scrubbed to a correlation ID;
- the published image and its build provenance (below);
- honoring `[auth] required` as a startup fail-fast when OIDC is absent, and rejecting
  IdP-claimed identities in the reserved `_*` namespace.

Delegated by design (out of scope for Lore, in scope for your deployment):

- **Authentication and Sybil resistance.** Lore trusts the `sub` claim from your IdP.
  Identity assurance, MFA, and one-human-one-account are the IdP's job. Running HTTP with
  no auth anywhere is a misconfiguration, not a Lore flaw; set `[auth] required = true`
  and startup refuses it.
- **TLS, rate limiting, and DoS protection.** These live at your edge proxy, the right
  vantage point. Lore emits no per-oracle traffic counters and returns opaque 5xx under
  backpressure on purpose.
- **Your OTel collector, IdP, reverse proxy, and database.** Their configuration and
  their CVEs are yours to manage. Redacting `oracle_id` from telemetry, for example, is a
  collector-side attribute processor, not an application setting.
- **Secrets provisioning.** Lore reads DSNs and keys from the environment. Keeping them
  out of logs and image layers is on your orchestration.

A report that reduces to "Lore does not authenticate on its own" or "an operator can
misconfigure the proxy" describes the documented model. A report where Lore leaks a
credential, builds injectable SQL, corrupts the ledger, or crashes on adversarial
`consult` input is squarely in scope.

## Supply Chain

Every published image carries verifiable provenance:

- SLSA build provenance (`provenance: mode=max`) and an SBOM, attached to the image in
  GHCR;
- a signed build-provenance attestation (`actions/attest-build-provenance`) binding the
  image digest to the workflow, commit, and runner that produced it.

Verify before you run:

```
gh attestation verify oci://ghcr.io/dmbch/lore:<tag> --owner dmbch
```

Images are multi-arch (`linux/amd64`, `linux/arm64`) and built only from tagged commits,
after the end-to-end and container smoke gates pass. Pin by digest in production.
