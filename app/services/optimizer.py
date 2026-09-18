"""Schedule optimization (Problem Statement Sections 05 and 09).

STUB — OR-Tools is not wired up yet. `solve` below returns a *feasible but not
cost-optimal* baseline so the API can be exercised end to end: use the available
solar, buy the rest from the grid, leave the battery idle. Battery neutrality
holds trivially because the battery never moves.

The baseline ignores no_charge / no_discharge / reserve directives (an idle
battery satisfies all three) but cannot honour `max_grid_window`, so it is a
placeholder only.

To implement: build an OR-Tools model with, per hour h,
    grid[h], solar_used[h], charge[h], discharge[h], energy_after[h]
subject to
    grid[h] + solar_used[h] + discharge[h] == demand[h] + charge[h]   (9.5)
    0 <= solar_used[h] <= effective_solar[h]                          (9.4)
    energy_after[h] == energy_before[h] + charge[h] - discharge[h]    (9.1)
    min_energy[h] <= energy_after[h] <= capacity                      (9.2)
    charge[h] <= max_charge, discharge[h] <= max_discharge            (9.3)
    charge[h] == 0 where not charge_allowed[h]                        (5.3)
    discharge[h] == 0 where not discharge_allowed[h]                  (5.3)
    grid[h] <= max_grid[h]                                            (5.3)
    energy_after[23] == initial_energy                                (9.6)
minimizing SUM(grid[h] * tariff[h]).
"""

from __future__ import annotations

import logging

from app.schemas.directive import BatteryAction
from app.schemas.request import OptimizeEnergyRequest
from app.schemas.response import HourlyPlanEntry, round_output
from app.services.constraints import CompiledDirectives

logger = logging.getLogger(__name__)


def solve(
    request: OptimizeEnergyRequest,
    compiled: CompiledDirectives,
) -> list[HourlyPlanEntry]:
    """Return a 24-entry schedule. Placeholder: solar-first, battery idle."""
    logger.warning("OR-Tools optimizer not implemented yet — returning baseline plan.")

    energy = request.battery.initial_energy_kwh
    plan: list[HourlyPlanEntry] = []

    for entry in request.hours_in_order():
        solar_used = min(entry.demand_kwh, compiled.effective_solar_kwh[entry.hour])
        grid = entry.demand_kwh - solar_used

        plan.append(
            HourlyPlanEntry(
                hour=entry.hour,
                grid_kwh=round_output(max(grid, 0.0)),
                solar_used_kwh=round_output(solar_used),
                battery_action=BatteryAction.IDLE,
                battery_kwh=0.0,
                battery_energy_after_kwh=round_output(energy),
            )
        )

    return plan


def plan_summary(compiled: CompiledDirectives, applied_count: int) -> str:
    """Short human-readable explanation for the response (Section 10.1)."""
    if applied_count == 0:
        return (
            "No operator directive affected the schedule. Available solar is used "
            "first and the remaining demand is bought from the grid."
        )
    return (
        f"Applied {applied_count} operator directive(s) to the 24-hour horizon. "
        "Available solar is used first and the remaining demand is bought from the grid."
    )
