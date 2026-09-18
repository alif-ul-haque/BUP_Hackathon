"""Environment-driven settings, loaded once from `.env` plus the real environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _get_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None and value.strip() else default


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get_str(name, str(default)))
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get_str(name, str(default)))
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    return _get_str(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _get_tuple(name: str, default: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _get_str(name, default).split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    # --- service ---
    app_name: str = "GridWise Energy Optimizer"
    log_level: str = "INFO"

    # --- LLM (Section 02/08) ---
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "gpt-4o-mini"
    #: Tried in order when the primary model is overloaded, rate-limited, or slow.
    llm_fallback_models: tuple[str, ...] = ()
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2

    # --- optimizer (Section 05) ---
    solver_time_limit_seconds: float = 10.0

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_name=_get_str("APP_NAME", "GridWise Energy Optimizer"),
        log_level=_get_str("LOG_LEVEL", "INFO").upper(),
        llm_api_key=_get_str("LLM_API_KEY", _get_str("OPENAI_API_KEY", "")),
        llm_base_url=_get_str("LLM_BASE_URL", ""),
        llm_model=_get_str("LLM_MODEL", "gpt-4o-mini"),
        llm_fallback_models=_get_tuple("LLM_FALLBACK_MODELS", ""),
        llm_temperature=_get_float("LLM_TEMPERATURE", 0.0),
        llm_timeout_seconds=_get_float("LLM_TIMEOUT_SECONDS", 20.0),
        llm_max_retries=_get_int("LLM_MAX_RETRIES", 2),
        solver_time_limit_seconds=_get_float("SOLVER_TIME_LIMIT_SECONDS", 10.0),
    )


#: Local-only switch: includes the exception message in 500 bodies. Never on in
#: deployment — Section 06.1 forbids exposing raw stack traces.
DEBUG = _get_bool("DEBUG", False)
