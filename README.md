# Agent Registry

[![CI](https://github.com/ilkerhalil/agent-registry/actions/workflows/ci.yml/badge.svg)](https://github.com/ilkerhalil/agent-registry/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

**A small service that makes "which agent is allowed to run, and with what access?" an answerable question.**

Agents multiply faster than anyone tracks them: copies of the same agent, versions with no owner,
no record of what data a given version could reach. A wiki page does not fix this, because a list
does not bind the runtime.

> Agent sprawl is not a documentation problem. It is a runtime problem.

This registry is the single source of truth the runtime is **forced** to obey: versions are immutable,
promotion is gated, resolution is signed, and the audit trail is append-only at the database level.

## How it fits together

```
   manifest.yaml ──► CI gate (7 checks) ──► registry ──► promote (gate re-runs server-side)
   in your repo                             │  immutable versions, deployments, audit
                                            │
   agent process ◄── verified manifest ◄── resolve ◄── signed response
                     runtime re-hashes      (HMAC or Cosign)
```

There is no escape hatch at any of the three joints:

| Contract | What it means | Why it cannot be bypassed |
|---|---|---|
| **Manifest** | What the agent is, what it may reach, how it is evaluated | Immutable: a change is a new row, never an `UPDATE`; `manifest_hash = sha256(canonical manifest)` |
| **Runtime** | Runs only what the registry resolved | Verifies the signature, re-hashes the manifest locally, refuses expired manifests, stops when the cache TTL runs out |
| **Audit** | Who promoted what, when | Postgres trigger rejects `UPDATE` and `DELETE` on `audit_event` |

## Quickstart

```bash
docker compose up -d db          # Postgres 16 on 127.0.0.1:5433
uv sync --extra dev
uv run python examples/demo.py   # register → version → eval → promote → resolve → audit
```

Real output:

```
▸ 1. Register the agent
  ✓ agent registered

▸ 2. Version 2.3.364 (immutable manifest)
  ✓ manifest_hash sha256:ec1a896218a4f5a76…

▸ 3. Eval result + validation
  ✓ eval passed · success_rate 0.947

▸ 4. Promote (the gate re-runs server-side)
  ✓ active in prod

▸ 5. Runtime resolve — signed manifest
  ✓ version 2.3.364 · signature hmac-sha256:a93bce5793…

▸ 6. Runtime SDK verification
  ✓ SDK verified the signature · claim-triage@2.3.364
  ✓ tampered manifest rejected → manifest hash does not match the hash in the response

▸ 7. Audit trail (append-only)
  · version.promote              version
  · version.validate             version
  · version.create               version
  · agent.register               agent

Done: register → version → eval → promote → signed resolve → audit.
```

Step 6 is the one that matters. The runtime is handed a manifest whose `data_scopes` were changed
after signing. It recomputes the hash, sees the mismatch and refuses to run the agent. A signature
check alone would pass — the signature covers `manifest_hash`, so unless the content is re-hashed
locally, a tampered manifest still verifies.

## Running the service

```bash
docker compose up -d db
uv run agent-registry init-db

REGISTRY_API_KEYS='{"pub-1":{"actor":"ci","roles":["publisher"],"tenants":["acme"]},
                    "rt-1":{"actor":"runtime","roles":["runtime"],"tenants":["acme"]},
                    "sec-1":{"actor":"sec","roles":["security"],"tenants":["*"]}}' \
REGISTRY_SIGNING_KEY='change-me-in-production' \
uv run agent-registry serve --port 8100
```

Interactive API docs: <http://127.0.0.1:8100/docs>

Every endpoint except `/healthz` requires `Authorization: Bearer <key>`. The actor and its roles come
from the key, never from a client-supplied header.

### Endpoints

```
POST /v1/agents                                register an agent
POST /v1/agents/{tenant}/{name}/versions       new immutable version
POST /v1/agents/{tenant}/{name}/versions/{v}:validate
POST /v1/agents/{tenant}/{name}/versions/{v}:promote
POST /v1/agents/{tenant}/{name}/versions/{v}:deprecate
POST /v1/agents/{tenant}/{name}/versions/{v}:retire
GET  /v1/agents/{tenant}/{name}/resolve?env=   signed resolve for the runtime
GET  /v1/agents?tenant=&owner=                 inventory
GET  /v1/audit?object_type=&object_id=         audit trail
GET  /metrics                                  Prometheus text format (security role)
PUT  /v1/catalogs/tools/{name}                 register a tool (publisher/security)
PUT  /v1/catalogs/models/{name}                register a model
PUT  /v1/catalogs/teams/{name}                 team, on-call, allowed scopes
GET  /v1/catalogs/tools|models|teams           read the catalogues
GET  /v1/catalogs/{tools|models}/{name}/impact which active agents use it
GET  /healthz
```

### Runtime side

```python
from agent_registry.runtime import AgentRuntime

runtime = AgentRuntime(
    base_url="http://127.0.0.1:8100",
    api_key="rt-1",
    signing_key=os.environ["REGISTRY_SIGNING_KEY"],
    tenant="acme",
)

agent = runtime.resolve("claim-triage", env="prod")
agent.manifest_obj.data_scopes  # what this version may actually reach
```

If the registry is unreachable, the SDK falls back to the last verified copy on disk — but only until
its `expires_at` passes. Expired, unsigned or tampered: the agent does not start.

## The gate — 7 checks

The same gate runs in your CI and again **server-side on promotion**. A client claiming "the gate
passed" is not trusted.

| # | Check | It stops |
|---|---|---|
| 1 | Manifest schema valid | malformed manifests reaching production |
| 2 | Eval thresholds met, no regression against the active version | "it feels better" promotions |
| 3 | `data_scopes` ⊆ owning team's allow-list | a version quietly widening its access |
| 4 | Every tool is in the tool catalogue | agents calling tools nobody owns |
| 5 | Every model in `model_policy` is in the model catalogue | shadow models |
| 6 | Owning team defined, on-call known | orphaned agents with no one to page |
| 7 | No scope conflict with the active version | two versions fighting over the same scope |

Run it locally, exactly as CI does:

```bash
uv run agent-registry gate --manifest examples/claim-triage.manifest.yaml \
                           --eval-result examples/claim-triage.eval.json
```

```
[PASS] manifest_schema  claim-triage@2.3.1
[PASS] eval             0.947 ≥ 0.920
[PASS] data_scopes      all scopes allowed
[PASS] team             on-call: hasar-nobet
[PASS] tools            all tools registered
[PASS] models           all models registered
[PASS] scope_conflict   no conflict

RESULT: PASS
```

## Signing: HMAC now, Cosign when you need it

Both backends implement the same narrow interface (`sign` / `verify`), so callers do not change.

```bash
# default: shared secret, fine for a single trust domain
REGISTRY_SIGNING_KEY='...' uv run agent-registry serve

# Sigstore: the signer holds the private key, the runtime holds only the public key
cosign generate-key-pair --output-key-prefix ./cosign
SIGNING_BACKEND=cosign COSIGN_KEY_PATH=./cosign.key \
COSIGN_PUB_KEY_PATH=./cosign.pub COSIGN_PASSWORD=... uv run agent-registry serve
```

With Cosign there is no shared secret to leak, and signatures travel with a Sigstore bundle.

## Why not just X?

| Instead of | Because it does not |
|---|---|
| **MLflow model registry** | Model metrics and artifacts, but no agent identity, tool scope, permission sets or retirement semantics |
| **Kubernetes ConfigMap** | Config distribution without an eval gate, signature or audit trail; anyone with write access ships anything |
| **A wiki page or spreadsheet** | A list does not bind the runtime — nothing stops a process from running an unlisted version |
| **Prompt/observability tools** | They record what happened, they do not decide what may run |

The registry borrows proven parts instead of inventing them: Postgres for state, object storage for
eval artifacts, cosign for signatures, Prometheus for metrics.

## Design decisions

- **Derived from Git, never hand-edited.** Changes arrive as PRs with a manifest; `manifest_hash` ties
  a version to its exact content. Allow manual edits and the audit trail stops meaning anything.
- **Versions are immutable.** No `UPDATE` on `manifest_json`; a change is a new row. `retired` never
  comes back — a rollback is a new version.
- **Promotion is transactional.** Gate re-check, state change, deployment row and audit event commit
  together or not at all.
- **Tenant isolation lives in the service layer,** not only in the query: `resolve` will not cross a
  tenant boundary even for a global key.
- **Eval results are content-addressed.** A validation stores `sha256(body)` in the database and the
  body in an artifact store; reading it back re-verifies the hash, so evidence cannot drift.
- **Regression is a gate, not a dashboard.** A new version may not drop more than
  `EVAL_MAX_REGRESSION` (default 0.02) below the active version's success rate.
- **Refusing is a feature.** Every unknown tool, unregistered model or missing owner fails closed.

## Tests

```bash
uv run pytest                                   # 96 tests; 5 skip when cosign is absent
uv run pytest --cov=src/agent_registry          # report; CI fails under 80%
COSIGN_BIN=$(command -v cosign) uv run pytest   # run the cosign tests too
```

96 tests, 92.6% line coverage, CI green on every push. Deliberately not split by marker:

- **Pure unit** — `manifest`, `signing`, `signers`, `states`, `gate`, `artifacts` with a fake S3 client.
- **Integration** — everything else against a real Postgres, schema applied per session, tables
  truncated between tests.
- **Real network** — `test_http_e2e.py` starts uvicorn on a free port and drives the runtime SDK over
  HTTP, covering auth headers, real JSON and the `urllib` path.
- **Concurrency** — `test_concurrency.py` fires simultaneous promotions and proves the partial unique
  indexes leave exactly one active version.

## Layout

```
src/agent_registry/
  manifest.py   schema, canonical JSON, sha256
  signing.py    HMAC-SHA256 sign/verify
  signers.py    signing backends: HMAC, Cosign (Sigstore bundle)
  store.py      Postgres access (psycopg3, parameterised SQL)
  service.py    lifecycle rules, gate re-check, signed resolve
  api.py        FastAPI routes, auth, error mapping
  runtime.py    client SDK: verify locally, TTL cache, offline fallback
  artifacts.py  content-addressed eval artifacts (filesystem + S3)
  gate.py       7 checks over catalogues
  cli.py        serve, init-db, gate, agents
tests/          96 tests (unit, Postgres integration, real HTTP, concurrency)
examples/       demo.py, example manifest + eval result
catalogs/       tools.yaml, models.yaml, teams.yaml
```

## Status

v0.2.0. The API and schema may still change between minor versions. Issues and design arguments are
welcome — especially about where a registry should *refuse* to be helpful.

## License

MIT — see [LICENSE](LICENSE).
