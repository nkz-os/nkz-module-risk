"""Ciclo de vida de las Alert: active → resolved | expired | dismissed.

Sin esto una Alert que dejaba de cumplirse quedaba `active` para siempre: el
worker solo publicaba por encima del umbral y nunca cerraba nada.

* `resolved`  — el modelo evalúa por debajo del umbral.
* `expired`   — nadie la ha vuelto a evaluar en su TTL (sin datos, riesgo
  desactivado, parcela borrada). Red de seguridad, no el camino normal.
* `dismissed` — la cierra el usuario. El worker no la reabre mientras la
  condición siga; cuando la condición termina pasa a `resolved` y la próxima
  vez que aparezca es un aviso nuevo.

notifications solo despacha `active`, así que ningún cambio de estado avisa.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

PUBLISH = "publish"
SKIP = "skip"
RESOLVE = "resolve"
NONE = "none"

# TTL por categoría (horas). Configurable por entrada del catálogo con
# `model_config.alert_ttl_hours`.
_TTL_BY_CATEGORY = {"weather": 12, "pest": 48, "disease": 48}
_TTL_DEFAULT = 24

_PAGE = 500


def decide(prev_status: Optional[str], score: float, threshold: float) -> str:
    """Qué hacer con la Alert de (riesgo, parcela) tras una evaluación."""
    if score >= threshold:
        return SKIP if prev_status == "dismissed" else PUBLISH
    if prev_status in ("active", "dismissed"):
        return RESOLVE
    return NONE


def ttl_hours(category: Optional[str], model_config: Optional[Dict[str, Any]]) -> float:
    override = (model_config or {}).get("alert_ttl_hours")
    if override:
        return float(override)
    return _TTL_BY_CATEGORY.get(category or "", _TTL_DEFAULT)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_expired(observed_at: Optional[str], ttl_h: float, now: datetime) -> bool:
    dt = _parse(observed_at)
    if dt is None:
        return False
    return (now - dt).total_seconds() > ttl_h * 3600


def _value(attr: Any) -> Any:
    return attr.get("value") if isinstance(attr, dict) else attr


def load_existing(orion) -> Dict[str, Dict[str, Any]]:
    """Alert del tenant indexadas por id: estado, categoría, tipo y observedAt."""
    out: Dict[str, Dict[str, Any]] = {}
    offset = 0
    while True:
        page = orion.query_entities(type="Alert", limit=_PAGE, offset=offset) or []
        for e in page:
            out[e["id"]] = {
                "status": _value(e.get("status")) or "active",
                "category": _value(e.get("category")),
                "alert_type": _value(e.get("alertType")),
                "observed_at": _value(e.get("observedAt")),
            }
        if len(page) < _PAGE:
            return out
        offset += _PAGE


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def mark(orion, alert_id: str, status: str, now: datetime) -> None:
    """Cambia el estado y sella `<estado>At`. Append: crea el atributo si falta."""
    orion.append_entity_attrs(alert_id, {
        "status": {"type": "Property", "value": status},
        f"{status}At": {"type": "Property", "value": _iso(now)},
    })


def sweep_expired(
    orion,
    existing: Dict[str, Dict[str, Any]],
    touched: Set[str],
    catalog_config: Dict[str, Dict[str, Any]],
    now: datetime,
) -> int:
    """Caduca las `active` que esta ejecución no tocó y superan su TTL."""
    expired = 0
    for alert_id, info in existing.items():
        if info["status"] != "active" or alert_id in touched:
            continue
        ttl = ttl_hours(info["category"], catalog_config.get(info["alert_type"] or ""))
        if is_expired(info["observed_at"], ttl, now):
            mark(orion, alert_id, "expired", now)
            expired += 1
    return expired
