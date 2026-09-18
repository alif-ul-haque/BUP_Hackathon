"""Interpreter tests — Section 02/04/08. No real network calls: the OpenAI client
is faked so these run offline and do not need LLM_API_KEY.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from openai import OpenAIError

from app.core.config import get_settings
from app.services import interpreter

NOTES = [
    "Solar output will drop to about 20% from 1 PM to 3 PM.",
    "The cafeteria menu changes tomorrow.",
]


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    """Queues one return value (or exception) per call to `.create`."""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.calls = 0

    def create(self, **_kwargs: Any) -> _FakeResponse:
        self.calls += 1
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return _FakeResponse(result)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, results: list[Any]) -> None:
        self.completions = _FakeCompletions(results)
        self.chat = _FakeChat(self.completions)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries would otherwise add real delay to the test run."""
    monkeypatch.setattr(interpreter.time, "sleep", lambda _seconds: None)


def _configured_settings(**overrides: Any):
    base = get_settings()
    merged = {"llm_api_key": "test-key", "llm_max_retries": 1, **overrides}
    return replace(base, **merged)


def test_falls_back_to_no_op_when_llm_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        interpreter, "get_settings", lambda: replace(get_settings(), llm_api_key="")
    )
    entries = interpreter.interpret_notes(NOTES, battery_capacity_kwh=500)

    assert [entry["note_index"] for entry in entries] == [0, 1]
    assert all(entry["directive_type"] == "no_op" for entry in entries)
    assert all(entry["applies"] is False for entry in entries)


def test_successful_call_returns_the_models_entries_unmodified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _configured_settings()
    monkeypatch.setattr(interpreter, "get_settings", lambda: settings)

    model_payload = {
        "directive_interpretation": [
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
                "explanation": "Panel cleaning.",
            },
            {
                "note_index": 1,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "Irrelevant.",
            },
        ]
    }
    fake_client = _FakeClient([json.dumps(model_payload)])
    monkeypatch.setattr(interpreter, "_get_client", lambda _settings: fake_client)

    entries = interpreter.interpret_notes(NOTES, battery_capacity_kwh=500)

    assert entries == model_payload["directive_interpretation"]
    assert fake_client.completions.calls == 1


def test_battery_capacity_and_notes_reach_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The percentage-reserve conversion (Section 07/08) needs capacity in context."""
    settings = _configured_settings()
    monkeypatch.setattr(interpreter, "get_settings", lambda: settings)

    captured: dict[str, Any] = {}
    fake_client = _FakeClient(
        [json.dumps({"directive_interpretation": []})] * 1
    )

    def _capturing_create(**kwargs: Any) -> _FakeResponse:
        captured["messages"] = kwargs["messages"]
        return _FakeResponse(json.dumps({"directive_interpretation": []}))

    fake_client.chat.completions.create = _capturing_create  # type: ignore[method-assign]
    monkeypatch.setattr(interpreter, "_get_client", lambda _settings: fake_client)

    interpreter.interpret_notes(
        ["Keep at least 50% of the battery capacity from 6 PM until 9 PM."],
        battery_capacity_kwh=200,
    )

    user_message = captured["messages"][-1]["content"]
    assert '"battery_capacity_kwh": 200' in user_message
    assert "50% of the battery capacity" in user_message


def test_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _configured_settings(llm_max_retries=2)
    monkeypatch.setattr(interpreter, "get_settings", lambda: settings)

    ok_payload = json.dumps({"directive_interpretation": []})
    fake_client = _FakeClient([OpenAIError("transient failure"), ok_payload])
    monkeypatch.setattr(interpreter, "_get_client", lambda _settings: fake_client)

    entries = interpreter.interpret_notes([NOTES[0]], battery_capacity_kwh=500)

    assert entries == []
    assert fake_client.completions.calls == 2


def test_all_attempts_failing_falls_back_to_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _configured_settings(llm_max_retries=2)
    monkeypatch.setattr(interpreter, "get_settings", lambda: settings)

    fake_client = _FakeClient(
        [OpenAIError("down"), OpenAIError("still down"), OpenAIError("still down")]
    )
    monkeypatch.setattr(interpreter, "_get_client", lambda _settings: fake_client)

    entries = interpreter.interpret_notes(NOTES, battery_capacity_kwh=500)

    assert [entry["note_index"] for entry in entries] == [0, 1]
    assert all(entry["directive_type"] == "no_op" for entry in entries)
    assert fake_client.completions.calls == 3


def test_unparsable_json_falls_back_to_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _configured_settings(llm_max_retries=0)
    monkeypatch.setattr(interpreter, "get_settings", lambda: settings)

    fake_client = _FakeClient(["this is not json"])
    monkeypatch.setattr(interpreter, "_get_client", lambda _settings: fake_client)

    entries = interpreter.interpret_notes(NOTES, battery_capacity_kwh=500)

    assert all(entry["directive_type"] == "no_op" for entry in entries)


def test_response_missing_the_wrapper_key_falls_back_to_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _configured_settings(llm_max_retries=0)
    monkeypatch.setattr(interpreter, "get_settings", lambda: settings)

    fake_client = _FakeClient([json.dumps({"something_else": []})])
    monkeypatch.setattr(interpreter, "_get_client", lambda _settings: fake_client)

    entries = interpreter.interpret_notes(NOTES, battery_capacity_kwh=500)

    assert all(entry["directive_type"] == "no_op" for entry in entries)


def test_bare_array_response_is_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 08 guardrails do the real shape-checking; this layer is lenient
    about the outer wrapper as long as *something* JSON-shaped comes back."""
    settings = _configured_settings()
    monkeypatch.setattr(interpreter, "get_settings", lambda: settings)

    bare_array = [
        {
            "note_index": 0,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "Irrelevant.",
        }
    ]
    fake_client = _FakeClient([json.dumps(bare_array)])
    monkeypatch.setattr(interpreter, "_get_client", lambda _settings: fake_client)

    entries = interpreter.interpret_notes([NOTES[1]], battery_capacity_kwh=500)

    assert entries == bare_array


def test_never_repairs_an_out_of_range_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guardrails (directive_validator), not the interpreter, own correctness —
    this module must hand a bad value through untouched, not clamp it."""
    settings = _configured_settings()
    monkeypatch.setattr(interpreter, "get_settings", lambda: settings)

    bad_payload = {
        "directive_interpretation": [
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": [13, 14], "factor": 1.4},
                "explanation": "Out of range on purpose.",
            }
        ]
    }
    fake_client = _FakeClient([json.dumps(bad_payload)])
    monkeypatch.setattr(interpreter, "_get_client", lambda _settings: fake_client)

    entries = interpreter.interpret_notes([NOTES[0]], battery_capacity_kwh=500)

    assert entries[0]["structured_adjustment"]["factor"] == 1.4


def test_unexpected_bug_in_this_module_still_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _configured_settings()
    monkeypatch.setattr(interpreter, "get_settings", lambda: settings)

    def _boom(_settings: Any) -> Any:
        raise RuntimeError("programming error, not an OpenAI error")

    monkeypatch.setattr(interpreter, "_get_client", _boom)

    entries = interpreter.interpret_notes(NOTES, battery_capacity_kwh=500)

    assert all(entry["directive_type"] == "no_op" for entry in entries)
