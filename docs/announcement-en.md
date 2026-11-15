# Announcement — English

## Show HN / X / dev.to intro

**Agent sprawl is not a documentation problem. It is a runtime problem.**

Six months into shipping agents, nobody can answer "which version is in production, what can it
reach, and who owns it?" The answers live in a wiki page. The wiki page does not stop anything.

So I built a registry the runtime is forced to obey:

- **Immutable manifests.** A change is a new version with a new hash; the old one never mutates. A
  Postgres trigger rejects any UPDATE that touches `manifest_json`.
- **Signed resolution.** The agent verifies the signature *and* rehashes the manifest locally. Change
  `data_scopes` after signing and the agent refuses to start.
- **Append-only audit.** Triggers reject UPDATE and DELETE on the audit table.
- **A gate of eight checks** that runs in CI and again server-side at promotion, because the CI
  machine is not one you control.

Repo: https://github.com/ilkerhalil/agent-registry

126 tests, 93% line coverage, Postgres 16 in CI, cosign installed so the signature tests run instead
of skipping.

It is not a model registry, not an observability tool, and not a config store. It is the narrow thing
between them: identity, permission, approval, proof.

## One-liners for replies

- "Why not MLflow?" — It tracks models and metrics. It does not know what an agent may touch or who
  to page when it breaks.
- "Why not a Kubernetes ConfigMap?" — Config distribution without a gate, a signature or an audit
  trail. Anyone with write access ships anything.
- "Why not just document it?" — A document does not bind the runtime.
- "Does it run evals?" — No. It requires the evidence to be complete, consistent, immutable and
  signed if you enable signing. Running evaluations is a different product.
- "What's missing?" — No load test; S3 store tested against a fake client; HMAC is the default
  signing backend, Cosign is supported and tested but opt-in.
