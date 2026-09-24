/**
 * ParcelAlertsSlot — panel de contexto del visor: avisos de la entidad seleccionada.
 *
 * Sustituye al ExampleSlot del template. Cuando el usuario selecciona una parcela
 * (o entidad) en el mapa, muestra sus Alert activos.
 */
import React, { useEffect, useState } from 'react';
import { useViewer, useTranslation } from '@nekazari/sdk';
import { SlotShell } from '@nekazari/viewer-kit';
import { ShieldCheck } from 'lucide-react';
import { useModuleApi, AlertItem, CatalogItem } from '../../services/api';

const moduleAccent = { base: '#3B82F6', soft: '#DBEAFE', strong: '#1D4ED8' };

const SEV: Record<string, string> = {
  critical: 'bg-nkz-danger-soft text-nkz-danger-strong',
  high: 'bg-nkz-warning-soft text-nkz-warning-strong',
  medium: 'bg-nkz-info-soft text-nkz-info-strong',
  low: 'text-nkz-text-muted',
};

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

const matchesEntity = (refEntity: unknown, entityId: string | null | undefined): boolean => {
  if (!entityId) return false;
  const ref = refObj(refEntity);
  if (ref === entityId) return true;
  const short = entityId.includes(':') ? entityId.split(':').pop() ?? entityId : entityId;
  return ref.endsWith(short);
};

export const ParcelAlertsSlot: React.FC<{ className?: string }> = ({ className }) => {
  const { t } = useTranslation('risk');
  const api = useModuleApi();
  const { selectedEntityId } = useViewer();
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [catalog, setCatalog] = useState<Map<string, CatalogItem>>(new Map());

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [a, c] = await Promise.all([api.getAlerts(), api.getCatalog()]);
        if (cancelled) return;
        setCatalog(new Map(c.map((x) => [x.alert_type, x])));
        setAlerts((a.alerts ?? []).filter((x) => matchesEntity(x.refEntity, selectedEntityId)));
      } catch {
        /* keep empty */
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedEntityId]);

  return (
    <SlotShell moduleId="risk" title={t('context.title')} accent={moduleAccent} className={className}>
      {alerts.length === 0 ? (
        <div className="flex items-center gap-2 text-sm text-nkz-text-muted py-2">
          <ShieldCheck className="w-4 h-4 flex-shrink-0 text-nkz-success" />
          <span>{t('context.empty')}</span>
        </div>
      ) : (
        <ul className="space-y-2">
          {alerts.map((a) => {
            const severity = String(val(a.severity) ?? 'low');
            const alertType = String(val(a.alertType) ?? '');
            const category = String(val(a.category) ?? '');
            return (
              <li key={a.id} className="flex items-center gap-2">
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEV[severity] ?? SEV.low}`}>
                  {severity}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="text-sm font-medium text-nkz-text-primary block truncate">
                    {catalog.get(alertType)?.name ?? alertType}
                  </span>
                  <span className="text-xs text-nkz-text-muted">{category}</span>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </SlotShell>
  );
};

export default ParcelAlertsSlot;
