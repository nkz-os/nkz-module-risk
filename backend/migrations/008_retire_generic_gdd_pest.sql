-- nkz-module-risk/backend/migrations/008_retire_generic_gdd_pest.sql
-- Retirar el preset genérico gdd_pest: no corresponde a ninguna plaga (sin
-- biofix, base ni umbrales propios) y aplica a todos los cultivos. Con la serie
-- de la campaña completa recomendaría tratamiento en todas las parcelas.
-- El modelo gdd_pest sigue disponible para presets de plagas concretas.
-- Idempotente; va después de 001, que lo activa al reaplicarse.
UPDATE risk.alert_catalog SET is_active = false
  WHERE alert_type = 'gdd_pest' AND is_active;
