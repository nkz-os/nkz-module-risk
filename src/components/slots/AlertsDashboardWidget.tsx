/**
 * AlertsDashboardWidget — widget del dashboard: últimos avisos activos del tenant.
 *
 * Se registra en el slot `dashboard-widget` del host. Muestra los avisos activos
 * ordenados por severidad (crítico primero), hasta un máximo de 5.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from '@nekazari/sdk';
import { SlotShell } from '@nekazari/viewer-kit';
import { BellRing } from 'lucide-react';
import { useModuleApi, AlertItem, CatalogItem } from '../../services/api';

const moduleAccent = { base: '#3B82F6', soft: '#DBEAFE', strong: '#1D4ED8' };

const SEV: Record<string, string> = {
  critical: 'bg-nkz-danger-soft text-nkz-danger-strong',
  high: 'bg-nkz-warning-soft text-nkz-warning-strong',
  medium: 'bg-nkz-info-soft text-nkz-info-strong',
  low: 'text-nkz-text-muted',
};

const SEV_WEIGHT: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

const val = (x: unknown): unknown =>
  x && typeof x === 'object' && 'value' in (x as Record<string, unknown>)
    ? (x as { value: unknown }).value
    : x;

export const AlertsDashboardWidget: React.FC<{ className?: string }> = ({ className }) => {
  const { t } = useTranslation('risk');
  const api = useModuleApi();
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [catalog, setCatalog] = useState<Map<string, CatalogItem>>(new Map());

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [a, c] = await Promise.all([api.getAlerts(), api.getCatalog()]);
        if (cancelled) return;
        setCatalog(new Map(c.map((x) => [x.alert_type, x])));
        const sorted = [...(a.alerts ?? [])].sort(
          (x, y) => (SEV_WEIGHT[String(val(x.severity) ?? 'low')] ?? 9) - (SEV_WEIGHT[String(val(y.severity) ?? 'low')] ?? 9),
        );
        setAlerts(sorted.slice(0, 5));
      } catch {
        /* keep empty */
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <SlotShell moduleId="risk" title={t('widget.title')} accent={moduleAccent} className={className}>
      {alerts.length === 0 ? (
        <div className="flex items-center gap-2 text-sm text-nkz-text-muted py-2">
          <BellRing className="w-4 h-4 flex-shrink-0" />
          <span>{t('widget.empty')}</span>
        </div>
      ) : (
        <ul className="space-y-2">
          {alerts.map((a) => {
            const severity = String(val(a.severity) ?? 'low');
            const alertType = String(val(a.alertType) ?? '');
            return (
              <li key={a.id} className="flex items-center gap-2">
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEV[severity] ?? SEV.low}`}>
                  {severity}
                </span>
                <span className="text-sm text-nkz-text-primary truncate">
                  {catalog.get(alertType)?.name ?? alertType}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </SlotShell>
  );
};

export default AlertsDashboardWidget;
