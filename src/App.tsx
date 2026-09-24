/**
 * Risk module main page — tabs: Monitor · Catalog · Custom · Integrations.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ShieldAlert } from 'lucide-react';
import { useModuleApi, AlertItem, CatalogItem } from './services/api';
import ConfigTab from './components/ConfigTab';
import IntegrationsTab from './components/IntegrationsTab';
import CustomTab from './components/CustomTab';

const SEV: Record<string, string> = {
  critical: 'bg-nkz-danger-soft text-nkz-danger-strong',
  high: 'bg-nkz-warning-soft text-nkz-warning-strong',
  medium: 'bg-nkz-info-soft text-nkz-info-strong',
  low: 'text-nkz-text-muted',
};

const SEV_DOT: Record<string, string> = {
  critical: 'bg-nkz-danger-strong',
  high: 'bg-nkz-warning',
  medium: 'bg-nkz-info',
  low: 'bg-nkz-text-muted',
};

const SEV_ORDER = ['critical', 'high', 'medium', 'low'] as const;

const val = (x: unknown): unknown =>
  x && typeof x === 'object' && 'value' in (x as Record<string, unknown>)
    ? (x as { value: unknown }).value
    : x;

const refObj = (x: unknown): string => {
  if (x && typeof x === 'object' && 'object' in (x as Record<string, unknown>)) {
    return String((x as { object: unknown }).object ?? '');
  }
  return String(x ?? '');
};

const shortId = (urn?: string): string => (urn ? urn.split(':').pop() ?? urn : '');

const TABS = [
  { id: 'monitor', label: 'monitor.tab' },
  { id: 'config', label: 'config.tab' },
  { id: 'custom', label: 'custom.tab' },
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

  const counts = useMemo(() => {
    const acc: Record<string, number> = { critical: 0, high: 0, medium: 0, low: 0 };
    for (const a of alerts) {
      const s = String(val(a.severity) ?? 'low');
      acc[s] = (acc[s] ?? 0) + 1;
    }
    return acc;
  }, [alerts]);

  if (loading) return <p className="text-nkz-text-muted">{t('monitor.loading')}</p>;

  return (
    <div className="space-y-4">
      {/* Resumen por severidad */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {SEV_ORDER.map((s) => (
          <div key={s} className="flex items-center gap-3 rounded-xl border border-nkz-border bg-nkz-surface-raised p-3">
            <span className={`h-3 w-3 rounded-full ${SEV_DOT[s]}`} />
            <div>
              <p className="text-2xl font-bold text-nkz-text-primary leading-none">{counts[s] ?? 0}</p>
              <p className="text-xs text-nkz-text-muted capitalize">{s}</p>
            </div>
          </div>
        ))}
      </div>

      {alerts.length === 0 ? (
        <div className="rounded-xl border border-nkz-border bg-nkz-surface-raised p-8 text-center">
          <ShieldAlert className="w-8 h-8 mx-auto text-nkz-text-muted mb-2" />
          <p className="text-nkz-text-muted">{t('monitor.empty')}</p>
        </div>
      ) : (
        <ul className="space-y-2">
          {alerts.map((a) => {
            const severity = String(val(a.severity) ?? 'low');
            const alertType = String(val(a.alertType) ?? '');
            const category = String(val(a.category) ?? '');
            const parcel = shortId(refObj(a.refEntity));
            return (
              <li
                key={a.id}
                className="flex items-center gap-3 rounded-xl border border-nkz-border bg-nkz-surface-raised p-3"
              >
                <span className={`h-2.5 w-2.5 rounded-full flex-shrink-0 ${SEV_DOT[severity] ?? SEV_DOT.low}`} />
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEV[severity] ?? SEV.low}`}>{severity}</span>
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-nkz-text-primary truncate">{catalog.get(alertType)?.name ?? alertType}</p>
                  <p className="text-xs text-nkz-text-muted">
                    {category}
                    {parcel ? ` · ${parcel}` : ''}
                  </p>
                </div>
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
    <div className="p-6 space-y-5 max-w-5xl">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold text-nkz-text-primary flex items-center gap-2">
          <ShieldAlert className="w-6 h-6 text-nkz-accent-base" />
          {t('title')}
        </h1>
        <p className="text-sm text-nkz-text-muted">{t('subtitle')}</p>
      </header>

      <nav className="flex gap-1 border-b border-nkz-border">
        {TABS.map((x) => (
          <button
            key={x.id}
            onClick={() => setTab(x.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === x.id
                ? 'border-nkz-accent-base text-nkz-accent-base'
                : 'border-transparent text-nkz-text-muted hover:text-nkz-text-primary'
            }`}
          >
            {t(x.label)}
          </button>
        ))}
      </nav>

      {tab === 'monitor' && <Monitor />}
      {tab === 'config' && <ConfigTab />}
      {tab === 'custom' && <CustomTab />}
      {tab === 'integrations' && <IntegrationsTab />}
    </div>
  );
};

export default App;
