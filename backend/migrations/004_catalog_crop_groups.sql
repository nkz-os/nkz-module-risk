-- nkz-module-risk/backend/migrations/004_catalog_crop_groups.sql
-- Añade selección por cultivo + documentación de ficha al catálogo.
ALTER TABLE risk.alert_catalog
    ADD COLUMN IF NOT EXISTS applicable_crop_groups TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS documentation JSONB NOT NULL DEFAULT '{}'::jsonb;
