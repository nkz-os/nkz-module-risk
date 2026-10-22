/**
 * Catálogo de riesgos — fichas editables (is_active, cultivos aplicables, documentación).
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useModuleApi, CatalogItem } from '../services/api';

const CATEGORY_LABEL: Record<string, string> = {
  disease: 'catalog.category.disease',
  pest: 'catalog.category.pest',
  weather: 'catalog.category.weather',
  agronomic: 'catalog.category.agronomic',
  robotic: 'catalog.category.robotic',
  energy: 'catalog.category.energy',
  sensor: 'catalog.category.sensor',
  crop: 'catalog.category.crop',
};

const CatalogTab: React.FC = () => {
  const { t } = useTranslation('risk');
  const api = useModuleApi();

  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = async () => {
    try {
      setCatalog(await api.getCatalog());
    } catch {
      /* keep state */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleActive = async (risk: CatalogItem) => {
    setBusy(risk.alert_type);
    try {
      const updated = await api.updateCatalog(risk.alert_type, {
        is_active: !risk.is_active,
      });
      setCatalog((c) => c.map((x) => (x.alert_type === risk.alert_type ? updated : x)));
    } catch {
      /* keep state */
    } finally {
      setBusy(null);
    }
  };

  const remove = async (risk: CatalogItem) => {
    setBusy(risk.alert_type);
    try {
      await api.deleteCatalog(risk.alert_type);
      setCatalog((c) => c.filter((x) => x.alert_type !== risk.alert_type));
    } catch {
      /* keep state */
    } finally {
      setBusy(null);
    }
  };

  if (loading) {
    return <div className="animate-pulse h-16 bg-nkz-bg-secondary rounded-lg" />;
  }

  const byCategory = new Map<string, CatalogItem[]>();
  for (const r of catalog) {
    const k = r.category || 'agronomic';
    if (!byCategory.has(k)) byCategory.set(k, []);
    byCategory.get(k)!.push(r);
  }

  const groups = [...byCategory.entries()].sort((a, b) => a[0].localeCompare(b[0]));

  return (
    <div className="space-y-6">
      <p className="text-sm text-nkz-text-muted">{t('catalog.description')}</p>
      {groups.map(([category, risks]) => (
        <section key={category} className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-nkz-text-muted">
            {t(CATEGORY_LABEL[category] ?? 'catalog.category.default', category)}
          </h2>
          <div className="grid grid-cols-1 gap-3">
            {risks.map((risk) => (
              <article
                key={risk.alert_type}
                className={`rounded-lg border p-4 bg-nkz-bg-secondary ${
                  risk.is_active ? 'border-nkz-border' : 'border-nkz-border opacity-60'
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-semibold text-nkz-text-primary">{risk.name}</h3>
                      <code className="text-xs text-nkz-text-muted">{risk.alert_type}</code>
                      {risk.model_type && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-nkz-bg-secondary border border-nkz-border text-nkz-text-muted">
                          {risk.model_type}
                        </span>
                      )}
                    </div>
                    {risk.description && (
                      <p className="text-sm text-nkz-text-muted">{risk.description}</p>
                    )}
                    {(risk.applicable_crop_groups?.length ?? 0) > 0 && (
                      <div className="flex items-center gap-1.5 flex-wrap pt-1">
                        <span className="text-xs text-nkz-text-muted">{t('catalog.cropGroups')}:</span>
                        {risk.applicable_crop_groups!.map((g) => (
                          <span
                            key={g}
                            className="text-xs px-2 py-0.5 rounded-full bg-nkz-success-soft text-nkz-success-strong border border-nkz-success-soft"
                          >
                            {g}
                          </span>
                        ))}
                      </div>
                    )}
                    {risk.documentation && typeof risk.documentation === 'object' && (
                      <dl className="text-xs text-nkz-text-muted pt-2 space-y-0.5">
                        {Object.entries(risk.documentation).map(([k, v]) => (
                          <div key={k} className="flex gap-2">
                            <dt className="capitalize text-nkz-text-muted">{k}:</dt>
                            <dd className="text-nkz-text-primary">
                              {Array.isArray(v) ? v.join(', ') : String(v ?? '')}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() => toggleActive(risk)}
                      disabled={busy === risk.alert_type}
                      className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-colors ${
                        risk.is_active
                          ? 'border-nkz-success-soft text-nkz-success-strong'
                          : 'border-nkz-border text-nkz-text-muted'
                      }`}
                    >
                      {risk.is_active ? t('catalog.active') : t('catalog.inactive')}
                    </button>
                    <button
                      onClick={() => remove(risk)}
                      disabled={busy === risk.alert_type}
                      className="px-2 py-1.5 text-xs font-medium rounded-md border border-nkz-border text-nkz-text-muted hover:text-nkz-text-primary"
                    >
                      {t('catalog.remove')}
                    </button>
                  </div>
                </div>
              </article>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
};

export default CatalogTab;
