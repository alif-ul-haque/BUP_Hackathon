"""Turns validated directives into per-hour numbers (Section 05.3).

This is the deterministic translation table the optimizer builds its model from
and the schedule validator replays against, so both sides read the directives
the same way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.schemas.directive import (
    HORIZON_HOURS,
    DirectiveInterpretation,
    DirectiveType,
    MaxGridWindowAdjustment,
    MinimumBatteryReserveAdjustment,
    SolarReductionAdjustment,
)
from app.schemas.request import OptimizeEnergyRequest


@dataclass
class CompiledDirectives:
    """Hour-indexed constraints after every applicable directive is applied."""

    effective_solar_kwh: list[float]
    min_energy_kwh: list[float]
    charge_allowed: list[bool] = field(default_factory=list)
    discharge_allowed: list[bool] = field(default_factory=list)
    max_grid_kwh: list[float] = field(default_factory=list)


def compile_directives(
    request: OptimizeEnergyRequest,
    interpretations: list[DirectiveInterpretation],
) -> CompiledDirectives:
    """Apply the Section 05.3 effects of every non-no_op directive.

    Overlapping directives of the same kind combine the strict way: the highest
    reserve, the lowest grid cap, the product of solar factors.
    """
    hours = request.hours_in_order()
    battery = request.battery

    compiled = CompiledDirectives(
        effective_solar_kwh=[entry.solar_kwh for entry in hours],
        min_energy_kwh=[battery.minimum_energy_kwh] * HORIZON_HOURS,
        charge_allowed=[True] * HORIZON_HOURS,
        discharge_allowed=[True] * HORIZON_HOURS,
        max_grid_kwh=[math.inf] * HORIZON_HOURS,
    )

    for entry in interpretations:
        if not entry.applies or entry.directive_type is DirectiveType.NO_OP:
            continue

        adjustment = entry.structured_adjustment
        if adjustment is None:  # unreachable after validation; kept as a guard
            continue

        for hour in adjustment.hours:
            if entry.directive_type is DirectiveType.SOLAR_REDUCTION:
                assert isinstance(adjustment, SolarReductionAdjustment)
                compiled.effective_solar_kwh[hour] *= adjustment.factor

            elif entry.directive_type is DirectiveType.MINIMUM_BATTERY_RESERVE:
                assert isinstance(adjustment, MinimumBatteryReserveAdjustment)
                compiled.min_energy_kwh[hour] = max(
                    compiled.min_energy_kwh[hour], adjustment.minimum_energy_kwh
                )

            elif entry.directive_type is DirectiveType.NO_CHARGE_WINDOW:
                compiled.charge_allowed[hour] = False

            elif entry.directive_type is DirectiveType.NO_DISCHARGE_WINDOW:
                compiled.discharge_allowed[hour] = False

            elif entry.directive_type is DirectiveType.MAX_GRID_WINDOW:
                assert isinstance(adjustment, MaxGridWindowAdjustment)
                compiled.max_grid_kwh[hour] = min(
                    compiled.max_grid_kwh[hour], adjustment.max_grid_kwh
                )

    return compiled
