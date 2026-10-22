/**
 * Modern module entry — `export default defineModule({...})`.
 *
 * Preferred form per module-builder: emits dist/manifest.json (NKZ data
 * manifest for api-gateway CSP enforcement) from the `data` declaration.
 */
import { defineModule } from '@nekazari/module-kit';
import { lazy } from 'react';
import './i18n';
import { moduleSlots } from './slots';
import pkg from '../package.json';

const MainPage = lazy(() => import('./App'));

export default defineModule({
  id: 'risk',
  displayName: 'Risk',
  version: pkg.version,
  hostApiVersion: '^2.0.0',
  description: 'Risk — Nekazari Platform Module',
  accent: { base: '#3B82F6', soft: '#DBEAFE', strong: '#1D4ED8' },
  icon: 'puzzle',
  main: MainPage,
  route: '/risk',
  api: { basePath: '/api/risk' },
  slots: moduleSlots as never,
  data: {
    entities: ['Alert'],
    timeseries: [],
  },
});
