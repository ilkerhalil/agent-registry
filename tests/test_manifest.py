import pytest
from pydantic import ValidationError

from agent_registry.manifest import AgentManifest

from .conftest import MANIFEST


def test_valid_manifest_hash_is_stable():
    m1 = AgentManifest.model_validate(MANIFEST)
    m2 = AgentManifest.model_validate(dict(MANIFEST))
    assert m1.sha256() == m2.sha256()
    assert m1.sha256().startswith("sha256:")


def test_hash_changes_with_content():
    m1 = AgentManifest.model_validate(MANIFEST)
    changed = dict(MANIFEST, purpose="Tamamen farklı bir amaç cümlesi")
    assert m1.sha256() != AgentManifest.model_validate(changed).sha256()


def test_bad_agent_name_rejected():
    with pytest.raises(ValidationError):
        AgentManifest.model_validate(dict(MANIFEST, agent="Claim_Triage"))


def test_bad_semver_rejected():
    with pytest.raises(ValidationError):
        AgentManifest.model_validate(dict(MANIFEST, version="2.3"))


def test_duplicate_tools_rejected():
    with pytest.raises(ValidationError):
        AgentManifest.model_validate(dict(MANIFEST, tools=["claim_read", "claim_read"]))


def test_canonical_json_is_deterministic_and_compact():
    import json

    m = AgentManifest.model_validate(MANIFEST)
    body = m.canonical_json()
    expected = json.dumps(
        json.loads(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert body == expected
    assert ", " not in body and '": ' not in body
