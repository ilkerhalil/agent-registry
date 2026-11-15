"""Eval artefakt deposu testleri — içerik adresleme ve bozulma tespiti."""

from __future__ import annotations

import json

import pytest

from agent_registry.artifacts import (
    ArtifactError,
    FileSystemStore,
    S3Store,
    artifact_key,
    canonical_bytes,
)


def test_put_get_roundtrip_is_content_addressed(tmp_path):
    store = FileSystemStore(tmp_path)
    payload = {"suite": "claim-triage-v4", "success_rate": 0.947}
    data = canonical_bytes(payload)

    key = store.put(data)

    assert key == artifact_key(data)
    assert store.exists(key)
    assert json.loads(store.get(key)) == payload


def test_put_is_idempotent(tmp_path):
    store = FileSystemStore(tmp_path)
    data = canonical_bytes({"a": 1})

    first = store.put(data)
    second = store.put(data)

    assert first == second
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1


def test_get_detects_tampered_artifact(tmp_path):
    store = FileSystemStore(tmp_path)
    key = store.put(canonical_bytes({"success_rate": 0.9}))
    path = store._path(key)
    path.write_bytes(canonical_bytes({"success_rate": 0.99}))

    with pytest.raises(ArtifactError, match="does not match its key"):
        store.get(key)


def test_missing_artifact_raises(tmp_path):
    store = FileSystemStore(tmp_path)
    missing = artifact_key(b"yok")

    with pytest.raises(ArtifactError, match="not found"):
        store.get(missing)


def test_invalid_key_rejected(tmp_path):
    store = FileSystemStore(tmp_path)

    with pytest.raises(ArtifactError):
        store.get("sha256:kisa")


class FakeS3:
    """boto3 istemcisinin test için küçük taklidi."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": _Body(self.objects[Key])}

    def head_object(self, *, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise KeyError(Key)
        return {}


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def test_s3_store_roundtrip():
    client = FakeS3()
    store = S3Store(client, bucket="evals", prefix="eval-artifacts/")
    data = canonical_bytes({"suite": "s", "success_rate": 0.5})

    key = store.put(data)

    assert store.exists(key)
    assert store.get(key) == data
    assert list(client.objects) == [f"eval-artifacts/{key[7:9]}/{key[7:]}.json"]


def test_s3_store_detects_corruption():
    client = FakeS3()
    store = S3Store(client, bucket="evals")
    key = store.put(canonical_bytes({"a": 1}))
    client.objects[store._object_name(key)] = canonical_bytes({"a": 2})

    with pytest.raises(ArtifactError, match="does not match its key"):
        store.get(key)
