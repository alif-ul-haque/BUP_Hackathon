"""Scenarios for the dashboard. Demo-only — the judge harness never calls this.

Prefers the organizers' public sample pack when it sits next to the app (its
`expected_output` doubles as a reference plan to compare our own against), and
falls back to one synthetic scenario so the dashboard works in a bare image.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SAMPLE_PACK = Path(__file__).resolve().parents[1] / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


def _fallback_case() -> dict[str, Any]:
    """A plausible campus day: quiet night, solar midday, expensive evening peak."""
    demand = [
        90, 85, 82, 80, 84, 96, 120, 150, 178, 192, 200, 205,
        210, 208, 198, 188, 186, 196, 226, 240, 232, 205, 160, 118,
    ]
    solar = [
        0, 0, 0, 0, 0, 4, 26, 68, 118, 158, 186, 198,
        200, 190, 164, 124, 74, 28, 4, 0, 0, 0, 0, 0,
    ]
    tariff = [
        6, 6, 6, 6, 6, 6, 7.5, 9, 9, 9, 9, 9,
        9, 9, 9, 9, 10.5, 13, 15, 15, 13, 10.5, 7.5, 6,
    ]

    return {
        "id": "DEMO-01",
        "label": "Built-in demo day",
        "input": {
            "scenario_id": "DEMO-01",
            "operator_notes": [
                "Facilities will wash the rooftop panels from noon until 2 PM, so treat usable "
                "solar as about a quarter of the forecast.",
                "Keep at least 150 kWh in the battery from 6 PM until 9 PM.",
                "The library will extend its opening hours next semester.",
            ],
            "hours": [
                {
                    "hour": hour,
                    "demand_kwh": demand[hour],
                    "solar_kwh": solar[hour],
                    "tariff_bdt_per_kwh": tariff[hour],
                }
                for hour in range(24)
            ],
            "battery": {
                "capacity_kwh": 420.0,
                "initial_energy_kwh": 200.0,
                "minimum_energy_kwh": 60.0,
                "max_charge_kwh_per_hour": 90.0,
                "max_discharge_kwh_per_hour": 90.0,
            },
        },
        "expected_output": None,
    }


@lru_cache(maxsize=1)
def sample_scenarios() -> list[dict[str, Any]]:
    """Return `[{id, label, input, expected_output}]` for the scenario picker."""
    if not SAMPLE_PACK.exists():
        logger.info("Public sample pack not found; serving the built-in demo scenario.")
        return [_fallback_case()]

    try:
        with SAMPLE_PACK.open(encoding="utf-8") as handle:
            cases = json.load(handle)["cases"]
    except (OSError, ValueError, KeyError):
        logger.warning("Public sample pack could not be read; using the built-in demo.")
        return [_fallback_case()]

    return [
        {
            "id": case.get("id", f"CASE-{index}"),
            "label": case.get("label", ""),
            "input": case["input"],
            "expected_output": case.get("expected_output"),
        }
        for index, case in enumerate(cases)
        if "input" in case
    ] or [_fallback_case()]
