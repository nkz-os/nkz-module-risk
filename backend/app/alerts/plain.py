"""Textos llanos (es/eu/en), urgencia y acción de cada Alert de risk.

Funciones puras. Sin jerga ni unidades técnicas: el texto lo lee un agricultor.
"""
from __future__ import annotations

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


def _val(x) -> str:
    return str(getattr(x, "value", x) or "")


def _short(entity_id: str) -> str:
    return entity_id.rsplit(":", 1)[-1] if entity_id else ""


def _disease_name(evaluation_data: dict | None) -> str:
    data = evaluation_data or {}
    name = data.get("disease") or data.get("disease_name") or ""
    return str(name).strip()


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
    disease = _disease_name(evaluation_data)
    key = _key(_val(alert_type), _val(category), _val(severity), bool(disease))
    parcel = (parcel_name or "").strip() or "la parcela"
    out = {}
    for lang, (title, body) in _TEXTS[key].items():
        out[lang] = {
            "title": title.format(parcel=parcel, disease=disease),
            "body": body.format(parcel=parcel, disease=disease),
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
