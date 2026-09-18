"""FastAPI application — the two endpoints the judge harness exercises (Section 06)."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status

from app import __version__
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.schemas.request import OptimizeEnergyRequest
from app.schemas.response import HealthResponse, OptimizeEnergyResponse
from app.services import pipeline
from app.services.scenario_checks import check_scenario

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    logger.info("%s v%s starting", settings.app_name, __version__)
    if not settings.llm_configured:
        logger.warning("LLM_API_KEY is not set — note interpretation will fall back.")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="GridWise — LLM-Assisted Energy Optimization",
    description=(
        "BUP CSE Fest 2026 preliminary. Interprets campus operator notes with an "
        "LLM, validates the extracted directives deterministically, and returns a "
        "valid low-cost 24-hour energy schedule."
    ),
    version=__version__,
    lifespan=lifespan,
)

register_exception_handlers(app)


@app.middleware("http")
async def log_request_duration(request: Request, call_next) -> Response:
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
    logger.info(
        "%s %s -> %d in %.1f ms",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Readiness probe",
)
async def health() -> HealthResponse:
    """Section 06.2: HTTP 200 with `{"status": "ok"}` when the service is ready."""
    return HealthResponse()


@app.post(
    "/optimize-energy",
    response_model=OptimizeEnergyResponse,
    status_code=status.HTTP_200_OK,
    summary="Interpret operator notes and return a 24-hour schedule",
    responses={
        400: {"description": "Malformed JSON or structurally invalid request"},
        422: {"description": "Well-formed but semantically invalid request"},
        500: {"description": "Controlled internal error"},
    },
)
async def optimize_energy(request: OptimizeEnergyRequest) -> OptimizeEnergyResponse:
    """Run the Section 03 flow for one scenario.

    Structural failures never reach this body — FastAPI rejects them first and the
    handler in `app.core.errors` turns them into 400.
    """
    check_scenario(request)  # raises SemanticValidationError -> 422
    return pipeline.run(request)
