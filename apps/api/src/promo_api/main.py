"""ASGI entry point: `uvicorn promo_api.main:create --factory`.

Configuration comes only from `PROMO_API_*` environment variables; missing or unsafe values stop
the process instead of falling back to an insecure mode.
"""

from __future__ import annotations

from fastapi import FastAPI

from promo_api.app import create_app
from promo_api.settings import ApiSettings


def create() -> FastAPI:
    return create_app(ApiSettings())  # values come from the environment
