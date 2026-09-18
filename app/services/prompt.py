"""Prompt construction for operator-note interpretation (Section 02/04/05/08).

Kept separate from `interpreter.py` so the wording can be iterated on without
touching the call/retry/parse logic, and so `tests/test_prompt.py` can assert on
its shape without spinning up a client.

Everything here is deterministic string-building. No network calls, no state.
"""

from __future__ import annotations

import json

SYSTEM_PROMPT = """\
You are the operator-note interpreter for GridWise, a campus energy-scheduling \
system. You read short natural-language notes written by campus operators and \
decide whether each one changes today's 24-hour energy schedule.

You will be given, as a JSON user message:
  - battery_capacity_kwh: the battery's total capacity in kWh (use this only to
    convert a percentage-of-capacity note into an absolute kWh number).
  - operator_notes: a list of {"note_index": int, "text": string} objects.

For each note, decide whether it maps to one of six supported directive types, \
or is irrelevant (no_op). A note is a distractor/no_op when it does not describe \
a change to solar, the battery, or grid import for the current 24-hour schedule \
(for example: unrelated campus announcements, scheduling of unrelated events, \
administrative notices).

## Supported directive types

- solar_reduction: usable solar is reduced during specific hours.
  structured_adjustment = {"hours": [int, ...], "factor": number between 0 and 1}
  factor is the FRACTION OF SOLAR THAT REMAINS, not the fraction lost. A note
  saying "80% reduction", "solar drops to 20%", "only about one-fifth of normal
  output remains", and "cut by four-fifths" all mean factor = 0.2. Read carefully
  whether the number in the note is the drop or the remainder.

- minimum_battery_reserve: the battery must stay at or above a level during
  specific hours.
  structured_adjustment = {"hours": [int, ...], "minimum_energy_kwh": number >= 0}
  If the note gives a percentage of capacity (e.g. "at least 50% of the
  battery's capacity", "a quarter of capacity"), multiply that fraction by the
  given battery_capacity_kwh and report the resulting kWh number. If the note
  already gives an absolute kWh number, use it directly.

- no_charge_window: the battery may not charge during specific hours.
  structured_adjustment = {"hours": [int, ...]}

- no_discharge_window: the battery may not discharge during specific hours.
  structured_adjustment = {"hours": [int, ...]}

- max_grid_window: grid import may not exceed a stated amount during specific
  hours.
  structured_adjustment = {"hours": [int, ...], "max_grid_kwh": number >= 0}

- no_op: the note does not affect the current 24-hour energy schedule.
  structured_adjustment = null

## Hard rules — every one of these is checked by a separate deterministic
## validator after you respond, so follow them exactly

1. Return exactly one interpretation entry per note, in note_index order
   (0, 1, ..., N-1). Never skip a note. Never return two entries for the same
   note_index.
2. Use only the six directive_type values listed above, spelled exactly as
   shown. Never invent a new directive type.
3. Never invent or alter demand, solar, tariff, or battery numbers that are not
   stated in a note. Only extract what the notes actually say.
4. Time windows are start-inclusive, end-exclusive, using whole hours. "1 PM to
   3 PM" means hours [13, 14] — 15 is NOT included. "6 PM until 9 PM" means
   hours [18, 19, 20]. "from 2 AM until 5 AM" means hours [2, 3, 4].
5. hours must be a list of unique integers from 0 to 23, in ascending order. It
   may never be empty. If a note states no time window and the rule applies for
   the whole day (e.g. "keep at least 50% of the battery in reserve"), enumerate
   all 24 hours: [0, 1, 2, ..., 23].
6. For every directive that is not no_op: applies must be true and
   structured_adjustment must be the exact object shape required for that type
   (never null, never missing a required key).
7. For no_op: applies must be false and structured_adjustment must be null.
8. The same underlying rule can be phrased in many different ways across
   different notes and different scenarios — judge each note by its meaning,
   not by matching it against wording you have seen before.
9. If a note is ambiguous, does not clearly describe one of the six directive
   types, or you are not confident in the interpretation, mark it no_op rather
   than guessing.
10. Give a short, one-sentence, human-readable "explanation" for every entry.
    Its exact wording is never graded — only the structured fields are.

## Output format

Respond with a single JSON object and nothing else — no prose, no markdown
code fences, no keys other than the one below:

{"directive_interpretation": [
  {
    "note_index": 0,
    "applies": true,
    "directive_type": "solar_reduction",
    "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
    "explanation": "Solar drops to 20% of forecast during the stated window."
  }
]}
"""


def _example(user_payload: dict, assistant_payload: dict) -> tuple[dict, dict]:
    return (
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        {
            "role": "assistant",
            "content": json.dumps(assistant_payload, ensure_ascii=False),
        },
    )


#: Few-shot examples. Deliberately worded differently from the public sample
#: pack (Section 11.4: hidden notes are paraphrased — these examples teach the
#: *pattern*, not a phrase to match) while covering all six directive types,
#: a percentage-based reserve conversion, a multi-note case with a distractor,
#: and two different phrasings of a solar-reduction percentage.
_EXAMPLES: list[tuple[dict, dict]] = [
    _example(
        {
            "battery_capacity_kwh": 500,
            "operator_notes": [
                {
                    "note_index": 0,
                    "text": "Solar output will drop to about 20% from 1 PM to 3 PM.",
                },
                {
                    "note_index": 1,
                    "text": "The cafeteria menu changes tomorrow.",
                },
            ],
        },
        {
            "directive_interpretation": [
                {
                    "note_index": 0,
                    "applies": True,
                    "directive_type": "solar_reduction",
                    "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
                    "explanation": "Solar falls to 20% of forecast in the stated window.",
                },
                {
                    "note_index": 1,
                    "applies": False,
                    "directive_type": "no_op",
                    "structured_adjustment": None,
                    "explanation": "A cafeteria menu change does not affect the energy schedule.",
                },
            ]
        },
    ),
    _example(
        {
            "battery_capacity_kwh": 400,
            "operator_notes": [
                {
                    "note_index": 0,
                    "text": "The battery charger will be offline for maintenance "
                    "between 2 PM and 4 PM.",
                },
                {
                    "note_index": 1,
                    "text": "Expect an 80% cut in rooftop solar between 11 AM and "
                    "2 PM due to inverter work.",
                },
            ],
        },
        {
            "directive_interpretation": [
                {
                    "note_index": 0,
                    "applies": True,
                    "directive_type": "no_charge_window",
                    "structured_adjustment": {"hours": [14, 15]},
                    "explanation": "Charging is unavailable while the charger is offline.",
                },
                {
                    "note_index": 1,
                    "applies": True,
                    "directive_type": "solar_reduction",
                    "structured_adjustment": {"hours": [11, 12, 13], "factor": 0.2},
                    "explanation": "An 80% cut leaves 20% of solar usable in the window.",
                },
            ]
        },
    ),
    _example(
        {
            "battery_capacity_kwh": 300,
            "operator_notes": [
                {
                    "note_index": 0,
                    "text": "Reserve at least a quarter of the battery's capacity "
                    "from 10 PM until midnight for backup lighting.",
                }
            ],
        },
        {
            "directive_interpretation": [
                {
                    "note_index": 0,
                    "applies": True,
                    "directive_type": "minimum_battery_reserve",
                    "structured_adjustment": {
                        "hours": [22, 23],
                        "minimum_energy_kwh": 75,
                    },
                    "explanation": "A quarter of the 300 kWh capacity is 75 kWh.",
                }
            ]
        },
    ),
    _example(
        {
            "battery_capacity_kwh": 250,
            "operator_notes": [
                {
                    "note_index": 0,
                    "text": "Do not discharge the battery between 5 PM and 7 PM "
                    "for relay testing.",
                },
                {
                    "note_index": 1,
                    "text": "Grid import must stay at or below 150 kWh per hour "
                    "from 6 PM until 9 PM because of a feeder limit.",
                },
                {
                    "note_index": 2,
                    "text": "Facilities is repainting the west stairwell next week.",
                },
            ],
        },
        {
            "directive_interpretation": [
                {
                    "note_index": 0,
                    "applies": True,
                    "directive_type": "no_discharge_window",
                    "structured_adjustment": {"hours": [17, 18]},
                    "explanation": "Discharging is disallowed during relay testing.",
                },
                {
                    "note_index": 1,
                    "applies": True,
                    "directive_type": "max_grid_window",
                    "structured_adjustment": {
                        "hours": [18, 19, 20],
                        "max_grid_kwh": 150,
                    },
                    "explanation": "Grid import is capped at 150 kWh per hour in the window.",
                },
                {
                    "note_index": 2,
                    "applies": False,
                    "directive_type": "no_op",
                    "structured_adjustment": None,
                    "explanation": "A stairwell repaint does not affect the energy schedule.",
                },
            ]
        },
    ),
]


def few_shot_messages() -> list[dict]:
    """Flattened user/assistant message pairs to splice in before the real query."""
    messages: list[dict] = []
    for user_message, assistant_message in _EXAMPLES:
        messages.append(user_message)
        messages.append(assistant_message)
    return messages


def build_user_message(notes: list[str], battery_capacity_kwh: float) -> str:
    """The real request, in the same shape the few-shot examples use."""
    payload = {
        "battery_capacity_kwh": battery_capacity_kwh,
        "operator_notes": [
            {"note_index": index, "text": text} for index, text in enumerate(notes)
        ],
    }
    return json.dumps(payload, ensure_ascii=False)
