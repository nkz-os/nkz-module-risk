-- nkz-module-risk/backend/migrations/002_alert_catalog_presets.sql
-- Presets declarativos: riesgos del catálogo histórico (Climate/WaterSoil/Fungi/Pests)
-- expresados con el motor de umbral (árbol AND/OR + between + aggregate + COUNT>=N).
-- Oídio (Gubler) y Mildiu (Magarey) se quedan como modelo (disease_evaluator), no aquí.

INSERT INTO risk.alert_catalog
    (alert_type, name, description, category, model_type, target_sdm_type, data_sources, model_config, severity_levels, is_active)
VALUES
  ('heat_stress', 'Golpe de calor', 'Estrés por calor extremo y baja humedad.',
   'weather', 'threshold', 'AgriParcel', '["weather"]', '{"logical_operator":"OR","conditions":[{"logical_operator":"AND","conditions":[{"source":"weather","attribute":"temp_avg","operator":">","value":35,"severity":"high"},{"source":"weather","attribute":"humidity","operator":"<","value":30,"severity":"high"}]},{"source":"weather","attribute":"temp_avg","operator":">","value":32,"severity":"medium"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('hail_proxy', 'Riesgo de granizo', 'Caída brusca de presión y temperatura con lluvia (proxy).',
   'weather', 'threshold', 'AgriParcel', '["weather"]', '{"logical_operator":"AND","conditions":[{"source":"weather","attribute":"pressure_hpa","operator":">","value":5,"aggregate":"delta","duration_minutes":360,"severity":"high"},{"source":"weather","attribute":"temp_avg","operator":">","value":8,"aggregate":"delta","duration_minutes":360,"severity":"high"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('wind_damage', 'Daño por viento', 'Peligro de rotura de ramas o caída de frutos.',
   'weather', 'threshold', 'AgriParcel', '["weather"]', '{"logical_operator":"OR","conditions":[{"source":"weather","attribute":"wind_speed_ms","operator":">","value":16.7,"severity":"high"},{"source":"weather","attribute":"wind_speed_ms","operator":">","value":11.1,"severity":"medium"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('sunburn', 'Insolación de fruto', 'Daño por radiación solar directa intensa.',
   'weather', 'threshold', 'AgriParcel', '["weather"]', '{"logical_operator":"OR","conditions":[{"logical_operator":"AND","conditions":[{"source":"weather","attribute":"solar_rad_w_m2","operator":">","value":850,"severity":"high"},{"source":"weather","attribute":"temp_avg","operator":">","value":33,"severity":"high"}]},{"source":"weather","attribute":"solar_rad_w_m2","operator":">","value":700,"severity":"medium"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('fire_30_30_30', 'Riesgo de incendio (30-30-30)', 'Regla clásica: T>30°C, HR<30%, viento>30 km/h (2 de 3).',
   'weather', 'threshold', 'AgriParcel', '["weather"]', '{"logical_operator":"COUNT>=2","conditions":[{"source":"weather","attribute":"temp_avg","operator":">","value":30,"severity":"high"},{"source":"weather","attribute":"humidity","operator":"<","value":30,"severity":"high"},{"source":"weather","attribute":"wind_speed_ms","operator":">","value":8.3,"severity":"high"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('waterlogging', 'Asfixia radicular', 'Saturación prolongada de agua en el suelo.',
   'agronomic', 'threshold', 'AgriParcel', '["weather"]', '{"conditions":[{"source":"weather","attribute":"soil_moisture_0_10cm","operator":">","value":45,"duration_minutes":2880,"severity":"high"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('saline_stress', 'Estrés salino', 'Bloqueo por exceso de sales en el suelo.',
   'agronomic', 'threshold', 'AgriParcel', '["soil"]', '{"logical_operator":"OR","conditions":[{"source":"soil","attribute":"ec","operator":">","value":4,"severity":"high"},{"source":"soil","attribute":"ec","operator":">","value":2.5,"severity":"medium"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('nutrient_leaching', 'Lixiviación de nutrientes', 'Lavado de fertilizantes por lluvia intensa.',
   'agronomic', 'threshold', 'AgriParcel', '["weather"]', '{"logical_operator":"OR","conditions":[{"source":"weather","attribute":"precip_mm","operator":">","value":40,"aggregate":"sum","duration_minutes":1440,"severity":"high"},{"source":"weather","attribute":"precip_mm","operator":">","value":25,"aggregate":"sum","duration_minutes":1440,"severity":"medium"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('root_thermal_stress', 'Estrés térmico radicular', 'Temperatura del suelo fuera del rango óptimo.',
   'agronomic', 'threshold', 'AgriParcel', '["soil"]', '{"logical_operator":"OR","conditions":[{"source":"soil","attribute":"temperature","operator":">","value":28,"severity":"high"},{"source":"soil","attribute":"temperature","operator":"<","value":5,"severity":"high"},{"source":"soil","attribute":"temperature","operator":"between","value":[25,28],"severity":"medium"},{"source":"soil","attribute":"temperature","operator":"between","value":[5,8],"severity":"medium"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('botrytis', 'Botrytis (pudrición)', 'Peligro por humedad foliar prolongada.',
   'disease', 'threshold', 'AgriParcel', '["leaf_wetness","weather"]', '{"logical_operator":"OR","conditions":[{"logical_operator":"AND","conditions":[{"source":"leaf_wetness","attribute":"hours","operator":">","value":12,"severity":"high"},{"source":"weather","attribute":"temp_avg","operator":"between","value":[15,20],"severity":"high"}]},{"source":"leaf_wetness","attribute":"hours","operator":">","value":8,"severity":"medium"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('rust_yellow', 'Roya amarilla/parda', 'Riesgo en cereales por humedad nocturna.',
   'disease', 'threshold', 'AgriParcel', '["leaf_wetness","weather"]', '{"logical_operator":"OR","conditions":[{"logical_operator":"AND","conditions":[{"source":"leaf_wetness","attribute":"hours","operator":">","value":8,"severity":"high"},{"source":"weather","attribute":"temp_avg","operator":"between","value":[10,15],"severity":"high"}]},{"source":"weather","attribute":"temp_avg","operator":"between","value":[5,10],"severity":"medium"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('fire_blight', 'Fuego bacteriano', 'Riesgo extremo en floración de frutales.',
   'disease', 'threshold', 'AgriParcel', '["leaf_wetness","weather"]', '{"logical_operator":"OR","conditions":[{"logical_operator":"AND","conditions":[{"source":"weather","attribute":"temp_avg","operator":">","value":18,"severity":"high"},{"source":"leaf_wetness","attribute":"hours","operator":">","value":4,"severity":"high"}]},{"source":"weather","attribute":"temp_avg","operator":">","value":15,"severity":"medium"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('red_spider', 'Araña roja', 'Proliferación en ambientes cálidos y secos.',
   'pest', 'threshold', 'AgriParcel', '["weather"]', '{"logical_operator":"OR","conditions":[{"logical_operator":"AND","conditions":[{"source":"weather","attribute":"temp_avg","operator":">","value":30,"severity":"high"},{"source":"weather","attribute":"humidity","operator":"<","value":40,"severity":"high"}]},{"source":"weather","attribute":"temp_avg","operator":">","value":25,"severity":"medium"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('fruit_fly', 'Mosca de la fruta', 'Actividad biológica según temperatura.',
   'pest', 'threshold', 'AgriParcel', '["weather"]', '{"logical_operator":"OR","conditions":[{"source":"weather","attribute":"temp_avg","operator":"between","value":[16,32],"severity":"high"},{"source":"weather","attribute":"temp_avg","operator":"between","value":[12,16],"severity":"medium"}]}',
   '{"critical":95,"high":80,"medium":60}', true),
  ('aphids', 'Pulgón', 'Riesgo por calor suave y vigor vegetativo alto.',
   'pest', 'threshold', 'AgriParcel', '["weather","ndvi"]', '{"logical_operator":"OR","conditions":[{"logical_operator":"AND","conditions":[{"source":"weather","attribute":"temp_avg","operator":"between","value":[20,25],"severity":"high"},{"source":"ndvi","attribute":"ndvi","operator":">","value":0.6,"severity":"high"}]},{"source":"weather","attribute":"temp_avg","operator":"between","value":[15,20],"severity":"medium"}]}',
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
