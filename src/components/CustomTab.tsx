/**
 * Tab "Custom" — editor de riesgos a medida (árbol de condiciones).
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useModuleApi, SourceCatalog } from '../services/api';

const OPERATORS = ['<', '<=', '>', '>=', '==', '!=', 'in', 'not_in'];
const SEVERITIES = ['low', 'medium', 'high', 'critical'];

interface Cond {
  source: string;
  attribute: string;
  operator: string;
  valueText: string;
  severity: string;
}

const App: React.FC = () => {
  const { t } = useTranslation('risk');
  const api = useModuleApi();

  const [sources, setSources] = useState<SourceCatalog>({});
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [operator, setOperator] = useState<'AND' | 'OR'>('AND');
  const [conditions, setConditions] = useState<Cond[]>([
    { source: 'weather', attribute: 'temp_min', operator: '<', valueText: '0', severity: 'high' },
  ]);
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setSources(await api.getCatalogSources());
      } catch {
        /* keep empty */
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const add = () =>
    setConditions((c) => [
      ...c,
      { source: 'weather', attribute: '', operator: '<', valueText: '0', severity: 'medium' },
    ]);
  const remove = (i: number) => setConditions((c) => c.filter((_, j) => j !== i));
  const update = (i: number, patch: Partial<Cond>) =>
    setConditions((c) => c.map((cond, j) => (j === i ? { ...cond, ...patch } : cond)));

  const parseValue = (op: string, text: string): number | string | (number | string)[] => {
    if (op === 'in' || op === 'not_in') {
      return text.split(',').map((s) => s.trim()).filter(Boolean);
    }
    const n = Number(text);
    return text.trim() !== '' && !Number.isNaN(n) ? n : text;
  };

  const submit = async () => {
    if (!name.trim()) return;
    const dataSources = [...new Set(conditions.map((c) => c.source))];
    setSaving(true);
    try {
      const res = await api.createCustomRisk({
        name: name.trim(),
        description,
        data_sources: dataSources,
        conditions: {
          logical_operator: operator,
          conditions: conditions.map((c) => ({
            source: c.source,
            attribute: c.attribute,
            operator: c.operator,
            value: parseValue(c.operator, c.valueText),
            severity: c.severity,
          })),
        },
      });
      setDone(res.alert_type);
      setName('');
      setDescription('');
    } catch {
      setDone(null);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      {done && (
        <div className="rounded-lg border border-nkz-success bg-nkz-success-soft p-3 text-sm text-nkz-success-strong">
          {t('custom.created')}: <code className="font-mono">{done}</code>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('custom.name')}
          className="border border-nkz-border rounded-lg px-3 py-2 text-sm"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t('custom.description')}
          className="border border-nkz-border rounded-lg px-3 py-2 text-sm"
        />
      </div>

      <div className="flex items-center gap-3">
        <span className="text-sm text-nkz-muted">{t('custom.logic')}</span>
        <select
          value={operator}
          onChange={(e) => setOperator(e.target.value as 'AND' | 'OR')}
          className="border border-nkz-border rounded-lg px-2 py-1.5 text-sm"
        >
          <option value="AND">AND</option>
          <option value="OR">OR</option>
        </select>
      </div>

      {conditions.map((c, i) => (
        <div key={i} className="grid grid-cols-2 md:grid-cols-6 gap-2 items-center">
          <select
            value={c.source}
            onChange={(e) => update(i, { source: e.target.value, attribute: '' })}
            className="border border-nkz-border rounded-lg px-2 py-1.5 text-sm"
          >
            {Object.entries(sources).map(([s, meta]) => (
              <option key={s} value={s}>{meta.label}</option>
            ))}
          </select>
          <select
            value={c.attribute}
            onChange={(e) => update(i, { attribute: e.target.value })}
            className="border border-nkz-border rounded-lg px-2 py-1.5 text-sm"
          >
            <option value="">…</option>
            {(sources[c.source]?.attributes ?? []).map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
          <select
            value={c.operator}
            onChange={(e) => update(i, { operator: e.target.value })}
            className="border border-nkz-border rounded-lg px-2 py-1.5 text-sm"
          >
            {OPERATORS.map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
          <input
            value={c.valueText}
            onChange={(e) => update(i, { valueText: e.target.value })}
            placeholder={c.operator === 'in' || c.operator === 'not_in' ? 'a,b,c' : 'valor'}
            className="border border-nkz-border rounded-lg px-2 py-1.5 text-sm"
          />
          <select
            value={c.severity}
            onChange={(e) => update(i, { severity: e.target.value })}
            className="border border-nkz-border rounded-lg px-2 py-1.5 text-sm"
          >
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <button
            onClick={() => remove(i)}
            className="text-sm text-nkz-danger-strong hover:underline"
          >
            ✕
          </button>
        </div>
      ))}

      <div className="flex gap-2">
        <button
          onClick={add}
          className="px-3 py-1.5 rounded-lg border border-nkz-border text-sm text-nkz-muted hover:bg-nkz-bg-secondary"
        >
          + {t('custom.condition')}
        </button>
        <button
          onClick={submit}
          disabled={saving || !name.trim()}
          className="px-4 py-1.5 rounded-lg bg-nkz-accent-base text-white text-sm font-medium disabled:opacity-50"
        >
          {saving ? t('config.saving') : t('custom.create')}
        </button>
      </div>
    </div>
  );
};

export default App;
