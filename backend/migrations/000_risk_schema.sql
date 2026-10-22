-- nkz-module-risk/backend/migrations/000_risk_schema.sql
-- Esquema del módulo risk. NO guarda telemetría: el historial temporal de Alert
-- vive en TimescaleDB vía suscripción Orion (regla ZERO DIRECT DB WRITES).
CREATE SCHEMA IF NOT EXISTS risk;

-- Catálogo de alertTypes. model_type apunta a los modelos reales del worker.
CREATE TABLE IF NOT EXISTS risk.alert_catalog (
    alert_type   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT,
    category     TEXT NOT NULL,           -- weather|agronomic|robotic|energy|sensor|crop (== risk_domain)
    model_type   TEXT,                     -- frost|water_stress|gdd_pest|... (ver factory)
    target_sdm_type TEXT NOT NULL DEFAULT 'AgriParcel',  -- tipo de entidad Orion a evaluar
    data_sources JSONB NOT NULL DEFAULT '[]'::jsonb,     -- ['weather','gdd','telemetry','weather_alerts']
    evaluation_mode TEXT NOT NULL DEFAULT 'batch',
    model_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    severity_levels JSONB NOT NULL DEFAULT '{"critical":95,"high":80,"medium":60}'::jsonb,
    is_active    BOOLEAN NOT NULL DEFAULT true,
    tenant_id    TEXT,                     -- NULL = global; no-NULL = riesgo custom de un tenant (fix B2)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alert_catalog_tenant ON risk.alert_catalog(tenant_id);

-- Suscripciones por tenant+alertType (umbral + canales habilitados).
CREATE TABLE IF NOT EXISTS risk.tenant_alert_subscriptions (
    id             BIGSERIAL PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    alert_type     TEXT NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT true,
    user_threshold REAL NOT NULL DEFAULT 50,
    channels       JSONB NOT NULL DEFAULT '{"email":true,"push":false}'::jsonb,
    entity_filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, alert_type)
);

-- Config de canales por tenant (una sola fuente para email/push/zulip/webhook/n8n/telegram).
CREATE TABLE IF NOT EXISTS risk.tenant_alert_channels (
    tenant_id TEXT PRIMARY KEY,
    email     JSONB NOT NULL DEFAULT '{"enabled":false}'::jsonb,
    push      JSONB NOT NULL DEFAULT '{"enabled":false}'::jsonb,
    zulip     JSONB NOT NULL DEFAULT '{"enabled":false}'::jsonb,
    webhook   JSONB NOT NULL DEFAULT '{"enabled":false}'::jsonb,
    telegram  JSONB NOT NULL DEFAULT '{"enabled":false}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Estado transaccional de entrega por canal (metadata operativa, NO telemetría).
CREATE TABLE IF NOT EXISTS risk.alert_deliveries (
    alert_id   TEXT NOT NULL,
    tenant_id  TEXT NOT NULL,
    channel    TEXT NOT NULL,
    status     TEXT NOT NULL,             -- sent|failed|retry
    attempts   INT NOT NULL DEFAULT 1,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (alert_id, channel)
);
