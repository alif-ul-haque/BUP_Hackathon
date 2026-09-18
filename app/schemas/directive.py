"""Directive types and structured adjustments (Problem Statement Section 04).

These models are the guardrail boundary described in Section 08: whatever the LLM
returns is untrusted until it parses cleanly into one of the models below.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HORIZON_HOURS = 24


class DirectiveType(str, Enum):
    """The only directive types the service is allowed to emit (Section 04.1)."""

    SOLAR_REDUCTION = "solar_reduction"
    MINIMUM_BATTERY_RESERVE = "minimum_battery_reserve"
    NO_CHARGE_WINDOW = "no_charge_window"
    NO_DISCHARGE_WINDOW = "no_discharge_window"
    MAX_GRID_WINDOW = "max_grid_window"
    NO_OP = "no_op"


class BatteryAction(str, Enum):
    """Allowed `battery_action` values in an hourly plan entry (Section 10.3)."""

    CHARGE = "charge"
    DISCHARGE = "discharge"
    IDLE = "idle"


def _validate_hours(hours: list[int]) -> list[int]:
    """Section 05.1: unique integers 0..23 in ascending order, at least one hour."""
    if not hours:
        raise ValueError("hours must contain at least one hour")
    if len(set(hours)) != len(hours):
        raise ValueError("hours must not contain duplicates")
    if hours != sorted(hours):
        raise ValueError("hours must be in ascending order")
    return hours


DirectiveHours = Annotated[
    list[Annotated[int, Field(ge=0, le=HORIZON_HOURS - 1)]],
    Field(min_length=1, max_length=HORIZON_HOURS),
]


class _AdjustmentBase(BaseModel):
    """Common config: reject anything the spec does not list for this shape."""

    model_config = ConfigDict(extra="forbid")


class _HoursAdjustment(_AdjustmentBase):
    hours: DirectiveHours

    _check_hours = field_validator("hours")(_validate_hours)


class SolarReductionAdjustment(_HoursAdjustment):
    """`{"hours": [...], "factor": number}` — factor is the fraction that remains."""

    factor: float = Field(ge=0.0, le=1.0)


class MinimumBatteryReserveAdjustment(_HoursAdjustment):
    """`{"hours": [...], "minimum_energy_kwh": number}`."""

    minimum_energy_kwh: float = Field(ge=0.0)


class NoChargeWindowAdjustment(_HoursAdjustment):
    """`{"hours": [...]}`."""


class NoDischargeWindowAdjustment(_HoursAdjustment):
    """`{"hours": [...]}`."""


class MaxGridWindowAdjustment(_HoursAdjustment):
    """`{"hours": [...], "max_grid_kwh": number}`."""

    max_grid_kwh: float = Field(ge=0.0)


StructuredAdjustment = Union[
    SolarReductionAdjustment,
    MinimumBatteryReserveAdjustment,
    MaxGridWindowAdjustment,
    NoChargeWindowAdjustment,
    NoDischargeWindowAdjustment,
]

#: Maps a directive type to the one adjustment shape Section 04 requires for it.
#: `no_op` is absent on purpose — its adjustment must be null.
ADJUSTMENT_MODELS: dict[DirectiveType, type[_HoursAdjustment]] = {
    DirectiveType.SOLAR_REDUCTION: SolarReductionAdjustment,
    DirectiveType.MINIMUM_BATTERY_RESERVE: MinimumBatteryReserveAdjustment,
    DirectiveType.NO_CHARGE_WINDOW: NoChargeWindowAdjustment,
    DirectiveType.NO_DISCHARGE_WINDOW: NoDischargeWindowAdjustment,
    DirectiveType.MAX_GRID_WINDOW: MaxGridWindowAdjustment,
}


class DirectiveInterpretation(BaseModel):
    """One entry per operator note (Section 10.2).

    The `before` validator routes `structured_adjustment` to the single model that
    `directive_type` allows, so a union never has to guess between two shapes that
    both start with `hours`.
    """

    note_index: int = Field(ge=0)
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[StructuredAdjustment] = None
    explanation: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _coerce_adjustment(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        raw_type = data.get("directive_type")
        raw_adjustment = data.get("structured_adjustment")
        if not isinstance(raw_adjustment, dict):
            return data

        try:
            directive_type = DirectiveType(raw_type)
        except ValueError:
            return data  # let the field validator report the bad type

        model = ADJUSTMENT_MODELS.get(directive_type)
        if model is None:
            return data  # no_op: the check below rejects a non-null adjustment

        coerced = dict(data)
        coerced["structured_adjustment"] = model.model_validate(raw_adjustment)
        return coerced

    @model_validator(mode="after")
    def _check_applies_semantics(self) -> DirectiveInterpretation:
        """Section 05.1 / 08: applies and adjustment shape must agree with the type."""
        if self.directive_type is DirectiveType.NO_OP:
            if self.applies:
                raise ValueError("no_op must use applies = false")
            if self.structured_adjustment is not None:
                raise ValueError("no_op must use structured_adjustment = null")
            return self

        if not self.applies:
            raise ValueError(
                f"{self.directive_type.value} must use applies = true; "
                "no_op is the only directive allowed with applies = false"
            )
        if self.structured_adjustment is None:
            raise ValueError(
                f"{self.directive_type.value} requires a structured_adjustment object"
            )

        expected = ADJUSTMENT_MODELS[self.directive_type]
        if not isinstance(self.structured_adjustment, expected):
            raise ValueError(
                f"structured_adjustment does not match the shape required for "
                f"{self.directive_type.value}"
            )
        return self

    @classmethod
    def no_op(cls, note_index: int, explanation: str) -> DirectiveInterpretation:
        """The safe fallback of Section 08: never invent a directive."""
        return cls(
            note_index=note_index,
            applies=False,
            directive_type=DirectiveType.NO_OP,
            structured_adjustment=None,
            explanation=explanation,
        )
