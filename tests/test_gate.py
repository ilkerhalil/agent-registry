from pathlib import Path

from agent_registry.gate import gate_passed, load_catalogs, run_gate

from .conftest import EVAL, MANIFEST

CATALOGS = load_catalogs(Path(__file__).resolve().parents[1] / "catalogs")


def test_full_pass():
    checks = run_gate(MANIFEST, EVAL, CATALOGS, conflicts=[])
    failed = [c for c in checks if not c.passed]
    assert not failed, failed
    assert len(checks) == 8


def test_unknown_tool_fails():
    checks = run_gate(dict(MANIFEST, tools=["claim_read", "ghost_tool"]), EVAL, CATALOGS)
    tool_check = next(c for c in checks if c.name == "tools")
    assert not tool_check.passed


def test_scope_outside_team_fails():
    checks = run_gate(dict(MANIFEST, data_scopes=["claims:read", "salaries:read"]), EVAL, CATALOGS)
    assert not next(c for c in checks if c.name == "data_scopes").passed


def test_low_eval_rate_fails():
    bad = dict(EVAL, success_rate=0.80)
    checks = run_gate(MANIFEST, bad, CATALOGS)
    assert not next(c for c in checks if c.name == "eval").passed


def test_failed_must_pass_suite_fails():
    bad = dict(EVAL, suites={"regression-core": {"passed": False}})
    checks = run_gate(MANIFEST, bad, CATALOGS)
    assert not next(c for c in checks if c.name == "eval").passed


def test_scope_conflict_with_other_team_fails():
    conflicts = [{"name": "other-agent", "owner_team": "platform", "scopes": ["claims:read"]}]
    checks = run_gate(MANIFEST, EVAL, CATALOGS, conflicts=conflicts)
    assert not next(c for c in checks if c.name == "scope_conflict").passed


def test_same_team_overlap_is_not_conflict():
    conflicts = [{"name": "sibling", "owner_team": "hasar-operasyon", "scopes": ["claims:read"]}]
    checks = run_gate(MANIFEST, EVAL, CATALOGS, conflicts=conflicts)
    assert next(c for c in checks if c.name == "scope_conflict").passed


def test_missing_eval_fails_gate():
    checks = run_gate(MANIFEST, None, CATALOGS)
    assert not gate_passed(checks)
