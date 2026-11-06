-- Agent Registry — şema v1
-- Değişmez sürümler + append-only denetim + ajan başına tek aktif sürüm.

CREATE TABLE IF NOT EXISTS agent (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT        NOT NULL,
    tenant              TEXT        NOT NULL,
    owner_team          TEXT        NOT NULL,
    purpose             TEXT        NOT NULL,
    criticality         TEXT        NOT NULL DEFAULT 'standard',
    data_classification TEXT        NOT NULL DEFAULT 'internal',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at          TIMESTAMPTZ,
    CONSTRAINT agent_name_format CHECK (name ~ '^[a-z][a-z0-9-]{2,62}$'),
    CONSTRAINT agent_tenant_name_uniq UNIQUE (tenant, name)
);

CREATE TABLE IF NOT EXISTS agent_version (
    id               BIGSERIAL PRIMARY KEY,
    agent_id         BIGINT      NOT NULL REFERENCES agent(id) ON DELETE RESTRICT,
    semver           TEXT        NOT NULL,
    manifest_json    JSONB       NOT NULL,
    manifest_hash    TEXT        NOT NULL,
    git_commit       TEXT,
    state            TEXT        NOT NULL DEFAULT 'draft',
    eval_result      JSONB,
    eval_suite       TEXT,
    signed_by        TEXT,
    created_by       TEXT        NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    state_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT agent_version_uniq UNIQUE (agent_id, semver),
    CONSTRAINT agent_version_state_chk CHECK (
        state IN ('draft','validated','candidate','active','deprecated','retired','blocked')
    ),
    CONSTRAINT agent_version_semver_chk CHECK (semver ~ '^[0-9]+\.[0-9]+\.[0-9]+$')
);

-- Ajan başına yalnızca bir aktif sürüm
CREATE UNIQUE INDEX IF NOT EXISTS agent_version_one_active
    ON agent_version (agent_id) WHERE state = 'active';

CREATE INDEX IF NOT EXISTS agent_version_state_idx ON agent_version (state);

CREATE TABLE IF NOT EXISTS deployment (
    id           BIGSERIAL PRIMARY KEY,
    version_id   BIGINT      NOT NULL REFERENCES agent_version(id) ON DELETE RESTRICT,
    agent_id     BIGINT      REFERENCES agent(id) ON DELETE RESTRICT,
    env          TEXT        NOT NULL,
    region       TEXT        NOT NULL DEFAULT 'default',
    config_hash  TEXT,
    deployed_by  TEXT        NOT NULL,
    deployed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    status       TEXT        NOT NULL DEFAULT 'active',
    CONSTRAINT deployment_status_chk CHECK (status IN ('active','stopped','failed'))
);

-- Mevcut kurulumlar için agent_id ekle ve geriye dönük doldur
ALTER TABLE deployment ADD COLUMN IF NOT EXISTS agent_id BIGINT REFERENCES agent(id) ON DELETE RESTRICT;
UPDATE deployment d
   SET agent_id = v.agent_id
  FROM agent_version v
 WHERE d.version_id = v.id AND d.agent_id IS NULL;

CREATE INDEX IF NOT EXISTS deployment_version_idx ON deployment (version_id);

-- Geçmiş kayıtlarda ortam başına birden çok "active" varsa en yenisi kalır
UPDATE deployment d
   SET status = 'stopped'
 WHERE d.status = 'active'
   AND EXISTS (
       SELECT 1
         FROM deployment d2
        WHERE d2.agent_id = d.agent_id
          AND d2.env = d.env
          AND d2.status = 'active'
          AND (d2.deployed_at, d2.id) > (d.deployed_at, d.id)
   );

-- Ajan + ortam başına tek aktif deployment
CREATE UNIQUE INDEX IF NOT EXISTS deployment_one_active
    ON deployment (agent_id, env) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS promotion (
    id                BIGSERIAL PRIMARY KEY,
    from_version      BIGINT REFERENCES agent_version(id) ON DELETE SET NULL,
    to_version        BIGINT NOT NULL REFERENCES agent_version(id) ON DELETE RESTRICT,
    gate_results_json JSONB  NOT NULL,
    approver          TEXT   NOT NULL,
    decision          TEXT   NOT NULL,
    decided_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT promotion_decision_chk CHECK (decision IN ('approved','rejected'))
);

CREATE TABLE IF NOT EXISTS dependency (
    id         BIGSERIAL PRIMARY KEY,
    version_id BIGINT NOT NULL REFERENCES agent_version(id) ON DELETE CASCADE,
    kind       TEXT   NOT NULL,
    ref        TEXT   NOT NULL,
    required   BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT dependency_kind_chk CHECK (kind IN ('agent','tool','dataset','model')),
    CONSTRAINT dependency_uniq UNIQUE (version_id, kind, ref)
);

CREATE TABLE IF NOT EXISTS audit_event (
    id          BIGSERIAL PRIMARY KEY,
    actor       TEXT        NOT NULL,
    action      TEXT        NOT NULL,
    object_type TEXT        NOT NULL,
    object_id   TEXT        NOT NULL,
    before_hash TEXT,
    after_hash  TEXT,
    detail      JSONB,
    source_ip   TEXT,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_object_idx ON audit_event (object_type, object_id);
CREATE INDEX IF NOT EXISTS audit_ts_idx ON audit_event (ts);

-- Append-only: UPDATE ve DELETE reddedilir
CREATE OR REPLACE FUNCTION audit_event_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only (% reddedildi)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_event_no_update ON audit_event;
CREATE TRIGGER audit_event_no_update
    BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();

-- Sürüm satırlarındaki manifest değişmez; yalnızca state/eval alanları ilerler
CREATE OR REPLACE FUNCTION agent_version_manifest_immutable() RETURNS trigger AS $$
BEGIN
    IF NEW.manifest_json <> OLD.manifest_json OR NEW.manifest_hash <> OLD.manifest_hash THEN
        RAISE EXCEPTION 'agent_version manifest değiştirilemez (yeni sürüm açın)';
    END IF;
    IF OLD.state = 'retired' AND NEW.state <> OLD.state THEN
        RAISE EXCEPTION 'retired durumundan geri dönüş yok';
    END IF;
    IF OLD.state = 'deprecated' AND NEW.state NOT IN ('deprecated','retired') THEN
        RAISE EXCEPTION 'deprecated yalnız retired''a ilerleyebilir';
    END IF;
    NEW.state_changed_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_version_immutable ON agent_version;
CREATE TRIGGER agent_version_immutable
    BEFORE UPDATE ON agent_version
    FOR EACH ROW EXECUTE FUNCTION agent_version_manifest_immutable();
