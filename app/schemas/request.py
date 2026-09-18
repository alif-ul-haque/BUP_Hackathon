"""Request contract for POST /optimize-energy (Problem Statement Section 07).

Anything checked here is *structural*: a failure becomes HTTP 400. Cross-field
checks that need a well-formed body first live in `app.services.scenario_checks`
and become HTTP 422.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.schemas.directive import HORIZON_HOURS

MAX_OPERATOR_NOTES = 3


class HourInput(BaseModel):
    """One hourly scenario row (Section 07.2)."""

    hour: int = Field(ge=0, le=HORIZON_HOURS - 1)
    demand_kwh: float = Field(ge=0.0)
    solar_kwh: float = Field(ge=0.0)
    tariff_bdt_per_kwh: float = Field(ge=0.0)


class Battery(BaseModel):
    """Battery parameters (Section 07.3)."""

    capacity_kwh: float = Field(gt=0.0)
    initial_energy_kwh: float = Field(ge=0.0)
    minimum_energy_kwh: float = Field(ge=0.0)
    max_charge_kwh_per_hour: float = Field(ge=0.0)
    max_discharge_kwh_per_hour: float = Field(ge=0.0)


class OptimizeEnergyRequest(BaseModel):
    """One scenario object (Section 07.1)."""

    scenario_id: str = Field(min_length=1)
    operator_notes: Annotated[
        list[str], Field(min_length=1, max_length=MAX_OPERATOR_NOTES)
    ]
    hours: Annotated[
        list[HourInput], Field(min_length=HORIZON_HOURS, max_length=HORIZON_HOURS)
    ]
    battery: Battery

    @field_validator("operator_notes")
    @classmethod
    def _notes_must_be_non_empty(cls, notes: list[str]) -> list[str]:
        for index, note in enumerate(notes):
            if not note.strip():
                raise ValueError(f"operator_notes[{index}] must be a non-empty string")
        return notes

    @field_validator("hours")
    @classmethod
    def _hours_must_cover_the_horizon(cls, hours: list[HourInput]) -> list[HourInput]:
        seen = {entry.hour for entry in hours}
        if seen != set(range(HORIZON_HOURS)):
            raise ValueError(
                f"hours must contain exactly one entry for each hour 0..{HORIZON_HOURS - 1}"
            )
        return hours

    def hours_in_order(self) -> list[HourInput]:
        """Scenario rows sorted by hour, so downstream code can index by hour."""
        return sorted(self.hours, key=lambda entry: entry.hour)
