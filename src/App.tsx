/**
 * Risk module main page — tabs: Monitor · Configuración · Integraciones.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useModuleApi, AlertItem, CatalogItem } from './services/api';
import ConfigTab from './components/ConfigTab';
import IntegrationsTab from './components/IntegrationsTab';

const SEV: Record<string, string> = {
  critical: 'bg-nkz-danger-soft text-nkz-danger-strong',
  high: 'bg-orange-100 text-orange-800',
  medium: 'bg-nkz-warning-soft text-nkz-warning-strong',
  low: 'bg-nkz-bg-secondary text-nkz-muted',
};

const val = (x: unknown): unknown =>
  x && typeof x === 'object' && 'value' in (x as Record<string, unknown>)
    ? (x as { value: unknown }).value
    : x;

const TABS = [
  { id: 'monitor', label: 'monitor.tab' },
  { id: 'config', label: 'config.tab' },
  { id: 'integrations', label: 'integrations.tab' },
] as const;

const Monitor: React.FC = () => {
  const { t } = useTranslation('risk');
  const api = useModuleApi();
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [catalog, setCatalog] = useState<Map<string, CatalogItem>>(new Map());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const [a, c] = await Promise.all([api.getAlerts(), api.getCatalog()]);
        setAlerts(a.alerts ?? []);
        setCatalog(new Map(c.map((x) => [x.alert_type, x])));
      } catch {
        /* keep empty state */
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-2">
      {loading ? (
        <p className="text-nkz-muted">{t('monitor.loading')}</p>
      ) : alerts.length === 0 ? (
        <p className="text-nkz-muted">{t('monitor.empty')}</p>
      ) : (
        <ul className="space-y-2">
          {alerts.map((a) => {
            const severity = String(val(a.severity) ?? 'low');
            const alertType = String(val(a.alertType) ?? '');
            const category = String(val(a.category) ?? '');
            return (
              <li key={a.id} className="flex items-center gap-3 rounded-lg border border-nkz-border bg-white p-3">
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEV[severity] ?? SEV.low}`}>{severity}</span>
                <span className="font-medium text-nkz-text">{catalog.get(alertType)?.name ?? alertType}</span>
                <span className="text-xs text-nkz-muted">{category}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

const App: React.FC = () => {
  const { t } = useTranslation('risk');
  const [tab, setTab] = useState<string>('monitor');

  return (
    <div className="p-6 space-y-4 max-w-5xl">
      <h1 className="text-2xl font-bold text-nkz-text">{t('title')}</h1>

      <div className="flex gap-1 border-b border-nkz-border">
        {TABS.map((x) => (
          <button
            key={x.id}
            onClick={() => setTab(x.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === x.id
                ? 'border-nkz-accent-base text-nkz-accent-base'
                : 'border-transparent text-nkz-muted hover:text-nkz-text'
            }`}
          >
            {t(x.label)}
          </button>
        ))}
      </div>

      {tab === 'monitor' && <Monitor />}
      {tab === 'config' && <ConfigTab />}
      {tab === 'integrations' && <IntegrationsTab />}
    </div>
  );
};

export default App;
