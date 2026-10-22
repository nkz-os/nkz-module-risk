-- nkz-module-risk/backend/migrations/007_alert_deliveries_severity.sql
-- Añade severity para el dedup de reenvíos (no reenviar si ya se entregó la
-- misma Alert con la misma severidad).
ALTER TABLE risk.alert_deliveries
    ADD COLUMN IF NOT EXISTS severity TEXT NOT NULL DEFAULT '';
