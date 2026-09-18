"""Final replay of the produced schedule (Problem Statement Sections 09 and 11.3).

The last gate before the response leaves the service: it re-walks `hourly_plan`
hour by hour exactly the way the judge does, using only the plan itself plus the
same compiled directive numbers the optimizer built its model from. It does not
trust the optimizer's internal solver state — only what it actually returned.
"""

from __future__ import annotations

import logging
import math

from app.schemas.directive import HORIZON_HOURS, BatteryAction
from app.schemas.request import OptimizeEnergyRequest
from app.schemas.response import HourlyPlanEntry
from app.services.constraints import CompiledDirectives

logger = logging.getLogger(__name__)

#: Judge tolerance from Section 11.5.
TOLERANCE = 0.01


class ScheduleViolation(Exception):
    """The produced schedule breaks a rule the judge will replay."""


def validate_schedule(
    request: OptimizeEnergyRequest,
    compiled: CompiledDirectives,
    plan: list[HourlyPlanEntry],
) -> list[str]:
    """Return the list of violations found. Empty list means the plan replays clean."""
    errors: list[str] = []

    if len(plan) != HORIZON_HOURS:
        return [f"hourly_plan has {len(plan)} entries, expected {HORIZON_HOURS}"]

    by_hour = {entry.hour: entry for entry in plan}
    if set(by_hour) != set(range(HORIZON_HOURS)):
        return [f"hourly_plan must cover each hour 0..{HORIZON_HOURS - 1} exactly once"]

    hours = request.hours_in_order()
    demand_by_hour = {entry.hour: entry.demand_kwh for entry in hours}
    tariff_by_hour = {entry.hour: entry.tariff_bdt_per_kwh for entry in hours}
    battery = request.battery

    previous_energy = battery.initial_energy_kwh
    for h in range(HORIZON_HOURS):
        entry = by_hour[h]

        for field_name, value in (
            ("grid_kwh", entry.grid_kwh),
            ("solar_used_kwh", entry.solar_used_kwh),
            ("battery_kwh", entry.battery_kwh),
            ("battery_energy_after_kwh", entry.battery_energy_after_kwh),
        ):
            if not math.isfinite(value):
                errors.append(f"hour {h}: {field_name} is not finite ({value})")
            elif value < -TOLERANCE:
                errors.append(f"hour {h}: {field_name} is negative ({value})")

        if entry.battery_action is BatteryAction.IDLE and abs(entry.battery_kwh) > TOLERANCE:
            errors.append(f"hour {h}: idle but battery_kwh={entry.battery_kwh}")

        charge_amt = entry.charge_kwh
        discharge_amt = entry.discharge_kwh

        max_charge = battery.max_charge_kwh_per_hour if compiled.charge_allowed[h] else 0.0
        max_discharge = (
            battery.max_discharge_kwh_per_hour if compiled.discharge_allowed[h] else 0.0
        )
        if charge_amt > max_charge + TOLERANCE:
            errors.append(f"hour {h}: charge {charge_amt} exceeds limit {max_charge}")
        if discharge_amt > max_discharge + TOLERANCE:
            errors.append(
                f"hour {h}: discharge {discharge_amt} exceeds limit {max_discharge}"
            )

        effective_solar = max(compiled.effective_solar_kwh[h], 0.0)
        if entry.solar_used_kwh > effective_solar + TOLERANCE:
            errors.append(
                f"hour {h}: solar_used_kwh {entry.solar_used_kwh} exceeds effective "
                f"solar {effective_solar}"
            )

        max_grid = compiled.max_grid_kwh[h]
        if math.isfinite(max_grid) and entry.grid_kwh > max_grid + TOLERANCE:
            errors.append(f"hour {h}: grid_kwh {entry.grid_kwh} exceeds cap {max_grid}")

        expected_after = previous_energy + charge_amt - discharge_amt
        if abs(entry.battery_energy_after_kwh - expected_after) > TOLERANCE:
            errors.append(
                f"hour {h}: battery_energy_after_kwh {entry.battery_energy_after_kwh} "
                f"!= expected {expected_after}"
            )

        min_energy = compiled.min_energy_kwh[h]
        if entry.battery_energy_after_kwh < min_energy - TOLERANCE:
            errors.append(
                f"hour {h}: battery energy {entry.battery_energy_after_kwh} below "
                f"minimum reserve {min_energy}"
            )
        if entry.battery_energy_after_kwh > battery.capacity_kwh + TOLERANCE:
            errors.append(
                f"hour {h}: battery energy {entry.battery_energy_after_kwh} exceeds "
                f"capacity {battery.capacity_kwh}"
            )

        balance_lhs = entry.grid_kwh + entry.solar_used_kwh + discharge_amt
        balance_rhs = demand_by_hour[h] + charge_amt
        if abs(balance_lhs - balance_rhs) > TOLERANCE:
            errors.append(
                f"hour {h}: energy balance violated ({balance_lhs} != {balance_rhs})"
            )

        previous_energy = entry.battery_energy_after_kwh

    final_energy = by_hour[HORIZON_HOURS - 1].battery_energy_after_kwh
    if abs(final_energy - battery.initial_energy_kwh) > TOLERANCE:
        errors.append(
            f"end-of-day neutrality violated: final={final_energy}, "
            f"initial={battery.initial_energy_kwh}"
        )

    recalculated_grid = sum(entry.grid_kwh for entry in plan)
    recalculated_cost = sum(
        entry.grid_kwh * tariff_by_hour[entry.hour] for entry in plan
    )
    recalculated_peak = max(entry.grid_kwh for entry in plan)
    logger.debug(
        "scenario=%s recalculated grid=%.4f cost=%.4f peak=%.4f",
        request.scenario_id,
        recalculated_grid,
        recalculated_cost,
        recalculated_peak,
    )

    return errors
