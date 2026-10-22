# Setup guide

> For full documentation see [README.md](README.md).

## 1. Install dependencies

```bash
pnpm install
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## 2. Configure environment

The backend reads its configuration from environment variables (no hardcoded fallback):

| Variable | Required | Purpose |
|---|---|---|
| `POSTGRES_URL` | yes | Metadata DB (`risk.*` schema) |
| `ORION_LD_URL` | yes | Orion-LD context broker |
| `CONTEXT_URL` | yes | Platform NGSI-LD `@context` |
| `INTERNAL_SERVICE_SECRET` | yes | Guards `/internal/*` routes |
| `WEATHER_API_URL` | no | weather-api (weather alerts source) |

## 3. Run

```bash
# Backend (FastAPI, port 8000)
cd backend && .venv/bin/uvicorn app.main:app --reload

# Frontend (Vite dev)
pnpm dev
```

## 4. Test

```bash
cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/ -q
pnpm typecheck
```

## 5. Migrations

Canonical numbered SQL files in `backend/migrations/`. Apply against the `nekazari` database
(the `risk` schema is created by `000_risk_schema.sql`).

## 6. Deploy

- Backend/worker image → GHCR, then bump the digest in `gitops-config/overlays/modules/risk/`
  (ArgoCD `risk-module`).
- Frontend → OIDC publish on push to `main` (automatic MinIO upload + pointer flip).
