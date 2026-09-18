"""Guardrail tests — Section 08. LLM output is untrusted until it survives these."""

from __future__ import annotations

from typing import Any

import pytest

from app.schemas.directive import DirectiveType
from app.services.directive_validator import validate_interpretations


def solar(**overrides: Any) -> dict[str, Any]:
    entry = {
        "note_index": 0,
        "applies": True,
        "directive_type": "solar_reduction",
        "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
        "explanation": "Panel cleaning.",
    }
    entry.update(overrides)
    return entry


def test_a_valid_directive_survives() -> None:
    [entry] = validate_interpretations([solar()], note_count=1)

    assert entry.directive_type is DirectiveType.SOLAR_REDUCTION
    assert entry.applies is True
    assert entry.structured_adjustment.hours == [13, 14]
    assert entry.structured_adjustment.factor == pytest.approx(0.2)


def test_always_returns_one_entry_per_note_in_order() -> None:
    entries = validate_interpretations([solar(note_index=2)], note_count=3)

    assert [entry.note_index for entry in entries] == [0, 1, 2]
    assert entries[0].directive_type is DirectiveType.NO_OP
    assert entries[2].directive_type is DirectiveType.SOLAR_REDUCTION


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(solar(directive_type="load_shedding"), id="unsupported-type"),
        pytest.param(
            solar(structured_adjustment={"hours": [14, 13], "factor": 0.2}),
            id="hours-not-ascending",
        ),
        pytest.param(
            solar(structured_adjustment={"hours": [13, 13], "factor": 0.2}),
            id="duplicate-hours",
        ),
        pytest.param(
            solar(structured_adjustment={"hours": [13, 24], "factor": 0.2}),
            id="hour-out-of-range",
        ),
        pytest.param(
            solar(structured_adjustment={"hours": [], "factor": 0.2}), id="no-hours"
        ),
        pytest.param(
            solar(structured_adjustment={"hours": [13], "factor": 1.4}),
            id="factor-above-one",
        ),
        pytest.param(
            solar(structured_adjustment={"hours": [13], "factor": -0.1}),
            id="negative-factor",
        ),
        pytest.param(
            solar(structured_adjustment={"hours": [13]}), id="missing-factor"
        ),
        pytest.param(solar(applies=False), id="non-no_op-with-applies-false"),
        pytest.param(solar(structured_adjustment=None), id="missing-adjustment"),
        pytest.param(
            solar(
                directive_type="max_grid_window",
                structured_adjustment={"hours": [13], "factor": 0.2},
            ),
            id="adjustment-shape-mismatch",
        ),
        pytest.param(
            solar(
                directive_type="minimum_battery_reserve",
                structured_adjustment={"hours": [18], "minimum_energy_kwh": -5},
            ),
            id="negative-reserve",
        ),
        pytest.param("not even an object", id="not-an-object"),
    ],
)
def test_unusable_entries_fall_back_to_no_op(bad: Any) -> None:
    """Section 08 safe failure: never invent a directive, never crash."""
    [entry] = validate_interpretations([bad], note_count=1)

    assert entry.directive_type is DirectiveType.NO_OP
    assert entry.applies is False
    assert entry.structured_adjustment is None


def test_out_of_range_note_index_is_dropped() -> None:
    [entry] = validate_interpretations([solar(note_index=7)], note_count=1)
    assert entry.directive_type is DirectiveType.NO_OP


def test_duplicate_note_index_keeps_only_the_first() -> None:
    entries = validate_interpretations(
        [solar(), solar(directive_type="no_charge_window")], note_count=1
    )

    assert len(entries) == 1
    assert entries[0].directive_type is DirectiveType.SOLAR_REDUCTION


def test_no_op_must_not_carry_an_adjustment() -> None:
    [entry] = validate_interpretations(
        [
            {
                "note_index": 0,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": {"hours": [1]},
                "explanation": "Irrelevant.",
            }
        ],
        note_count=1,
    )
    assert entry.directive_type is DirectiveType.NO_OP
    assert entry.structured_adjustment is None


def test_missing_interpretations_are_filled_with_no_op() -> None:
    entries = validate_interpretations([], note_count=2)

    assert len(entries) == 2
    assert all(entry.directive_type is DirectiveType.NO_OP for entry in entries)
