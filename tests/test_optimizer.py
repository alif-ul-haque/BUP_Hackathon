"""Optimizer correctness against the 10 public sample cases.

`test_api.py` exercises the full HTTP pipeline, but with the interpreter stub
(all-no_op) that proves only schema acceptance, not directive correctness.
These tests bypass the LLM entirely and feed each sample case's own
ground-truth `directive_interpretation` straight through the same
`compile_directives` -> `optimizer.solve` -> `schedule_validator.validate_schedule`
path production code uses, then check the returned plan replays clean and its
cost matches the organizer reference closely (both are true LP optima for the
same problem, so cost should match almost exactly even if the exact hour-by-hour
schedule differs — Section 11: "no byte-for-byte matching").
"""

from __future__ import annotations

from typing import Any

from app.schemas.directive import BatteryAction, DirectiveInterpretation
from app.schemas.request import OptimizeEnergyRequest
from app.services import optimizer, schedule_validator
from app.services.constraints import compile_directives

COST_TOLERANCE_BDT = 1.0


def _ground_truth_directives(case: dict[str, Any]) -> list[DirectiveInterpretation]:
    return [
        DirectiveInterpretation.model_validate(entry)
        for entry in case["expected_output"]["directive_interpretation"]
    ]


def test_every_public_sample_case_optimizes_to_a_valid_plan(sample_cases):
    for case in sample_cases:
        request = OptimizeEnergyRequest.model_validate(case["input"])
        directives = _ground_truth_directives(case)
        compiled = compile_directives(request, directives)

        plan = optimizer.solve(request, compiled)

        violations = schedule_validator.validate_schedule(request, compiled, plan)
        assert not violations, f"{case['id']} produced an invalid plan: {violations}"


def test_every_public_sample_case_reaches_reference_cost(sample_cases):
    for case in sample_cases:
        request = OptimizeEnergyRequest.model_validate(case["input"])
        directives = _ground_truth_directives(case)
        compiled = compile_directives(request, directives)

        plan = optimizer.solve(request, compiled)

        tariff_by_hour = {entry.hour: entry.tariff_bdt_per_kwh for entry in request.hours}
        cost = sum(entry.grid_kwh * tariff_by_hour[entry.hour] for entry in plan)
        reference_cost = case["expected_output"]["total_cost_bdt"]

        diff = abs(cost - reference_cost)
        assert diff <= COST_TOLERANCE_BDT, (
            f"{case['id']}: cost {cost} vs reference {reference_cost} (diff {diff})"
        )


def test_zero_directives_baseline_is_valid(sample_cases):
    """No operator notes apply: optimizer must still return a valid schedule."""
    case = sample_cases[0]
    request = OptimizeEnergyRequest.model_validate(case["input"])
    compiled = compile_directives(request, interpretations=[])

    plan = optimizer.solve(request, compiled)

    violations = schedule_validator.validate_schedule(request, compiled, plan)
    assert not violations, f"baseline (no directives) plan invalid: {violations}"


def test_schedule_validator_catches_a_broken_plan(sample_cases):
    """Sanity check that the replay validator actually rejects bad plans."""
    case = sample_cases[0]
    request = OptimizeEnergyRequest.model_validate(case["input"])
    directives = _ground_truth_directives(case)
    compiled = compile_directives(request, directives)

    plan = optimizer.solve(request, compiled)
    # Break energy-balance/idle consistency directly (validate_assignment is off,
    # so this bypasses HourlyPlanEntry's own construction-time checks on purpose).
    object.__setattr__(plan[0], "battery_action", BatteryAction.IDLE)
    object.__setattr__(plan[0], "battery_kwh", 5.0)

    violations = schedule_validator.validate_schedule(request, compiled, plan)
    assert violations, "validator should have rejected the mutated plan"
