"""Operator-note interpretation (Problem Statement Section 02/08).

STUB — the LLM call is not implemented yet. This module owns the boundary the
rest of the pipeline codes against: notes in, *untrusted* raw dicts out. Nothing
downstream may assume the output is well-formed; `directive_validator` is what
turns it into trusted directives.

To implement: call the LLM with a schema-constrained prompt, parse its JSON, and
return it as-is. Do not clean it up here — repairing output inside the
interpreter hides exactly the failures the guardrails are meant to catch.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings
from app.schemas.directive import DirectiveType

logger = logging.getLogger(__name__)

#: One raw, untrusted interpretation as returned by the model.
RawInterpretation = dict[str, Any]


def interpret_notes(notes: list[str]) -> list[RawInterpretation]:
    """Return one raw interpretation per operator note, in note_index order."""
    settings = get_settings()

    if not settings.llm_configured:
        logger.warning(
            "No LLM configured (LLM_API_KEY unset) — falling back to all-no_op "
            "interpretation. Set the key before submitting."
        )
        return _all_no_op(notes)

    # TODO: replace with the real LLM call.
    #   client = OpenAI(api_key=settings.llm_api_key, base_url=... or None)
    #   response = client.chat.completions.create(
    #       model=settings.llm_model,
    #       temperature=settings.llm_temperature,
    #       response_format={"type": "json_object"},
    #       messages=[{"role": "system", "content": SYSTEM_PROMPT}, ...],
    #   )
    #   return json.loads(response.choices[0].message.content)["directive_interpretation"]
    logger.warning("LLM interpreter not implemented yet — returning all-no_op.")
    return _all_no_op(notes)


def _all_no_op(notes: list[str]) -> list[RawInterpretation]:
    return [
        {
            "note_index": index,
            "applies": False,
            "directive_type": DirectiveType.NO_OP.value,
            "structured_adjustment": None,
            "explanation": "Note interpretation is not available; treated as no_op.",
        }
        for index, _ in enumerate(notes)
    ]
