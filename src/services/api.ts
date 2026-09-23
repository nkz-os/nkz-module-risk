/**
 * API Client for Risk — canonical NKZClient + useAuth pattern.
 */
import { NKZClient, useAuth } from '@nekazari/sdk';

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

export interface Subscription {
  id: number;
  tenant_id: string;
  alert_type: string;
  is_active: boolean;
  user_threshold: number;
  channels: { email?: boolean; push?: boolean };
  entity_filters?: Record<string, unknown>;
}

export interface Channels {
  email: Record<string, unknown>;
  push: Record<string, unknown>;
  zulip: Record<string, unknown>;
  webhook: Record<string, unknown>;
  telegram: Record<string, unknown>;
}

export interface Webhook {
  id: string;
  name: string;
  url: string;
  secret?: string | null;
  min_severity: string;
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
    getSubscriptions: () => client.get<Subscription[]>('/subscriptions'),
    createSubscription: (d: Partial<Subscription>) => client.post<Subscription>('/subscriptions', d),
    updateSubscription: (id: number, d: Partial<Subscription>) => client.patch<Subscription>(`/subscriptions/${id}`, d),
    getChannels: () => client.get<Channels>('/channels'),
    putChannels: (d: Partial<Channels>) => client.put<Channels>('/channels', d),
    getWebhooks: () => client.get<Webhook[]>('/webhooks'),
    createWebhook: (d: { name: string; url: string; secret?: string; min_severity: string }) => client.post<Webhook>('/webhooks', d),
    deleteWebhook: (id: string) => client.delete<void>(`/webhooks/${id}`),
  };
}
