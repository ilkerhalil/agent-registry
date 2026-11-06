# Agent Registry — Plan

> Ajan kataloğu ve çalışma zamanı çözümleme servisi.
> Tez: **registry bir katalog değil, çalışma zamanının tek doğruluk kaynağı ve denetim defteridir.**
> Katalog görünümü bunun yan ürünüdür.

## Problem

Kurumda ajanlar dağınık üretiliyor: aynı işi yapan kopyalar, sahibi bilinmeyen sürümler,
hangi ajanın hangi veriye eriştiğinin merkezi kaydı yok. Merkezi bir liste tutmak yetmiyor —
liste, çalışma zamanını bağlamadığı sürece kâğıt üstünde kalır.

## Çözüm

Üç sözleşme:

```
manifest   ajanın ne olduğu, neye eriştiği, nasıl değerlendirildiği (immutable, imzalı)
runtime    yalnız registry'den yüklenen imzalı manifest ile çalışır
registry   değişmez sürümler + onay kayıtları + append-only denetim izi
```

**Kaçış yolu yok:** kayıtsız ajanı çalıştırmanın bir yolu bırakılmaz.

## Kapsam (v0.1 — bu depo)

| Var | Yok (bilinçli) |
|---|---|
| Postgres şeması (6 tablo, append-only audit) | Model ağırlıkları saklama |
| Manifest şeması + doğrulama | Eval **çalıştırma** (yalnız sonuç kaydı) |
| Sürüm yaşam döngüsü (draft→active→retired) | Prompt/artifact depolama |
| HMAC imzalama + doğrulama | Cosign/Sigstore backend (arayüz hazır) |
| REST API (7 uç nokta) | Web arayüzü |
| CI kapısı (7 kontrol, CLI) | Çok bölgeli dağıtım |
| Çok kiracılı ayrım (tenant) | Kiracılar arası paylaşım |
| pytest testleri | Yük testi |

## Mimari

```
Git (manifestler)  ──PR+review──►  CI kapısı (7 kontrol)
                                        │ geçerse
                                        ▼
                              Registry (Postgres, append-only)
                                        │ resolve + imza
                                        ▼
                              Runtime (yalnız imzalı manifest)
```

Git manifestlerin kaynağıdır; registry ondan üretilen indekstir. **Registry elle düzenlenmez** —
her değişiklik Git'ten gelir, aksi hâlde denetim izi anlamını yitirir.

## Veri modeli

```sql
agent           name, tenant, owner_team, purpose, criticality,
                data_classification, created_at, retired_at
agent_version   agent_id, semver, manifest_json, manifest_hash, git_commit,
                state, eval_result, signed_by, created_by           (IMMUTABLE)
deployment      version_id, agent_id, env, region, config_hash, deployed_by, status
promotion       from_version, to_version, gate_results_json, approver, decision
dependency      version_id, kind(agent|tool|dataset|model), ref, required
audit_event     actor, action, object_type, object_id, before/after_hash, ts
```

Kısıtlar:
- `UNIQUE(agent_id, semver)`
- `UNIQUE INDEX ... WHERE state='active'` → ajan başına tek aktif sürüm (prod)
- `UNIQUE INDEX ... (agent_id, env) WHERE status='active'` → ajan + ortam başına tek aktif deployment
- `audit_event` üzerinde UPDATE/DELETE trigger ile reddedilir

## Yaşam döngüsü

```
draft ──validate──► validated ──promote──► active ──deprecate──► deprecated ──retire──► retired
                        │                                            ▲
                        └──────────── blocked (güvenlik) ────────────┘
```

- `resolve` sürümü `deployment` tablosundan **ortam bazında** çözer; aktif deployment yoksa 404
- `promote --env prod` aktif sürümü değiştirir; prod dışı terfi sürümü `candidate` yapar ve
  prod'daki aktif sürümü etkilemez
- `deprecated` / `retired` / `blocked` sürümlerin tüm deployment'ları kapatılır
- `retired` geri dönmez; geri dönüş = yeni sürüm
- `blocked` yalnız güvenlik rolü tarafından konur/kaldırılır

## API

```
POST /v1/agents                              ajan kaydı
POST /v1/agents/{name}/versions              yeni sürüm (immutable)
POST /v1/agents/{name}/versions/{v}:validate eval sonucunu kaydet + doğrula
POST /v1/agents/{name}/versions/{v}:promote  sunucuda kapıyı yeniden koşarak terfi
POST /v1/agents/{name}/versions/{v}:deprecate
POST /v1/agents/{name}/versions/{v}:retire
GET  /v1/agents/{name}/resolve?env=prod      runtime çözümleme (imzalı)
GET  /v1/agents?tenant=&stale=&owner=        envanter
GET  /v1/audit?object=...                    denetim izi
GET  /healthz
```

`/healthz` dışındaki tüm uçlar `Authorization: Bearer <anahtar>` ister; yetki rol ve
kiracı kapsamıyla denetlenir (bkz. Güvenlik kararları).

`resolve` yanıtı imza taşır:

```json
{"agent":"claim-triage","version":"2.3.1","env":"prod","manifest":{...},
 "manifest_hash":"sha256:...","signature":"hmac-sha256:...","expires_at":"..."}
```

## CI kapısı — 7 kontrol

```
1  manifest şeması geçerli
2  eval eşikleri sağlandı, regresyonda kayıp yok
3  data_scopes ⊆ ekibin izin listesi
4  tüm araçlar araç kataloğunda kayıtlı
5  model_policy modelleri model kataloğunda kayıtlı
6  sahip ekip tanımlı, on-call var
7  aynı ajanın aktif sürümüyle çakışan kapsam yok
```

Kontroller lokalde katalog dosyaları (tools.yaml, models.yaml, teams.yaml) üzerinden koşar;
v0.2'de bunlar da registry'ye taşınır.

Aynı yedi kontrol `promote` sırasında **registry tarafından yeniden koşulur**; istemcinin
gönderdiği bir kapı sonucuna güvenilmez. Kapı geçmezse sürüm `validated` kalır, `promotion`
tablosuna `rejected` kaydı ve `audit_event`'e `version.promote_rejected` yazılır.

## Güvenlik kararları

- **API kimlik doğrulaması zorunlu.** Bearer API anahtarları `REGISTRY_API_KEYS` ortam
  değişkeninden (JSON) okunur. Roller: `runtime` (resolve), `publisher` (yazma uçları),
  `security` (block + audit). Her anahtar bir kiracı kümesine bağlıdır; `*` tüm kiracılar.
  Anahtar tanımlı değilse sunucu başlamaz. `X-Actor` / `X-Role` başlıkları kaldırıldı;
  aktör ve rol yalnız token'dan gelir. v0.2'de OIDC/mTLS backend'i aynı `Principal`
  arayüzünü üretir.
- **İmza zorunlu.** `resolve` imzasız/geçersiz manifest döndürmez.
- **İmza anahtarı ortamdan** (`REGISTRY_SIGNING_KEY`); kodda asla.
- **Registry çökerse** runtime son imzalı kopyayla çalışır (TTL'li) — imzasız asla.
- **Tenant izolasyonu** servis katmanında: `resolve` yalnız kendi kiracısını çözer.
- **Audit append-only**: trigger, UPDATE ve DELETE'i reddeder.

## Kilometre taşları

| # | İçerik | Durum |
|---|---|---|
| M0 | Şema, manifest, imza, yaşam döngüsü, API, kapı CLI, testler, docker | **TAMAM** (31 test) |
| M1 | Eval sonuçlarının kalıcı referansı (S3) + regresyon karşılaştırması | sonra |
| M2 | Araç/model kataloglarının registry'ye taşınması + etki analizi | sonra |
| M3 | Cosign imza backend'i + runtime SDK (`resolve` istemcisi) | sonra |
| M4 | Metrikler (coverage, blocked calls, stale), Prometheus | sonra |

## Riskler

| Risk | Etki | Önlem |
|---|---|---|
| Registry "ikinci CMS"e dönüşür | Denetim izi anlamsızlaşır | Elle yazma yolu yok; yalnız Git'ten |
| Kayıtsız çalıştırma yolu bırakılır | Katalog boş kalır | Runtime sözleşmesi + CI kapısı |
| Manifest şişer, bürokrasi olur | Ekipler kaçınır | Zorunlu alanlar minimum; gerisi opsiyonel |
| Registry tek arıza noktası olur | Üretim durur | İmzalı son kopya + TTL önbellek |

## Teknoloji

```
Python 3.12+ · FastAPI · psycopg3 · Pydantic v2 · pytest · ruff
Postgres 16 (docker, port 5433) · uv (bağımlılık yönetimi)
```

Mevcut MLflow benzeri bir model registry'ye **zorlanmadı**: model odaklı; ajan sürümü,
araç kapsamı, izin kümesi ve emeklilik semantiği taşımıyor.
