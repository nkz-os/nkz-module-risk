"""Textos llanos (es/eu/en), urgencia y acción de cada Alert de risk.

Funciones puras. Sin jerga ni unidades técnicas: el texto lo lee un agricultor.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

_HIGH = {"high", "critical"}
_WEATHER_TYPES = {
    "frost", "heat_stress", "hail_proxy", "wind_damage", "fire_30_30_30", "weather_alert",
}

# (title, body) por idioma; {parcel} y {disease} se sustituyen al construir.
_TEXTS: dict[str, dict[str, tuple[str, str]]] = {
    "disease_act": {
        "es": ("Riesgo de {disease} en {parcel}", "Hay que tratar pronto: condiciones favorables para {disease}."),
        "eu": ("{disease} arriskua {parcel} lursailean", "Laster tratatu behar da. Baldintza onak daude honentzat: {disease}."),
        "en": ("Risk of {disease} in {parcel}", "Treat soon: conditions favour {disease}."),
    },
    "disease_act_nameless": {
        "es": ("Riesgo de enfermedad en {parcel}", "Hay que tratar pronto: condiciones favorables para la enfermedad."),
        "eu": ("Gaixotasun arriskua {parcel} lursailean", "Laster tratatu behar da: gaixotasunerako baldintza onak daude."),
        "en": ("Risk of disease in {parcel}", "Treat soon: conditions favour disease."),
    },
    "disease_watch": {
        "es": ("Vigilar {disease} en {parcel}", "Condiciones algo favorables para {disease}; conviene vigilar."),
        "eu": ("Zaindu {disease} {parcel} lursailean", "Baldintza batzuk onak dira honentzat: {disease}. Zaintzea komeni da."),
        "en": ("Watch for {disease} in {parcel}", "Conditions are somewhat favourable to {disease}; keep an eye out."),
    },
    "disease_watch_nameless": {
        "es": ("Vigilar enfermedad en {parcel}", "Condiciones algo favorables para la enfermedad; conviene vigilar."),
        "eu": ("Zaindu gaixotasuna {parcel} lursailean", "Gaixotasunerako baldintza batzuk onak dira; zaintzea komeni da."),
        "en": ("Watch for disease in {parcel}", "Conditions are somewhat favourable to disease; keep an eye out."),
    },
    "pest": {
        "es": ("Riesgo de plaga en {parcel}", "Conviene revisar la parcela: la acumulación de calor favorece la plaga."),
        "eu": ("Izurrite arriskua {parcel} lursailean", "Lursaila aztertzea komeni da: bero metaketak izurritea errazten du."),
        "en": ("Pest risk in {parcel}", "Worth checking the plot: the build-up of warmth favours the pest."),
    },
    "crop_stress": {
        "es": ("La planta sufre en {parcel}", "Estrés detectado; mira la ficha de la parcela."),
        "eu": ("Landareak sufritzen du {parcel} lursailean", "Estresa antzeman da; begiratu lursailaren fitxa."),
        "en": ("The crop is struggling in {parcel}", "Stress detected; see the plot page."),
    },
    "frost": {
        "es": ("Helada prevista en {parcel}", "Protege lo sensible esta noche."),
        "eu": ("Izotza espero da {parcel} lursailean", "Babestu sentikorra den guztia gaur gauean."),
        "en": ("Frost expected in {parcel}", "Protect what is sensitive tonight."),
    },
    "heat_stress": {
        "es": ("Calor extremo en {parcel}", "Revisa el riego y protege los cultivos delicados."),
        "eu": ("Bero handiegia {parcel} lursailean", "Begiratu ureztatzea eta babestu laborantza delikatuak."),
        "en": ("Extreme heat in {parcel}", "Check irrigation and protect delicate crops."),
    },
    "hail_proxy": {
        "es": ("Posible granizo en {parcel}", "Protege lo que puedas antes de la tormenta."),
        "eu": ("Kazkabarra izan daiteke {parcel} lursailean", "Babestu ahal duzuna ekaitza baino lehen."),
        "en": ("Possible hail in {parcel}", "Protect what you can before the storm."),
    },
    "wind_damage": {
        "es": ("Viento fuerte en {parcel}", "Asegura lo que pueda moverse o romperse."),
        "eu": ("Haize gogorra {parcel} lursailean", "Ziurtatu mugi edo hautsi daitekeen guztia."),
        "en": ("Strong wind in {parcel}", "Secure anything that could move or break."),
    },
    "fire_30_30_30": {
        "es": ("Riesgo de incendio en {parcel}", "Calor, sequedad y viento: evita fuegos y chispas."),
        "eu": ("Sute arriskua {parcel} lursailean", "Beroa, lehortasuna eta haizea: saihestu suak eta txinparta."),
        "en": ("Fire risk in {parcel}", "Heat, dryness and wind: avoid fires and sparks."),
    },
    "weather_alert": {
        "es": ("Aviso meteorológico en {parcel}", "Hay un aviso del tiempo; mira la ficha de la parcela."),
        "eu": ("Eguraldi abisua {parcel} lursailean", "Eguraldi abisu bat dago; begiratu lursailaren fitxa."),
        "en": ("Weather warning in {parcel}", "There is a weather warning; see the plot page."),
    },
    "generic": {
        "es": ("Aviso en {parcel}", "Mira la ficha de la parcela."),
        "eu": ("Abisua {parcel} lursailean", "Begiratu lursailaren fitxa."),
        "en": ("Notice in {parcel}", "See the plot page."),
    },
}


# Aviso de helada por noche. Sin nombres de modelo ni umbrales. La mínima es la más
# fría de dos modelos, así que el texto dice "puede bajar", nunca "bajará".
# ASSUMPTION: los textos y días de la semana en eu son míos; revisar con hablante nativo.
_FROST_TITLE = {
    "es": "Posible helada en {parcel}",
    "eu": "Izotza izan daiteke {parcel} lursailean",
    "en": "Possible frost in {parcel}",
}
_FROST_BODY = {
    "frost_dawn": {
        "es": "Esta madrugada puede bajar a {tmin} °C.",
        "eu": "Gaur goizaldean {tmin} °C-ra jaitsi daiteke.",
        "en": "Early this morning it may drop to {tmin} °C.",
    },
    "frost_tonight": {
        "es": "Esta noche puede bajar a {tmin} °C.",
        "eu": "Gaur gauean {tmin} °C-ra jaitsi daiteke.",
        "en": "Tonight it may drop to {tmin} °C.",
    },
    "frost_night": {
        "es": "Noche del {weekday} {date}: puede bajar a {tmin} °C.",
        "eu": "{weekday} {date} gaua: {tmin} °C-ra jaitsi daiteke.",
        "en": "Night of {weekday} {date}: it may drop to {tmin} °C.",
    },
}
# Lunes = 0, como date.weekday().
_WEEKDAYS = {
    "es": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
    "eu": ("astelehena", "asteartea", "asteazkena", "osteguna", "ostirala", "larunbata", "igandea"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}


# Antes de esta hora local, «esta noche» se leería como la noche que aún está en curso.
_TONIGHT_FROM_HOUR = 6


def round_tmin(tmin: float) -> int:
    """Mínima tal y como la lee el agricultor: grados enteros, .5 hacia arriba."""
    return int(math.floor(float(tmin) + 0.5))


def frost_body_key(lead: int, local_hour: int | None) -> str:
    """Forma del texto. Sin hora conocida, la fechada, que nunca dice «esta noche» en falso."""
    if lead == 0:
        return "frost_dawn"
    if lead == 1 and local_hour is not None and local_hour >= _TONIGHT_FROM_HOUR:
        return "frost_tonight"
    return "frost_night"


def _frost_night_info(evaluation_data: dict | None):
    """(día D de la noche, antelación, mínima redondeada, hora local) o None si falta algo."""
    data = evaluation_data or {}
    try:
        # text_*: la noche que nombra el texto (a escalón severo, la severa).
        pre = "text" if "text_night" in data else "next"
        night = date.fromisoformat(str(data[f"{pre}_night"]))
        lead = int(data[f"{pre}_lead"] if pre == "next" else data["text_lead"])
        tmin = data[f"{pre}_tmin_c"]
        if isinstance(tmin, bool) or not math.isfinite(float(tmin)):
            return None
        hour = data.get("local_hour")
        return night, lead, round_tmin(tmin), hour if isinstance(hour, int) else None
    except (KeyError, TypeError, ValueError):
        return None


def _frost_plain(parcel: str, info) -> dict:
    night, lead, tmin, hour = info
    start = night - timedelta(days=1)  # la noche empieza el día anterior a D
    key = frost_body_key(lead, hour)
    out = {}
    for lang, title in _FROST_TITLE.items():
        out[lang] = {
            "title": title.format(parcel=parcel),
            "body": _FROST_BODY[key][lang].format(
                tmin=tmin,
                weekday=_WEEKDAYS[lang][start.weekday()],
                date=f"{start:%d/%m}",
            ),
        }
    return out


def _val(x) -> str:
    return str(getattr(x, "value", x) or "")


def _short(entity_id: str) -> str:
    return entity_id.rsplit(":", 1)[-1] if entity_id else ""


# ASSUMPTION: los nombres eu/en son equivalentes naturales míos; revisar con hablante nativo.
_DISEASE_NAMES: dict[str, dict[str, str]] = {
    "downy_mildew": {"es": "mildiu", "eu": "mihiu", "en": "downy mildew"},
    "powdery_mildew": {"es": "oídio", "eu": "hauts-izerdia", "en": "powdery mildew"},
    "apple_scab": {"es": "moteado del manzano", "eu": "sagarrondoko zikinkeria", "en": "apple scab"},
    "alternaria": {"es": "alternaria", "eu": "alternaria", "en": "alternaria"},
    "botrytis": {"es": "botritis", "eu": "botritisa", "en": "botrytis"},
    "rust_yellow": {"es": "roya amarilla", "eu": "ñabardura horia", "en": "yellow rust"},
    "fire_blight": {"es": "fuego bacteriano", "eu": "sute bakterianoa", "en": "fire blight"},
}


def _disease_names(evaluation_data: dict | None) -> dict[str, str] | None:
    """Nombre por idioma, o None (variante sin nombre) si falta o es un código."""
    data = evaluation_data or {}
    raw = str(data.get("disease") or data.get("disease_name") or "").strip()
    if not raw:
        return None
    known = _DISEASE_NAMES.get(raw.lower())
    if known:
        return known
    if "_" in raw:
        return None
    return {"es": raw, "eu": raw, "en": raw}


def _key(alert_type: str, category: str, severity: str, has_disease: bool) -> str:
    if alert_type == "crop_stress":
        return "crop_stress"
    if alert_type in _WEATHER_TYPES:
        return alert_type
    if category == "pest" or alert_type == "gdd_pest":
        return "pest"
    if category == "disease":
        base = "disease_act" if severity in _HIGH else "disease_watch"
        return base if has_disease else base + "_nameless"
    return "generic"


def plain_for(alert_type, category, severity, evaluation_data, parcel_name) -> dict:
    """Siempre devuelve {es, eu, en}, cada uno con title y body."""
    if _val(alert_type) == "frost":
        info = _frost_night_info(evaluation_data)
        if info is not None:
            return _frost_plain((parcel_name or "").strip() or "la parcela", info)
    names = _disease_names(evaluation_data)
    key = _key(_val(alert_type), _val(category), _val(severity), names is not None)
    parcel = (parcel_name or "").strip() or "la parcela"
    out = {}
    for lang, (title, body) in _TEXTS[key].items():
        out[lang] = {
            "title": title.format(parcel=parcel, disease=(names or {}).get(lang, "")),
            "body": body.format(parcel=parcel, disease=(names or {}).get(lang, "")),
        }
    return out


def urgency_for(category, severity, alert_type) -> str:
    """'vibrate' solo para enfermedad/plaga alta y meteo extrema; resto 'silent'."""
    cat, sev, typ = _val(category), _val(severity), _val(alert_type)
    if sev not in _HIGH:
        return "silent"
    if cat in ("disease", "pest") or typ in _WEATHER_TYPES:
        return "vibrate"
    return "silent"


def action_for(entity_id: str, entity_type: str) -> dict:
    if entity_type == "AgriParcel":
        return {"kind": "open_parcel", "params": {"parcelId": entity_id}}
    return {"kind": "none", "params": {}}
