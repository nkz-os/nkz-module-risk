-- nkz-module-risk/backend/migrations/009_frost_forecast_alert.sql
-- Aviso de helada anticipado por noche: interruptor de vibración por parcela y
-- configuración del catálogo (umbrales por antelación, noches cercanas, horizonte).
-- Idempotente y aditiva.
CREATE TABLE IF NOT EXISTS risk.parcel_alert_settings (
    tenant_id   TEXT NOT NULL,
    parcel_id   TEXT NOT NULL,
    alert_type  TEXT NOT NULL,
    vibrate     BOOLEAN NOT NULL DEFAULT true,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, parcel_id, alert_type)
);

UPDATE risk.alert_catalog
   SET model_config = COALESCE(model_config, '{}'::jsonb) || '{"thresholds_by_lead":[{"max_lead":4,"tmin_c":3.5},{"max_lead":5,"tmin_c":4.0},{"max_lead":7,"tmin_c":4.5}],"near_nights":4,"horizon_nights":7}'::jsonb,
       description = 'Riesgo de helada por noche, con antelación'
 WHERE alert_type = 'frost';
