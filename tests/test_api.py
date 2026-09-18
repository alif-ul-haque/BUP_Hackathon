"""API contract tests — Sections 06, 07, 10 and the parts of 11.3 the API owns."""

from __future__ import annotations

from typing import Any

import pytest

HORIZON = 24
TOLERANCE = 0.01


def test_health_returns_ok(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_optimize_returns_the_full_response_shape(client, scenario) -> None:
    response = client.post("/optimize-energy", json=scenario)
    assert response.status_code == 200, response.text

    body = response.json()
    assert set(body) == {
        "scenario_id",
        "directive_interpretation",
        "hourly_plan",
        "total_grid_kwh",
        "total_cost_bdt",
        "peak_grid_kwh",
        "plan_summary",
    }
    assert body["scenario_id"] == scenario["scenario_id"]
    assert isinstance(body["plan_summary"], str) and body["plan_summary"]


def test_one_interpretation_per_note_in_index_order(client, scenario) -> None:
    body = client.post("/optimize-energy", json=scenario).json()
    entries = body["directive_interpretation"]

    assert len(entries) == len(scenario["operator_notes"])
    assert [entry["note_index"] for entry in entries] == list(range(len(entries)))

    for entry in entries:
        assert set(entry) == {
            "note_index",
            "applies",
            "directive_type",
            "structured_adjustment",
            "explanation",
        }
        if entry["directive_type"] == "no_op":
            assert entry["applies"] is False
            assert entry["structured_adjustment"] is None
        else:
            assert entry["applies"] is True
            assert entry["structured_adjustment"] is not None


def test_hourly_plan_covers_every_hour_once(client, scenario) -> None:
    plan = client.post("/optimize-energy", json=scenario).json()["hourly_plan"]

    assert len(plan) == HORIZON
    assert [entry["hour"] for entry in plan] == list(range(HORIZON))

    for entry in plan:
        assert set(entry) == {
            "hour",
            "grid_kwh",
            "solar_used_kwh",
            "battery_action",
            "battery_kwh",
            "battery_energy_after_kwh",
        }
        assert entry["battery_action"] in {"charge", "discharge", "idle"}
        assert entry["grid_kwh"] >= 0
        assert entry["solar_used_kwh"] >= 0
        if entry["battery_action"] == "idle":
            assert entry["battery_kwh"] == 0


def test_totals_match_values_recalculated_from_the_plan(client, scenario) -> None:
    """Section 11.3: the three totals must agree with the returned plan."""
    body = client.post("/optimize-energy", json=scenario).json()
    plan = body["hourly_plan"]
    tariff = {entry["hour"]: entry["tariff_bdt_per_kwh"] for entry in scenario["hours"]}

    assert body["total_grid_kwh"] == pytest.approx(
        sum(entry["grid_kwh"] for entry in plan), abs=TOLERANCE
    )
    assert body["total_cost_bdt"] == pytest.approx(
        sum(entry["grid_kwh"] * tariff[entry["hour"]] for entry in plan), abs=TOLERANCE
    )
    assert body["peak_grid_kwh"] == pytest.approx(
        max(entry["grid_kwh"] for entry in plan), abs=TOLERANCE
    )


def test_every_public_sample_case_is_accepted(client, sample_cases) -> None:
    """The API must accept all ten organizer inputs without a schema rejection."""
    for case in sample_cases:
        response = client.post("/optimize-energy", json=case["input"])
        assert response.status_code == 200, f"{case['id']}: {response.text}"
        assert response.json()["scenario_id"] == case["input"]["scenario_id"]


# --- Section 06.1: 400 for malformed or structurally invalid requests ---


def test_malformed_json_returns_400(client) -> None:
    response = client.post(
        "/optimize-energy",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert "detail" in response.json()


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda s: s.pop("battery"), id="missing-battery"),
        pytest.param(lambda s: s.pop("scenario_id"), id="missing-scenario-id"),
        pytest.param(lambda s: s["hours"].pop(), id="23-hours"),
        pytest.param(
            lambda s: s["hours"].append(dict(s["hours"][0])), id="duplicate-hour"
        ),
        pytest.param(
            lambda s: s["hours"][5].update({"hour": 99}), id="hour-out-of-range"
        ),
        pytest.param(lambda s: s.update({"operator_notes": []}), id="zero-notes"),
        pytest.param(lambda s: s.update({"operator_notes": ["a"] * 4}), id="four-notes"),
        pytest.param(lambda s: s.update({"operator_notes": ["  "]}), id="blank-note"),
        pytest.param(
            lambda s: s["hours"][0].update({"demand_kwh": "lots"}), id="demand-not-a-number"
        ),
        pytest.param(
            lambda s: s["hours"][0].update({"demand_kwh": -5}), id="negative-demand"
        ),
    ],
)
def test_structurally_invalid_request_returns_400(client, scenario, mutate) -> None:
    mutate(scenario)
    response = client.post("/optimize-energy", json=scenario)
    assert response.status_code == 400, response.text


# --- Section 06.1: 422 for well-formed but semantically invalid requests ---


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initial_energy_kwh", 900.0),  # above capacity
        ("minimum_energy_kwh", 900.0),  # above capacity
        ("initial_energy_kwh", 10.0),  # below the base minimum
    ],
)
def test_impossible_battery_returns_422(
    client, scenario, field: str, value: Any
) -> None:
    scenario["battery"][field] = value
    response = client.post("/optimize-energy", json=scenario)
    assert response.status_code == 422, response.text
    assert "detail" in response.json()


# --- Section 06.1: 500 must stay controlled ---


def test_internal_failure_returns_500_without_leaking_details(
    client, scenario, monkeypatch
) -> None:
    from app.services import pipeline

    def boom(_request):
        raise RuntimeError("solver crashed with token sk-do-not-leak")

    monkeypatch.setattr(pipeline, "run", boom)
    response = client.post("/optimize-energy", json=scenario)

    assert response.status_code == 500
    assert response.json()["detail"]
    assert "sk-do-not-leak" not in response.text
    assert "Traceback" not in response.text


def test_unknown_request_fields_are_ignored(client, scenario) -> None:
    """Be permissive on input: an extra harness field must not fail the request."""
    scenario["harness_metadata"] = {"attempt": 1}
    assert client.post("/optimize-energy", json=scenario).status_code == 200
