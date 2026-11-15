-- Kataloglar registry'ye taşınıyor (M2).
-- CI kapısı artık bu tabloları okuyor; YAML dosyaları yalnız yerel/CI geri düşüşü.
CREATE TABLE IF NOT EXISTS tool_catalog (
    name        TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_catalog (
    name        TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS team_catalog (
    name           TEXT PRIMARY KEY,
    on_call        TEXT NOT NULL,
    allowed_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
