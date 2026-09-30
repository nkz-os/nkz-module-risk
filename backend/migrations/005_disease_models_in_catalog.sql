-- nkz-module-risk/backend/migrations/005_disease_models_in_catalog.sql
-- Registra los modelos epidemiológicos como entradas del catálogo (categoría disease)
-- y retira los presets threshold duplicados (oidio_gubler/mildiu_goidanich).
INSERT INTO risk.alert_catalog
  (alert_type, name, description, category, model_type, target_sdm_type,
   data_sources, model_config, applicable_crop_groups, documentation)
VALUES
  ('powdery_mildew', 'Oídio (oidio)', 'Índice Gubler-Thomas (temperatura).',
   'disease', 'gubler_pm', 'AgriParcel', '["weather"]',
   '{}', ARRAY['grapevine'],
   '{"source":"Gubler-Thomas (UC Davis)","inputs":["temp_avg"],"action":"fungicida preventivo si HIGH"}'),
  ('downy_mildew', 'Mildiu', 'Índice Magarey (LWD + T + precipitación 48h).',
   'disease', 'magarey_mildew', 'AgriParcel', '["weather"]',
   '{}', ARRAY['grapevine'],
   '{"source":"Magarey","inputs":["lwd_hours","temp_avg","precip_48h"],"action":"tratamiento si HIGH"}'),
  ('apple_scab', 'Moteado del manzano', 'Índice Mills (LWD + T).',
   'disease', 'mills_scab', 'AgriParcel', '["weather"]',
   '{}', ARRAY['pome_fruit'],
   '{"source":"Mills","inputs":["lwd_hours","temp_avg"],"action":"vigilar si MEDIUM"}'),
  ('alternaria', 'Alternaria (tomate)', 'Modelo TomCast (LWD + T).',
   'disease', 'tomcast_alternaria', 'AgriParcel', '["weather"]',
   '{}', ARRAY['solanaceous'],
   '{"source":"TomCast","inputs":["lwd_hours","temp_avg"],"action":"tratamiento si HIGH"}')
ON CONFLICT (alert_type) DO UPDATE SET
  category = EXCLUDED.category,
  model_type = EXCLUDED.model_type,
  applicable_crop_groups = EXCLUDED.applicable_crop_groups,
  documentation = EXCLUDED.documentation,
  is_active = true;

-- Presets de enfermedad existentes: rellenar applicable_crop_groups.
UPDATE risk.alert_catalog SET applicable_crop_groups = ARRAY['grapevine']
  WHERE alert_type = 'botrytis';
UPDATE risk.alert_catalog SET applicable_crop_groups = ARRAY['cereal']
  WHERE alert_type = 'rust_yellow';
UPDATE risk.alert_catalog SET applicable_crop_groups = ARRAY['pome_fruit','stone_fruit']
  WHERE alert_type = 'fire_blight';

-- Retirar presets duplicados (D4): la enfermedad pasa a evaluarse con el modelo
-- epidemiológico del catálogo (powdery_mildew / downy_mildew).
UPDATE risk.alert_catalog SET is_active = false
  WHERE alert_type IN ('oidio_gubler', 'mildiu_goidanich');
