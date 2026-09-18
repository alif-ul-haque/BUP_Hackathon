"""Error contract for the API (Problem Statement Section 06.1).

    400 — malformed JSON or a structurally invalid request
    422 — well-formed but semantically invalid request
    500 — controlled internal error, never a raw stack trace

FastAPI answers request-validation failures with 422 by default; Section 06.1
assigns those to 400, so the handler below remaps them and 422 is reserved for
the semantic checks the service runs itself.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import DEBUG

logger = logging.getLogger(__name__)


class SemanticValidationError(Exception):
    """A well-formed scenario that cannot describe a real system — HTTP 422."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


class OptimizationError(Exception):
    """The optimizer could not produce a valid schedule — HTTP 500."""


def _body(detail: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"detail": detail}
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _summarize(errors: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """Trim Pydantic's error list to something small and JSON-safe."""
    summary = []
    for error in errors[:limit]:
        summary.append(
            {
                "field": ".".join(str(part) for part in error.get("loc", ())),
                "message": error.get("msg", ""),
                "type": error.get("type", ""),
            }
        )
    return summary


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _on_request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        malformed_json = any(error.get("type") == "json_invalid" for error in errors)
        detail = (
            "Malformed JSON body."
            if malformed_json
            else "Structurally invalid request body."
        )
        logger.warning("400 on %s: %s", request.url.path, detail)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_body(detail, errors=_summarize(errors)),
        )

    @app.exception_handler(SemanticValidationError)
    async def _on_semantic_error(
        request: Request, exc: SemanticValidationError
    ) -> JSONResponse:
        logger.warning("422 on %s: %s", request.url.path, exc.message)
        return JSONResponse(
            status_code=422,  # spelled out: Starlette renamed the constant
            content=_body(exc.message, field=exc.field),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _on_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _on_unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # Full trace to the logs, a generic message to the caller.
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_body(
                "Internal error while processing the scenario.",
                cause=f"{type(exc).__name__}: {exc}" if DEBUG else None,
            ),
        )
