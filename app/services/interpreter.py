"""Operator-note interpretation (Problem Statement Section 02/04/08).

This module owns the boundary the rest of the pipeline codes against: notes in,
*untrusted* raw dicts out. Nothing downstream may assume the output is
well-formed — `app.services.directive_validator` is what turns it into trusted
`DirectiveInterpretation` objects. Accordingly this module never repairs or
second-guesses the model's answer (no clamping a factor, no inventing a missing
field): doing that here would hide exactly the failures the guardrails exist to
catch. It only guarantees the *outer* contract — one attempt at a real JSON
object comes back, or a safe all-no_op fallback does.

Section 02 / Section 04 (LLM REQUIREMENT): a language-capable generative model
must genuinely produce `directive_interpretation`. This module calls one through
the OpenAI SDK (works against OpenAI itself or any OpenAI-compatible endpoint —
see LLM_BASE_URL in `app.core.config`) using JSON-mode structured output, with
few-shot examples covering all six directive types plus the two conversions the
guardrails will check numerically: percentage-of-capacity reserves and
"reduction vs. remaining" solar phrasing.

Section 08 (guardrails) / SAFE FAILURE: if the model is not configured, times
out, errors, or returns something that cannot even be parsed as
`{"directive_interpretation": [...]}`, this module falls back to marking every
note no_op rather than crash the request or invent a directive. It never raises
— `interpret_notes` is safe to call unconditionally from the pipeline.
"""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from typing import Any

from openai import OpenAI, OpenAIError

from app.core.config import Settings, get_settings
from app.schemas.directive import DirectiveType
from app.services import prompt

logger = logging.getLogger(__name__)

#: One raw, untrusted interpretation entry as returned by the model.
RawInterpretation = dict[str, Any]

_FALLBACK_EXPLANATION = "Note interpretation is not available; treated as no_op."


def interpret_notes(
    notes: list[str],
    battery_capacity_kwh: float,
) -> list[RawInterpretation]:
    """Return one raw interpretation per operator note, in note_index order.

    `battery_capacity_kwh` is passed through so the model can convert a
    percentage-of-capacity reserve note (Section 07's `battery.capacity_kwh`,
    e.g. the public sample "keep at least 50% of the battery capacity") into
    the absolute `minimum_energy_kwh` the guardrails and optimizer expect.

    Never raises: every failure path — no key configured, a provider error, an
    unparsable response, or an unexpected bug in this function — is caught and
    turned into the Section 08 safe fallback (all notes marked no_op) so a
    single bad LLM call cannot take down `/optimize-energy`.
    """
    settings = get_settings()

    if not settings.llm_configured:
        logger.warning(
            "No LLM configured (LLM_API_KEY unset) — falling back to all-no_op "
            "interpretation. Set LLM_API_KEY before submitting; the LLM "
            "requirement is mandatory (Section 02/04)."
        )
        return _all_no_op(notes)

    try:
        return _interpret_with_retries(notes, battery_capacity_kwh, settings)
    except Exception:  # this boundary must never propagate to the caller
        logger.exception(
            "Unexpected failure in interpret_notes — falling back to all-no_op."
        )
        return _all_no_op(notes)


def _interpret_with_retries(
    notes: list[str],
    battery_capacity_kwh: float,
    settings: Settings,
) -> list[RawInterpretation]:
    client = _get_client(settings)
    messages = _build_messages(notes, battery_capacity_kwh)

    attempts = max(1, settings.llm_max_retries + 1)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            content = _call_model(client, settings, messages)
            entries = _parse_response(content)
        except (OpenAIError, ValueError, TypeError, json.JSONDecodeError) as exc:
            last_error = exc
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.warning(
                "LLM interpretation attempt %d/%d failed after %.0f ms: %s: %s",
                attempt,
                attempts,
                elapsed_ms,
                type(exc).__name__,
                exc,
            )
            if attempt < attempts:
                time.sleep(min(2.0, 0.5 * attempt))
            continue

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "LLM interpretation ok: model=%s notes=%d attempt=%d/%d elapsed_ms=%.0f",
            settings.llm_model,
            len(notes),
            attempt,
            attempts,
            elapsed_ms,
        )
        return entries

    logger.error(
        "LLM interpretation failed after %d attempt(s): %s — falling back to all-no_op.",
        attempts,
        last_error,
    )
    return _all_no_op(notes)


@lru_cache(maxsize=1)
def _get_client(settings: Settings) -> OpenAI:
    """One client per distinct `Settings` — cheap to build, but no need to repeat."""
    kwargs: dict[str, Any] = {
        "api_key": settings.llm_api_key,
        "timeout": settings.llm_timeout_seconds,
    }
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url
    return OpenAI(**kwargs)


def _build_messages(notes: list[str], battery_capacity_kwh: float) -> list[dict]:
    return [
        {"role": "system", "content": prompt.SYSTEM_PROMPT},
        *prompt.few_shot_messages(),
        {
            "role": "user",
            "content": prompt.build_user_message(notes, battery_capacity_kwh),
        },
    ]


def _call_model(client: OpenAI, settings: Settings, messages: list[dict]) -> str:
    response = client.chat.completions.create(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        response_format={"type": "json_object"},
        messages=messages,
        timeout=settings.llm_timeout_seconds,
    )
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise ValueError("model returned an empty response body")
    return content


def _parse_response(content: str) -> list[RawInterpretation]:
    """Extract the `directive_interpretation` array. Entries are returned as-is —
    parsing failures here mean "the model did not even return usable JSON
    shape"; anything that *does* parse is handed to the guardrails unmodified.
    """
    payload = json.loads(content)

    if isinstance(payload, list):
        entries = payload  # tolerate a bare array instead of the wrapper object
    elif isinstance(payload, dict):
        entries = payload.get("directive_interpretation")
    else:
        entries = None

    if not isinstance(entries, list):
        raise TypeError(
            "response JSON did not contain a 'directive_interpretation' array"
        )
    return entries


def _all_no_op(notes: list[str]) -> list[RawInterpretation]:
    return [
        {
            "note_index": index,
            "applies": False,
            "directive_type": DirectiveType.NO_OP.value,
            "structured_adjustment": None,
            "explanation": _FALLBACK_EXPLANATION,
        }
        for index, _ in enumerate(notes)
    ]
