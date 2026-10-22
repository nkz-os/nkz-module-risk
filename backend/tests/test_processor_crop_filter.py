"""Tests del filtro por cultivo del worker (resolución de crop_group)."""
from unittest.mock import MagicMock

from app.worker.processor import _parcel_crop_group


def _crop_entity(category):
    return {"id": "urn:ngsi-ld:AgriCrop:t:p:2026", "category": category}


def test_parcel_crop_group_resolves_category_dict():
    orion = MagicMock()
    orion.get_entity.return_value = _crop_entity(
        {"type": "Property", "value": "cereal"}
    )
    entity = {
        "id": "urn:ngsi-ld:AgriParcel:p",
        "hasAgriCrop": {
            "type": "Relationship",
            "object": "urn:ngsi-ld:AgriCrop:t:p:2026",
        },
    }
    assert _parcel_crop_group(orion, entity) == "cereal"


def test_parcel_crop_group_string_relationship():
    orion = MagicMock()
    orion.get_entity.return_value = _crop_entity("grapevine")
    entity = {
        "id": "urn:ngsi-ld:AgriParcel:p",
        "hasAgriCrop": "urn:ngsi-ld:AgriCrop:t:p:2026",
    }
    assert _parcel_crop_group(orion, entity) == "grapevine"


def test_parcel_crop_group_no_relationship():
    orion = MagicMock()
    entity = {"id": "urn:ngsi-ld:AgriParcel:p"}
    assert _parcel_crop_group(orion, entity) is None


def test_parcel_crop_group_get_entity_fails():
    orion = MagicMock()
    orion.get_entity.side_effect = RuntimeError("boom")
    entity = {
        "id": "urn:ngsi-ld:AgriParcel:p",
        "hasAgriCrop": "urn:ngsi-ld:AgriCrop:t:p:2026",
    }
    assert _parcel_crop_group(orion, entity) is None
