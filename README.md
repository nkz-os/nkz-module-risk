# nkz-module-risk

Módulo federado de **riesgos y avisos agronómicos** de la plataforma Nekazari.

Consolida el sistema de riesgos en una única entidad FIWARE Smart Data Model (`Alert`),
con un **motor de condiciones declarativo** que permite configurar cualquier riesgo desde
el panel de control, sin escribir código. Publica los avisos en Orion-LD y los entrega por
múltiples canales (email, push, Zulip, webhook/N8N, Telegram).

---

## Qué hace

- **Evalúa** riesgos agronómicos (clima, agua/suelo, enfermedades, plagas) por parcela,
  leyendo las fuentes canónicas de la plataforma (Orion-LD + TimescaleDB).
- **Publica** una entidad `Alert` por riesgo detectado (bus canónico, nunca escribe
  telemetría directamente).
- **Entrega** los avisos por los canales configurados por tenant.
- **Expone** un **panel de control** (UI) con:
  - **Monitor** — avisos activos en tiempo real.
  - **Catálogo** — riesgos disponibles.
  - **Custom** — editor visual de riesgos a medida (árbol de condiciones).
  - **Integraciones** — canales (email/push/Zulip/webhook/Telegram).

## Dónde se ven los avisos

- **Campana global** en la barra de navegación del host (badge high/critical + desplegable).
- **Panel de detalle de parcela** — sección «Avisos» con los riesgos de esa parcela.
- **Tab Monitor** del módulo.

---

## Arquitectura

```
fuentes (Orion-LD + TimescaleDB)
        │  SOURCE_FETCHERS (registry)
        ▼
model_config (árbol de condiciones declarativo)
        │  ThresholdRiskModel / modelos epidemiológicos
        ▼
Alert (SDM) ──► Orion-LD ──► canales (email / push / zulip / webhook / telegram)
```

Una única entidad `Alert` (SDM `dataModel.Alert`), con `category`, `alertType`, `severity`,
`refEntity` (la parcela), `probabilityScore`, `confidence`, `evaluationData` y `observedAt`.

---

## Catálogo de riesgos (24)

> Los 21 de la tabla viven en `risk.alert_catalog`. Los 3 de enfermedad con asterisco (*)
> son **modelos epidemiológicos** evaluados por `disease_evaluator` (no configurables por
> umbral; oídio y mildiu se mantienen como modelo a propósito).

### Clima — `weather`

| alert_type | Riesgo | Condición |
|---|---|---|
| heat_stress | Golpe de calor | temp_avg > 35 °C **y** humidity < 30 % (alta); > 32 °C (media) |
| hail_proxy | Granizo (proxy) | Δpresión > 5 hPa **y** Δtemp > 8 °C en 6 h |
| wind_damage | Daño por viento | viento > 60 km/h (alta); > 40 km/h (media) |
| sunburn | Insolación de fruto | radiación > 850 W/m² **y** T > 33 °C (alta); > 700 W/m² (media) |
| fire_30_30_30 | Incendio (30-30-30) | 2 de 3: T > 30 °C, HR < 30 %, viento > 30 km/h |
| weather_alert | Aviso meteorológico | AEMET / MeteoAlarm sobre la parcela |

### Agua y suelo — `agronomic`

| alert_type | Riesgo | Condición |
|---|---|---|
| frost | Helada | temp_min < 0 °C (modelo) |
| water_stress | Estrés hídrico | cascada CWSI → déficit → suelo < 15-20 % (modelo) |
| waterlogging | Asfixia radicular | humedad suelo > 45 % sostenida 48 h |
| saline_stress | Estrés salino | CE > 4 dS/m (alta); > 2,5 (media) — *requiere dato EC* |
| nutrient_leaching | Lixiviación | lluvia > 40 mm/24 h (alta); > 25 mm (media) |
| root_thermal_stress | Térmico radicular | T suelo > 28 °C o < 5 °C (alta); 25-28 o 5-8 (media) |
| gdd_pest | Plaga por grados-día | acumulación GDD (modelo) |
| wind_spray | Deriva en pulverización | viento (modelo) |
| spray_suitability | Idoneidad de pulverización | meteorología apta (modelo) |

### Enfermedades — `disease`

| alert_type | Riesgo | Condición |
|---|---|---|
| botrytis | Botrytis (pudrición) | mojado foliar > 12 h **y** T 15-20 °C (alta); > 8 h (media) |
| rust_yellow | Roya amarilla/parda | mojado > 8 h **y** T 10-15 °C (alta); T 5-10 °C (media) |
| fire_blight | Fuego bacteriano | T > 18 °C **y** mojado > 4 h (alta); T > 15 °C (media) |
| oidio_gubler* | Oídio (Gubler-Thomas) | modelo epidemiológico |
| mildiu_goidanich* | Mildiu (Magarey) | modelo epidemiológico |
| alternaria* | Alternaria (Tom-Cast) | modelo epidemiológico |

### Plagas — `pest`

| alert_type | Riesgo | Condición |
|---|---|---|
| red_spider | Araña roja | T > 30 °C **y** HR < 40 % (alta); T > 25 °C (media) |
| fruit_fly | Mosca de la fruta | T entre 16-32 °C (alta); 12-16 °C (media) |
| aphids | Pulgón | T 20-25 °C **y** NDVI > 0,6 (alta); T 15-20 °C (media) |

---

## DSL de condiciones (riesgos a medida)

Un riesgo a medida es una fila del catálogo con `model_type = "threshold"` y un
`model_config` declarativo:

```jsonc
// HOJA (condición)
{
  "source": "weather",            // fuente (ver tabla de fuentes)
  "attribute": "temp_avg",        // atributo aplanado
  "operator": "< | <= | > | >= | == | != | in | not_in | between",
  "value": 0,                     // escalar, o [lo, hi] para between/in
  "severity": "high",             // low | medium | high | critical
  "duration_minutes": 0,          // ventana temporal
  "aggregate": null               // null | sum | avg | min | max | delta (sobre la serie)
}

// GRUPO (recursivo)
{
  "logical_operator": "AND | OR | COUNT>=N",
  "min_conditions": 2,            // para COUNT>=N (N de M)
  "conditions": [ /* hojas o subgrupos */ ]
}
```

Semántica:

- `between [lo, hi]` → se cumple si `lo ≤ valor ≤ hi`.
- `duration_minutes > 0` sin `aggregate` → condición **sostenida** (≥60 % de cobertura en la ventana).
- `duration_minutes > 0` con `aggregate` → agrega la serie y compara:
  - `sum` — lluvia acumulada (p. ej. > 40 mm/24 h).
  - `delta` — amplitud máx−mín (p. ej. caída de presión).
  - `avg` / `min` / `max` — media, mínimo, máximo de la ventana.
- `COUNT>=N` → se cumple si al menos N hijos se cumplen (AND = COUNT>=M, OR = COUNT>=1).

---

## Fuentes de datos

| Fuente | Atributos (aplanados) | Origen |
|---|---|---|
| `weather` | temp_min, temp_max, temp_avg, humidity, precip_mm, eto_mm, wind_speed_ms, wind_direction_deg, solar_rad_w_m2, pressure_hpa, soil_moisture_0_10cm, soil_moisture_10_40cm, gdd_accumulated | `WeatherObserved` + `WeatherForecast` (Orion-LD) |
| `soil` | texture, awc, field_capacity, wilting_point, ec, temperature | `AgriSoil` / `AgriSoilExtended` |
| `ndvi` | ndvi, savi | `EOProduct` |
| `crop_health` | cwsi, compaction_risk_score, soil_water_ratio, vhi, vci, gdd_accumulated | `CropHealthAssessment` |
| `gdd` | gdd_season_total, days_accumulated | TimescaleDB |
| `leaf_wetness` | hours | derivada (NHRH: horas de HR ≥ 90 %) |
| `weather_alerts` | (lista AEMET/MeteoAlarm) | weather-api |
| `telemetry` | value | `telemetry_measurements` (dispositivo) |

> `soil.ec` y `soil.temperature` se leen del broker; si aún no hay dato, la condición
> queda sin evaluar (no se inventa un valor) y no dispara.

---

## Cómo añadir un riesgo

### Desde el panel (recomendado)

1. Abre el módulo → tab **Custom**.
2. Pon nombre, descripción y categoría.
3. Construye el árbol: añade condiciones y/o grupos, elige fuente → atributo → operador →
   valor → severidad, y opcionalmente duración y agregación.
4. Crea el riesgo. Aparece en el catálogo y el worker lo evalúa en el siguiente ciclo.

### Programático (API)

```bash
POST /api/risk/catalog/custom          # tenant-scoped (corrige la fuga cross-tenant)
GET  /api/risk/catalog                 # catálogo activo (global + custom del tenant)
GET  /api/risk/catalog/sources         # fuentes + atributos (para autocompletar la UI)
GET  /api/risk/alerts                  # Alert activos del tenant (filtrable category/severity)
```

---

## Desarrollo

```bash
# Backend (FastAPI)
cd backend
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q

# Frontend (Module Federation 2.0)
pnpm typecheck
pnpm build:module
```

- **Test suite backend**: `backend/tests/` (motor de condiciones, catálogo, dispatcher, publicador).
- **Deploy**: imagen GHCR + bump del digest en `gitops-config/overlays/modules/risk/` (ArgoCD);
  el frontend se publica vía OIDC en push a `main`.
- **Reglas de plataforma**: una sola entidad `Alert`; las escrituras de telemetría fluyen por
  Orion-LD (nunca escrituras directas); `hasAgriParcel | refAgriParcel` como relación por parcela;
  los temporales NGSI-LD (`observedAt`) van como string ISO desnudo, no como `Property`.
