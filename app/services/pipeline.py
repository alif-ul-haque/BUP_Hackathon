"""End-to-end processing flow (Problem Statement Section 03).

    notes -> LLM interpreter -> directive validator -> optimizer
          -> schedule validator -> response

The route handler owns HTTP; this module owns the order of the stages.
"""

from __future__ import annotations

import logging

from app.core.errors import OptimizationError
from app.schemas.directive import DirectiveType
from app.schemas.request import OptimizeEnergyRequest
from app.schemas.response import OptimizeEnergyResponse
from app.services import interpreter, optimizer, schedule_validator
from app.services.constraints import compile_directives
from app.services.directive_validator import validate_interpretations

logger = logging.getLogger(__name__)


def run(request: OptimizeEnergyRequest) -> OptimizeEnergyResponse:
    # 1. LLM reads the notes. Output is untrusted.
    raw_interpretations = interpreter.interpret_notes(request.operator_notes)

    # 2. Guardrails turn it into exactly one trusted entry per note (Section 08).
    interpretations = validate_interpretations(
        raw_interpretations, note_count=len(request.operator_notes)
    )

    # 3. Directives become per-hour numbers (Section 05.3).
    compiled = compile_directives(request, interpretations)
    applied_count = sum(
        1
        for entry in interpretations
        if entry.directive_type is not DirectiveType.NO_OP
    )
    logger.info(
        "scenario=%s notes=%d applied_directives=%d",
        request.scenario_id,
        len(request.operator_notes),
        applied_count,
    )

    # 4. Optimizer schedules against those numbers (Sections 05 and 09).
    plan = optimizer.solve(request, compiled)

    # 5. Final replay before anything leaves the service (Section 11.3).
    violations = schedule_validator.validate_schedule(request, compiled, plan)
    if violations:
        logger.error(
            "scenario=%s produced an invalid schedule: %s",
            request.scenario_id,
            violations[:5],
        )
        raise OptimizationError(
            f"Produced schedule failed replay validation ({len(violations)} violation(s))"
        )

    return OptimizeEnergyResponse.build(
        scenario_id=request.scenario_id,
        directive_interpretation=interpretations,
        hourly_plan=plan,
        hours=request.hours,
        plan_summary=optimizer.plan_summary(compiled, applied_count),
    )
