-- nkz-module-risk/backend/migrations/001_alert_catalog_seed.sql
-- Catálogo inicial: los 6 modelos reales del factory, con sus fuentes de datos.
-- crop_stress (crop-health) y los modelos de enfermedad (disease_evaluator) NO
-- van aquí: son rutas de evaluación separadas en el worker.
INSERT INTO risk.alert_catalog
    (alert_type, name, description, category, model_type, target_sdm_type, data_sources, model_config, severity_levels, is_active)
VALUES
  ('frost', 'Helada', 'Riesgo de helada por temperatura mínima diaria',
   'agronomic', 'frost', 'AgriParcel', '["weather"]', '{}',
   '{"critical":95,"high":80,"medium":60}', true),

  ('water_stress', 'Estrés hídrico', 'Falta de agua disponible en la zona radicular',
   'agronomic', 'water_stress', 'AgriParcel', '["weather","soil"]', '{}',
   '{"critical":95,"high":80,"medium":60}', true),

  ('gdd_pest', 'Plaga por grados-día', 'Ciclo biológico por acumulación de calor (GDD)',
   'agronomic', 'gdd_pest', 'AgriParcel', '["gdd","weather"]', '{"season_start_doy":1}',
   '{"critical":95,"high":80,"medium":60}', true),

  ('wind_spray', 'Deriva de viento', 'Riesgo de deriva en pulverización por viento',
   'agronomic', 'wind_spray', 'AgriParcel', '["weather"]', '{}',
   '{"critical":95,"high":80,"medium":60}', true),

  ('spray_suitability', 'Idoneidad de pulverización', 'Condiciones meteorológicas aptas para pulverizar',
   'agronomic', 'spray_suitability', 'AgriParcel', '["weather"]', '{}',
   '{"critical":95,"high":80,"medium":60}', true),

  ('weather_alert', 'Aviso meteorológico', 'Alertas AEMET/MeteoAlarm que afectan a la parcela',
   'weather', 'weather_alert', 'AgriParcel', '["weather_alerts"]', '{}',
   '{"critical":95,"high":80,"medium":60}', true)

ON CONFLICT (alert_type) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    category = EXCLUDED.category,
    model_type = EXCLUDED.model_type,
    target_sdm_type = EXCLUDED.target_sdm_type,
    data_sources = EXCLUDED.data_sources,
    model_config = EXCLUDED.model_config,
    severity_levels = EXCLUDED.severity_levels,
    is_active = true;
