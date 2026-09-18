"""Response contract for the GridWise API (Problem Statement Section 10)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.directive import HORIZON_HOURS, BatteryAction, DirectiveInterpretation
from app.schemas.request import HourInput

#: Guards against float noise such as 179.99999999999997 in the emitted JSON.
#: Well inside the 0.01 judge tolerance of Section 11.5.
OUTPUT_DECIMALS = 6


def round_output(value: float) -> float:
    return round(float(value), OUTPUT_DECIMALS)


class HealthResponse(BaseModel):
    """GET /health body (Section 06.2)."""

    status: Literal["ok"] = "ok"


class HourlyPlanEntry(BaseModel):
    """One scheduled hour (Section 10.3)."""

    hour: int = Field(ge=0, le=HORIZON_HOURS - 1)
    grid_kwh: float = Field(ge=0.0)
    solar_used_kwh: float = Field(ge=0.0)
    battery_action: BatteryAction
    battery_kwh: float = Field(ge=0.0)
    battery_energy_after_kwh: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _idle_must_not_move_energy(self) -> HourlyPlanEntry:
        if self.battery_action is BatteryAction.IDLE and self.battery_kwh != 0:
            raise ValueError("battery_kwh must be 0 when battery_action is idle")
        return self

    @property
    def charge_kwh(self) -> float:
        return self.battery_kwh if self.battery_action is BatteryAction.CHARGE else 0.0

    @property
    def discharge_kwh(self) -> float:
        return (
            self.battery_kwh if self.battery_action is BatteryAction.DISCHARGE else 0.0
        )


class OptimizeEnergyResponse(BaseModel):
    """POST /optimize-energy body (Section 10.1)."""

    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: Annotated[
        list[HourlyPlanEntry],
        Field(min_length=HORIZON_HOURS, max_length=HORIZON_HOURS),
    ]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str

    @model_validator(mode="after")
    def _plan_must_cover_the_horizon(self) -> OptimizeEnergyResponse:
        seen = {entry.hour for entry in self.hourly_plan}
        if seen != set(range(HORIZON_HOURS)):
            raise ValueError(
                f"hourly_plan must contain exactly one entry for each hour "
                f"0..{HORIZON_HOURS - 1}"
            )
        return self

    @classmethod
    def build(
        cls,
        *,
        scenario_id: str,
        directive_interpretation: list[DirectiveInterpretation],
        hourly_plan: list[HourlyPlanEntry],
        hours: list[HourInput],
        plan_summary: str,
    ) -> OptimizeEnergyResponse:
        """Assemble the response, deriving the three totals from `hourly_plan`.

        Section 11.3 requires total_grid_kwh, total_cost_bdt and peak_grid_kwh to
        match values recalculated from the plan, so they are never passed in —
        they are always computed here from the plan that is actually returned.
        """
        plan = sorted(hourly_plan, key=lambda entry: entry.hour)
        tariff_by_hour = {entry.hour: entry.tariff_bdt_per_kwh for entry in hours}

        total_grid_kwh = sum(entry.grid_kwh for entry in plan)
        total_cost_bdt = sum(
            entry.grid_kwh * tariff_by_hour[entry.hour] for entry in plan
        )
        peak_grid_kwh = max((entry.grid_kwh for entry in plan), default=0.0)

        return cls(
            scenario_id=scenario_id,
            directive_interpretation=directive_interpretation,
            hourly_plan=plan,
            total_grid_kwh=round_output(total_grid_kwh),
            total_cost_bdt=round_output(total_cost_bdt),
            peak_grid_kwh=round_output(peak_grid_kwh),
            plan_summary=plan_summary,
        )
