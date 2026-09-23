/**
 * API Client for Risk — uses the canonical NKZClient + useAuth pattern.
 */
import { NKZClient, useAuth } from '@nekazari/sdk';

// No hardcoded domain: read VITE_API_URL at build time (placeholder fallback).
const API_BASE = (import.meta as any).env?.VITE_API_URL || 'https://your-api-domain';

export interface AlertItem {
  id: string;
  category?: { value?: string } | string;
  alertType?: { value?: string } | string;
  severity?: { value?: string } | string;
  status?: { value?: string } | string;
}

export interface CatalogItem {
  alert_type: string;
  name: string;
  description?: string | null;
  category: string;
  model_type?: string | null;
}

export function useModuleApi() {
  const { getToken, getTenantId } = useAuth();

  const client = new NKZClient({
    baseUrl: `${API_BASE}/api/risk`,
    getToken,
    getTenantId,
  });

  return {
    getAlerts: () => client.get<{ alerts: AlertItem[]; count: number }>('/alerts'),
    getCatalog: () => client.get<CatalogItem[]>('/catalog'),
  };
}
