# Agent Registry

**A small service that makes "which agent is allowed to run, and with what access?" an answerable question.**

Agents multiply faster than anyone can track them: copies of the same agent, versions with no owner,
no record of what data a given version could reach. A wiki page or a spreadsheet does not fix this,
because a list does not bind the runtime. This project treats the registry as the **single source of
truth the runtime is forced to obey** — not a catalogue that happens to sit next to production.

> Agent sprawl is not a documentation problem. It is a runtime problem.

## The three contracts

```
manifest   what the agent is, what it may reach, how it is evaluated   (immutable, signed)
runtime    runs only a manifest resolved from the registry, signature verified locally
registry   immutable versions + promotion records + append-only audit trail
```

The point is the third word of each line: **there is no escape hatch.** A manifest is never edited in
place (new version = new row), a runtime never trusts an unsigned answer, and the audit table rejects
`UPDATE` and `DELETE` at the database level.

## What it does today

| Works | Deliberately out of scope |
|---|---|
| Postgres schema, 6 tables, append-only audit (trigger-enforced) | Storing model weights |
| Manifest schema + validation, immutable `manifest_hash` | Running evaluations (results only) |
| Lifecycle `draft → validated → active → deprecated → retired` (+ `blocked`) | Prompt/artifact storage |
| HMAC-SHA256 signing; narrow interface for a Cosign backend | Web UI |
| REST API, Bearer keys with roles and tenant scope | Multi-region deployment |
| Server-side CI gate (7 checks), re-run on promotion | Cross-tenant sharing |
| Runtime SDK: signature + hash + expiry verified locally, TTL cache, offline fallback | Load testing |
| 54 tests, ruff clean | Managed hosting |

## Try it in one minute

```bash
docker compose up -d db            # Postgres 16 on 127.0.0.1:5433
uv sync --extra dev
uv run python examples/demo.py     # the whole loop against the real database
```

Real output of that script:

```
▸ 1. Ajan kaydı                       ✓ ajan kaydedildi
▸ 2. Sürüm 2.3.188                    ✓ manifest_hash sha256:80df76b18e594871a…
▸ 3. Eval sonucu + doğrulama          ✓ eval geçti · success_rate 0.947
▸ 4. Terfi (kapı sunucuda yeniden)    ✓ prod ortamında aktif
▸ 5. Runtime resolve                  ✓ version 2.3.188 · imza hmac-sha256:91d0aeb4…
▸ 6. Runtime SDK doğrulaması          ✓ SDK imzayı doğruladı · claim-triage@2.3.188
                                      ✓ kurcalanmış manifest reddedildi
▸ 7. Denetim izi                      · version.promote / .validate / .create / agent.register
```

Step 6 is the one that matters. The runtime is handed a manifest whose `data_scopes` were changed
after signing; it recomputes the hash, sees the mismatch and refuses to run the agent. A signature
check alone would pass — it covers `manifest_hash`, so the content has to be re-hashed locally or a
tampered manifest still verifies.

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

API docs: <http://127.0.0.1:8100/docs>

### Endpoints

```
POST /v1/agents                               register an agent
POST /v1/agents/{tenant}/{name}/versions      new immutable version
POST /v1/agents/{tenant}/{name}/versions/{v}:validate
POST /v1/agents/{tenant}/{name}/versions/{v}:promote
POST /v1/agents/{tenant}/{name}/versions/{v}:deprecate
POST /v1/agents/{tenant}/{name}/versions/{v}:retire
GET  /v1/agents/{tenant}/{name}/resolve?env=  signed resolve for the runtime
GET  /v1/agents?tenant=&owner=                inventory
GET  /v1/audit?object_type=&object_id=        audit trail
GET  /healthz
```

Every endpoint except `/healthz` requires `Authorization: Bearer <key>`; the actor and its roles come
from the key, never from a client-supplied header.

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
agent.manifest_obj.data_scopes     # what this version may actually reach
```

If the registry is unreachable the SDK falls back to the last verified copy on disk — but only while
its `expires_at` has not passed. Expired or unsigned: refuse to start.

## The gate

Seven checks run locally in CI and again **server-side on promotion**; a client's claim that the gate
passed is never trusted:

```
1  manifest schema valid
2  eval thresholds met, no regression against the active version
3  data_scopes ⊆ the owning team's allow-list
4  every tool is registered in the tool catalogue
5  every model in model_policy is in the model catalogue
6  owning team defined, on-call known
7  no scope conflict with the agent's currently active version
```

## Design decisions

- **The registry is derived from Git, never edited by hand.** Every change arrives through a PR with
  a signed manifest; `manifest_hash = sha256(canonical manifest)`. Allow manual edits and the audit
  trail stops meaning anything.
- **Versions are immutable.** No `UPDATE` on `manifest_json`; a change is a new row. `retired` never
  comes back — rollback is a new version.
- **Promotion is transactional.** Gate re-check, state change, deployment row and audit event commit
  together or not at all.
- **Tenant isolation lives in the service layer**, not only in the query: `resolve` will not cross a
  tenant boundary even for a global key.
- **MLflow was not forced into this role.** It has no concept of agent identity, tool scope, permission
  sets or retirement semantics. The registry borrows Postgres, object storage and cosign instead.
- **HMAC by default, Cosign as a backend.** The signing interface is deliberately narrow (`sign` /
  `verify`) so a Sigstore backend drops in without touching callers.

## Roadmap

| # | Scope | Status |
|---|---|---|
| M0 | Schema, manifest, signing, lifecycle, API, gate CLI, tests, docker | done |
| M1 | Eval artifacts in object storage + regression comparison | next |
| M2 | Tool/model catalogues moved into the registry + impact analysis | planned |
| M3 | Cosign backend + runtime SDK | SDK done, Cosign pending |
| M4 | Metrics (coverage, blocked calls, stale versions), Prometheus | planned |

## Layout

```
src/agent_registry/
  manifest.py   schema, canonical JSON, sha256
  signing.py    HMAC-SHA256 sign/verify
  store.py      Postgres access (psycopg3, parameterised SQL)
  service.py    lifecycle rules, gate re-check, signed resolve
  api.py        FastAPI routes, auth, error mapping
  runtime.py    client SDK: verify locally, TTL cache, offline fallback
  gate.py       7 checks over catalogues
tests/          54 tests
examples/       demo.py, example manifest + eval result
catalogs/       tools.yaml, models.yaml, teams.yaml
```

## Status

Early (v0.1). The core loop works end to end and is covered by tests; the API and schema may still
change between minor versions. Issues and design arguments are welcome — especially about where a
registry should *refuse* to be helpful.

## License

MIT (to be added with the first public release).
