-- nkz-module-risk/backend/migrations/006_disease_preset_documentation.sql
-- Rellena la ficha (documentation) de los presets de enfermedad por umbral.
UPDATE risk.alert_catalog SET documentation = '{"source":"threshold (LWD + T)","inputs":["lwd_hours","temp_avg"],"action":"vigilar si MEDIUM, tratar si HIGH"}'::jsonb
  WHERE alert_type = 'botrytis';

UPDATE risk.alert_catalog SET documentation = '{"source":"threshold (LWD + T)","inputs":["lwd_hours","temp_avg"],"action":"vigilar en cereales si MEDIUM"}'::jsonb
  WHERE alert_type = 'rust_yellow';

UPDATE risk.alert_catalog SET documentation = '{"source":"threshold (T + LWD)","inputs":["temp_avg","lwd_hours"],"action":"riesgo extremo en floración, tratar si HIGH"}'::jsonb
  WHERE alert_type = 'fire_blight';
