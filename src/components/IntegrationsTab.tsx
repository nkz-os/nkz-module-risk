/**
 * Integraciones — webhooks salientes (cubre N8N). Firma HMAC por target.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useModuleApi, Webhook } from '../services/api';

const App: React.FC = () => {
  const { t } = useTranslation('risk');
  const api = useModuleApi();
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [secret, setSecret] = useState('');
  const [minSeverity, setMinSeverity] = useState('medium');
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      setWebhooks(await api.getWebhooks());
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

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !url.trim()) return;
    setSaving(true);
    try {
      await api.createWebhook({
        name: name.trim(),
        url: url.trim(),
        secret: secret.trim() || undefined,
        min_severity: minSeverity,
      });
      setName('');
      setUrl('');
      setSecret('');
      await load();
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: string) => {
    await api.deleteWebhook(id);
    await load();
  };

  return (
    <div className="p-6 space-y-4 max-w-4xl">
      <form onSubmit={create} className="rounded-xl border border-nkz-border bg-white p-4 space-y-3">
        <h2 className="font-semibold text-nkz-text">{t('integrations.new')}</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('integrations.name')}
            className="border border-nkz-border rounded-lg px-3 py-2 text-sm"
            required
          />
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={t('integrations.url')}
            className="border border-nkz-border rounded-lg px-3 py-2 text-sm"
            required
          />
          <input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder={t('integrations.secret_optional')}
            className="border border-nkz-border rounded-lg px-3 py-2 text-sm"
          />
          <select
            value={minSeverity}
            onChange={(e) => setMinSeverity(e.target.value)}
            className="border border-nkz-border rounded-lg px-3 py-2 text-sm"
          >
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
            <option value="critical">critical</option>
          </select>
        </div>
        <button
          type="submit"
          disabled={saving}
          className="px-4 py-2 rounded-lg bg-nkz-accent-base text-white text-sm font-medium disabled:opacity-50"
        >
          {t('integrations.add')}
        </button>
      </form>

      <div className="rounded-xl border border-nkz-border bg-white divide-y divide-nkz-border/50">
        {loading ? (
          <p className="p-4 text-sm text-nkz-muted">{t('config.loading')}</p>
        ) : webhooks.length === 0 ? (
          <p className="p-4 text-sm text-nkz-muted">{t('integrations.empty')}</p>
        ) : (
          webhooks.map((w) => (
            <div key={w.id} className="flex items-center justify-between p-3">
              <div className="min-w-0">
                <p className="text-sm font-medium text-nkz-text truncate">{w.name}</p>
                <p className="text-xs text-nkz-muted truncate">{w.url}</p>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-nkz-muted">≥ {w.min_severity}</span>
                <button onClick={() => remove(w.id)} className="text-xs text-nkz-danger-strong hover:underline">
                  {t('integrations.remove')}
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default App;
