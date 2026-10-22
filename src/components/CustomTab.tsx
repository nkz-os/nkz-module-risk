/**
 * Tab "Custom" — editor de riesgos a medida con árbol de condiciones recursivo.
 * Expresa cualquier riesgo: AND/OR/COUNT>=N, operadores +between, agregaciones
 * temporales (sum/avg/min/max/delta) y duración (duration_minutes).
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useModuleApi, SourceCatalog, RiskCondition, RiskConditionGroup } from '../services/api';

const OPERATORS = ['<', '<=', '>', '>=', '==', '!=', 'in', 'not_in', 'between'];
const AGGREGATES = ['', 'sum', 'avg', 'min', 'max', 'delta'];
const SEVERITIES = ['low', 'medium', 'high', 'critical'];
const CATEGORIES = ['weather', 'agronomic', 'disease', 'pest', 'robotic', 'energy', 'sensor', 'crop'];
const LOGIC = ['AND', 'OR', 'COUNT'];

type Cond = {
  kind: 'cond';
  source: string;
  attribute: string;
  operator: string;
  valueText: string;
  durationText: string;
  aggregate: string;
  severity: string;
};
type Group = { kind: 'group'; op: string; minN: string; children: Node[] };
type Node = Cond | Group;

const newCond = (): Cond => ({
  kind: 'cond',
  source: 'weather',
  attribute: 'temp_avg',
  operator: '<',
  valueText: '0',
  durationText: '',
  aggregate: '',
  severity: 'high',
});
const newGroup = (): Group => ({ kind: 'group', op: 'AND', minN: '2', children: [newCond()] });

const parseValue = (op: string, text: string): number | string | (number | string)[] => {
  if (op === 'between') {
    return text.split(',').map((s) => s.trim()).filter(Boolean).map((p) => (Number.isNaN(Number(p)) ? p : Number(p)));
  }
  if (op === 'in' || op === 'not_in') {
    return text.split(',').map((s) => s.trim()).filter(Boolean);
  }
  const n = Number(text);
  return text.trim() !== '' && !Number.isNaN(n) ? n : text;
};

const toPayload = (n: Node): RiskCondition | RiskConditionGroup => {
  if (n.kind === 'cond') {
    const payload: RiskCondition = {
      source: n.source,
      attribute: n.attribute,
      operator: n.operator,
      value: parseValue(n.operator, n.valueText),
      severity: n.severity,
    };
    const dur = Number(n.durationText) || 0;
    if (dur > 0) payload.duration_minutes = dur;
    if (n.aggregate) payload.aggregate = n.aggregate;
    return payload;
  }
  const op = n.op === 'COUNT' ? `COUNT>=${Number(n.minN) || 1}` : n.op;
  const payload: RiskConditionGroup = {
    logical_operator: op,
    conditions: n.children.map(toPayload),
  };
  if (n.op === 'COUNT') payload.min_conditions = Number(n.minN) || 1;
  return payload;
};

const collectSources = (n: Node, acc: Set<string>): Set<string> => {
  if (n.kind === 'cond') acc.add(n.source);
  else n.children.forEach((c) => collectSources(c, acc));
  return acc;
};

// ── Mutación inmutable por path (relativo a root.children) ─────────────
function updateAt(nodes: Node[], path: number[], updater: (n: Node) => Node): Node[] {
  if (path.length === 0) return nodes;
  const [head, ...rest] = path;
  return nodes.map((n, i) => {
    if (i !== head) return n;
    if (rest.length === 0) return updater(n);
    return { ...(n as Group), children: updateAt((n as Group).children, rest, updater) };
  });
}
function insertAt(nodes: Node[], path: number[], node: Node): Node[] {
  if (path.length === 0) return [...nodes, node];
  const [head, ...rest] = path;
  return nodes.map((n, i) => {
    if (i !== head) return n;
    return { ...(n as Group), children: insertAt((n as Group).children, rest, node) };
  });
}
function removeAt(nodes: Node[], path: number[]): Node[] {
  if (path.length === 0) return nodes;
  const [head, ...rest] = path;
  if (rest.length === 0) return nodes.filter((_, i) => i !== head);
  return nodes.map((n, i) => {
    if (i !== head) return n;
    return { ...(n as Group), children: removeAt((n as Group).children, rest) };
  });
}

const inputCls = 'border border-nkz-border rounded-lg px-2 py-1.5 text-sm w-full';

function ConditionRow({
  cond,
  sources,
  update,
  remove,
}: {
  cond: Cond;
  sources: SourceCatalog;
  update: (patch: Partial<Cond>) => void;
  remove: () => void;
}) {
  const { t } = useTranslation('risk');
  const attrs = sources[cond.source]?.attributes ?? [];
  const needsAggregate = Number(cond.durationText) > 0;

  return (
    <div className="rounded-lg border border-nkz-border/60 p-2 space-y-2 bg-nkz-bg-secondary/40">
      <div className="grid grid-cols-2 md:grid-cols-6 gap-2 items-center">
        <select value={cond.source} onChange={(e) => update({ source: e.target.value, attribute: '' })} className={inputCls}>
          {Object.entries(sources).map(([s, meta]) => (
            <option key={s} value={s}>{meta.label}</option>
          ))}
        </select>
        <select value={cond.attribute} onChange={(e) => update({ attribute: e.target.value })} className={inputCls}>
          <option value="">…</option>
          {attrs.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <select value={cond.operator} onChange={(e) => update({ operator: e.target.value })} className={inputCls}>
          {OPERATORS.map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>
        <input
          value={cond.valueText}
          onChange={(e) => update({ valueText: e.target.value })}
          placeholder={cond.operator === 'between' ? 'lo, hi' : cond.operator === 'in' || cond.operator === 'not_in' ? 'a,b,c' : 'valor'}
          className={inputCls}
        />
        <input value={cond.durationText} onChange={(e) => update({ durationText: e.target.value })} placeholder={t('custom.duration')} type="number" min="0" className={inputCls} />
        <select value={cond.severity} onChange={(e) => update({ severity: e.target.value })} className={inputCls}>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>
      <div className="flex items-center gap-2">
        {needsAggregate && (
          <select value={cond.aggregate} onChange={(e) => update({ aggregate: e.target.value })} className={inputCls + ' w-auto'}>
            {AGGREGATES.map((a) => (
              <option key={a} value={a}>{a || t('custom.noAggregate')}</option>
            ))}
          </select>
        )}
        <button onClick={remove} className="ml-auto text-sm text-nkz-danger-strong hover:underline">
          ✕ {t('custom.remove')}
        </button>
      </div>
    </div>
  );
}

function GroupEditor({
  group,
  path,
  sources,
  setTree,
}: {
  group: Group;
  path: number[];
  sources: SourceCatalog;
  setTree: React.Dispatch<React.SetStateAction<Group>>;
}) {
  const { t } = useTranslation('risk');

  const mutate = (fn: (children: Node[]) => Node[]) => setTree((root) => ({ ...root, children: fn(root.children) }));

  const updateSelf = (patch: Partial<Group>) => {
    if (path.length === 0) setTree((root) => ({ ...root, ...patch }));
    else mutate((children) => updateAt(children, path, (n) => ({ ...n, ...patch } as Node)));
  };
  const updateChild = (i: number, patch: Partial<Cond> | Partial<Group>) =>
    mutate((children) => updateAt(children, [...path, i], (n) => ({ ...n, ...patch } as Node)));
  const removeChild = (i: number) => mutate((children) => removeAt(children, [...path, i]));
  const addChild = (node: Node) => mutate((children) => insertAt(children, path, node));

  return (
    <div className="rounded-lg border border-nkz-border p-3 space-y-2">
      <div className="flex items-center gap-2">
        <select value={group.op} onChange={(e) => updateSelf({ op: e.target.value })} className={inputCls + ' w-auto'}>
          {LOGIC.map((l) => (
            <option key={l} value={l}>{l}</option>
          ))}
        </select>
        {group.op === 'COUNT' && (
          <span className="text-sm text-nkz-muted">
            {t('custom.atLeast')}
            <input value={group.minN} onChange={(e) => updateSelf({ minN: e.target.value })} type="number" min="1" className={inputCls + ' w-16 mx-1 inline'} />
            {t('custom.conditions')}
          </span>
        )}
      </div>

      {group.children.map((child, i) =>
        child.kind === 'group' ? (
          <GroupEditor key={i} group={child} path={[...path, i]} sources={sources} setTree={setTree} />
        ) : (
          <ConditionRow
            key={i}
            cond={child}
            sources={sources}
            update={(patch) => updateChild(i, patch)}
            remove={() => removeChild(i)}
          />
        ),
      )}

      <div className="flex gap-2">
        <button onClick={() => addChild(newCond())} className="px-2 py-1 rounded-lg border border-nkz-border text-xs text-nkz-muted hover:bg-nkz-bg-secondary">
          + {t('custom.condition')}
        </button>
        <button onClick={() => addChild(newGroup())} className="px-2 py-1 rounded-lg border border-nkz-border text-xs text-nkz-muted hover:bg-nkz-bg-secondary">
          + {t('custom.group')}
        </button>
      </div>
    </div>
  );
}

const App: React.FC = () => {
  const { t } = useTranslation('risk');
  const api = useModuleApi();

  const [sources, setSources] = useState<SourceCatalog>({});
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('agronomic');
  const [tree, setTree] = useState<Group>(newGroup());
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

  const submit = async () => {
    if (!name.trim()) return;
    const dataSources = [...collectSources(tree, new Set())];
    setSaving(true);
    try {
      const res = await api.createCustomRisk({
        name: name.trim(),
        description,
        category,
        data_sources: dataSources,
        conditions: toPayload(tree) as RiskConditionGroup,
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

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('custom.name')} className={inputCls} />
        <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t('custom.description')} className={inputCls} />
        <select value={category} onChange={(e) => setCategory(e.target.value)} className={inputCls}>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </div>

      <GroupEditor group={tree} path={[]} sources={sources} setTree={setTree} />

      <button
        onClick={submit}
        disabled={saving || !name.trim()}
        className="px-4 py-1.5 rounded-lg bg-nkz-accent-base text-white text-sm font-medium disabled:opacity-50"
      >
        {saving ? t('config.saving') : t('custom.create')}
      </button>
    </div>
  );
};

export default App;
