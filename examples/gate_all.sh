#!/usr/bin/env bash
# Runs the CI gate over every example manifest and prints a one-line verdict each.
# The broken/ manifests are expected to fail — that is the point of the directory.
set -u
cd "$(dirname "$0")/.."

pass=0
fail=0

run() {
  local manifest="$1" eval_file="${2:-}"
  local args=(gate --manifest "$manifest")
  [ -n "$eval_file" ] && args+=(--eval-result "$eval_file")
  local out
  out=$(uv run agent-registry "${args[@]}" 2>&1)
  local rc=$?
  printf '%-52s %s\n' "$manifest" "$(echo "$out" | grep -E 'RESULT:' | sed 's/RESULT: //')"
  if [ $rc -eq 0 ]; then pass=$((pass+1)); else fail=$((fail+1)); fi
}

echo "PASS expected"
run examples/claim-triage.manifest.yaml       examples/claim-triage.eval.json
run examples/support-triage.manifest.yaml     examples/support-triage.eval.json
run examples/kb-rag.manifest.yaml             examples/kb-rag.eval.json

echo
echo "FAIL expected — each one trips a different check"
run examples/broken/unknown-tool.manifest.yaml       examples/broken/unknown-tool.eval.json
run examples/broken/unregistered-model.manifest.yaml examples/broken/unregistered-model.eval.json
run examples/broken/scope-escape.manifest.yaml       examples/broken/scope-escape.eval.json
run examples/support-triage.manifest.yaml            examples/broken/low-eval.json

echo
echo "pass=$pass fail=$fail"
[ $pass -eq 3 ] && [ $fail -eq 4 ]
