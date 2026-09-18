"""Schedule optimization (Problem Statement Sections 05 and 09).

Builds an OR-Tools GLOP linear program, per hour h:

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

This is a pure LP (no integer/binary variables): simultaneously charging and
discharging is never cost-optimal, so a tiny throughput penalty on the
objective is enough to steer the solver away from that degenerate tie without
ever changing the true optimal grid cost.
"""

from __future__ import annotations

import logging
import math

from ortools.linear_solver import pywraplp

from app.core.config import get_settings
from app.core.errors import OptimizationError
from app.schemas.directive import BatteryAction, HORIZON_HOURS
from app.schemas.request import OptimizeEnergyRequest
from app.schemas.response import HourlyPlanEntry, round_output
from app.services.constraints import CompiledDirectives

logger = logging.getLogger(__name__)

#: Tiny objective penalty on simultaneous charge+discharge throughput. Breaks
#: LP ties so the solver reports a single clean action per hour instead of an
#: equal-cost but wasteful charge-and-discharge-at-once tie. Small enough to
#: never change the true optimal grid cost.
THROUGHPUT_PENALTY = 1e-6

#: Net charge/discharge below this is reported as idle.
ZERO_EPS = 1e-6

_STATUS_NAMES = {
    pywraplp.Solver.OPTIMAL: "OPTIMAL",
    pywraplp.Solver.FEASIBLE: "FEASIBLE",
    pywraplp.Solver.INFEASIBLE: "INFEASIBLE",
    pywraplp.Solver.UNBOUNDED: "UNBOUNDED",
    pywraplp.Solver.ABNORMAL: "ABNORMAL",
    pywraplp.Solver.NOT_SOLVED: "NOT_SOLVED",
}


def solve(
    request: OptimizeEnergyRequest,
    compiled: CompiledDirectives,
) -> list[HourlyPlanEntry]:
    """Return the cost-minimal 24-entry schedule for this scenario."""
    hours = request.hours_in_order()
    battery = request.battery
    demand = [entry.demand_kwh for entry in hours]
    tariff = [entry.tariff_bdt_per_kwh for entry in hours]
    capacity = battery.capacity_kwh
    initial_energy = battery.initial_energy_kwh

    solver = pywraplp.Solver.CreateSolver("GLOP")
    if solver is None:
        raise OptimizationError("GLOP LP solver is unavailable in this OR-Tools build")

    settings = get_settings()
    solver.SetTimeLimit(int(settings.solver_time_limit_seconds * 1000))
    inf = solver.infinity()

    grid = [
        solver.NumVar(
            0,
            compiled.max_grid_kwh[h] if math.isfinite(compiled.max_grid_kwh[h]) else inf,
            f"grid_{h}",
        )
        for h in range(HORIZON_HOURS)
    ]
    solar_used = [
        solver.NumVar(0, max(compiled.effective_solar_kwh[h], 0.0), f"solar_used_{h}")
        for h in range(HORIZON_HOURS)
    ]
    charge = [
        solver.NumVar(
            0, battery.max_charge_kwh_per_hour if compiled.charge_allowed[h] else 0.0,
            f"charge_{h}",
        )
        for h in range(HORIZON_HOURS)
    ]
    discharge = [
        solver.NumVar(
            0,
            battery.max_discharge_kwh_per_hour if compiled.discharge_allowed[h] else 0.0,
            f"discharge_{h}",
        )
        for h in range(HORIZON_HOURS)
    ]
    energy_after = [
        solver.NumVar(compiled.min_energy_kwh[h], capacity, f"energy_after_{h}")
        for h in range(HORIZON_HOURS)
    ]

    objective = solver.Objective()
    for h in range(HORIZON_HOURS):
        objective.SetCoefficient(grid[h], tariff[h])
        objective.SetCoefficient(charge[h], THROUGHPUT_PENALTY)
        objective.SetCoefficient(discharge[h], THROUGHPUT_PENALTY)
    objective.SetMinimization()

    for h in range(HORIZON_HOURS):
        # energy balance (9.5)
        solver.Add(
            grid[h] + solar_used[h] + discharge[h] == demand[h] + charge[h],
            f"energy_balance_{h}",
        )
        # battery transition (9.1)
        previous = energy_after[h - 1] if h > 0 else initial_energy
        solver.Add(
            energy_after[h] == previous + charge[h] - discharge[h],
            f"battery_transition_{h}",
        )

    # end-of-day neutrality (9.6)
    solver.Add(energy_after[HORIZON_HOURS - 1] == initial_energy, "end_of_day_neutrality")

    status = solver.Solve()
    if status != pywraplp.Solver.OPTIMAL:
        status_name = _STATUS_NAMES.get(status, str(status))
        logger.error("scenario=%s solver status=%s", request.scenario_id, status_name)
        raise OptimizationError(
            f"Optimizer could not find an optimal schedule (status={status_name})"
        )

    plan: list[HourlyPlanEntry] = []
    for h in range(HORIZON_HOURS):
        c = charge[h].solution_value()
        d = discharge[h].solution_value()
        net = c - d
        if net > ZERO_EPS:
            action, battery_kwh = BatteryAction.CHARGE, net
        elif net < -ZERO_EPS:
            action, battery_kwh = BatteryAction.DISCHARGE, -net
        else:
            action, battery_kwh = BatteryAction.IDLE, 0.0

        plan.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=round_output(max(grid[h].solution_value(), 0.0)),
                solar_used_kwh=round_output(max(solar_used[h].solution_value(), 0.0)),
                battery_action=action,
                battery_kwh=round_output(battery_kwh),
                battery_energy_after_kwh=round_output(energy_after[h].solution_value()),
            )
        )

    return plan


def plan_summary(compiled: CompiledDirectives, applied_count: int) -> str:
    """Short human-readable explanation for the response (Section 10.1)."""
    if applied_count == 0:
        return (
            "No operator directive affected the schedule. The optimizer minimized "
            "grid cost using available solar and battery flexibility."
        )
    return (
        f"Applied {applied_count} operator directive(s) to the 24-hour horizon and "
        "minimized grid cost subject to them, using available solar and battery "
        "flexibility while restoring the initial battery level by hour 23."
    )
