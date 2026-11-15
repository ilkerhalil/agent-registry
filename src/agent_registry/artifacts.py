"""Eval artefaktları — içerik adresli depo.

Doğrulama sonucu (eval_result) yalnız DB'de bir JSONB olarak durursa iki şey
kaybolur: sonucun tam gövdesi (suite kırılımı, vaka listesi) ve o gövdenin
değişmediğinin kanıtı. Bu modül sonucu içerik adresli olarak saklar:
anahtar = "sha256:<hex>", gövde dosya sisteminde (ya da S3'te) durur.

Aynı gövde iki kez yazılmaz; anahtar gövdeden üretildiği için okunan artefakt
beklenen hash ile karşılaştırılabilir.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Protocol


class ArtifactError(Exception):
    pass


def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def artifact_key(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class ArtifactStore(Protocol):
    def put(self, data: bytes) -> str: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


class FileSystemStore:
    """İçerik adresli dosya deposu: <root>/<ilk iki hex>/<hex>.json"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if not key.startswith("sha256:"):
            raise ArtifactError(f"geçersiz artefakt anahtarı: {key[:20]}")
        digest = key.removeprefix("sha256:")
        if len(digest) != 64:
            raise ArtifactError("artefakt anahtarı sha256 uzunluğunda değil")
        return self.root / digest[:2] / f"{digest}.json"

    def put(self, data: bytes) -> str:
        key = artifact_key(data)
        path = self._path(key)
        if path.exists():
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        # Yarım dosya bırakmamak için önce geçici dosyaya yaz, sonra taşı.
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return key

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise ArtifactError(f"artefakt bulunamadı: {key}")
        data = path.read_bytes()
        # İçerik adresli depoda okuma da doğrulanır.
        if artifact_key(data) != key:
            raise ArtifactError("artefakt içeriği anahtarıyla uyuşmuyor")
        return data

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Store:
    """S3 / MinIO arkası. boto3 istemcisi dışarıdan verilir (test edilebilirlik)."""

    def __init__(self, client, bucket: str, prefix: str = "evals/") -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix

    def _object_name(self, key: str) -> str:
        if not key.startswith("sha256:"):
            raise ArtifactError(f"geçersiz artefakt anahtarı: {key[:20]}")
        digest = key.removeprefix("sha256:")
        if len(digest) != 64:
            raise ArtifactError("artefakt anahtarı sha256 uzunluğunda değil")
        return f"{self.prefix}{digest[:2]}/{digest}.json"

    def put(self, data: bytes) -> str:
        key = artifact_key(data)
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._object_name(key),
            Body=data,
            ContentType="application/json",
        )
        return key

    def get(self, key: str) -> bytes:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._object_name(key))
        except Exception as exc:  # noqa: BLE001 - istemci hatası tek tipe indirgenir
            raise ArtifactError(f"artefakt okunamadı: {key}") from exc
        data = obj["Body"].read()
        if artifact_key(data) != key:
            raise ArtifactError("artefakt içeriği anahtarıyla uyuşmuyor")
        return data

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._object_name(key))
        except Exception:  # noqa: BLE001
            return False
        return True
