"""The shipped examples must keep telling the truth.

Green manifests have to pass; every file under examples/broken/ has to fail on the
check its comment names. Without this, examples rot into documentation that lies.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent_registry.gate import gate_passed, load_catalogs, run_gate

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
CATALOGS = load_catalogs(ROOT / "catalogs")


def _gate(manifest_path: Path, eval_path: Path):
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    evals = json.loads(eval_path.read_text(encoding="utf-8"))
    return run_gate(manifest, evals, CATALOGS, conflicts=[])


@pytest.mark.parametrize(
    ("manifest", "evals"),
    [
        ("claim-triage.manifest.yaml", "claim-triage.eval.json"),
        ("support-triage.manifest.yaml", "support-triage.eval.json"),
        ("kb-rag.manifest.yaml", "kb-rag.eval.json"),
    ],
)
def test_green_examples_pass(manifest, evals):
    checks = _gate(EXAMPLES / manifest, EXAMPLES / evals)

    failures = [c for c in checks if not c.passed]
    assert not failures, failures
    assert gate_passed(checks)


@pytest.mark.parametrize(
    ("manifest", "evals", "check_name"),
    [
        ("unknown-tool.manifest.yaml", "broken/unknown-tool.eval.json", "tools"),
        ("unregistered-model.manifest.yaml", "broken/unregistered-model.eval.json", "models"),
        ("scope-escape.manifest.yaml", "broken/scope-escape.eval.json", "data_scopes"),
    ],
)
def test_broken_examples_fail_on_the_named_check(manifest, evals, check_name):
    checks = _gate(EXAMPLES / "broken" / manifest, EXAMPLES / evals)

    failed = {c.name for c in checks if not c.passed}
    assert failed == {check_name}, f"beklenen {check_name}, düşen {failed}"


def test_low_eval_example_fails_the_eval_check():
    checks = _gate(EXAMPLES / "support-triage.manifest.yaml", EXAMPLES / "broken/low-eval.json")

    failed = {c.name for c in checks if not c.passed}
    assert "eval" in failed
