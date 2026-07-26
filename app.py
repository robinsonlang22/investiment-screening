"""ASGI entry point for ``uvicorn app:app``."""

from api.app import app

__all__ = ["app"]
