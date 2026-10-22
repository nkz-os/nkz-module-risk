-- =============================================================================
-- Risk — Marketplace Registration
-- =============================================================================
-- Run once per environment to register this module in marketplace_modules.
-- module_id MUST match the `module_id` in .github/workflows/build-push.yml
-- and the `id` in src/Module.tsx (both `risk`).
-- =============================================================================

INSERT INTO marketplace_modules (
    id, name, display_name, description, remote_entry_url, version, author,
    category, route_path, label, module_type, required_plan_type, pricing_tier,
    is_local, is_active, required_roles, metadata
) VALUES (
    'risk',
    'risk',
    'Risk',
    'Riesgos y avisos — evaluación agronómica y entrega multicanal',
    '/modules/risk/mf-manifest.json',
    '1.0.0',
    'nkz-os',
    'analytics',
    '/risk',
    'Risk',
    'ADDON_FREE',
    'basic',
    'FREE',
    false,
    true,
    ARRAY['Farmer', 'TenantAdmin', 'PlatformAdmin'],
    '{"icon": "shield-alert", "color": "#3B82F6", "description_i18n": {"es": "Riesgos y avisos", "en": "Risk & alerts", "eu": "Arriskuak", "fr": "Risques", "pt": "Riscos", "ca": "Riscos"}}'::jsonb
) ON CONFLICT (id) DO UPDATE SET
    display_name     = EXCLUDED.display_name,
    description      = EXCLUDED.description,
    remote_entry_url = EXCLUDED.remote_entry_url,
    is_active        = true,
    updated_at       = NOW();
