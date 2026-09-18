"""Semantic checks on a well-formed scenario — failures become HTTP 422.

Structural rules (field presence, types, 24 hours, 1-3 notes) already ran in
`app.schemas.request` and would have produced a 400 before reaching here.
"""

from __future__ import annotations

import math

from app.core.errors import SemanticValidationError
from app.schemas.request import OptimizeEnergyRequest


def _require_finite(value: float, field: str) -> None:
    if not math.isfinite(value):
        raise SemanticValidationError(f"{field} must be a finite number", field=field)


def check_scenario(request: OptimizeEnergyRequest) -> None:
    """Raise `SemanticValidationError` if the scenario cannot describe a real system."""
    for entry in request.hours:
        _require_finite(entry.demand_kwh, f"hours[{entry.hour}].demand_kwh")
        _require_finite(entry.solar_kwh, f"hours[{entry.hour}].solar_kwh")
        _require_finite(
            entry.tariff_bdt_per_kwh, f"hours[{entry.hour}].tariff_bdt_per_kwh"
        )

    battery = request.battery
    for name in (
        "capacity_kwh",
        "initial_energy_kwh",
        "minimum_energy_kwh",
        "max_charge_kwh_per_hour",
        "max_discharge_kwh_per_hour",
    ):
        _require_finite(getattr(battery, name), f"battery.{name}")

    if battery.minimum_energy_kwh > battery.capacity_kwh:
        raise SemanticValidationError(
            "battery.minimum_energy_kwh cannot exceed battery.capacity_kwh",
            field="battery.minimum_energy_kwh",
        )
    if battery.initial_energy_kwh > battery.capacity_kwh:
        raise SemanticValidationError(
            "battery.initial_energy_kwh cannot exceed battery.capacity_kwh",
            field="battery.initial_energy_kwh",
        )
    if battery.initial_energy_kwh < battery.minimum_energy_kwh:
        raise SemanticValidationError(
            "battery.initial_energy_kwh cannot start below battery.minimum_energy_kwh",
            field="battery.initial_energy_kwh",
        )
