-- nkz-module-risk/backend/migrations/010_frost_threshold_margin.sql
-- Aviso de helada anticipado: umbrales por antelación subidos a 4,5 / 5,0 / 5,5 °C
-- (antes 3,5 / 4,0 / 4,5) para cubrir el sesgo cálido de la mínima del modelo en finca.
-- Solo cambia la tabla de umbrales; el resto de model_config se conserva. Idempotente.
UPDATE risk.alert_catalog
   SET model_config = COALESCE(model_config, '{}'::jsonb) || '{"thresholds_by_lead":[{"max_lead":4,"tmin_c":4.5},{"max_lead":5,"tmin_c":5.0},{"max_lead":7,"tmin_c":5.5}]}'::jsonb
 WHERE alert_type = 'frost';
