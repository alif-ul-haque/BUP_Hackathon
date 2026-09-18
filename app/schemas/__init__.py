"""Pydantic contracts for the GridWise API.

`request` and `response` mirror Sections 07 and 10 of the problem statement;
`directive` mirrors Section 04.
"""

from app.schemas.directive import (
    ADJUSTMENT_MODELS,
    BatteryAction,
    DirectiveInterpretation,
    DirectiveType,
    MaxGridWindowAdjustment,
    MinimumBatteryReserveAdjustment,
    NoChargeWindowAdjustment,
    NoDischargeWindowAdjustment,
    SolarReductionAdjustment,
    StructuredAdjustment,
)
from app.schemas.request import Battery, HourInput, OptimizeEnergyRequest
from app.schemas.response import HealthResponse, HourlyPlanEntry, OptimizeEnergyResponse

__all__ = [
    "ADJUSTMENT_MODELS",
    "Battery",
    "BatteryAction",
    "DirectiveInterpretation",
    "DirectiveType",
    "HealthResponse",
    "HourInput",
    "HourlyPlanEntry",
    "MaxGridWindowAdjustment",
    "MinimumBatteryReserveAdjustment",
    "NoChargeWindowAdjustment",
    "NoDischargeWindowAdjustment",
    "OptimizeEnergyRequest",
    "OptimizeEnergyResponse",
    "SolarReductionAdjustment",
    "StructuredAdjustment",
]
