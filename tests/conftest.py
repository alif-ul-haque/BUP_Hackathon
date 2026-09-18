"""Shared fixtures: a test client and a valid 24-hour scenario to mutate."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PACK = REPO_ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


@pytest.fixture(scope="session")
def client() -> TestClient:
    # raise_server_exceptions=False so the 500 contract can be asserted instead of
    # the exception bubbling out of the test client.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def scenario() -> dict[str, Any]:
    """A minimal valid request body (Section 07.4)."""
    return {
        "scenario_id": "TEST-001",
        "operator_notes": [
            "Solar output will drop to about 20% from 1 PM to 3 PM.",
            "The cafeteria menu changes tomorrow.",
        ],
        "hours": [
            {
                "hour": hour,
                "demand_kwh": 100.0 + hour,
                "solar_kwh": 60.0 if 8 <= hour <= 16 else 0.0,
                "tariff_bdt_per_kwh": 12.0 if 18 <= hour <= 22 else 6.0,
            }
            for hour in range(24)
        ],
        "battery": {
            "capacity_kwh": 500.0,
            "initial_energy_kwh": 200.0,
            "minimum_energy_kwh": 50.0,
            "max_charge_kwh_per_hour": 100.0,
            "max_discharge_kwh_per_hour": 100.0,
        },
    }


@pytest.fixture
def sample_cases() -> list[dict[str, Any]]:
    """The organizers' public sample pack, if it sits next to the app."""
    if not SAMPLE_PACK.exists():
        pytest.skip("public sample case pack not present")
    with SAMPLE_PACK.open(encoding="utf-8") as handle:
        return copy.deepcopy(json.load(handle)["cases"])
