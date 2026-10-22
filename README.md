# nkz-module-risk

Federated **agronomic risk and alert** module for the Nekazari platform.

It consolidates the risk system into a single FIWARE Smart Data Model entity (`Alert`),
with a **declarative condition engine** that lets you configure any risk from the control
panel without writing code. It publishes alerts to Orion-LD and delivers them over multiple
channels (email, push, Zulip, webhook/N8N, Telegram).

---

## What it does

- **Evaluates** agronomic risks (climate, water/soil, disease, pests) per parcel, reading the
  platform's canonical sources (Orion-LD + TimescaleDB).
- **Publishes** one `Alert` entity per detected risk (canonical bus — never writes telemetry
  directly).
- **Delivers** alerts over the channels configured per tenant.
- **Exposes** a control panel (UI) with:
  - **Monitor** — active alerts in real time.
  - **Catalog** — available risks.
  - **Custom** — visual editor for custom risks (condition tree).
  - **Integrations** — channels (email/push/Zulip/webhook/Telegram).

## Where alerts surface

- **Global bell** in the host top navigation (high/critical badge + dropdown).
- **Parcel details panel** — an «Alerts» section with that parcel's risks.
- **Monitor tab** of the module.

---

## Architecture

```
sources (Orion-LD + TimescaleDB)
        │  SOURCE_FETCHERS (registry)
        ▼
model_config (declarative condition tree)
        │  ThresholdRiskModel / epidemiological models
        ▼
Alert (SDM) ──► Orion-LD ──► channels (email / push / zulip / webhook / telegram)
```

A single `Alert` entity (SDM `dataModel.Alert`) with `category`, `alertType`, `severity`,
`refEntity` (the parcel), `probabilityScore`, `confidence`, `evaluationData` and `observedAt`.

---

## Risk catalog (24)

> The 21 in the table below live in `risk.alert_catalog`. The 3 disease entries marked with an
> asterisk (*) are **epidemiological models** evaluated by `disease_evaluator` (not threshold-
> configurable; oídio and mildiu stay as models on purpose).

### Climate — `weather`

| alert_type | Risk | Condition |
|---|---|---|
| heat_stress | Heat stress | temp_avg > 35 °C **and** humidity < 30 % (high); > 32 °C (medium) |
| hail_proxy | Hail (proxy) | Δpressure > 5 hPa **and** Δtemp > 8 °C over 6 h |
| wind_damage | Wind damage | wind > 60 km/h (high); > 40 km/h (medium) |
| sunburn | Fruit sunburn | radiation > 850 W/m² **and** T > 33 °C (high); > 700 W/m² (medium) |
| fire_30_30_30 | Fire (30-30-30) | 2 of 3: T > 30 °C, RH < 30 %, wind > 30 km/h |
| weather_alert | Weather alert | AEMET / MeteoAlarm over the parcel |

### Water & soil — `agronomic`

| alert_type | Risk | Condition |
|---|---|---|
| frost | Frost | temp_min < 0 °C (model) |
| water_stress | Water stress | CWSI → deficit → soil < 15-20 % cascade (model) |
| waterlogging | Root asphyxia | soil moisture > 45 % sustained 48 h |
| saline_stress | Saline stress | EC > 4 dS/m (high); > 2.5 (medium) — *requires EC data* |
| nutrient_leaching | Nutrient leaching | rain > 40 mm/24 h (high); > 25 mm (medium) |
| root_thermal_stress | Root thermal stress | soil T > 28 °C or < 5 °C (high); 25-28 or 5-8 (medium) |
| gdd_pest | Degree-day pest | GDD accumulation (model) |
| wind_spray | Spray drift | wind (model) |
| spray_suitability | Spray suitability | suitable meteorology (model) |

### Disease — `disease`

| alert_type | Risk | Condition |
|---|---|---|
| botrytis | Botrytis (rot) | leaf wetness > 12 h **and** T 15-20 °C (high); > 8 h (medium) |
| rust_yellow | Yellow/brown rust | wetness > 8 h **and** T 10-15 °C (high); T 5-10 °C (medium) |
| fire_blight | Fire blight | T > 18 °C **and** wetness > 4 h (high); T > 15 °C (medium) |
| oidio_gubler* | Powdery mildew (Gubler-Thomas) | epidemiological model |
| mildiu_goidanich* | Downy mildew (Magarey) | epidemiological model |
| alternaria* | Alternaria (Tom-Cast) | epidemiological model |

### Pests — `pest`

| alert_type | Risk | Condition |
|---|---|---|
| red_spider | Red spider mite | T > 30 °C **and** RH < 40 % (high); T > 25 °C (medium) |
| fruit_fly | Fruit fly | T between 16-32 °C (high); 12-16 °C (medium) |
| aphids | Aphids | T 20-25 °C **and** NDVI > 0.6 (high); T 15-20 °C (medium) |

---

## Condition DSL (custom risks)

A custom risk is a catalog row with `model_type = "threshold"` and a declarative
`model_config`:

```jsonc
// LEAF (condition)
{
  "source": "weather",            // source (see source table)
  "attribute": "temp_avg",        // flattened attribute
  "operator": "< | <= | > | >= | == | != | in | not_in | between",
  "value": 0,                     // scalar, or [lo, hi] for between/in
  "severity": "high",             // low | medium | high | critical
  "duration_minutes": 0,          // time window
  "aggregate": null               // null | sum | avg | min | max | delta (over the series)
}

// GROUP (recursive)
{
  "logical_operator": "AND | OR | COUNT>=N",
  "min_conditions": 2,            // for COUNT>=N (N of M)
  "conditions": [ /* leaves or subgroups */ ]
}
```

Semantics:

- `between [lo, hi]` → matches when `lo ≤ value ≤ hi`.
- `duration_minutes > 0` without `aggregate` → **sustained** condition (≥60 % window coverage).
- `duration_minutes > 0` with `aggregate` → aggregates the series and compares:
  - `sum` — accumulated rain (e.g. > 40 mm/24 h).
  - `delta` — max−min swing (e.g. pressure drop).
  - `avg` / `min` / `max` — mean, minimum, maximum of the window.
- `COUNT>=N` → matches when at least N children match (AND = COUNT>=M, OR = COUNT>=1).

---

## Data sources

| Source | Attributes (flattened) | Origin |
|---|---|---|
| `weather` | temp_min, temp_max, temp_avg, humidity, precip_mm, eto_mm, wind_speed_ms, wind_direction_deg, solar_rad_w_m2, pressure_hpa, soil_moisture_0_10cm, soil_moisture_10_40cm, gdd_accumulated | `WeatherObserved` + `WeatherForecast` (Orion-LD) |
| `soil` | texture, awc, field_capacity, wilting_point, ec, temperature | `AgriSoil` / `AgriSoilExtended` |
| `ndvi` | ndvi, savi | `EOProduct` |
| `crop_health` | cwsi, compaction_risk_score, soil_water_ratio, vhi, vci, gdd_accumulated | `CropHealthAssessment` |
| `gdd` | gdd_season_total, days_accumulated | TimescaleDB |
| `leaf_wetness` | hours | derived (NHRH: hours of RH ≥ 90 %) |
| `weather_alerts` | (AEMET/MeteoAlarm list) | weather-api |
| `telemetry` | value | `telemetry_measurements` (device) |

> `soil.ec` and `soil.temperature` are read from the broker; if no data is present yet, the
> condition stays unevaluated (no invented value) and does not fire.

---

## How to add a risk

### From the panel (recommended)

1. Open the module → **Custom** tab.
2. Set name, description and category.
3. Build the tree: add conditions and/or groups, choose source → attribute → operator →
   value → severity, and optionally duration and aggregation.
4. Create the risk. It appears in the catalog and the worker evaluates it on the next cycle.

### Programmatic (API)

```bash
POST /api/risk/catalog/custom          # tenant-scoped (fixes the cross-tenant leak)
GET  /api/risk/catalog                 # active catalog (global + tenant custom)
GET  /api/risk/catalog/sources         # sources + attributes (for UI autocomplete)
GET  /api/risk/alerts                  # active tenant alerts (filterable category/severity)
```

---

## Development

```bash
# Backend (FastAPI)
cd backend
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q

# Frontend (Module Federation 2.0)
pnpm typecheck
pnpm build:module
```

- **Backend test suite**: `backend/tests/` (condition engine, catalog, dispatcher, publisher).
- **Deploy**: GHCR image + digest bump in `gitops-config/overlays/modules/risk/` (ArgoCD);
  the frontend is published via OIDC on push to `main`.
- **Platform rules**: a single `Alert` entity; telemetry writes flow through Orion-LD (never
  direct writes); `hasAgriParcel | refAgriParcel` as the per-parcel relationship; NGSI-LD
  temporal properties (`observedAt`) go as a bare ISO string, not a `Property`.
