/**
 * Configuración de canales y suscripciones de alertas.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useModuleApi, CatalogItem, Subscription, Channels } from '../services/api';

const App: React.FC = () => {
  const { t } = useTranslation('risk');
  const api = useModuleApi();

  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [subs, setSubs] = useState<Map<string, Subscription>>(new Map());
  const [channels, setChannels] = useState<Channels | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const [cat, s, ch] = await Promise.all([
        api.getCatalog(),
        api.getSubscriptions(),
        api.getChannels(),
      ]);
      setCatalog(cat);
      setSubs(new Map(s.map((x) => [x.alert_type, x])));
      setChannels(ch);
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

  const saveChannels = async () => {
    if (!channels) return;
    setSaving(true);
    try {
      const saved = await api.putChannels(channels);
      setChannels(saved);
    } finally {
      setSaving(false);
    }
  };

  const setChannel = (channel: keyof Channels, patch: Record<string, unknown>) => {
    setChannels((prev) => (prev ? { ...prev, [channel]: { ...prev[channel], ...patch } } : prev));
  };

  if (loading) return <p className="p-6 text-nkz-muted">{t('config.loading')}</p>;

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      {/* ── Canales ── */}
      <section className="rounded-xl border border-nkz-border bg-white p-4 space-y-4">
        <h2 className="font-semibold text-nkz-text">{t('config.channels')}</h2>

        <ChannelRow
          label={t('config.email')}
          enabled={Boolean((channels?.email as any)?.enabled)}
          onToggle={(v) => setChannel('email', { enabled: v })}
        >
          <label className="text-xs text-nkz-muted">{t('config.email_to')}</label>
          <input
            type="email"
            value={String((channels?.email as any)?.to ?? '')}
            onChange={(e) => setChannel('email', { to: e.target.value })}
            placeholder="you@example.com"
            className="w-full border border-nkz-border rounded-lg px-3 py-1.5 text-sm"
          />
        </ChannelRow>

        <ChannelRow
          label={t('config.push')}
          enabled={Boolean((channels?.push as any)?.enabled)}
          onToggle={(v) => setChannel('push', { enabled: v })}
        />

        <ChannelRow
          label={t('config.zulip')}
          enabled={Boolean((channels?.zulip as any)?.enabled)}
          onToggle={(v) => setChannel('zulip', { enabled: v })}
        >
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-nkz-muted">{t('config.zulip_stream')}</label>
              <input
                value={String((channels?.zulip as any)?.stream ?? 'alerts')}
                onChange={(e) => setChannel('zulip', { stream: e.target.value })}
                className="w-full border border-nkz-border rounded-lg px-3 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="text-xs text-nkz-muted">{t('config.zulip_topic')}</label>
              <input
                value={String((channels?.zulip as any)?.topic ?? 'notifications')}
                onChange={(e) => setChannel('zulip', { topic: e.target.value })}
                className="w-full border border-nkz-border rounded-lg px-3 py-1.5 text-sm"
              />
            </div>
          </div>
        </ChannelRow>

        <ChannelRow
          label={t('config.telegram')}
          enabled={Boolean((channels?.telegram as any)?.enabled)}
          onToggle={(v) => setChannel('telegram', { enabled: v })}
        />

        <button
          onClick={saveChannels}
          disabled={saving}
          className="px-4 py-2 rounded-lg bg-nkz-accent-base text-white text-sm font-medium disabled:opacity-50"
        >
          {saving ? t('config.saving') : t('config.save')}
        </button>
      </section>

      {/* ── Suscripciones ── */}
      <section className="rounded-xl border border-nkz-border bg-white p-4 space-y-2">
        <h2 className="font-semibold text-nkz-text">{t('config.subscriptions')}</h2>
        {catalog.length === 0 ? (
          <p className="text-sm text-nkz-muted">{t('config.empty')}</p>
        ) : (
          catalog.map((risk) => {
            const sub = subs.get(risk.alert_type);
            const active = sub?.is_active ?? false;
            return (
              <div
                key={risk.alert_type}
                className={`border rounded-lg p-3 ${active ? 'border-green-200 bg-nkz-success-soft/40' : 'border-nkz-border'}`}
              >
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-nkz-text">{risk.name}</p>
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
                      className="w-full accent-green-600"
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

const ChannelRow: React.FC<{
  label: string;
  enabled: boolean;
  onToggle: (v: boolean) => void;
  children?: React.ReactNode;
}> = ({ label, enabled, onToggle, children }) => (
  <div className="border-b border-nkz-border/50 pb-3 last:border-0">
    <div className="flex items-center justify-between mb-1">
      <span className="text-sm font-medium text-nkz-text">{label}</span>
      <button
        onClick={() => onToggle(!enabled)}
        className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${enabled ? 'bg-green-600' : 'bg-nkz-bg-secondary'}`}
      >
        <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${enabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
      </button>
    </div>
    {enabled && children}
  </div>
);

export default App;
