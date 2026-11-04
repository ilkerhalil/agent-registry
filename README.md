# Agent Registry

Ajan manifestlerini **değişmez sürümler** olarak saklayan, **imzalı** olarak çalışma zamanına
çözen ve tüm eylemleri **append-only** denetim izine yazan küçük bir servis.

> Tez: registry bir katalog değil, çalışma zamanının tek doğruluk kaynağıdır.

Tasarım gerekçeleri: [PLAN.md](PLAN.md)

## Hızlı başlangıç

```bash
docker compose up -d db          # Postgres 16 (port 5433)
uv sync --extra dev
uv run agent-registry init-db
uv run agent-registry serve --port 8100
```

API: <http://127.0.0.1:8100/docs>

## CI kapısı

```bash
uv run agent-registry gate \
  --manifest examples/claim-triage.manifest.yaml \
  --eval-result examples/claim-triage.eval.json
```

Yedi kontrol: manifest şeması · eval eşikleri · veri kapsamı ⊆ ekip izinleri · araçlar kayıtlı ·
modeller kayıtlı · ekip ve on-call tanımlı · başka ekipte kapsam çakışması yok.

Çıkış kodu 0 değilse merge edilmemeli.

## Yaşam döngüsü

```
draft ──validate──► validated ──promote──► active ──deprecate──► deprecated ──retire──► retired
```

- Ajan başına **tek aktif sürüm** (kısmi unique index ile DB'de zorunlu)
- Manifest satırı **değiştirilemez** (trigger); yeni sürüm açılır
- `resolve` yalnız aktif sürümü döndürür ve yanıtı HMAC ile imzalar

## Testler

```bash
docker compose up -d db
uv run pytest
```

## Mimari

```
Git (manifestler) ──► CI kapısı ──► Registry (Postgres) ──► Runtime (imzalı resolve)
```

Registry elle düzenlenmez; her değişiklik Git'ten geçer. Aksi hâlde denetim izi anlamını yitirir.
