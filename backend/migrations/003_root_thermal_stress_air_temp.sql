-- nkz-module-risk/backend/migrations/003_root_thermal_stress_air_temp.sql
-- root_thermal_stress: la temperatura del suelo es dinámica (no una propiedad
-- estática del AgriSoilExtended). Se deriva del aire (proxy de primer orden
-- para suelo superficial) usando weather.temp_avg, en vez de soil.temperature
-- que no existe en el broker.

UPDATE risk.alert_catalog
SET data_sources = '["weather"]',
    model_config = '{"logical_operator":"OR","conditions":[{"source":"weather","attribute":"temp_avg","operator":">","value":28,"severity":"high"},{"source":"weather","attribute":"temp_avg","operator":"<","value":5,"severity":"high"},{"source":"weather","attribute":"temp_avg","operator":"between","value":[25,28],"severity":"medium"},{"source":"weather","attribute":"temp_avg","operator":"between","value":[5,8],"severity":"medium"}]}'
WHERE alert_type = 'root_thermal_stress';
