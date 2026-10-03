/**
 * Configuración de suscripciones de alertas.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useModuleApi, CatalogItem, Subscription } from '../services/api';

const App: React.FC = () => {
  const { t } = useTranslation('risk');
  const api = useModuleApi();

  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [subs, setSubs] = useState<Map<string, Subscription>>(new Map());
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const [cat, s] = await Promise.all([
        api.getCatalog(),
        api.getSubscriptions(),
      ]);
      setCatalog(cat);
      setSubs(new Map(s.map((x) => [x.alert_type, x])));
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

  const toggleSub = async (risk: CatalogItem) => {
    const existing = subs.get(risk.alert_type);
    try {
      if (existing) {
        const u = await api.updateSubscription(existing.id, { is_active: !existing.is_active });
        setSubs((m) => new Map(m).set(risk.alert_type, u));
      } else {
        const c = await api.createSubscription({
          alert_type: risk.alert_type,
          is_active: true,
          user_threshold: 50,
          channels: { email: true, push: false },
        });
        setSubs((m) => new Map(m).set(risk.alert_type, c));
      }
    } catch {
      /* keep state */
    }
  };

  const setThreshold = async (risk: CatalogItem, value: number) => {
    const existing = subs.get(risk.alert_type);
    if (!existing) return;
    try {
      const u = await api.updateSubscription(existing.id, { user_threshold: value });
      setSubs((m) => new Map(m).set(risk.alert_type, u));
    } catch {
      /* keep state */
    }
  };

  if (loading) return <p className="p-6 text-nkz-muted">{t('config.loading')}</p>;

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      {/* ── Notificaciones ── */}
      <div className="rounded-xl border border-nkz-border bg-nkz-info-soft p-4 text-sm text-nkz-text-primary flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <span>{t('config.notifications_moved')}</span>
        <a
          href="/notifications"
          className="text-sm font-medium text-nkz-accent-base hover:underline whitespace-nowrap"
        >
          {t('config.go_to_notifications')} &rarr;
        </a>
      </div>

      {/* ── Suscripciones ── */}
      <section className="rounded-xl border border-nkz-border bg-white p-4 space-y-2">
        <h2 className="font-semibold text-nkz-text-primary">{t('config.subscriptions')}</h2>
        {catalog.length === 0 ? (
          <p className="text-sm text-nkz-muted">{t('config.empty')}</p>
        ) : (
          catalog.map((risk) => {
            const sub = subs.get(risk.alert_type);
            const active = sub?.is_active ?? false;
            return (
              <div
                key={risk.alert_type}
                className={`border rounded-lg p-3 ${active ? 'border-nkz-success-soft bg-nkz-success-soft' : 'border-nkz-border'}`}
              >
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-nkz-text-primary">{risk.name}</p>
                    <p className="text-xs text-nkz-muted">{risk.alert_type}</p>
                  </div>
                  <button
                    onClick={() => toggleSub(risk)}
                    className={`px-3 py-1 rounded-full text-xs font-medium ${
                      active ? 'bg-nkz-success-soft text-nkz-success-strong' : 'bg-nkz-bg-secondary text-nkz-muted'
                    }`}
                  >
                    {active ? t('config.on') : t('config.off')}
                  </button>
                </div>
                {active && sub && (
                  <div className="mt-2 space-y-2">
                    <label className="text-xs text-nkz-muted">
                      {t('config.threshold')}: {sub.user_threshold}%
                    </label>
                    <input
                      type="range"
                      min="0"
                      max="100"
                      value={sub.user_threshold}
                      onChange={(e) => setThreshold(risk, parseInt(e.target.value, 10))}
                      className="w-full accent-nkz-success"
                    />
                  </div>
                )}
              </div>
            );
          })
        )}
      </section>
    </div>
  );
};

export default App;

