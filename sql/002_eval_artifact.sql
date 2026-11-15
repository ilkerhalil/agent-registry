-- Eval artefaktı: doğrulama sonucunun tam gövdesi içerik adresli depoda durur.
-- eval_result kolonu sorgulanabilir özeti, eval_artifact kolonu kanıtı taşır.
ALTER TABLE agent_version ADD COLUMN IF NOT EXISTS eval_artifact TEXT;
