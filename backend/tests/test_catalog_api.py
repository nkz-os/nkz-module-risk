"""Tests del catálogo: validación del árbol de condiciones + fuentes."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.catalog import Condition, ConditionGroup, _validate_tree
from app.main import app


def test_validate_tree_ok():
    g = ConditionGroup(conditions=[
        Condition(source="weather", attribute="temp_min", operator="<", value=0, severity="critical"),
    ])
    _validate_tree(g, ["weather"])  # no raise


def test_validate_tree_rejects_unknown_source():
    g = ConditionGroup(conditions=[
        Condition(source="nope", attribute="x", operator="<", value=0),
    ])
    with pytest.raises(HTTPException):
        _validate_tree(g, ["nope"])


def test_validate_tree_rejects_undeclared_source():
    g = ConditionGroup(conditions=[
        Condition(source="weather", attribute="temp_min", operator="<", value=0),
    ])
    with pytest.raises(HTTPException):
        _validate_tree(g, ["soil"])  # weather no declarada en data_sources


def test_validate_tree_rejects_bad_operator():
    g = ConditionGroup(conditions=[
        Condition(source="weather", attribute="temp_min", operator="~", value=0),
    ])
    with pytest.raises(HTTPException):
        _validate_tree(g, ["weather"])


def test_validate_tree_nested():
    g = ConditionGroup(logical_operator="OR", conditions=[
        Condition(source="weather", attribute="temp_min", operator="<", value=0),
        ConditionGroup(logical_operator="AND", conditions=[
            Condition(source="weather", attribute="wind_speed_ms", operator=">", value=11),
            Condition(source="soil", attribute="awc", operator="<", value=15),
        ]),
    ])
    _validate_tree(g, ["weather", "soil"])  # no raise


def test_catalog_sources_endpoint():
    with TestClient(app) as c:
        r = c.get("/api/risk/catalog/sources")
        assert r.status_code == 200
        data = r.json()
        assert "weather" in data
        assert "soil" in data
        assert "temp_min" in data["weather"]["attributes"]


def test_validate_tree_accepts_between_aggregate_count():
    g = ConditionGroup(logical_operator="COUNT>=2", conditions=[
        Condition(source="weather", attribute="temp_avg", operator="between", value=[16, 32]),
        Condition(source="weather", attribute="precip_mm", operator=">", value=40,
                  duration_minutes=1440, aggregate="sum"),
        Condition(source="weather", attribute="pressure_hpa", operator=">", value=5,
                  duration_minutes=360, aggregate="delta"),
    ])
    _validate_tree(g, ["weather"])  # no raise


def test_validate_tree_rejects_between_without_list():
    g = ConditionGroup(conditions=[
        Condition(source="weather", attribute="temp_avg", operator="between", value=20),
    ])
    with pytest.raises(HTTPException):
        _validate_tree(g, ["weather"])


def test_validate_tree_rejects_bad_aggregate():
    g = ConditionGroup(conditions=[
        Condition(source="weather", attribute="precip_mm", operator=">", value=40,
                  aggregate="median"),
    ])
    with pytest.raises(HTTPException):
        _validate_tree(g, ["weather"])
