# Examples

Everything here runs against the real database unless it says otherwise. The `broken/` directory is
deliberate: each manifest fails exactly one gate check, so you can see what each check actually
catches.

| File | What it shows |
|---|---|
| `claim-triage.manifest.yaml` + `.eval.json` | The happy path: three tools, a classifier with an escalation model, a human gate above a threshold |
| `support-triage.manifest.yaml` + `.eval.json` | A second domain: ticket creation, narrower scopes, a stricter human gate |
| `kb-rag.manifest.yaml` + `.eval.json` | Retrieval-augmented answering: embeddings, one read-only scope, a `citation-grounding` suite |
| `broken/unknown-tool.manifest.yaml` | Fails check 4 — the manifest calls a tool nobody registered |
| `broken/unregistered-model.manifest.yaml` | Fails check 5 — a shadow model in `model_policy` |
| `broken/scope-escape.manifest.yaml` | Fails check 3 — `data_scopes` asks for something the team may not have |
| `broken/low-eval.json` | Fails check 2 — success rate below the manifest's own threshold |
| `gate_all.sh` | Runs the gate over all seven and expects **3 pass / 4 fail** |
| `demo.py` | The full lifecycle in-process against Postgres |
| `runtime_offline.py` | The SDK with no registry at all: cache fallback, expiry, tamper rejection |
| `http_walkthrough.sh` | The same lifecycle over plain HTTP with `curl` and `jq` |

## Run them

```bash
docker compose up -d db
uv sync --extra dev

bash examples/gate_all.sh          # 3 pass, 4 fail — both are the point
uv run python examples/demo.py     # needs Postgres
uv run python examples/runtime_offline.py   # no database, no server

# over HTTP: start the server first, then
PUB_KEY=pub-1 RT_KEY=rt-1 bash examples/http_walkthrough.sh
```

Every eval file carries the full evidence set — case counts, dataset hash, harness version,
timestamps — because the gate refuses a bare `success_rate`. Run any of them with
`--require-attestation` and an unsigned result fails on `eval_evidence`.

`tests/test_examples.py` keeps these honest: the green manifests must keep passing and every
`broken/` file must keep failing on the check its comment names. If a gate rule changes, these
examples fail the test suite instead of quietly becoming wrong documentation.
