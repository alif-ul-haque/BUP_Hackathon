"""Final replay of the produced schedule (Problem Statement Sections 09 and 11.3).

STUB — the full replay is not implemented yet. This is the last gate before the
response leaves the service: it re-walks `hourly_plan` hour by hour exactly the
way the judge does, so a schedule that breaks a rule is caught here rather than
in scoring.

To implement, check for every hour h:
    hourly_plan covers hours 0..23 exactly once            (11.3)
    every value is finite and non-negative                 (11.3)
    solar_used[h] <= effective_solar[h] + TOLERANCE        (9.4)
    grid[h] + solar_used[h] + discharge[h]
        == demand[h] + charge[h]  (within TOLERANCE)       (9.5)
    energy_after[h] == energy_before[h] +- battery_kwh     (9.1)
    min_energy[h] <= energy_after[h] <= capacity           (9.2)
    battery_kwh <= the matching hourly rate limit          (9.3)
    charge == 0 / discharge == 0 in forbidden windows      (5.3)
    grid[h] <= max_grid[h] + TOLERANCE                     (5.3)
    energy_after[23] == initial_energy                     (9.6)
"""

from __future__ import annotations

import logging

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
    logger.warning("Schedule replay validator not implemented yet — plan not verified.")
    return []
