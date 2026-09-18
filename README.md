# GridWise — LLM-Assisted Energy Optimization

BUP CSE Fest 2026 preliminary round submission. GridWise reads free-text
operator notes about a campus's energy day, uses an LLM to turn them into
structured directives, validates those directives deterministically, and
runs a linear-programming optimizer to produce a cost-minimal 24-hour grid
schedule.

```
notes + hourly data  ->  LLM Interpreter  ->  Guardrail Validator
                       ->  Optimizer (OR-Tools)  ->  Schedule Validator  ->  API response
```

The LLM's output is never trusted directly: every directive it proposes is
re-validated against a fixed schema before it can affect the schedule, and
every schedule the optimizer produces is replayed and re-checked before it
leaves the service. If the LLM is unreachable, misconfigured, or returns
something unparsable, the service falls back to treating every note as
`no_op` rather than failing the request.

## Contents

- [Architecture](#architecture)
- [Project layout](#project-layout)
- [Requirements](#requirements)
- [Quickstart (local, clean environment)](#quickstart-local-clean-environment)
- [Environment variables](#environment-variables)
- [Running with Docker](#running-with-docker)
- [API contract](#api-contract)
- [Testing](#testing)
- [Dependencies & credits](#dependencies--credits)
- [Known limitations](#known-limitations)
- [Secret handling](#secret-handling)

## Architecture

1. **LLM Interpreter** (`app/services/interpreter.py`, prompt in
   `app/services/prompt.py`) — sends the operator notes plus battery
   capacity to an LLM in JSON mode, with a system prompt describing the six
   supported directive types, the hour-window convention (`[13, 14]` means
   1 PM–2 PM inclusive), and the solar-factor convention (`factor = 0.2`
   means usable solar is scaled to 20% of forecast). An overloaded, slow, or
   retired model is abandoned after one attempt and the next model in
   `LLM_FALLBACK_MODELS` is tried immediately; rejected credentials stop the
   call outright, since no other model would accept them. Any failure that
   outlives the model list (timeout, bad JSON, unreachable provider, missing
   API key) falls back to a safe `no_op` entry per note instead of raising.
2. **Guardrail Validator** (`app/services/directive_validator.py`,
   models in `app/schemas/directive.py`) — the LLM's raw output is treated
   as untrusted input. Each entry is checked against the exact schema for
   its `directive_type` (enum membership, hours ascending/unique/within
   0–23, `factor` in `[0, 1]`, the adjustment shape matching `applies`),
   deduplicated by `note_index`, and anything that fails validation is
   replaced with `no_op` rather than repaired or guessed at.
3. **Optimizer** (`app/services/optimizer.py`) — compiles the validated
   directives into per-hour numeric constraints
   (`app/services/constraints.py`) and solves a linear program with
   Google OR-Tools (GLOP): for each hour it picks `grid`, `solar_used`,
   `charge`, and `discharge` to satisfy the energy balance and battery
   transition equations while minimizing total grid cost, subject to
   battery capacity/rate limits, any LLM-derived charge/discharge/grid
   windows, and an end-of-day battery-neutrality constraint.
4. **Schedule Validator** (`app/services/schedule_validator.py`) — before
   any plan leaves the service, it is replayed hour-by-hour from scratch
   (energy balance, battery transition, capacity/rate limits, reserve
   minimums) using only the plan and the same compiled directive numbers
   the optimizer used. If replay finds a violation, the request fails with
   a controlled 500 instead of returning an invalid schedule.

A small read-only dashboard (`app/static/`, served at `/dashboard`) is
included for demoing scenarios in a browser. It calls the same
`/optimize-energy` endpoint a judge would and is not part of the judged
API contract.

## Project layout

```
BUP_Hackathon/
├── app/
│   ├── main.py                    # FastAPI app, /health and /optimize-energy
│   ├── demo_data.py                # Scenario list for the dashboard picker
│   ├── static/                     # Dashboard (not part of the judged contract)
│   ├── core/
│   │   ├── config.py                # Env-driven settings (.env)
│   │   ├── errors.py                # 400/422/500 error contract
│   │   └── logging.py
│   ├── schemas/
│   │   ├── request.py               # POST /optimize-energy request models
│   │   ├── response.py              # Response models
│   │   └── directive.py             # Directive types + structured adjustments
│   └── services/
│       ├── interpreter.py           # LLM call + retry/fallback
│       ├── prompt.py                # System prompt + few-shot examples
│       ├── directive_validator.py   # Guardrails
│       ├── constraints.py           # Directives -> per-hour numeric limits
│       ├── optimizer.py             # OR-Tools linear program
│       ├── schedule_validator.py    # Final replay check
│       ├── scenario_checks.py       # Semantic (422) checks
│       └── pipeline.py              # Wires the stages together
├── tests/                           # pytest suite (offline; LLM is faked)
├── BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json  # Organizer public samples
├── Dockerfile
├── requirements.txt
├── pyproject.toml
├── .env.example
└── README.md
```

## Requirements

- Python 3.11+
- pip
- Docker, for the container path below

## Quickstart (local, clean environment)

```bash
# 1. Clone and enter the project
git clone <this-repo-url>
cd BUP_Hackathon

# 2. Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure environment variables
cp .env.example .env
# then edit .env and set LLM_API_KEY (and LLM_BASE_URL / LLM_MODEL if not using
# OpenAI directly — see "Environment variables" below). The service still runs
# without an LLM key: every note is interpreted as no_op instead of failing.

# 4. Start the service
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 5. Confirm it's up
curl http://localhost:8000/health
# {"status":"ok"}

# 6. Run one public sample case against /optimize-energy
python3 - <<'PY'
import json, urllib.request

with open("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json") as f:
    case = json.load(f)["cases"][0]

req = urllib.request.Request(
    "http://localhost:8000/optimize-energy",
    data=json.dumps(case["input"]).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req) as resp:
    print(resp.status)
    print(json.dumps(json.load(resp), indent=2)[:1000])
PY
```

Equivalent one-liner with `curl` + `jq` if you prefer:

```bash
jq '.cases[0].input' BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json \
  | curl -s -X POST http://localhost:8000/optimize-energy \
      -H "Content-Type: application/json" -d @- | jq .
```

## Environment variables

Set in `.env` (never commit this file — see [Secret handling](#secret-handling)).
Names only; no secret values below.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `APP_NAME` | no | `GridWise Energy Optimizer` | Service name in logs |
| `LOG_LEVEL` | no | `INFO` | Python logging level |
| `DEBUG` | no | `false` | Adds exception messages to 500 bodies. Keep `false` in deployment (Section 06.1 forbids exposing stack traces) |
| `LLM_API_KEY` | for LLM interpretation | *(empty)* | API key for the LLM provider. If unset, notes fall back to `no_op` instead of the request failing |
| `LLM_BASE_URL` | for non-OpenAI providers | *(empty = OpenAI default)* | Base URL of an OpenAI-compatible chat-completions endpoint. This project runs against **OpenRouter** (`https://openrouter.ai/api/v1`); leave blank to use OpenAI directly, or point at any other OpenAI-compatible provider (Groq, Together, etc.) |
| `LLM_MODEL` | no | `gpt-4o-mini` | Model name to request. We run `google/gemini-2.5-flash-lite` through OpenRouter |
| `LLM_FALLBACK_MODELS` | no | *(empty)* | Comma-separated models tried in order when the primary is overloaded, rate-limited, slow, or retired. Each is given one attempt per round before the next round backs off |
| `LLM_TEMPERATURE` | no | `0.0` | Sampling temperature (kept at 0 for deterministic directive extraction) |
| `LLM_MAX_TOKENS` | no | `2000` | Caps the reply. Providers that bill against a credit balance (OpenRouter) reserve the model's whole context when this is unset and reject the call with a 402 if the balance cannot cover it |
| `LLM_TIMEOUT_SECONDS` | no | `20` | Per-attempt request timeout |
| `LLM_MAX_RETRIES` | no | `2` | Extra rounds over the full model list before falling back to `no_op` |
| `SOLVER_TIME_LIMIT_SECONDS` | no | `10` | Time budget given to the OR-Tools LP solver per request |

Model/provider identifier: **`google/gemini-2.5-flash-lite`, served through
OpenRouter** (the OpenAI Python SDK is used unmodified, pointed at OpenRouter via
`LLM_BASE_URL`). Any OpenAI-compatible provider works by changing `LLM_BASE_URL`
and `LLM_MODEL`.

The primary model was chosen by scoring candidates against the 18 operator notes
in the public sample pack, which ship with expected interpretations:
`gemini-2.5-flash-lite` 18/18 (~14 s), `gpt-4.1-mini` 18/18 (~20 s),
`gpt-4o-mini` 14/18, `gemini-3.5-flash` 13/18. `LLM_FALLBACK_MODELS` lists the
rest in that order.

## Running with Docker

```bash
docker build -t gridwise .
docker run --rm -p 8000:8000 --env-file .env gridwise

curl http://localhost:8000/health
```

The container installs `requirements.txt`, copies the `app/` package and the
public sample-cases JSON (used only by the dashboard picker, not the judged
API), and starts `uvicorn` bound to `0.0.0.0` on `$PORT` (defaults to `8000`
if `$PORT` is not set, which covers hosts like Render/Railway/Fly that inject
their own port). No secrets are baked into the image — `LLM_API_KEY` must be
supplied at `docker run` time via `--env-file` or `-e`.

## API contract

### `GET /health`

Returns `200 {"status": "ok"}` once the service is ready to accept requests.

### `POST /optimize-energy`

Request body — one scenario object:

```json
{
  "scenario_id": "SAMPLE-01",
  "operator_notes": [
    "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
    "The sports office moved next month's registration deadline."
  ],
  "hours": [
    { "hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6 },
    { "...": "24 entries total, one per hour 0-23" }
  ],
  "battery": {
    "capacity_kwh": 220,
    "initial_energy_kwh": 110,
    "minimum_energy_kwh": 40,
    "max_charge_kwh_per_hour": 50,
    "max_discharge_kwh_per_hour": 50
  }
}
```

Response body:

```json
{
  "scenario_id": "SAMPLE-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": { "hours": [12, 13], "factor": 0.25 },
      "explanation": "Panel cleaning reduces usable solar to 25% for hours 12-13."
    },
    { "note_index": 1, "applies": false, "directive_type": "no_op", "structured_adjustment": null, "explanation": "Irrelevant to energy scheduling." }
  ],
  "hourly_plan": [
    { "hour": 0, "grid_kwh": 12.5, "solar_used_kwh": 0.0, "battery_action": "idle", "battery_kwh": 0.0, "battery_energy_after_kwh": 110.0 },
    { "...": "24 entries total" }
  ],
  "total_grid_kwh": 0,
  "total_cost_bdt": 0,
  "peak_grid_kwh": 0,
  "plan_summary": "Applied 1 operator directive(s) to the 24-hour horizon and minimized grid cost subject to them, using available solar and battery flexibility while restoring the initial battery level by hour 23."
}
```

Error responses (never expose stack traces or secrets):

| Status | Meaning |
|---|---|
| `400` | Malformed JSON or a structurally invalid request (missing field, wrong type, wrong number of hours/notes) |
| `422` | Well-formed request that cannot describe a real system (e.g. `minimum_energy_kwh > capacity_kwh`, non-finite numbers) |
| `500` | Controlled internal error (e.g. the optimizer could not find a feasible schedule) |

Try it:

```bash
curl -s http://localhost:8000/health

curl -s -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

## Testing

```bash
pip install -r requirements.txt
pytest
```

The suite runs entirely offline: the LLM client is faked in
`tests/test_interpreter.py`, so no `LLM_API_KEY` or network access is
required to run tests. It covers the guardrail validator, the prompt shape,
the interpreter's retry/fallback behavior, and the optimizer/schedule
validator against known scenarios.

Lint (optional, not required to run the service):

```bash
ruff check .
```

## Dependencies & credits

- [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) — API framework and ASGI server
- [Pydantic v2](https://docs.pydantic.dev/) — request/response/directive schema validation
- [python-dotenv](https://github.com/theskumar/python-dotenv) — loads `.env` into the environment
- [OpenAI Python SDK](https://github.com/openai/openai-python) — used to call the LLM (pointed at OpenRouter; works unmodified with OpenAI or any other OpenAI-compatible provider)
- [OpenRouter](https://openrouter.ai/) (`google/gemini-2.5-flash-lite`) — the LLM gateway used for note interpretation
- [Google OR-Tools](https://developers.google.com/optimization) (GLOP linear solver) — the schedule optimizer
- [pytest](https://docs.pytest.org/) + [httpx](https://www.python-httpx.org/) — test suite
- Core architecture (LLM interpreter, guardrail validator, optimizer, schedule validator, API contract) is this team's own implementation, written for BUP CSE Fest 2026.

## Known limitations

- If `LLM_API_KEY` is unset or the LLM call fails after `LLM_MAX_RETRIES`
  attempts, every operator note is interpreted as `no_op` — the optimizer
  still runs and returns a valid, cost-minimal schedule, just without any
  directive applied. This is a deliberate safety fallback, not a crash.
- The interpreter targets one LLM call per request covering all notes in
  that scenario (up to 3, per the request schema); it has been exercised
  against OpenRouter and should work with any OpenAI-compatible provider.
- The optimizer is a linear program (OR-Tools GLOP) with a small throughput
  penalty to break simultaneous charge/discharge ties; it assumes the
  battery model and directive constraints given are jointly feasible; a
  genuinely infeasible scenario (e.g. contradictory windows) surfaces as a
  controlled `500`, not a partial/invalid schedule.
- `LLM_TIMEOUT_SECONDS` and `LLM_MAX_RETRIES` are configurable; with the
  defaults (`20s` timeout, `2` retries) a fully-exhausted retry path can
  approach the platform's request budget. If deploying somewhere with a
  strict end-to-end response deadline, consider lowering
  `LLM_TIMEOUT_SECONDS` and/or `LLM_MAX_RETRIES` in `.env`.
- The dashboard under `/dashboard` is a demo aid only; it is not part of
  the judged API contract and reads the same public sample pack this
  README uses for the reproduction test above.

## Secret handling

- Never commit `.env`, API keys, tokens, or passwords. `.env` is already
  listed in `.gitignore`; only `.env.example` (with empty/placeholder
  values) is committed.
- No secret values appear anywhere in this README — only environment
  variable *names*. Fill in real values locally in your own `.env`.
- The service never echoes secrets, raw prompts containing secrets, or
  stack traces in logs or API responses; `DEBUG=true` (local-only) adds an
  exception message to `500` bodies but never a key or token.
