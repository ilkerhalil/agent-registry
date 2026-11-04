# Agent Registry — Claude Code Configuration

## What this is

Ajan manifestlerini **değişmez sürümler** olarak saklayan, **imzalı** olarak çalışma zamanına
çözen ve tüm eylemleri **append-only** denetim izine yazan küçük bir servis.

Tez: *registry bir katalog değil, çalışma zamanının tek doğruluk kaynağıdır.*

Tasarım gerekçeleri `PLAN.md`'de. Kod yazmadan önce orayı oku.

## Rules

- Do what has been asked; nothing more, nothing less.
- Read a file before editing it.
- Never commit secrets. `.env` gitignore'da; `.env.example` güncel kalır.
- Keep files under 500 lines. Büyüyen modülü böl, yeni katman uydurma.
- No ORM. SQL açık ve okunur kalır (psycopg3, parametreli).
- Her davranış değişikliği test ister. Commit öncesi: `uv run pytest` + `uv run ruff check .`
- Commit mesajı conventional commit (`feat:`, `fix:`, `docs:`, `test:`). `Co-Authored-By` ekleme.
- Başka projelere dokunma. Bu depo `/home/ilker/projects/agentic-framework` dışına yazmaz.

## Invariants — bunlar bozulursa tasarım çöker

1. **Manifest satırı değişmez.** Yeni sürüm = yeni satır. DB trigger'ı (`agent_version_immutable`)
   bunu zorlar; trigger'ı zayıflatma.
2. **Ajan başına tek aktif sürüm.** `agent_version_one_active` kısmi unique index'i korur;
   uygulama katmanında da kontrol edilir.
3. **`resolve` imzasız dönmez.** İmza `build_signed_payload()` gövdesi üzerinedir; runtime aynı
   biçimi doğrular. İmzayı opsiyonel yapma.
4. **`audit_event` append-only.** UPDATE/DELETE trigger ile reddedilir.
5. **Kapı fail-closed ve sunucu tarafında.** `run_gate()` içinde bir kontrol çalıştırılamıyorsa
   `passed=False`. "Bilmiyorum" asla "geçti" sayılmaz. `promote` kapıyı registry'de yeniden
   koşar; istemcinin gönderdiği `gate_results`'a güvenilmez.
6. **Tenant izolasyonu servis katmanında.** `resolve` yalnız kendi kiracısını çözer.
   (Not: v0.1'de kimlik doğrulama katmanı yok — bu invariant henüz hedeftir.)
7. **Registry elle düzenlenmez.** Doğruluk kaynağı Git'teki manifestlerdir; registry onların
   indeksidir. "Kolaylık olsun" diye yazma ucu ekleme.

## Mimari harita

```
src/agent_registry/
  manifest.py    pydantic şema · kanonik JSON · sha256 (değişmez sözleşme)
  signing.py     HMAC-SHA256 imza/doğrulama (Cosign backend için genişletilecek)
  states.py      yaşam döngüsü geçiş kuralları (tek kaynak)
  store.py       SQL erişimi — iş kuralı YOK
  service.py     iş kuralları: kayıt, doğrulama, terfi, çözümleme
  api.py         FastAPI uç noktaları (ince katman)
  gate.py        CI kapısı: 7 kontrol, DB'den bağımsız ve test edilebilir
  cli.py         serve · init-db · gate · agents
sql/001_schema.sql   şema + trigger'lar (idempotent)
catalogs/            tools · models · teams (kapı bunları okur)
examples/            örnek manifest + eval sonucu
tests/               conftest DB'yi truncate eder; her test temiz başlar
```

Katman kuralı: **api → service → store**. API'de iş kuralı, store'da karar olmaz.

## Commands

```bash
docker compose up -d db                      # Postgres 16, port 5433 (5432 başka projede)
uv sync --extra dev
uv run agent-registry init-db                # şemayı uygular (idempotent)
uv run agent-registry serve --port 8100      # http://127.0.0.1:8100/docs
uv run pytest                                # 31 test
uv run ruff format . && uv run ruff check .
uv run agent-registry gate \
  --manifest examples/claim-triage.manifest.yaml \
  --eval-result examples/claim-triage.eval.json
```

DB olmadan testler koşmaz — `conftest.py` şemayı uygular ve tabloları truncate eder.

## Durum (2026-11-04)

M0 tamam; 2026-11-04 düzeltmeleriyle (validate hata eşlemesi, sahiplik kontrolü,
sunucu tarafı kapı, tek transaction) durum:

```
35 test geçiyor · ruff temiz
canlı uvicorn üzerinde uçtan uca doğrulandı:
  register → version(draft) → validate → promote(active) → resolve(imzalı)
  imza doğrulandı; bozulmuş yük reddedildi
CI kapısı örnek manifestte 7/7 PASS
```

## Sıradaki işler

### M3 — runtime SDK + Cosign (ÖNERİLEN, önce bu)

**Neden:** "Kayıtsız ajan çalıştırılamaz" kuralının ikinci yarısı burada kapanıyor.
Şu an API'yi doğrudan çağıran bir istemci kayıtsız çalışabilir.

Kabul kriterleri:

- `src/agent_registry/runtime.py`: `load_agent(name, tenant, env)` →
  `resolve` çağırır, **imzayı doğrular**, geçersizse `SignatureError` atar,
  başarılıysa manifesti `expires_at`'e kadar önbelleğe alır.
- Registry erişilemezse **son imzalı kopya** ile devam eder (disk önbelleği, TTL'li).
  İmzasız hiçbir durumda çalışmaz — önbellek yoksa hata.
- Testler: geçerli imza yüklenir · bozulmuş imza reddedilir · süresi geçmiş imza yeniden çeker ·
  registry kapalı + önbellek var → yüklenir · registry kapalı + önbellek yok → hata.
- Cosign: `signing.py` backend seçilebilir olsun (`REGISTRY_SIGNING_BACKEND=hmac|cosign`).
  Cosign yolu `cosign sign-blob` / `verify-blob` kullanır, anahtar dosyadan gelir.
  **HMAC varsayılan kalır** — mevcut testler kırılmaz.

### M1 — eval sonuçlarının kalıcı referansı

- Eval raporu object storage'a yazılır; `agent_version.eval_result` yalnız referans + özet tutar.
- `promote` sırasında önceki sürümün eval sonucuyla **regresyon karşılaştırması** yapılır;
  `regression-core` kaybı terfiyi engeller.
- Test: düşük skorlu aday, önceki sürümden kötüyse promote reddedilir.

### M2 — katalogların registry'ye taşınması

- `catalogs/*.yaml` yerine `tool`, `model`, `team` tabloları; kapı bunları okur.
- `dependency` tablosu etki analizi için kullanılır: "bu aracı kullanan ajanlar kimler".
- Test: katalogdan silinmiş araç, onu kullanan ajanın terfisini engeller.

### M4 — metrikler

- `GET /v1/metrics`: coverage, kayıtsız çalıştırma denemesi (0'a inmeli), bayat sürüm,
  sahipsiz ajan, promote geçiş oranı, rollback sayısı.
- Prometheus formatı; Grafana panosu ayrı iş.

## Yapma

- SQLAlchemy/ORM ekleme.
- Eval **çalıştırma** altyapısı yazma — bu depo yalnız sonucu kaydeder.
- Prompt/artifact depolama ekleme.
- Web arayüzü ekleme (M4'ten önce).
- Public API şeklini `PLAN.md` güncellenmeden değiştirme.
- Testleri "geçsin diye" zayıflatma; kırılan test ya koddaki hatayı ya da yanlış varsayımı gösterir.
