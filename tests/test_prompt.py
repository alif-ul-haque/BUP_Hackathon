"""Prompt-shape tests — no network calls, no client. Pure string/JSON assertions."""

from __future__ import annotations

import json

from app.schemas.directive import DirectiveType
from app.services import prompt


def test_system_prompt_names_every_supported_directive_type() -> None:
    for directive_type in DirectiveType:
        assert directive_type.value in prompt.SYSTEM_PROMPT


def test_system_prompt_states_the_time_window_convention() -> None:
    assert "[13, 14]" in prompt.SYSTEM_PROMPT


def test_system_prompt_states_the_solar_factor_convention() -> None:
    assert "factor = 0.2" in prompt.SYSTEM_PROMPT


def test_few_shot_messages_alternate_user_and_assistant() -> None:
    messages = prompt.few_shot_messages()

    assert messages, "expected at least one few-shot example"
    assert len(messages) % 2 == 0
    for index, message in enumerate(messages):
        expected_role = "user" if index % 2 == 0 else "assistant"
        assert message["role"] == expected_role


def test_few_shot_messages_are_valid_json_and_cover_every_directive_type() -> None:
    seen_types: set[str] = set()

    for message in prompt.few_shot_messages():
        payload = json.loads(message["content"])
        if message["role"] != "assistant":
            continue
        for entry in payload["directive_interpretation"]:
            seen_types.add(entry["directive_type"])

    assert seen_types == {directive_type.value for directive_type in DirectiveType}


def test_few_shot_examples_include_a_percentage_reserve_conversion() -> None:
    """Section 07/08: a percentage-of-capacity note must resolve to kWh."""
    found = False
    for message in prompt.few_shot_messages():
        if message["role"] != "assistant":
            continue
        payload = json.loads(message["content"])
        for entry in payload["directive_interpretation"]:
            if entry["directive_type"] == "minimum_battery_reserve":
                found = True
                assert isinstance(
                    entry["structured_adjustment"]["minimum_energy_kwh"], (int, float)
                )
    assert found


def test_build_user_message_embeds_capacity_and_notes_in_order() -> None:
    notes = ["First note.", "Second note."]
    message = prompt.build_user_message(notes, battery_capacity_kwh=321)
    payload = json.loads(message)

    assert payload["battery_capacity_kwh"] == 321
    assert [entry["text"] for entry in payload["operator_notes"]] == notes
    assert [entry["note_index"] for entry in payload["operator_notes"]] == [0, 1]
