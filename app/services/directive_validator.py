"""Guardrails between the LLM and the optimizer (Problem Statement Section 08).

Every rule here is deterministic. LLM output is untrusted structured data until
it survives this module, and the safe failure of Section 08 is always the same:
drop the unusable entry to `no_op` rather than invent a directive or crash.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from app.schemas.directive import DirectiveInterpretation

logger = logging.getLogger(__name__)

_UNPARSEABLE = "Interpretation could not be validated; treated as no_op."
_MISSING = "No interpretation was returned for this note; treated as no_op."


def validate_interpretations(
    raw_entries: list[dict[str, Any]],
    note_count: int,
) -> list[DirectiveInterpretation]:
    """Return exactly `note_count` trusted entries, one per note, in index order.

    Enforces the Section 08 guardrails:

    * `directive_type` is one of the six supported values
    * `note_index` maps to a real note and appears exactly once
    * hours are unique integers 0..23 in ascending order
    * `factor` is within [0, 1]; reserve and grid cap are finite and non-negative
    * `applies` / `structured_adjustment` agree with the directive type

    The last three are carried by the Pydantic models themselves; this function
    owns the mapping rules and the fallback.
    """
    by_index: dict[int, DirectiveInterpretation] = {}

    for position, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            logger.warning("Interpretation at position %d is not an object", position)
            continue

        note_index = raw.get("note_index", position)
        if not isinstance(note_index, int) or isinstance(note_index, bool):
            logger.warning(
                "Interpretation at position %d has a non-integer note_index", position
            )
            continue
        if not 0 <= note_index < note_count:
            logger.warning("Interpretation note_index %r is out of range", note_index)
            continue
        if note_index in by_index:
            logger.warning("Duplicate interpretation for note_index %d", note_index)
            continue

        try:
            by_index[note_index] = DirectiveInterpretation.model_validate(raw)
        except ValidationError as exc:
            logger.warning(
                "Interpretation for note_index %d failed validation: %s",
                note_index,
                exc.errors(include_url=False)[:3],
            )
            by_index[note_index] = DirectiveInterpretation.no_op(
                note_index, _UNPARSEABLE
            )

    return [
        by_index[index]
        if index in by_index
        else DirectiveInterpretation.no_op(index, _MISSING)
        for index in range(note_count)
    ]
