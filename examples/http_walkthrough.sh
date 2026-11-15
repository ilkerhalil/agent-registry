#!/usr/bin/env bash
# The whole lifecycle over plain HTTP, no Python client.
#
#   1) start the server (see README "Running the service")
#   2) PUB_KEY=pub-1 RT_KEY=rt-1 bash examples/http_walkthrough.sh
#
# Requires: curl, jq.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${BASE:-http://127.0.0.1:8100}"
PUB_KEY="${PUB_KEY:?set PUB_KEY to a publisher API key}"
RT_KEY="${RT_KEY:?set RT_KEY to a runtime API key}"
TENANT="${TENANT:-acme}"
AGENT="kb-rag"
VERSION="0.9.2"

pub() { curl -sS -f -X "$1" "$BASE$2" -H "Authorization: Bearer $PUB_KEY" \
        -H 'Content-Type: application/json' ${3:+-d "$3"}; }

step() { printf '\n▸ %s\n' "$1"; }

step "1. register the agent"
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/v1/agents" \
  -H "Authorization: Bearer $PUB_KEY" -H 'Content-Type: application/json' \
  -d "{\"name\":\"$AGENT\",\"tenant\":\"$TENANT\",\"owner_team\":\"support-ops\",
       \"purpose\":\"Answers from support articles with citations\"}" \
  | sed 's/^/   HTTP /'

step "2. create an immutable version"
uv run python - "$AGENT" "$TENANT" "$VERSION" > /tmp/body_version.json <<'PY'
import json, sys, pathlib, yaml
agent, tenant, version = sys.argv[1:4]
root = pathlib.Path("examples")
manifest = yaml.safe_load((root / "kb-rag.manifest.yaml").read_text())
manifest["version"] = version
print(json.dumps({"manifest": manifest, "git_commit": "example"}))
PY
HASH=$(pub POST "/v1/agents/$TENANT/$AGENT/versions" @/tmp/body_version.json | jq -r .manifest_hash)
echo "   manifest_hash ${HASH:0:30}…"

step "3. validate with the eval result"
pub POST "/v1/agents/$TENANT/$AGENT/versions/$VERSION:validate" \
  "$(cat examples/kb-rag.eval.json)" | jq -r '"   state \(.state)"'

step "4. promote to prod (the gate re-runs server-side)"
pub POST "/v1/agents/$TENANT/$AGENT/versions/$VERSION:promote" '{"env":"prod"}' \
  | jq -r '"   active \(.env)"'

step "5. resolve as the runtime would"
curl -sS "$BASE/v1/agents/$TENANT/$AGENT/resolve?env=prod" \
  -H "Authorization: Bearer $RT_KEY" \
  | jq -r '"   version \(.version)\n   signature \(.signature[0:28])…\n   scopes \(.manifest.data_scopes | join(", "))"'

step "done"
